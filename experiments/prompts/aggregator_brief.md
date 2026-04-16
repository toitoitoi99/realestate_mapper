# Scoring experiments — aggregator

You are running as a headless Claude Code agent after N variant agents have
each produced a `REPORT.md` under their own worktree. The orchestrator has
collected all reports into `experiments/_collected/<slug>/REPORT.md` in the
main repo.

## Your job

1. **Read every `experiments/_collected/*/REPORT.md`.** For each variant
   extract: rubric_score, distribution sd, spearman vs labels, spearman vs
   production, neighborhood stability, band shares.

2. **Rank the variants** on a weighted composite:
   - 45% label correlation (primary signal of a better scorer)
   - 20% neighborhood stability
   - 20% distribution spread in [10, 25]
   - 15% production-sanity band (ρ_prod between 0.3 and 0.9)

   If any metric is missing for a variant, redistribute its weight over
   the remaining metrics for that variant and note the handicap.

3. **Inspect the top 20 / bottom 20 tables** qualitatively. A variant that
   ranks high numerically but puts obvious junk (e.g. 40 m² lofts at
   €20k/m²) at the top should be flagged and down-weighted.

4. **Write `experiments/SUMMARY.md`** with:
   - One-paragraph executive recommendation (which variant to merge, or
     "keep production" if none clearly wins).
   - Ranked table (slug, rubric_score, ρ_labels, ρ_prod, stability, sd,
     qualitative notes).
   - Merge instructions for the winner:

     ```bash
     # From repo root, main checked out and clean:
     git fetch . refs/heads/{winner_branch}:{winner_branch}
     git merge --no-ff {winner_branch} -m "Adopt scoring variant: {winner_slug}"
     # Then backfill production:
     python3 backend/signal_batch.py   # rescores everything
     ```

   - "Keep around" notes for variants worth revisiting (e.g. promising
     idea but needs more labels).
   - "Discard" notes for variants to delete.

5. **Do not merge anything yourself.** Write instructions only. The human
   operator reviews SUMMARY.md and executes.

## Ground rules

- Do not modify any variant's code or REPORT.
- Do not run scoring yourself — trust the numbers in each REPORT, or flag
  suspicious ones for human review.
- Keep SUMMARY.md under 400 lines; link to each REPORT with relative paths.
