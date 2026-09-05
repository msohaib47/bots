#!/bin/bash

# SchwabStopLossBot Setup Script
# Helps configure the bot for deployment

set -e

echo "=== Schwab Stop-Loss Bot Setup ==="
echo

# Check Node.js
if ! command -v node &> /dev/null; then
    echo "❌ Node.js not found. Please install Node.js 18+ first."
    exit 1
fi

NODE_VERSION=$(node -v)
echo "✅ Node.js $NODE_VERSION found"
echo

# Install dependencies
echo "Installing npm dependencies..."
npm install
echo "✅ Dependencies installed"
echo

# Create .env if it doesn't exist
if [ ! -f .env ]; then
    echo "Creating .env from template..."
    cp .env.example .env
    echo "✅ .env created (edit with your credentials)"
else
    echo "⚠️  .env already exists, skipping"
fi
echo

# Create logs directory
mkdir -p logs
echo "✅ logs/ directory created"
echo

# Run dry-run test
echo "Running dry-run test..."
if npm run dry-run; then
    echo "✅ Dry-run completed successfully"
else
    echo "❌ Dry-run failed. Check .env configuration."
    exit 1
fi
echo

# Crontab instruction
echo "=== Next Steps ==="
echo
echo "1. Edit .env and add your Schwab API credentials:"
echo "   nano .env"
echo
echo "2. Test with real data:"
echo "   npm run once"
echo "   npm run status"
echo
echo "3. Add to crontab for production:"
echo "   crontab -e"
echo
echo "   Every minute during market hours:"
echo "   * 13-21 * * 1-5 cd $(pwd) && /usr/bin/node bot.js --once >> ~/bots/logs/schwab-bot.log 2>&1"
echo
echo "   Or as continuous scheduler:"
echo "   @reboot cd $(pwd) && nohup /usr/bin/node scheduler.js > ~/bots/logs/schwab-scheduler.log 2>&1 &"
echo
echo "4. Check logs:"
echo "   tail -f logs/schwab-bot.log"
echo
echo "Setup complete! 🚀"
