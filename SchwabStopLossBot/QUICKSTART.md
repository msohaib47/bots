# Quick Start Guide

Get the Schwab Stop-Loss Bot running in 5 minutes.

## 1. Install Dependencies

```bash
npm install
```

## 2. Configure Credentials & Get Access Token

Copy the example config:
```bash
cp .env.example .env
```

Edit `.env` and add your Schwab **Client ID** and **Client Secret**:
```env
SCHWAB_CLIENT_ID=your_client_id
SCHWAB_CLIENT_SECRET=your_client_secret
SCHWAB_ACCOUNT_NUMBER=your_account_number
DRY_RUN=true
```

**Don't have Client ID/Secret?** Get them from [Schwab Developer Portal](https://developer.schwab.com)

## 3. Get Access Token

Run the login script to authorize with Schwab:
```bash
node login.js
```

This opens a browser where you:
1. Sign in with your Schwab account
2. Grant the app access to your account
3. Get redirected to a success page

The script automatically saves your access token to `.env`.

**Verify it worked:**
```bash
node login.js --status
```

## 4. Test in Dry-Run Mode

Run a single pass without placing real orders:
```bash
npm run dry-run
```

You should see output like:
```
info: Starting Schwab stop-loss check...
info: Found option positions (count: 2)
info: Stop-loss check completed
info: === Schwab Stop-Loss Bot Status ===
```

## 5. View Status

Check current positions:
```bash
npm run status
```

## 6. Enable Real Orders (Optional)

When ready, update `.env`:
```env
DRY_RUN=false
```

Then run once:
```bash
npm run once
```

## 7. Schedule for Production

### Option A: Cron (One-shot every minute)
```bash
crontab -e
```

Add:
```cron
* 13-21 * * 1-5 cd /path/to/SchwabStopLossBot && node bot.js --once >> ~/bots/logs/schwab-bot.log 2>&1
```

### Option B: Node Scheduler (Continuous)
```bash
node scheduler.js &
```

Or as a systemd service / PM2 daemon for production.

## Stop-Loss Tiers (Default)

| Price Range | Stop Margin | Example |
|---|---|---|
| < $100 | $10 or 25% | Stop at $35 for $50 contract |
| $100-$200 | $15 | Stop at $135 for $150 contract |
| > $200 | $25 | Stop at $225 for $250 contract |

Adjust in `config.js` if needed.

## Troubleshooting

### "Missing SCHWAB_API_KEY" error
→ Verify `.env` file exists and has all required fields

### "No open option positions found"
→ Normal if you have no options in the account right now

### "Failed to authenticate"
→ Check API credentials are correct and token is not expired

### Stops not updating in real mode
→ Verify `DRY_RUN=false` in `.env`
→ Check it's during market hours (9:30 AM - 4:00 PM ET)

## Next Steps

- Review [README.md](./README.md) for full documentation
- Check `logs/schwab-bot.log` for detailed information
- Monitor first few runs closely before leaving unattended
- Set up notifications in `.env` for alerts

## Key Commands

```bash
npm run once       # Single run with live data
npm run dry-run    # Single run dry-run mode
npm run status     # Check current positions
npm start          # Run continuous scheduler
node bot.js --reset # Clear all tracked positions
```
