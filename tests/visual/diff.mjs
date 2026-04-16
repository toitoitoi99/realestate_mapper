// Pixel-level diff between baselines/ and actuals/.
//
// For each STATE:
//   - Decode both PNGs
//   - If dimensions differ → fail with clear error
//   - Run pixelmatch, produce diff PNG
//   - Compare diffPct against state.maxDiffPct
//
// Emits:
//   - diffs/<name>.png          — visual diff image (red pixels where they differ)
//   - diffs/report.json         — structured results
//   - stdout summary

import { promises as fs } from 'node:fs';
import path from 'node:path';
import { PNG } from 'pngjs';
import pixelmatch from 'pixelmatch';
import { STATES } from './states.mjs';

async function readPng(filePath) {
  const buf = await fs.readFile(filePath);
  return PNG.sync.read(buf);
}

export async function diffAll({ baselineDir, actualDir, diffDir }) {
  await fs.mkdir(diffDir, { recursive: true });
  const report = { generatedAt: new Date().toISOString(), states: [] };
  for (const state of STATES) {
    const basePath = path.join(baselineDir, `${state.name}.png`);
    const actualPath = path.join(actualDir, `${state.name}.png`);
    const diffPath = path.join(diffDir, `${state.name}.png`);
    const entry = { name: state.name, baseline: basePath, actual: actualPath, diff: diffPath };
    try {
      await fs.access(basePath);
    } catch {
      entry.status = 'missing-baseline';
      entry.message = `No baseline at ${basePath}. Run \`npm run baseline\` first.`;
      report.states.push(entry);
      continue;
    }
    try {
      await fs.access(actualPath);
    } catch {
      entry.status = 'missing-actual';
      entry.message = `No capture at ${actualPath}. Did the runner fail?`;
      report.states.push(entry);
      continue;
    }
    const base = await readPng(basePath);
    const act = await readPng(actualPath);
    if (base.width !== act.width || base.height !== act.height) {
      entry.status = 'dimension-mismatch';
      entry.baselineSize = [base.width, base.height];
      entry.actualSize = [act.width, act.height];
      entry.message = `Dimensions differ: baseline ${base.width}x${base.height} vs actual ${act.width}x${act.height}`;
      report.states.push(entry);
      continue;
    }
    const diff = new PNG({ width: base.width, height: base.height });
    const numDiffPixels = pixelmatch(
      base.data, act.data, diff.data,
      base.width, base.height,
      { threshold: state.threshold ?? 0.1, includeAA: false },
    );
    const total = base.width * base.height;
    const diffPct = (numDiffPixels / total) * 100;
    await fs.writeFile(diffPath, PNG.sync.write(diff));
    entry.diffPixels = numDiffPixels;
    entry.totalPixels = total;
    entry.diffPct = Number(diffPct.toFixed(4));
    entry.maxDiffPct = state.maxDiffPct ?? 0.5;
    entry.status = diffPct <= entry.maxDiffPct ? 'pass' : 'fail';
    report.states.push(entry);
  }
  const reportPath = path.join(diffDir, 'report.json');
  await fs.writeFile(reportPath, JSON.stringify(report, null, 2));
  return { report, reportPath };
}

export function formatReport(report) {
  const lines = [];
  const pass = report.states.filter(s => s.status === 'pass').length;
  const fail = report.states.filter(s => s.status && s.status !== 'pass').length;
  for (const s of report.states) {
    const marker = s.status === 'pass' ? 'PASS'
      : s.status === 'fail' ? 'FAIL'
      : s.status?.toUpperCase() || 'UNKNOWN';
    const pct = s.diffPct != null ? `${s.diffPct}% (≤${s.maxDiffPct}%)` : (s.message || '');
    lines.push(`  [${marker}] ${s.name}  ${pct}`);
  }
  lines.push('');
  lines.push(`  ${pass} pass, ${fail} fail`);
  return lines.join('\n');
}
