---
name: visual-regression
description: Run the visual regression suite after UI changes and drive the iterate-until-green loop. Invoke whenever a task touches frontend/ code that renders anything visual.
---

# Visual regression — iterate until green

You MUST invoke this skill whenever a task modifies anything in `frontend/src/` that affects rendering (components, CSS, layout, styling, map, sidebar, charts, badges). Do not mark a UI task complete without it.

## The loop

1. **Implement** the user's requested change.
2. **Launch or confirm the app is running.** Use `preview_start("app")` if needed. Note the frontend URL printed in the logs (usually `http://localhost:3000`).
3. **Run `npm run check`** from `tests/visual/`:
   ```bash
   cd tests/visual && npm run check -- --url http://localhost:<port>
   ```
4. **Read `tests/visual/diffs/report.json`.** It lists every state with `status` (`pass` | `fail` | `missing-baseline` | `dimension-mismatch`) and `diffPct`.
5. **For each failing state**, decide:
   - **Intended diff** (the change you just made produced this) → after verifying visually, promote via `node approve.mjs <name>`. Explain in your summary to the user *why* the baseline was updated.
   - **Regression** (unintended change) → fix the code, re-run `npm run check`.
   - **Flakiness** (map tiles, font rendering, ~0.3% pixel noise) → bump the state's `maxDiffPct` in `states.mjs` with a comment explaining why, then re-run.
6. **Inspect failing diffs** using Preview MCP for speed:
   - `preview_screenshot` to see the current state live.
   - `preview_snapshot` to read the accessibility tree.
   - `preview_inspect` for computed CSS (far more reliable than pixel comparison for font/color/spacing).
   - Open `tests/visual/diffs/<name>.png` to see the red-pixel overlay.
7. **Iterate** until every state is `pass` or explicitly approved.
8. **Commit baselines separately** when you intentionally update them — one commit per logical UI change, with a message like `Re-baseline sidebar-collapsed after nav refactor`. This keeps git diff noise reviewable.

## When a baseline is missing

If `status: missing-baseline`, you are on a fresh branch or a new state was added. Run `npm run baseline -- --only <name>` (or without `--only` for all) to capture, then re-run check. Commit the new PNG.

## When the runner itself fails

- **Capture error** (`ERR` in the runner output): usually a selector drift — a button was renamed or restructured. Open `states.mjs`, update the selector in the failing state's `setup()`, re-run. Do NOT disable the state silently.
- **`dimension-mismatch`**: viewport changed or the page is rendering a different layout. Investigate the viewport config; do not paper over with `approve.mjs`.

## Before declaring UI work complete

Before your final reply to the user, make sure you can honestly say:

- `npm run check` was run (or explicitly skipped with a reason — e.g. the change isn't browser-observable).
- Every failing state is either now passing or has an approved baseline update you can defend to the user.
- You mention, in your summary, which baselines you updated and why.

If you cannot say all three, you are NOT done.
