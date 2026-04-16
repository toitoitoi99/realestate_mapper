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
#     others. Per-scraper consecutive-failure counts are tracked.
#   - Transient failures (network timeouts, connection resets, 5xx) trigger
#     one automatic retry after a 5-minute delay. Persistent failures (bot
#     challenges, auth errors) do not retry.
#   - Any scraper hitting 2+ consecutive failures triggers notifications
#     (both Slack and macOS). Typically means idealista's DataDome session
#     expired — run `python3 backend/scrapers/idealista_playwright.py --setup`.
#   - Slack: posts a batch summary to #realestate-bud via the webhook URL
#     stored in backend/data/.slack_webhook (gitignored, chmod 600).
#     Missing/unreadable webhook silently falls back to macOS-only.
#
# To adjust schedule: edit the StartCalendarInterval in
# ~/Library/LaunchAgents/com.lisbon-realestate.scrape.plist and
# `launchctl kickstart -k gui/$UID/com.lisbon-realestate.scrape`
set -u

REPO_DIR="/Users/tord/Claude_projects/lisbon-realestate"
DATA_DIR="$REPO_DIR/backend/data"
LOG_FILE="$DATA_DIR/scrape.log"
LAST_ATTEMPT_FILE="$DATA_DIR/.last_scrape_attempt"
WEBHOOK_FILE="$DATA_DIR/.slack_webhook"
MIN_INTERVAL_SECONDS=$((20 * 3600))  # 20 hours
RETRY_DELAY_SECONDS=$((5 * 60))      # 5 minutes
PYTHON="/usr/bin/python3"

# Ordered list of scrapers to run. Each <name> maps to
# backend/scrapers/<name>_playwright.py.
SCRAPERS=(idealista remax era)

# Regex patterns that indicate a failure is transient and worth retrying.
# Kept loose — false positives just cost one retry, false negatives skip
# the retry and let the 2-strike notification catch the pattern.
TRANSIENT_PATTERNS='Timeout|timed out|ECONNRESET|ECONNREFUSED|ETIMEDOUT|net::ERR_|HTTP 5[0-9]{2}|Read timed out|ConnectionError|ConnectionResetError|Temporary failure|Connection refused|BrowserContext closed|Target page, context or browser has been closed'

# Regex patterns that indicate the failure is NOT worth retrying — bot
# challenges, auth, quota. If any of these appear alongside a transient
# marker, skip the retry (the site is actively blocking us).
PERSISTENT_PATTERNS='DataDome|geo.captcha|hCaptcha|reCAPTCHA|challenge|HTTP 40[13]|Access Denied|Forbidden|bot detect|rate limit|quota exceeded|Cloudflare'

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

notify_mac() {
    /usr/bin/osascript -e "display notification \"$1\" with title \"Lisbon Scraper\"" 2>/dev/null || true
}

notify_slack() {
    # $1 = plain text payload. Silently no-op if the webhook file is missing
    # or unreadable. Never echoes the URL — curl's -s keeps it quiet too.
    local msg="$1"
    if [ ! -r "$WEBHOOK_FILE" ]; then
        return 0
    fi
    local url
    url=$(head -n1 "$WEBHOOK_FILE" 2>/dev/null | tr -d '[:space:]')
    if [ -z "$url" ]; then
        return 0
    fi
    # JSON-escape the message: escape backslashes first, then quotes, then newlines.
    local escaped
    escaped=$(printf '%s' "$msg" | sed 's/\\/\\\\/g; s/"/\\"/g' | awk 'BEGIN{ORS="\\n"} {print}')
    /usr/bin/curl -sS -X POST -H 'Content-Type: application/json' \
        --data "{\"text\":\"$escaped\"}" \
        --max-time 15 \
        "$url" >/dev/null 2>&1 || log "WARN: Slack notify failed"
}

notify_all() {
    notify_mac "$1"
    notify_slack "$1"
}

