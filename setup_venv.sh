#!/usr/bin/env bash
# Create or update the shared Python venv for all bots.
# Venv lives at ~/bots-venv/ (NOT inside the CIFS mount — avoids exec permission issues).
#
# Run once on the server:
#   bash ~/bots/setup_venv.sh
#
# After running, update crontab to use the venv python:
#   crontab -e
#   Change: python3 bot.py  →  ~/bots-venv/bin/python3 bot.py

set -e

PYEXE="$HOME/.pyenv/versions/3.12.13/bin/python3"
VENV="$HOME/bots-venv"
REQ="$HOME/bots/requirements.txt"

echo "=== Bots venv setup ==="
printf 'Python : %s\n' "$PYEXE"
printf 'Venv   : %s\n' "$VENV"
printf 'Reqs   : %s\n' "$REQ"
echo ""

if [ ! -x "$PYEXE" ]; then
    echo "ERROR: $PYEXE not found. Check pyenv installation."
    exit 1
fi

if [ ! -f "$REQ" ]; then
    echo "ERROR: $REQ not found."
    exit 1
fi

if [ ! -d "$VENV" ]; then
    echo "Creating venv..."
    "$PYEXE" -m venv "$VENV"
else
    echo "Venv already exists — updating packages."
fi

echo ""
echo "Upgrading pip..."
"$VENV/bin/pip" install --upgrade pip --quiet

echo "Installing requirements..."
"$VENV/bin/pip" install -r "$REQ"

echo ""
echo "=== Installed packages ==="
"$VENV/bin/pip" list --format=columns

echo ""
echo "=== Done ==="
echo ""
echo "Next steps:"
echo "  1. test_bots.sh already uses ~/bots-venv/bin/python3 automatically."
echo "  2. Update crontab entries:"
echo "       crontab -e"
echo "       Replace: python3 bot.py"
echo "       With:    \$HOME/bots-venv/bin/python3 bot.py"
echo ""
