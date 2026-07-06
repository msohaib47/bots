#!/usr/bin/env bash
# setup.sh — Install dependencies and configure cron for eBay sold price agent
# Run once on the server: bash setup.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON=/home/claude/.pyenv/versions/3.12.13/bin/python3

# Venv and data live on local storage, not the CIFS mount
VENV=/home/claude/venvs/ebaybot
DB_PATH=/home/claude/data/ebay_sold.db
SITE_DIR=/home/sohaib/ebaylisting
LOG_DIR=/home/claude/bots/logs
WRAPPER=/home/claude/ebaybot.sh

echo ""
echo "── eBay Sold Price Agent Setup ──"
echo "Script dir : $SCRIPT_DIR"
echo "Venv       : $VENV"
echo "DB         : $DB_PATH"
echo "Site out   : $SITE_DIR"
echo ""

# 1. Create virtualenv on local filesystem
echo "[1/4] Creating virtual environment..."
$PYTHON -m venv "$VENV"
source "$VENV/bin/activate"

# 2. Install Python deps
echo "[2/4] Installing Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet curl-cffi beautifulsoup4

# 3. Initialize the database and site output dir
echo "[3/4] Initializing database and site directory..."
mkdir -p "$(dirname "$DB_PATH")"
mkdir -p "$LOG_DIR"
mkdir -p "$SITE_DIR"
chmod 755 "$SITE_DIR"

EBAY_DB_PATH=$DB_PATH PYTHONPYCACHEPREFIX=/home/claude/.pycache \
  "$VENV/bin/python" "$SCRIPT_DIR/db.py"

# 4. Write wrapper script and update cron
echo "[4/4] Installing wrapper and cron job..."

cat > "$WRAPPER" << EOF
#!/bin/bash
export EBAY_DB_PATH=$DB_PATH
export PYTHONPYCACHEPREFIX=/home/claude/.pycache
PYTHON=$VENV/bin/python
BOT_DIR=$SCRIPT_DIR

cd \$BOT_DIR
\$PYTHON agent.py run >> $LOG_DIR/ebaybot.log 2>&1
\$PYTHON generate.py >> $LOG_DIR/ebaybot.log 2>&1
EOF
chmod +x "$WRAPPER"

# Add cron entry (Sun + Wed at 7am) — skip if already present
CRON_MARKER="ebaybot.sh"
if ! crontab -l 2>/dev/null | grep -q "$CRON_MARKER"; then
  (crontab -l 2>/dev/null; echo ""; echo "# eBay scraper - Sun+Wed at 7am"; echo "0 7 * * 0,3 $WRAPPER") | crontab -
  echo "  Cron entry added."
else
  echo "  Cron entry already present, skipping."
fi

echo ""
echo "✓ Setup complete!"
echo ""
echo "── Quick commands ──"
echo "  Scrape + regenerate site  :  bash $WRAPPER"
echo "  Add search config         :  EBAY_DB_PATH=$DB_PATH $VENV/bin/python $SCRIPT_DIR/agent.py add"
echo "  List configs              :  EBAY_DB_PATH=$DB_PATH $VENV/bin/python $SCRIPT_DIR/agent.py list"
echo "  View stats                :  EBAY_DB_PATH=$DB_PATH $VENV/bin/python $SCRIPT_DIR/agent.py stats"
echo "  View log                  :  tail -f $LOG_DIR/ebaybot.log"
echo "  Site                      :  http://192.168.1.250:8081"
echo ""
echo "── Cron schedule reference ──"
echo "  0 7 * * 0,3   → Sun + Wed at 7am  (default)"
echo "  0 7 * * *     → Every day at 7am"
echo ""
