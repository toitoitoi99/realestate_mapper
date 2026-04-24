#!/usr/bin/env node
// Reads DESIGN.md YAML frontmatter and generates frontend/src/design-tokens.css
// with a Tailwind v4 @theme {} block so that every color token in DESIGN.md
// becomes a utility class (bg-primary, text-primary, border-primary-border, etc.)
//
// Usage: node scripts/sync-design-tokens.mjs
//        (or: cd frontend && npm run sync:tokens)

import { readFileSync, writeFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(__dirname, '..')

// ── Parse YAML frontmatter ────────────────────────────────────────────────────
function parseFrontmatter(src) {
  const m = src.match(/^---\n([\s\S]*?)\n---/)
  if (!m) throw new Error('No YAML frontmatter found in DESIGN.md')
  return parseYaml(m[1])
}

// Tiny structural YAML parser sufficient for DESIGN.md's token sections.
// Handles: top-level keys, indented string keys, and quoted/unquoted values.
function parseYaml(text) {
  const lines = text.split('\n')
  const root = {}
  const stack = [{ obj: root, indent: -1 }]

  for (const line of lines) {
    if (!line.trim() || line.trim().startsWith('#')) continue
    const indent = line.search(/\S/)
    const rest = line.trim()
    const colonIdx = rest.indexOf(':')
    if (colonIdx === -1) continue
    const key = rest.slice(0, colonIdx).trim()
    let val = rest.slice(colonIdx + 1).trim().replace(/^["']|["']$/g, '')

    // Pop stack to the correct depth
    while (stack.length > 1 && stack[stack.length - 1].indent >= indent) stack.pop()
    const parent = stack[stack.length - 1].obj

    if (val === '' || val === '>') {
      // Nested object
      parent[key] = {}
      stack.push({ obj: parent[key], indent })
    } else {
      parent[key] = val
    }
  }
  return root
}

// ── Generate CSS ──────────────────────────────────────────────────────────────
function toCssVarName(token) {
  // "primary-hover" → "--color-primary-hover"
  return `--color-${token}`
}

const design = readFileSync(resolve(ROOT, 'DESIGN.md'), 'utf8')
const tokens = parseFrontmatter(design)
const colors = tokens.colors || {}

if (Object.keys(colors).length === 0) {
  console.error('No colors found in DESIGN.md frontmatter')
  process.exit(1)
}

const lines = ['/* AUTO-GENERATED — do not edit by hand. Run: npm run sync:tokens */', '@theme {']
for (const [name, value] of Object.entries(colors)) {
  lines.push(`  ${toCssVarName(name)}: ${value};`)
}
lines.push('}', '')

const css = lines.join('\n')
const outPath = resolve(ROOT, 'frontend/src/design-tokens.css')
writeFileSync(outPath, css, 'utf8')

console.log(`✓ Wrote ${Object.keys(colors).length} color tokens → frontend/src/design-tokens.css`)
console.log('  Colors:', Object.keys(colors).join(', '))
