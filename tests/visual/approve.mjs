#!/usr/bin/env node
// Promote a set of current captures (actuals/) into baselines/.
//
//   node approve.mjs NAME [NAME ...]
//   node approve.mjs --all
//
// Use after a --mode check run where the diff is intentional.

import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { STATES } from './states.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

async function main() {
  const args = process.argv.slice(2);
  if (!args.length) {
    console.error('Usage: node approve.mjs NAME [NAME ...]  |  --all');
    process.exit(2);
  }
  const names = args.includes('--all') ? STATES.map(s => s.name) : args;
  let promoted = 0;
  for (const name of names) {
    const src = path.join(__dirname, 'actuals', `${name}.png`);
    const dst = path.join(__dirname, 'baselines', `${name}.png`);
    try {
      await fs.copyFile(src, dst);
      console.log(`  approved ${name}`);
      promoted++;
    } catch (err) {
      console.error(`  skip ${name}: ${err.message}`);
    }
  }
  console.log(`\n${promoted} baseline(s) updated.`);
}

main();
