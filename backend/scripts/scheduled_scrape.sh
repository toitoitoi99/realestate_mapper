#!/bin/bash
# Daily scheduled idealista scrape, invoked by launchd.
#
# Behavior:
#   - Writes rolling log to backend/data/scrape.log (rotated at 10 MB)
#   - Skips the run if the last successful scrape was < 20 hours ago
#     (prevents double-runs after the Mac wakes from sleep past 3am)
#   - Tracks consecutive failures; sends a macOS notification after
#     2+ failures in a row (usually means DataDome session expired and
#     needs a manual `python3 backend/scrapers/idealista_playwright.py --setup`)
#
# To adjust schedule: edit the StartCalendarInterval in
# ~/Library/LaunchAgents/com.lisbon-realestate.scrape.plist and
# `launchctl kickstart -k gui/$UID/com.lisbon-realestate.scrape`
set -u

REPO_DIR="/Users/tord/Claude_projects/lisbon-realestate"
DATA_DIR="$REPO_DIR/backend/data"
LOG_FILE="$DATA_DIR/scrape.log"
LAST_SUCCESS_FILE="$DATA_DIR/.last_scrape_success"
FAIL_COUNT_FILE="$DATA_DIR/.scrape_fail_count"
MIN_INTERVAL_SECONDS=$((20 * 3600))  # 20 hours
PYTHON="/usr/bin/python3"

mkdir -p "$DATA_DIR"

# Rotate log if > 10 MB
if [ -f "$LOG_FILE" ]; then
    size=$(stat -f%z "$LOG_FILE" 2>/dev/null || echo 0)
    if [ "$size" -gt 10485760 ]; then
        mv "$LOG_FILE" "$LOG_FILE.1"
    fi
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

notify() {
    /usr/bin/osascript -e "display notification \"$1\" with title \"Lisbon Scraper\"" 2>/dev/null || true
}

# Debounce: skip if last successful run is recent
if [ -f "$LAST_SUCCESS_FILE" ]; then
    last=$(cat "$LAST_SUCCESS_FILE" 2>/dev/null || echo 0)
    now=$(date +%s)
    age=$((now - last))
    if [ "$age" -lt "$MIN_INTERVAL_SECONDS" ]; then
        log "Skipping: last successful run was $((age / 3600))h ago (< 20h)"
        exit 0
    fi
fi

log "=== Starting scheduled scrape ==="

cd "$REPO_DIR" || { log "ERROR: cannot cd to $REPO_DIR"; exit 1; }

# Run scraper (stdout + stderr appended to the log)
"$PYTHON" backend/scrapers/idealista_playwright.py >> "$LOG_FILE" 2>&1
rc=$?

if [ "$rc" -eq 0 ]; then
    date +%s > "$LAST_SUCCESS_FILE"
    echo 0 > "$FAIL_COUNT_FILE"
    log "=== Scrape finished successfully ==="
else
    fails=0
    if [ -f "$FAIL_COUNT_FILE" ]; then
        fails=$(cat "$FAIL_COUNT_FILE" 2>/dev/null || echo 0)
    fi
    fails=$((fails + 1))
    echo "$fails" > "$FAIL_COUNT_FILE"
    log "=== Scrape FAILED (exit $rc, consecutive failures: $fails) ==="
    if [ "$fails" -ge 2 ]; then
        notify "Scraper failed $fails times in a row. DataDome session may be expired — run: python3 backend/scrapers/idealista_playwright.py --setup"
    fi
fi

exit "$rc"
