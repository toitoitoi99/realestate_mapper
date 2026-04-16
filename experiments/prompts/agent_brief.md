# Scoring variant experiment — {title}

You are running as a headless Claude Code agent inside an isolated git
worktree (`{worktree_path}`) on branch `{branch}`. Another N-1 agents are
running the same protocol in parallel on their own branches — do not touch
anything outside this worktree.

## Hypothesis

{brief}

## Your deliverables

1. **Implement the variant.** Modify `backend/scoring_engine.py`,
   `backend/signal_batch.py`, and/or `backend/region_profiles.py` as needed.
   Keep the change minimal and targeted — this is an experiment, not a
   rewrite. Do NOT modify anything under `experiments/lib/` (shared
   evaluation code — keeping it stable is what makes cross-variant
   comparison valid).

2. **Recompute scores into a scratch DB.** Use the helper:

   ```bash
   python3 -c "from experiments.lib.scratch_db import clone_db, add_score_columns; \
       from pathlib import Path; \
       clone_db(Path('backend/data/lisboa_realestate.db'), Path('experiments/{slug}/scratch.db')); \
       add_score_columns(Path('experiments/{slug}/scratch.db'), {{'variant_flip_score': 'REAL', 'variant_flip_factors': 'TEXT', 'variant_rent_score': 'REAL', 'variant_rent_factors': 'TEXT'}})"
   ```

   Then run your modified signal_batch against the scratch DB, writing to
   `variant_flip_score` / `variant_rent_score` columns (NOT `flip_score` —
   we must preserve production scores for comparison). The easiest path is
   to add a `--score-col` flag or a separate `signal_batch_variant.py`
   driver. Keep the diff small.

3. **Evaluate + write REPORT.md.** Use the shared evaluator. Minimal
   harness (put in `experiments/{slug}/run_eval.py` or run inline):

   ```python
   from pathlib import Path
   from experiments.lib.eval import evaluate, top_bottom
   from experiments.lib.report import write_report
   from experiments.lib.labels import default_path as labels_default

   db = Path("experiments/{slug}/scratch.db")
   # Flip variant: grade sales against flip_labels only.
   m = evaluate(db, "variant_flip_score", labels_path=labels_default(),
                table="sales", label_kind="flip")
   tb = top_bottom(db, "variant_flip_score", table="sales")
   # If you also recompute rent_score, evaluate it separately with
   # label_kind="rent" and table="rentals" — flip and rent labels are
   # disjoint by design; passing the wrong combination raises.
   write_report(
       Path("experiments/{slug}/REPORT.md"),
       slug="{slug}",
       title="{title}",
       hypothesis="""{brief}""",
       implementation_notes="... fill in what you changed and why ...",
       metrics=m,
       top_bottom=tb,
   )
   ```

4. **Commit.** `git add -A && git commit -m "exp({slug}): <one-line summary>"`.
   Do NOT push. The orchestrator will merge / discard per the aggregator's
   recommendation.

## Rubric (shared across variants)

- **Distribution shape** — sd in [10, 25], not bimodal, no collapse.
- **Correlation with labels** (`data/labeled_deals.json`) — Spearman ρ.
  If the file is missing or has <3 usable labels, the evaluator skips this
  and notes it. Do not invent labels.
- **Neighborhood stability** — per-parish mean should not dominate sd.
- **Sanity vs production** — Spearman ρ against current `flip_score`
  should be in [0.3, 0.9]: too high means no change, too low means the
  signal is probably broken.

## Ground rules

- Do not touch `data/labeled_deals.json` or any file under `experiments/lib/`.
- Do not modify `backend/data/lisboa_realestate.db`. Only touch
  `experiments/{slug}/scratch.db`.
- If you need Python packages, install into a local venv or document the
  dep in REPORT.md — do not modify global state.
- Budget: spend the majority of effort on implementing the variant well
  and writing a clear REPORT. The scoring run can sample (1–5k listings)
  if the full catalog would be too slow — note the sample size in the
  report.
- When you are done, exit. Do not open PRs, do not push, do not tag.
