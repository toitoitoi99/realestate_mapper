#!/usr/bin/env node
// One-time migration: replace literal Tailwind blue classes with semantic tokens
// Run once from project root: node scripts/migrate-to-semantic-colors.mjs

import { readFileSync, writeFileSync, readdirSync, statSync } from 'fs'
import { resolve, extname, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const SRC = resolve(__dirname, '../frontend/src')

// Ordered replacements — more specific patterns first to avoid partial matches.
// Each entry: [exactClass, replacement]
const REPLACEMENTS = [
  // ── hover/focus variants (most specific first) ──────────────────────────────
  ['hover:bg-blue-700',     'hover:bg-primary-hover'],
  ['hover:bg-blue-200',     'hover:bg-primary-border'],
  ['hover:bg-blue-100',     'hover:bg-primary-tint'],
  ['hover:bg-blue-50',      'hover:bg-primary-tint'],
  ['hover:border-blue-500', 'hover:border-primary'],
  ['hover:border-blue-400', 'hover:border-primary-border'],
  ['hover:border-blue-300', 'hover:border-primary-border'],
  ['hover:text-blue-800',   'hover:text-primary'],
  ['hover:text-blue-700',   'hover:text-primary'],
  ['hover:text-blue-600',   'hover:text-primary'],
  ['hover:text-blue-500',   'hover:text-primary'],
  ['focus:border-blue-400', 'focus:border-primary-border'],
  ['focus:ring-blue-500',   'focus:ring-primary'],
  ['focus:ring-blue-400',   'focus:ring-primary'],

  // ── base backgrounds ────────────────────────────────────────────────────────
  ['bg-blue-600',   'bg-primary'],
  ['bg-blue-500',   'bg-primary'],
  ['bg-blue-100',   'bg-primary-tint'],
  ['bg-blue-50',    'bg-primary-tint'],

  // ── base text ───────────────────────────────────────────────────────────────
  ['text-blue-900', 'text-primary'],
  ['text-blue-800', 'text-primary'],
  ['text-blue-700', 'text-primary'],
  ['text-blue-600', 'text-primary'],
  ['text-blue-500', 'text-primary'],
  ['text-blue-400', 'text-primary'],

  // ── base borders ────────────────────────────────────────────────────────────
  ['border-blue-600', 'border-primary'],
  ['border-blue-500', 'border-primary'],
  ['border-blue-400', 'border-primary-border'],
  ['border-blue-300', 'border-primary-border'],
  ['border-blue-200', 'border-primary-border'],
  ['border-blue-100', 'border-primary-border'],

  // ── ring ────────────────────────────────────────────────────────────────────
  ['ring-blue-200', 'ring-primary-border'],

  // ── accent ──────────────────────────────────────────────────────────────────
  ['accent-blue-600', 'accent-primary'],
  ['accent-blue-500', 'accent-primary'],
]

// Build a single regex that matches any of the class tokens at word boundaries
// (preceded/followed by non-alphanumeric-dash chars, or string delimiters)
function makePattern(cls) {
  const escaped = cls.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  // Match the class as a standalone token: preceded and followed by non-class chars
  return new RegExp(`(?<![\\w:-])${escaped}(?![\\w-])`, 'g')
}

const patterns = REPLACEMENTS.map(([from, to]) => ({ re: makePattern(from), to, from }))

function migrateFile(filePath) {
  const original = readFileSync(filePath, 'utf8')
  let content = original
  const applied = []

  for (const { re, to, from } of patterns) {
    const next = content.replace(re, to)
    if (next !== content) {
      applied.push(from + ' → ' + to)
      content = next
    }
  }

  if (content !== original) {
    writeFileSync(filePath, content, 'utf8')
    return applied
  }
  return []
}

function walk(dir) {
  const files = []
  for (const name of readdirSync(dir)) {
    const p = resolve(dir, name)
    if (statSync(p).isDirectory()) {
      if (name === 'node_modules' || name === '.git') continue
      files.push(...walk(p))
    } else if (['.jsx', '.js', '.ts', '.tsx'].includes(extname(name))) {
      files.push(p)
    }
  }
  return files
}

let totalFiles = 0
for (const file of walk(SRC)) {
  const changes = migrateFile(file)
  if (changes.length) {
    console.log(`  ${file.replace(SRC + '/', '')}`)
    for (const c of changes) console.log(`    ${c}`)
    totalFiles++
  }
}
console.log(`\n✓ Migrated ${totalFiles} file(s)`)
