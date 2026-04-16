# Scoring experiments

Parallel A/B/C/D framework for evaluating variants of the flip/rent scoring
engine. Each hypothesis runs in its own git worktree with its own headless
Claude Code agent and its own scratch SQLite DB, so variants never collide.

## Layout

```
experiments/
  orchestrator.py        # main entry point
  hypotheses.json        # configure which variants to try
  prompts/
    agent_brief.md       # rendered into each variant agent's initial prompt
    aggregator_brief.md  # initial prompt for the final aggregator
  lib/
    scratch_db.py        # clone prod DB into a per-experiment scratch copy
    labels.py            # load data/labeled_deals.json (optional ground truth)
    eval.py              # distribution / label-correlation / stability rubric
    report.py            # REPORT.md writer used by variant agents
  _collected/<slug>/REPORT.md   # populated by orchestrator after each run
  _logs/                        # per-agent stdout/stderr
  SUMMARY.md                    # written by the aggregator
```

## Running

```bash
# From repo root:
python3 experiments/orchestrator.py                      # all hypotheses
python3 experiments/orchestrator.py --only log-scaling    # one variant
python3 experiments/orchestrator.py --dry-run             # print the plan
python3 experiments/orchestrator.py --skip-aggregate      # variants only
python3 experiments/orchestrator.py --keep-worktrees      # debug a failed variant
```

The orchestrator:
1. creates `exp/<slug>` branches + worktrees under
   `.claude/worktrees/exp-<slug>`,
2. runs `claude -p <brief> --dangerously-skip-permissions` in each worktree
   concurrently (default max 4 in parallel),
3. copies `experiments/<slug>/REPORT.md` out of each worktree into
   `experiments/_collected/<slug>/REPORT.md` in the main repo,
4. launches the aggregator agent in the main repo, which writes
   `experiments/SUMMARY.md` with a ranked recommendation and merge
   instructions.

Worktrees are removed after the run unless `--keep-worktrees`. Branches are
NOT deleted — SUMMARY.md's merge instructions reference them by name.

## Ground truth: `data/labeled_deals.json`

Schema — **disjoint by kind** to prevent rent outcomes from grading flip
and vice versa:

```json
{
  "flip_labels": [
    {"source": "idealista", "source_id": "33123456",
     "label": "good", "source_of_label": "derived",
     "notes": "sold in 18d, 0 cuts", "labeled_at": "2026-04-15"}
  ],
  "rent_labels": [
    {"source": "idealista", "source_id": "44987654",
     "label": "good", "source_of_label": "manual",
     "notes": "yield ~6%, found tenant in 1 week",
     "labeled_at": "2026-04-15"}
  ]
}
```

`label` ∈ `good | neutral | bad`. `source_of_label` ∈ `manual | derived`.
Missing labels are handled gracefully — the evaluator silently skips
label-correlation for that kind and the aggregator notes the handicap.

### Populating labels

Two complementary paths:

**A) Derived labels from real outcomes** — run

```bash
python3 scripts/derive_labels.py
```

It reads `listing_history` status transitions (which are exogenous to the
scorer — they record what actually happened in the market) and emits
`flip_labels`:

- `good` — status → `sold` within 45 days, 0 price cuts
- `bad` — status → `delisted` after 180 days with ≥2 cuts, OR any delist
  with ≥10% cumulative price cut
- `neutral` — every other terminal transition

Thresholds are tunable via `--fast-days / --slow-days / --big-cut-pct`.
Only flip_labels are derived — **rent outcomes are not in the DB, so
auto-deriving rent labels from price behavior alone would re-use the
same features rent_score consumes (circular).** Rent labels are
manual-only.

**B) Manual curation** — run

```bash
python3 scripts/label_deal.py <listing_id> good -n "T2 Anjos, strong comps"
python3 scripts/label_deal.py <listing_id> --rent good -n "yield ~6%"
python3 scripts/label_deal.py --list               # show flip labels
python3 scripts/label_deal.py --list --rent        # show rent labels
```

Sale-vs-rental is inferred from which table the `listing_id` sits in.
Manual labels are never overwritten by the derivation script — you can
re-run `derive_labels.py` safely.

**C) Audit before trusting** — derivation rules are heuristics. Sample
and eyeball a batch before you let a variant train on them:

```bash
python3 scripts/audit_labels.py                     # 10 per class, read-only
python3 scripts/audit_labels.py --only bad -n 20    # 20 bad labels only
python3 scripts/audit_labels.py --interactive       # keep/override/discard
```

For each sampled label the audit prints the listing, current flip_score,
and the full price + status timeline. In interactive mode you can:

- `k` keep as-is
- `g`/`n`/`b` override the label — promoted to `manual`, survives re-derives
- `d` discard the label entirely
- `s` skip, `q` quit and save

Recommended cadence: after each `derive_labels.py` run, audit ~10 per
class. If the base rate of obvious mislabels is high, tighten the
thresholds (`--fast-days`, `--slow-days`, `--big-cut-pct`) rather than
fixing individual rows.

## Evaluation rubric

Implemented in `lib/eval.py`, used uniformly by every variant:

| Component | Weight | Definition |
| --- | --- | --- |
| Label correlation (Spearman ρ) | 45 % | score vs labeled_deals.json |
| Neighborhood stability | 20 % | 1 − MAD(parish means)/overall sd |
| Distribution spread | 20 % | peaks at sd = 17.5, zero at sd = 0 or 35 |
| Production sanity | 15 % | ρ against current `flip_score` ∈ [0.3, 0.9] |

Missing components (e.g. labels absent) are dropped and the remaining
weights renormalize per variant. Aggregator flags any variant that is
ranked primarily because of a missing component.

## Adding a hypothesis

Edit `hypotheses.json` and add:

```json
{
  "slug": "my-variant",
  "title": "Short human-readable title",
  "brief": "1–3 sentences: the idea, the rationale, the scope of changes."
}
```

Keep the brief specific. The agent inherits it verbatim and has no other
channel to learn what you want.
