#!/usr/bin/env node
// CLI entry for the visual regression suite.
//
//   node run.mjs --mode baseline [--url http://localhost:3000]
//   node run.mjs --mode check    [--url http://localhost:3000]
//   node run.mjs --mode check --only sidebar-collapsed,map-zoom-14
//
// Exit codes:
//   0 — all pass (check mode) or baselines written (baseline mode)
//   1 — one or more states failed the diff threshold
//   2 — infrastructure error (app not reachable, capture crashed, etc.)

import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { captureAll } from './runner.mjs';
import { diffAll, formatReport } from './diff.mjs';
import { STATES } from './states.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function parseArgs(argv) {
  const out = { mode: 'check', url: 'http://localhost:3000', only: null };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--mode') out.mode = argv[++i];
    else if (a === '--url') out.url = argv[++i];
    else if (a === '--only') out.only = argv[++i].split(',').map(s => s.trim());
    else if (a === '-h' || a === '--help') out.help = true;
  }
  return out;
}

function help() {
  console.log(`Usage: node run.mjs --mode <baseline|check> [--url URL] [--only NAME,...]

  --mode baseline   Capture screenshots and write them to baselines/.
                    Overwrites existing baselines without prompting.
  --mode check      Capture screenshots to actuals/ and diff against baselines/.
                    Writes diffs/<name>.png and diffs/report.json. Exits 1 on fail.
  --url URL         Frontend URL (default http://localhost:3000).
  --only NAMES      Comma-separated state names to run (default: all).
`);
}

async function main() {
  const args = parseArgs(process.argv);
  if (args.help) { help(); return 0; }

  // Optional state filter
  if (args.only) {
    const wanted = new Set(args.only);
    const filtered = STATES.filter(s => wanted.has(s.name));
    if (!filtered.length) {
      console.error(`No states match --only: ${args.only.join(', ')}`);
      return 2;
    }
    STATES.length = 0;
    STATES.push(...filtered);
  }

  const outDir = args.mode === 'baseline'
    ? path.join(__dirname, 'baselines')
    : path.join(__dirname, 'actuals');

  console.log(`\nVisual regression · mode=${args.mode} · url=${args.url}`);
  console.log(`Capturing ${STATES.length} state(s) into ${path.relative(process.cwd(), outDir)}/\n`);

  let captures;
  try {
    captures = await captureAll({
      url: args.url,
      outDir,
      onProgress: ({ name, ok, error }) => {
        console.log(`  ${ok ? 'OK ' : 'ERR'} ${name}${error ? '  ' + error : ''}`);
      },
    });
  } catch (err) {
    console.error(`\nCapture failed: ${err?.message || err}`);
    return 2;
  }

  const captureFails = captures.filter(c => !c.ok);
  if (args.mode === 'baseline') {
    console.log(`\nWrote ${captures.length - captureFails.length} baseline(s).`);
    if (captureFails.length) {
      console.log(`${captureFails.length} state(s) failed to capture — inspect above.`);
      return 1;
    }
    return 0;
  }

  // check mode → diff
  const baselineDir = path.join(__dirname, 'baselines');
  const diffDir = path.join(__dirname, 'diffs');
  const { report, reportPath } = await diffAll({
    baselineDir,
    actualDir: outDir,
    diffDir,
  });

  console.log(`\n${formatReport(report)}`);
  console.log(`\nReport: ${path.relative(process.cwd(), reportPath)}`);

  const anyFail = report.states.some(s => s.status && s.status !== 'pass') || captureFails.length > 0;
  return anyFail ? 1 : 0;
}

main().then(code => process.exit(code ?? 0), err => {
  console.error(err);
  process.exit(2);
});
