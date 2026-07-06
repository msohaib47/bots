# How to Get Your Schwab Access Token

You have **Client ID** and **Client Secret** from Schwab. Now get your access token in 5 minutes.

## Step 1: Update .env (2 min)

Navigate to the bot directory:
```bash
cd Z:\work\bots\SchwabStopLossBot
```

Copy the template:
```bash
cp .env.example .env
```

Edit `.env` and add your Client ID and Secret:
```env
SCHWAB_CLIENT_ID=YOUR_CLIENT_ID_HERE
SCHWAB_CLIENT_SECRET=YOUR_CLIENT_SECRET_HERE
SCHWAB_ACCOUNT_NUMBER=YOUR_ACCOUNT_NUMBER
```

**Finding your account number:**
- Log into your Schwab account online
- Go to Account Settings
- Look for account number (usually 8 digits)

Example `.env`:
```env
SCHWAB_CLIENT_ID=7a8f9e3c-2b1f-4d5e-8c9a-1b2c3d4e5f6g
SCHWAB_CLIENT_SECRET=AbCdEfGhIjKlMnOpQrStUvWxYz1234567890
SCHWAB_ACCOUNT_NUMBER=12345678
DRY_RUN=true
LOG_LEVEL=info
STOPLOSS_INITIAL_MARGIN_PCT=0.25
STOPLOSS_INITIAL_MARGIN_DOLLAR=10
NOTIFY_TOPIC=sohaib-trading-2026
```

## Step 2: Install Dependencies (1 min)

```bash
npm install
```

You should see packages installing for: dotenv, axios, node-cron, winston.

## Step 3: Run Login Script (2 min)

This is the key step — it opens Schwab's login, gets your approval, and saves your access token:

```bash
node login.js
```

**What you'll see:**

```
=== Schwab OAuth Login ===

📱 Opening browser for Schwab login...

Authorization URL:
https://api.schwabapi.com/v1/oauth/authorize?client_id=...&scope=PlaceTrades...
```

**In your browser:**

1. Schwab login page opens
2. Sign in with your Schwab username/password
3. You see a page asking to authorize "Trading Bot" app
4. Click **Approve** or **Authorize**
5. Browser redirects to: `http://localhost:8080/callback` with a success message

**Back in terminal:**

```
✅ Authorization code received

🔄 Exchanging code for access token...

✅ Authentication successful!

   Access Token: eyJhbGciOiJIUzI1NiIsInR5cCI...
   Expires: 2025-06-11T15:30:00.000Z
   Refresh Token: Saved (valid for 7 days)

Ready to use! Run the bot with:
  npm run once
```

## ✅ Done!

Your `.env` now has:
- `SCHWAB_ACCESS_TOKEN` (added automatically)
- `SCHWAB_REFRESH_TOKEN` (added automatically)

These will be used by the bot for all API calls.

## Verify It Worked

Check your token status:
```bash
npm run login -- --status
```

Or:
```bash
node login.js --status
```

Output:
```
=== Schwab Token Status ===

✅ Access Token:
   eyJhbGciOiJIUzI1NiIsInR5cC...
   ✅ Valid until 6/11/2025, 3:30:00 PM

✅ Refresh Token:
   ewo0WVW5OWUxN2QtZDkwZC00...
   Valid for 7 days

💾 Stored in:
   - .schwab_tokens.json
   - .env (SCHWAB_ACCESS_TOKEN and SCHWAB_REFRESH_TOKEN)
```

## Test API Connection

Verify everything works:
```bash
npm run test-api
```

Output:
```
=== Testing Schwab API Connection ===

✅ Configuration loaded
   Account: 12345678
   Token: eyJhbGciOiJIUzI1NiIsInR5...

🔍 Test 1: Fetching account information...
✅ Account access: SUCCESS

🔍 Test 2: Fetching quotes for SPY...
✅ Market data access: SUCCESS
   SPY Price: $545.23

🔍 Test 3: Fetching open positions...
✅ Position access: SUCCESS
   Open orders/positions: 2

=== Connection Test Summary ===

✅ All API endpoints accessible
✅ Authentication valid
✅ Ready to run bot

Next steps:
  npm run dry-run     # Test with dry-run mode
  npm run once        # Single run with real data
  npm run status      # Check current positions
```

## 🚀 Next Steps

Now that you have your access token:

1. **Test dry-run mode** (no real orders):
   ```bash
   npm run dry-run
   ```

2. **Check current positions**:
   ```bash
   npm run status
   ```

3. **Make a real run** (when ready):
   ```bash
   npm run once
   ```

4. **Review logs**:
   ```bash
   tail -f logs/schwab-bot.log
   ```

## 🔄 Token Refresh

Tokens expire, but the bot handles it automatically. If you need to manually refresh:

```bash
node login.js --refresh
```

Or full re-authentication (if refresh fails):
```bash
node login.js
```

## ⚠️ If Something Goes Wrong

### Browser doesn't open
**Solution:** Copy the authorization URL from console, paste in browser manually

### "Failed to exchange code for token"
**Solution:**
1. Check Client ID and Secret are correct (copy-paste again)
2. Verify Schwab app redirect URI is exactly: `http://localhost:8080/callback`
3. Delete `.schwab_tokens.json` and try again

### "Invalid redirect_uri"
**Solution:** Go to Schwab Developer Portal → Your App → Settings
- Make sure redirect URI is exactly: `http://localhost:8080/callback`
- Save changes and try `node login.js` again

### "No Schwab access token found" when running bot
**Solution:** Run `node login.js` again

### API test fails with 401
**Solution:** Token expired
```bash
node login.js --refresh
npm run test-api
```

## 📚 More Info

- [Complete OAuth Setup Guide](./OAUTH_SETUP.md)
- [Token Quick Reference](./TOKEN_QUICK_REFERENCE.md)
- [README](./README.md) — Full documentation

---

**That's it!** Your access token is ready. The bot will automatically refresh it when needed. 🎉
