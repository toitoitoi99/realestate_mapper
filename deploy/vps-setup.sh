#!/usr/bin/env bash
# VPS Setup Script — Lisbon Real Estate Scrapers
# Tested on Ubuntu 22.04+ / Debian 12+
#
# Usage:
#   1. Spin up a VPS (Hetzner CX22 ~€4/mo, DigitalOcean $6/mo, etc.)
#   2. SSH in as root: ssh root@<ip>
#   3. Run: bash vps-setup.sh
#   4. Then: sudo -u scraper bash -c 'cd ~/lisbon-realestate && python3 scrapers/idealista_playwright.py --setup'
#      (repeat --setup for each scraper that needs DataDome/Cloudflare challenge solving)

set -euo pipefail

REPO_URL="${REPO_URL:-git@github.com:YOUR_USER/lisbon-realestate.git}"
SCRAPER_USER="scraper"
INSTALL_DIR="/home/${SCRAPER_USER}/lisbon-realestate"

echo "=== 1. System packages ==="
apt-get update
apt-get install -y \
  python3 python3-pip python3-venv \
  git curl wget unzip \
  cron \
  # Playwright/Chromium dependencies
  libnss3 libatk-bridge2.0-0 libdrm2 libxcomposite1 libxdamage1 \
  libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
  libxshmfence1 libx11-xcb1 fonts-liberation xdg-utils

echo "=== 2. Create scraper user ==="
if ! id "$SCRAPER_USER" &>/dev/null; then
  useradd -m -s /bin/bash "$SCRAPER_USER"
fi

echo "=== 3. Clone repo ==="
sudo -u "$SCRAPER_USER" bash -c "
  if [ ! -d '$INSTALL_DIR' ]; then
    git clone '$REPO_URL' '$INSTALL_DIR'
  else
    cd '$INSTALL_DIR' && git pull
  fi
"

echo "=== 4. Python venv + dependencies ==="
sudo -u "$SCRAPER_USER" bash -c "
  cd '$INSTALL_DIR/backend'
  python3 -m venv venv
  source venv/bin/activate
  pip install --upgrade pip
  pip install -r requirements.txt
  playwright install chromium
  playwright install-deps chromium
"

echo "=== 5. Create log + data directories ==="
sudo -u "$SCRAPER_USER" mkdir -p "$INSTALL_DIR/backend/data/logs"

echo "=== 6. Install cron schedule ==="
CRON_FILE="/etc/cron.d/lisbon-scrapers"
VENV="$INSTALL_DIR/backend/venv/bin/python3"
SCRAPERS="$INSTALL_DIR/backend/scrapers"
LOGS="$INSTALL_DIR/backend/data/logs"

cat > "$CRON_FILE" << EOF
# Lisbon Real Estate — daily listing scrapers at 06:00 UTC
# Staggered by 10 minutes to avoid DB contention
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin

# Idealista (most important — largest source)
0 6 * * * $SCRAPER_USER cd $INSTALL_DIR/backend && $VENV $SCRAPERS/idealista_playwright.py >> $LOGS/idealista.log 2>&1

# ERA
10 6 * * * $SCRAPER_USER cd $INSTALL_DIR/backend && $VENV $SCRAPERS/era_playwright.py >> $LOGS/era.log 2>&1

# Imovirtual
20 6 * * * $SCRAPER_USER cd $INSTALL_DIR/backend && $VENV $SCRAPERS/imovirtual_playwright.py >> $LOGS/imovirtual.log 2>&1

# Casa Sapo
30 6 * * * $SCRAPER_USER cd $INSTALL_DIR/backend && $VENV $SCRAPERS/casa_sapo_playwright.py >> $LOGS/casa_sapo.log 2>&1

# OLX
40 6 * * * $SCRAPER_USER cd $INSTALL_DIR/backend && $VENV $SCRAPERS/olx_playwright.py >> $LOGS/olx.log 2>&1

# Remax
50 6 * * * $SCRAPER_USER cd $INSTALL_DIR/backend && $VENV $SCRAPERS/remax_playwright.py >> $LOGS/remax.log 2>&1
EOF

chmod 644 "$CRON_FILE"

echo "=== 7. DB sync script ==="
cat > "$INSTALL_DIR/sync-db.sh" << 'SYNC'
#!/usr/bin/env bash
# Sync the scraped DB back to your local machine.
# Run FROM YOUR MAC:
#   scp scraper@<vps-ip>:~/lisbon-realestate/backend/data/lisboa_realestate.db ./backend/data/
#
# Or set up a cron on the VPS to push to your repo:
#   cd ~/lisbon-realestate && git add backend/data/lisboa_realestate.db && git commit -m "Daily scrape $(date -u +%Y-%m-%d)" && git push
SYNC
chmod +x "$INSTALL_DIR/sync-db.sh"

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Update REPO_URL in this script (or re-clone manually)"
echo "  2. For each scraper that needs interactive challenge solving, run:"
echo "     sudo -u $SCRAPER_USER bash -c 'cd $INSTALL_DIR/backend && source venv/bin/activate && python3 scrapers/idealista_playwright.py --setup'"
echo "     (You'll need X11 forwarding: ssh -X root@<ip>, or use a VNC/noVNC session)"
echo "  3. To get the DB back to your Mac, run from your Mac:"
echo "     scp $SCRAPER_USER@<vps-ip>:$INSTALL_DIR/backend/data/lisboa_realestate.db ./backend/data/"
echo "  4. Check logs at: $LOGS/"
echo ""
