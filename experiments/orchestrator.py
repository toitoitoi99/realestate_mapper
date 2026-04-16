#!/usr/bin/env python3
"""Parallel scoring-experiment orchestrator.

Pipeline:
  1. For each hypothesis in hypotheses.json, create a git worktree from
     `main` at `.claude/worktrees/exp-<slug>` on branch `exp/<slug>`.
  2. Launch `claude -p` (headless) in each worktree with a rendered
     agent brief. All variant agents run in parallel.
  3. Wait for all variants to finish. Collect each worktree's
     `experiments/<slug>/REPORT.md` into `experiments/_collected/<slug>/`
     in the main repo.
  4. Launch a final aggregator agent in the main repo with the
     aggregator brief; it writes `experiments/SUMMARY.md`.

Usage:
  python3 experiments/orchestrator.py                 # run all hypotheses
  python3 experiments/orchestrator.py --only log-scaling,gradient-weights
  python3 experiments/orchestrator.py --dry-run       # print plan only
  python3 experiments/orchestrator.py --skip-aggregate

The orchestrator should be run from the MAIN repo checkout (not from
inside a worktree). It finds the repo root by walking up from this file.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


HERE = Path(__file__).resolve().parent


def _resolve_main_repo(start: Path) -> Path:
    """Walk up from `start` to find the main git checkout (not a worktree).

    `git rev-parse --git-common-dir` returns the shared .git dir even from
    inside a worktree; its parent is the main working tree.
    """
    r = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--git-common-dir"],
        capture_output=True, text=True, check=True,
    )
    common = Path(r.stdout.strip())
    if not common.is_absolute():
        common = (start / common).resolve()
    return common.parent


REPO_ROOT = _resolve_main_repo(HERE)


@dataclass
class Hypothesis:
    slug: str
    title: str
    brief: str


def load_hypotheses(path: Path, only: Optional[set[str]] = None) -> list[Hypothesis]:
    data = json.loads(path.read_text())
    out = []
    for h in data.get("hypotheses", []):
        if only and h["slug"] not in only:
            continue
        out.append(Hypothesis(slug=h["slug"], title=h["title"], brief=h["brief"]))
    return out


def render_brief(template: str, hyp: Hypothesis, worktree_path: Path, branch: str) -> str:
    return template.format(
        title=hyp.title,
        brief=hyp.brief,
        slug=hyp.slug,
        worktree_path=str(worktree_path),
        branch=branch,
    )


def git_main_branch(repo: Path) -> str:
    """Return `main` or `master`, whichever exists."""
    for candidate in ("main", "master"):
        r = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", candidate],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            return candidate
    raise RuntimeError("no main/master branch found")


def create_worktree(repo: Path, slug: str, base: str) -> tuple[Path, str]:
    branch = f"exp/{slug}"
    wt_path = repo / ".claude" / "worktrees" / f"exp-{slug}"
    if wt_path.exists():
        print(f"  [{slug}] worktree already exists at {wt_path} — reusing")
        return wt_path, branch
    # Create a fresh branch off base and a worktree pointing at it.
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", branch, str(wt_path), base],
        check=True,
    )
    return wt_path, branch


def remove_worktree(repo: Path, wt_path: Path) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "remove", "--force", str(wt_path)],
        check=False,
    )


def run_agent(
    wt_path: Path,
    prompt: str,
    log_path: Path,
    extra_allowed_dirs: list[Path] | None = None,
    timeout_s: int = 60 * 60,
) -> int:
    """Launch `claude -p` inside wt_path. Streams output to log_path. Returns exit code."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "claude", "-p", prompt,
        "--dangerously-skip-permissions",
    ]
    for d in extra_allowed_dirs or []:
        cmd += ["--add-dir", str(d)]
    with log_path.open("w") as logf:
        logf.write(f"# cwd: {wt_path}\n# cmd: {shlex.join(cmd[:2])} <prompt> ...\n\n")
        logf.flush()
        proc = subprocess.Popen(
            cmd, cwd=str(wt_path),
            stdout=logf, stderr=subprocess.STDOUT,
            env={**os.environ, "CLAUDE_CODE_NONINTERACTIVE": "1"},
        )
        try:
            return proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            logf.write(f"\n\n# TIMEOUT after {timeout_s}s — killed\n")
            return 124


