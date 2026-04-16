#!/usr/bin/env bash
# Bootstrap a fresh clone or worktree.
# Idempotent — safe to re-run. Exits non-zero on first failure.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

say() { printf "\n\033[1;34m==>\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m!!\033[0m %s\n" "$*" >&2; }

# 1. Python deps (system python3 per CLAUDE.md)
say "Installing Python deps"
/usr/bin/python3 -m pip install --user -q -r backend/requirements.txt

# 2. Playwright chromium (idempotent — skips if already installed)
say "Ensuring Playwright chromium is installed"
/usr/bin/python3 -m playwright install chromium

# 3. Frontend deps
if [ ! -d frontend/node_modules ]; then
  say "Installing frontend deps"
  (cd frontend && npm install)
else
  say "frontend/node_modules exists — skipping npm install"
fi

# 4. DB check (launch.json seeds from main repo for worktrees; warn if missing for non-worktree runs)
DB="backend/data/lisboa_realestate.db"
if [ ! -f "$DB" ] || [ "$(stat -f%z "$DB" 2>/dev/null || echo 0)" -lt 500000 ]; then
  warn "$DB missing or <500KB. In worktrees, preview_start('app') will seed it."
  warn "For a fresh clone, run the scrapers (see CLAUDE.md) or copy a DB in."
fi

# 5. gh CLI sanity (friendly warning only)
if ! command -v gh >/dev/null 2>&1; then
  warn "gh CLI not found — PRs will require manual compare URLs."
elif ! gh auth status >/dev/null 2>&1; then
  warn "gh not authenticated — run: gh auth login"
fi

say "Bootstrap complete."
