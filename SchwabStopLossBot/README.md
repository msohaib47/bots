# Schwab Stop-Loss Bot

Automated stop-loss manager for Schwab option positions. Monitors open options contracts and maintains dynamic trailing stop-losses with tiered margin adjustments.

## Features

- **Option-Only Management**: Monitors options positions only, ignores stock holdings
- **Dynamic Stop-Loss Tiers**:
  - Price < $100: $10 or 25% margin (whichever is larger)
  - $100 ≤ Price < $200: $15 margin
  - Price ≥ $200: $25 margin
- **Trailing Stops**: Automatically moves stops up as price increases, never moves down
- **Dry-Run Mode**: Test all logic without placing real orders
- **Market Hours Only**: Runs only during 9:30 AM - 4:00 PM ET weekdays
- **Automatic Scheduling**: Runs every minute during market hours via cron
- **Push Notifications**: Updates via ntfy.sh when stops are adjusted
- **State Persistence**: Tracks positions and highest prices in `guard_state.json`

## Installation

### Prerequisites

- Node.js 18.0.0 or later
- npm or yarn
- Schwab API credentials (API key, secret, access token)
- Schwab account number

### Setup

1. **Install dependencies**:
   ```bash
   npm install
   ```

2. **Register for Schwab API** (one-time):
   - Go to [Schwab Developer Portal](https://developer.schwab.com)
   - Create an app with redirect URI: `http://localhost:8080/callback`
   - Copy your **Client ID** and **Client Secret**

3. **Create .env file**:
   ```bash
   cp .env.example .env
   ```

4. **Configure credentials** (edit `.env`):
   ```
   SCHWAB_CLIENT_ID=your_client_id
   SCHWAB_CLIENT_SECRET=your_client_secret
   SCHWAB_ACCOUNT_NUMBER=your_account_number
   ```

5. **Get access token**:
   ```bash
   node login.js
   ```
   This opens Schwab's login page. Sign in and authorize the app. Your access token is saved automatically.

6. **Verify setup**:
   ```bash
   node test-connection.js
   ```

7. **Start in dry-run mode** (recommended first):
   ```bash
   npm run dry-run
   ```

## Usage

### Authentication

**Get access token** (one-time setup):
```bash
node login.js
```

**Check token status**:
```bash
node login.js --status
```

**Refresh token** (if expired):
```bash
node login.js --refresh
```

### Test API Connection

Verify Schwab API is accessible with your credentials:
```bash
node test-connection.js
```

### Single Run (Dry-Run)
Test the bot without placing real orders:
```bash
npm run dry-run
```

### Single Run (Real)
Execute one pass with real orders (if DRY_RUN=false in .env):
```bash
npm run once
```

### Status Check
View current positions and stop-loss status:
```bash
npm run status
```

### Continuous Mode
Start the scheduled bot (runs every minute during market hours):
```bash
npm start
```

Or with scheduler:
```bash
node scheduler.js
```

### Reset State
Clear all tracked positions:
```bash
node bot.js --reset
```

## Configuration

### Stop-Loss Tiers

Edit the `config.js` file to adjust margin tiers:

```javascript
tiers: [
  { minPrice: 0, maxPrice: 100, marginDollar: 10, marginPercent: 0.25 },
  { minPrice: 100, maxPrice: 200, marginDollar: 15, marginPercent: 0.25 },
  { minPrice: 200, maxPrice: Infinity, marginDollar: 25, marginPercent: 0.25 },
]
```

### Market Hours

Default: 9:30 AM - 4:00 PM ET (Monday-Friday)

Edit in `config.js`:
```javascript
marketOpen: 9.5,  // 9:30 AM
marketClose: 16.0, // 4:00 PM
```

### Logging

Set log level via environment variable:
```bash
LOG_LEVEL=debug npm run once
```

Levels: `error`, `warn`, `info`, `debug`

## Deployment

### Ubuntu Server (Cron)

1. **Install Node.js** (if not already installed):
   ```bash
   curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
   sudo apt-get install -y nodejs
   ```

2. **Navigate to bot directory** and install:
   ```bash
   cd ~/bots/SchwabStopLossBot
   npm install
   cp .env.example .env
   # Edit .env with credentials
   ```

3. **Add to crontab** (every minute during market hours):
   ```bash
   crontab -e
   ```

   Add this line:
   ```cron
   * 13-21 * * 1-5 cd /home/claude/bots/SchwabStopLossBot && /usr/bin/node bot.js --once >> /home/claude/bots/logs/schwab-bot.log 2>&1
   ```

   Or use the scheduler for continuous operation:
   ```cron
   @reboot cd /home/claude/bots/SchwabStopLossBot && nohup /usr/bin/node scheduler.js > /home/claude/bots/logs/schwab-scheduler.log 2>&1 &
   ```

4. **Create logs directory** if needed:
   ```bash
   mkdir -p ~/bots/logs
   ```

### Docker (Optional)

```dockerfile
FROM node:20-alpine

WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production

COPY . .

CMD ["node", "scheduler.js"]
```

Build and run:
```bash
docker build -t schwab-bot .
docker run -d --env-file .env schwab-bot
```

## Architecture

```
SchwabStopLossBot/
├── bot.js              # Main bot class and entry point
├── scheduler.js        # Cron scheduler
├── schwab.js           # Schwab API wrapper
├── position-manager.js # Stop-loss calculation and state
├── config.js           # Configuration and environment
├── notifier.js         # Push notifications (ntfy.sh)
├── utils.js            # Helper functions
├── logger.js           # Winston logging setup
├── guard_state.json    # Position state (persisted)
├── logs/               # Log files
├── package.json        # Dependencies
└── .env               # Credentials (not committed)
```

## API Integration

The bot uses Schwab's REST API endpoints:

- **Get Positions**: `GET /trader/v1/accounts/{accountNumber}/orders`
- **Get Quotes**: `GET /marketdata/v1/quotes`
- **Place Order**: `POST /trader/v1/accounts/{accountNumber}/orders`
- **Cancel Order**: `DELETE /trader/v1/accounts/{accountNumber}/orders/{orderId}`

Requires valid OAuth token in `SCHWAB_ACCESS_TOKEN`.

## Notifications

Updates are sent to ntfy.sh topic (default: `sohaib-trading-2026`):

- `Stop-Loss Update`: When a stop is adjusted
- `Bot Status`: Periodic status checks
- `Error`: When issues occur

Override the topic:
```bash
NOTIFY_TOPIC=my-topic npm run once
```

## Troubleshooting

### "No Schwab access token found"
Set `SCHWAB_ACCESS_TOKEN` in `.env` file.

### "Failed to fetch option positions"
- Check API credentials are correct
- Verify account number matches format expected by Schwab API
- Check network connectivity
- Review logs for API error details

### Stops not adjusting
- Verify `DRY_RUN=false` in `.env`
- Check market hours (9:30 AM - 4:00 PM ET)
- Review position prices vs calculated stops in logs
- Check `guard_state.json` for existing position data

### High API rate limits
The bot runs every minute, hitting API ~390 times per day (5-day week × 78 minutes/day).
Schwab typically allows 120 requests/minute, so this is safe, but monitor for throttling.

## Logs

- **Console**: Real-time output to terminal
- **File**: `logs/schwab-bot.log` (rotates at 10MB)
- **Crontab**: `/home/claude/bots/logs/schwab-bot.log` (stdout/stderr)

View logs:
```bash
tail -f logs/schwab-bot.log
```

## Token Management

### How It Works

1. **OAuth 2.0 Flow:**
   - `login.js` handles Schwab's OAuth authorization
   - Access token: 30-minute lifetime (auto-refreshes)
   - Refresh token: 7-day lifetime

2. **Automatic Refresh:**
   - Bot checks token expiration before each API call
   - Automatically refreshes if needed (no user intervention)
   - Refresh token updates on each refresh

3. **Manual Operations:**
   ```bash
   node login.js              # Full re-authentication
   node login.js --status     # Check token status
   node login.js --refresh    # Manually refresh token
   ```

### Token Storage

- `.env` — Access token and refresh token (in your project, not committed)
- `.schwab_tokens.json` — Backup tokens (also not committed)

Both are in `.gitignore` for security.

### Troubleshooting Tokens

| Issue | Solution |
|-------|----------|
| "Invalid access token" | Run `node login.js --refresh` |
| "Refresh token expired" | Run `node login.js` for fresh auth |
| "Token not found" | Complete `.env` setup and run `node login.js` |

See [OAUTH_SETUP.md](./OAUTH_SETUP.md) for detailed OAuth documentation.

## State Management

Position state is persisted in `guard_state.json`:

```json
{
  "positions": {
    "SPY_20250620_C500_1": {
      "symbol": "SPY 06/20/25 C500",
      "quantity": 1,
      "lastPrice": 45.50,
      "stopPrice": 34.50,
      "highestPrice": 48.00,
      "margin": 15,
      "stopOrderId": "123456789",
      "createdAt": "2025-06-10T15:30:00.000Z",
      "lastUpdated": "2025-06-10T15:45:00.000Z"
    }
  }
}
```

Reset state if needed:
```bash
node bot.js --reset
```

## License

ISC

## Support

- Check logs for detailed error messages
- Review configuration matches Schwab account setup
- Test with `--once` flag before scheduling
- Use `--status` to verify current state