# Detect transient failure by scanning the tail of the log for known markers.
# Returns 0 (true) if the failure looks transient, 1 otherwise.
is_transient_failure() {
    local lines="${1:-300}"
    local tail_output
    tail_output=$(tail -n "$lines" "$LOG_FILE" 2>/dev/null || true)
    if printf '%s' "$tail_output" | grep -Eq -- "$PERSISTENT_PATTERNS"; then
        return 1
    fi
    if printf '%s' "$tail_output" | grep -Eq -- "$TRANSIENT_PATTERNS"; then
        return 0
    fi
    return 1
}

# Run one scraper, with up to one retry on transient failure.
# Sets global $LAST_RC to the final exit code.
run_scraper() {
    local scraper="$1"
    local script="backend/scrapers/${scraper}_playwright.py"

    log "--- Running $scraper ---"
    "$PYTHON" "$script" >> "$LOG_FILE" 2>&1
    LAST_RC=$?
    if [ "$LAST_RC" -eq 0 ]; then
        return 0
    fi

    if is_transient_failure 400; then
        log "--- $scraper: transient failure (exit $LAST_RC), retrying in $((RETRY_DELAY_SECONDS / 60))m ---"
        sleep "$RETRY_DELAY_SECONDS"
        log "--- Retrying $scraper ---"
        "$PYTHON" "$script" >> "$LOG_FILE" 2>&1
        LAST_RC=$?
        if [ "$LAST_RC" -eq 0 ]; then
            log "--- $scraper: OK on retry ---"
        fi
    else
        log "--- $scraper: persistent failure (exit $LAST_RC), no retry ---"
    fi
    return "$LAST_RC"
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

batch_start_ts=$(date +%s)
log "=== Starting scheduled batch: ${SCRAPERS[*]} ==="

succeeded_scrapers=()
failed_scrapers=()

for scraper in "${SCRAPERS[@]}"; do
    script="backend/scrapers/${scraper}_playwright.py"
    fail_file="$DATA_DIR/.scrape_fail_count_${scraper}"

    if [ ! -f "$script" ]; then
        log "--- SKIP $scraper: $script not found ---"
        continue
    fi

    LAST_RC=0
    run_scraper "$scraper"

    if [ "$LAST_RC" -eq 0 ]; then
        echo 0 > "$fail_file"
        succeeded_scrapers+=("$scraper")
        log "--- $scraper: OK ---"
    else
        fails=0
        if [ -f "$fail_file" ]; then
            fails=$(cat "$fail_file" 2>/dev/null || echo 0)
        fi
        fails=$((fails + 1))
        echo "$fails" > "$fail_file"
        failed_scrapers+=("$scraper(exit=$LAST_RC, ${fails}x)")
        log "--- $scraper: FAILED (exit $LAST_RC, consecutive: $fails) ---"
    fi
done

batch_duration=$(( $(date +%s) - batch_start_ts ))
duration_str="$((batch_duration / 60))m$((batch_duration % 60))s"

# Build Slack summary
host_label=$(/bin/hostname -s 2>/dev/null || echo "unknown-host")
summary="Lisbon scrape batch on \`${host_label}\` finished in ${duration_str}."
if [ "${#succeeded_scrapers[@]}" -gt 0 ]; then
    summary="${summary}
✅ OK: ${succeeded_scrapers[*]}"
fi
if [ "${#failed_scrapers[@]}" -gt 0 ]; then
    summary="${summary}
❌ FAIL: ${failed_scrapers[*]}"
fi

if [ "${#failed_scrapers[@]}" -eq 0 ]; then
    log "=== Batch finished: all ${#SCRAPERS[@]} scrapers succeeded (${duration_str}) ==="
    # All-success: Slack only (no Mac notification for routine success)
    notify_slack "$summary"
    exit 0
fi

log "=== Batch finished with failures: ${failed_scrapers[*]} (${duration_str}) ==="

# Surface repeat failures (2+ consecutive) more loudly
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
    alert="🚨 Persistent scraper failures: ${repeat_failures[*]}
If idealista is listed, run: \`python3 backend/scrapers/idealista_playwright.py --setup\`"
    notify_all "$alert"
    notify_slack "$summary"
else
    # One-off failure: Slack summary only. Mac notification would be noisy.
    notify_slack "$summary"
fi

# Exit 0 so launchd doesn't treat partial-failure batches as a hard error
# (the notification + log already surface the problem).
exit 0