def collect_report(wt_path: Path, slug: str, dest_root: Path) -> Optional[Path]:
    src = wt_path / "experiments" / slug / "REPORT.md"
    if not src.exists():
        return None
    dest_dir = dest_root / slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "REPORT.md"
    shutil.copy2(src, dest)
    # Also copy any supporting files (weights.json, run_eval.py, etc.) so the
    # aggregator can cite them.
    for extra in (wt_path / "experiments" / slug).iterdir():
        if extra.name == "REPORT.md" or extra.name == "scratch.db":
            continue
        if extra.is_file() and extra.stat().st_size < 2_000_000:
            shutil.copy2(extra, dest_dir / extra.name)
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hypotheses", default=str(HERE / "hypotheses.json"))
    ap.add_argument("--only", help="Comma-separated slugs to run")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-aggregate", action="store_true")
    ap.add_argument("--keep-worktrees", action="store_true",
                    help="Don't remove worktrees after run")
    ap.add_argument("--timeout", type=int, default=60 * 60,
                    help="Per-agent timeout in seconds")
    ap.add_argument("--max-parallel", type=int, default=4)
    args = ap.parse_args()

    only = set(s.strip() for s in args.only.split(",")) if args.only else None
    hyps = load_hypotheses(Path(args.hypotheses), only=only)
    if not hyps:
        print("no hypotheses selected", file=sys.stderr)
        sys.exit(2)

    template = (HERE / "prompts" / "agent_brief.md").read_text()
    collected_root = REPO_ROOT / "experiments" / "_collected"
    logs_root = REPO_ROOT / "experiments" / "_logs"
    logs_root.mkdir(parents=True, exist_ok=True)

    base = git_main_branch(REPO_ROOT)
    print(f"base branch: {base}")
    print(f"repo root:   {REPO_ROOT}")
    print(f"running {len(hyps)} variant(s): {[h.slug for h in hyps]}")

    if args.dry_run:
        for h in hyps:
            wt = REPO_ROOT / ".claude" / "worktrees" / f"exp-{h.slug}"
            print(f"  would create {wt} on branch exp/{h.slug}")
        print("  would run aggregator: yes" if not args.skip_aggregate else "  skip aggregate")
        return

    # 1. create all worktrees up front (cheap, serial)
    prepared: list[tuple[Hypothesis, Path, str]] = []
    for h in hyps:
        wt, br = create_worktree(REPO_ROOT, h.slug, base)
        prepared.append((h, wt, br))

    # 2. launch agents in parallel
    def _one(h: Hypothesis, wt: Path, br: str) -> tuple[str, int, Path]:
        prompt = render_brief(template, h, wt, br)
        log = logs_root / f"{h.slug}.log"
        print(f"  [{h.slug}] launching agent → log {log}")
        # Allow reads of the main repo so the agent can reference stable
        # experiments/lib helpers even if its worktree copy diverges.
        rc = run_agent(wt, prompt, log, timeout_s=args.timeout)
        return h.slug, rc, log

    started = time.time()
    results: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=args.max_parallel) as pool:
        futs = [pool.submit(_one, h, wt, br) for h, wt, br in prepared]
        for fut in as_completed(futs):
            slug, rc, log = fut.result()
            results[slug] = rc
            mark = "✓" if rc == 0 else f"✗ rc={rc}"
            print(f"  [{slug}] done in {time.time()-started:.0f}s {mark}")

    # 3. collect reports
    collected: list[str] = []
    for h, wt, _ in prepared:
        dest = collect_report(wt, h.slug, collected_root)
        if dest:
            print(f"  [{h.slug}] report → {dest.relative_to(REPO_ROOT)}")
            collected.append(h.slug)
        else:
            print(f"  [{h.slug}] NO REPORT produced — see {logs_root/f'{h.slug}.log'}")

    # 4. aggregator
    if collected and not args.skip_aggregate:
        agg_prompt = (HERE / "prompts" / "aggregator_brief.md").read_text()
        agg_log = logs_root / "_aggregator.log"
        print(f"  [aggregator] launching → log {agg_log}")
        rc = run_agent(REPO_ROOT, agg_prompt, agg_log, timeout_s=args.timeout)
        print(f"  [aggregator] rc={rc}  → see experiments/SUMMARY.md")

    # 5. cleanup
    if not args.keep_worktrees:
        for _, wt, _ in prepared:
            remove_worktree(REPO_ROOT, wt)
            print(f"  cleaned {wt.name}")
    else:
        print("  --keep-worktrees set, not removing")

    failed = [s for s, rc in results.items() if rc != 0]
    if failed:
        print(f"FAILED: {failed}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
