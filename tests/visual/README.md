# Visual Regression Suite

Captures deterministic screenshots of the frontend across ~13 representative UI states and pixel-diffs them against checked-in baselines.

## Quick start

```bash
# 1. From repo root, launch the app via the Claude Code preview:
#    preview_start("app")  →  note the frontend URL (usually http://localhost:3000)

# 2. First-time baseline capture
cd tests/visual
npm install
npx playwright install chromium
npm run baseline -- --url http://localhost:3000

# 3. Check mode (run after UI changes)
npm run check -- --url http://localhost:3000

# 4. If a diff is intentional, promote it
node approve.mjs sidebar-collapsed filter-status-sold
# or
node approve.mjs --all
```

Exit codes: `0` all green · `1` one or more states failed the threshold · `2` infrastructure failure.

## Layout

```
tests/visual/
  states.mjs        Manifest: one entry per snapshot state. Edit this to add states.
  runner.mjs        Playwright driver — launches headless Chromium, iterates states, writes PNGs.
  diff.mjs          pixelmatch-based diff; emits diffs/<name>.png + diffs/report.json.
  run.mjs           CLI entry (baseline | check).
  approve.mjs       Promote actuals/<name>.png → baselines/<name>.png.
  baselines/        Checked into git — the source of truth.
  actuals/          Generated per run — gitignored.
  diffs/            Generated per run — gitignored. Human-readable diff images + report.json.
```

## Why Playwright, not the Preview MCP?

The `preview_*` MCP tools can only be called by the Claude agent, not from a CLI script. For a hermetic, CI-invokable runner we drive a headless Chromium directly via Playwright. The Preview MCP remains useful for **live debugging inside the agent loop** (see `.claude/skills/visual-regression/SKILL.md`): when a diff fails, the agent uses `preview_eval` / `preview_screenshot` / `preview_inspect` to investigate the state in real time, then edits code or updates the baseline.

## Adding a new state

Append an entry to `STATES` in [states.mjs](states.mjs):

```js
{
  name: 'my-new-state',
  viewport: { width: 1440, height: 900 },
  threshold: 0.1,       // pixelmatch per-pixel threshold (lower = stricter)
  maxDiffPct: 0.5,      // % of pixels allowed to differ before failing
  stabilize: 400,       // ms to wait after setup() completes
  async setup(page) {
    await waitForListings(page);
    await page.getByRole('button', { name: /my thing/i }).click();
  },
}
```

Then `npm run baseline -- --only my-new-state` to capture.

## Determinism caveats

- **DB snapshot**: baselines are tied to the DB that was in the worktree when they were captured. When the main-branch DB changes significantly, expect legitimate diffs in `app-initial-load`, `listing-*`, and `sidebar-*`. Re-baseline intentionally.
- **Map tiles**: come from external tile servers. Map states use looser `maxDiffPct` (2-6%) and longer `stabilize`.
- **Fonts**: system fonts differ across OSes — baselines captured on macOS may diff on Linux CI. Run in the same environment or add a font-loading stabilize step.
- **Animations**: globally disabled by `runner.mjs` via injected CSS.

## CI hook

A GitHub Actions job can run:

```yaml
- run: cd tests/visual && npm ci && npx playwright install --with-deps chromium
- run: cd tests/visual && npm run check -- --url http://localhost:3000
- uses: actions/upload-artifact@v4
  if: failure()
  with:
    name: visual-diffs
    path: tests/visual/diffs/
```

(Not yet wired — add when we move this to CI.)
