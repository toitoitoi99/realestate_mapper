#!/bin/bash
# Daily scheduled multi-scraper batch, invoked by launchd.
#
# Runs (in order): idealista → remax → era. These three are chosen over
# the full 6-scraper set because the aggregators (imovirtual, casa_sapo)
# largely duplicate idealista's catalog, and OLX's unique non-cross-post
# volume is marginal. Idealista carries per-listing lat/lon and the richest
# feature data; remax + era cover agency-exclusive inventory.
#
# Behavior:
#   - Writes rolling log to backend/data/scrape.log (rotated at 10 MB)
#   - Skips the batch if the last attempt was < 20 hours ago (prevents
#     double-runs after the Mac wakes from sleep past 3am)
#   - Log-and-continue on failure: one scraper failing doesn't block the
#     others. Per-scraper consecutive-failure counts are tracked; any
#     scraper hitting 2+ consecutive failures triggers a macOS notification
#     (typically means idealista's DataDome session expired — run:
#     `python3 backend/scrapers/idealista_playwright.py --setup`)
#
# To adjust schedule: edit the StartCalendarInterval in
# ~/Library/LaunchAgents/com.lisbon-realestate.scrape.plist and
# `launchctl kickstart -k gui/$UID/com.lisbon-realestate.scrape`
set -u

REPO_DIR="/Users/tord/Claude_projects/lisbon-realestate"
DATA_DIR="$REPO_DIR/backend/data"
LOG_FILE="$DATA_DIR/scrape.log"
LAST_ATTEMPT_FILE="$DATA_DIR/.last_scrape_attempt"
MIN_INTERVAL_SECONDS=$((20 * 3600))  # 20 hours
PYTHON="/usr/bin/python3"

# Ordered list of scrapers to run. Each <name> maps to
# backend/scrapers/<name>_playwright.py.
SCRAPERS=(idealista remax era)

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

# Debounce: skip if last batch attempt is recent
if [ -f "$LAST_ATTEMPT_FILE" ]; then
    last=$(cat "$LAST_ATTEMPT_FILE" 2>/dev/null || echo 0)
    now=$(date +%s)
    age=$((now - last))
    if [ "$age" -lt "$MIN_INTERVAL_SECONDS" ]; then
        log "Skipping: last batch attempt was $((age / 3600))h ago (< 20h)"
        exit 0
    fi
fi

date +%s > "$LAST_ATTEMPT_FILE"

cd "$REPO_DIR" || { log "ERROR: cannot cd to $REPO_DIR"; exit 1; }

log "=== Starting scheduled batch: ${SCRAPERS[*]} ==="

failed_scrapers=()

for scraper in "${SCRAPERS[@]}"; do
    script="backend/scrapers/${scraper}_playwright.py"
    fail_file="$DATA_DIR/.scrape_fail_count_${scraper}"

    if [ ! -f "$script" ]; then
        log "--- SKIP $scraper: $script not found ---"
        continue
    fi

    log "--- Running $scraper ---"
    "$PYTHON" "$script" >> "$LOG_FILE" 2>&1
    rc=$?

    if [ "$rc" -eq 0 ]; then
        echo 0 > "$fail_file"
        log "--- $scraper: OK ---"
    else
        fails=0
        if [ -f "$fail_file" ]; then
            fails=$(cat "$fail_file" 2>/dev/null || echo 0)
        fi
        fails=$((fails + 1))
        echo "$fails" > "$fail_file"
        failed_scrapers+=("$scraper(exit=$rc, ${fails}x)")
        log "--- $scraper: FAILED (exit $rc, consecutive: $fails) ---"
    fi
done

if [ "${#failed_scrapers[@]}" -eq 0 ]; then
    log "=== Batch finished: all ${#SCRAPERS[@]} scrapers succeeded ==="
    exit 0
fi

log "=== Batch finished with failures: ${failed_scrapers[*]} ==="

# Notify on any scraper with 2+ consecutive failures
repeat_failures=()
for scraper in "${SCRAPERS[@]}"; do
    fail_file="$DATA_DIR/.scrape_fail_count_${scraper}"
    if [ -f "$fail_file" ]; then
        fails=$(cat "$fail_file" 2>/dev/null || echo 0)
        if [ "$fails" -ge 2 ]; then
            repeat_failures+=("$scraper(${fails}x)")
        fi
    fi
done

if [ "${#repeat_failures[@]}" -gt 0 ]; then
    notify "Persistent failures: ${repeat_failures[*]}. If idealista, run: python3 backend/scrapers/idealista_playwright.py --setup"
fi

# Exit 0 so launchd doesn't treat partial-failure batches as a hard error
# (the notification + log already surface the problem).
exit 0
