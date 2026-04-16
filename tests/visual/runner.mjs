// Shared Playwright driver used by both baseline capture and check runs.
//
// Usage:
//   import { captureAll } from './runner.mjs';
//   const results = await captureAll({ url, outDir });
//
// Returns [{ name, path, ok, error? }] in the same order as STATES.

import { chromium } from 'playwright';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { STATES } from './states.mjs';

const DISABLE_ANIMATIONS_CSS = `
  *, *::before, *::after {
    animation-duration: 0s !important;
    animation-delay: 0s !important;
    transition-duration: 0s !important;
    transition-delay: 0s !important;
    caret-color: transparent !important;
  }
  /* Blinking cursors, spinning loaders, etc. */
  .leaflet-marker-icon, .leaflet-marker-shadow { transition: none !important; }
`;

export async function captureAll({ url, outDir, onProgress = () => {} }) {
  await fs.mkdir(outDir, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const results = [];
  try {
    for (const state of STATES) {
      const outPath = path.join(outDir, `${state.name}.png`);
      try {
        const context = await browser.newContext({
          viewport: state.viewport,
          deviceScaleFactor: 1,
          reducedMotion: 'reduce',
        });
        const page = await context.newPage();
        await page.addInitScript(() => {
          // Seed Math.random for a touch more determinism; listings rely on server ordering,
          // but any client-side randomness (e.g. animation kickoffs) benefits from this.
          let seed = 1337;
          Math.random = function () {
            seed = (seed * 16807) % 2147483647;
            return seed / 2147483647;
          };
        });
        await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
        await page.addStyleTag({ content: DISABLE_ANIMATIONS_CSS });
        await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {});
        await state.setup(page);
        if (state.stabilize) await page.waitForTimeout(state.stabilize);
        const buf = await page.screenshot({ type: 'png', fullPage: false });
        await fs.writeFile(outPath, buf);
        results.push({ name: state.name, path: outPath, ok: true });
        onProgress({ name: state.name, ok: true });
        await context.close();
      } catch (err) {
        results.push({ name: state.name, path: outPath, ok: false, error: String(err?.message || err) });
        onProgress({ name: state.name, ok: false, error: String(err?.message || err) });
      }
    }
  } finally {
    await browser.close();
  }
  return results;
}
