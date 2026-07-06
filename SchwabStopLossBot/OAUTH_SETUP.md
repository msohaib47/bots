# Schwab OAuth Setup Guide

Complete step-by-step guide to get your Schwab access token for the bot.

## Prerequisites

- Schwab trading account
- Client ID and Client Secret (from Schwab API registration)
- Node.js installed locally

## Step 1: Register for Schwab API

1. Go to [Schwab Developer Portal](https://developer.schwab.com)
2. Sign in with your Schwab account
3. Navigate to **Apps** → **My Apps**
4. Click **Create a new app**
5. Fill in the app details:
   - **App Name:** e.g., "Trading Bot"
   - **App Description:** "Automated stop-loss manager"
   - **Redirect URI:** `http://localhost:8080/callback` (important!)
6. Accept terms and create the app
7. View the app details and copy:
   - **Client ID**
   - **Client Secret**

⚠️ **Important:** The redirect URI must match exactly: `http://localhost:8080/callback`

## Step 2: Update .env File

1. Navigate to the SchwabStopLossBot directory:
   ```bash
   cd Z:\work\bots\SchwabStopLossBot
   ```

2. Copy the template:
   ```bash
   cp .env.example .env
   ```

3. Edit `.env` and add your credentials:
   ```env
   SCHWAB_CLIENT_ID=your_client_id_from_step_1
   SCHWAB_CLIENT_SECRET=your_client_secret_from_step_1
   SCHWAB_ACCOUNT_NUMBER=your_account_number
   DRY_RUN=true
   ```

   **Finding your account number:**
   - Log into Schwab account
   - Go to Account Settings
   - Look for account number (usually 8 digits, sometimes shown as "Account #")

4. Install dependencies:
   ```bash
   npm install
   ```

## Step 3: Run the Login Script

This script opens Schwab's login page, gets your authorization, and exchanges it for an access token.

```bash
node login.js
```

**What happens:**
1. A URL opens in your default browser (or printed to console)
2. You'll see Schwab's login page
3. Sign in with your Schwab credentials
4. You'll see a permissions screen asking to authorize the app
5. **Grant access** — you'll be redirected to a success page
6. Return to terminal — the script saves your access token automatically

**Output should look like:**
```
=== Schwab OAuth Login ===

Opening browser for Schwab login...

✅ Authorization code received

🔄 Exchanging code for access token...

✅ Authentication successful!

   Access Token: eyJhbGciOiJIUzI1NiIsInR5cC...
   Expires: 2025-06-11T15:30:00.000Z
   Refresh Token: Saved (valid for 7 days)
```

## Step 4: Verify Setup

Check that your token was saved:

```bash
node login.js --status
```

**Output:**
```
=== Schwab Token Status ===

✅ Access Token:
   eyJhbGciOiJIUzI1NiIsInR5cC...
   ✅ Valid until 6/11/2025, 3:30:00 PM

✅ Refresh Token:
   ...aaXJlIjoiUkVGUkVTSCJ9Cg==...
   Valid for 7 days

💾 Stored in:
   - .schwab_tokens.json
   - .env (SCHWAB_ACCESS_TOKEN and SCHWAB_REFRESH_TOKEN)
```

## Step 5: Test the Bot

Now that you have an access token, test the bot:

```bash
npm run dry-run
```

If successful, you'll see:
```
info: Starting Schwab stop-loss check...
info: Found option positions (count: 2)
...
```

## Token Management

### How Tokens Work

- **Access Token:** Short-lived (typically 30 minutes). Used to make API calls.
- **Refresh Token:** Long-lived (7 days). Used to get a new access token when the old one expires.

### Automatic Refresh

The bot automatically refreshes your access token when:
- It's about to expire (within 5 minutes)
- Before making API calls

No action needed — just keep the refresh token in your `.env`.

### Manual Refresh

If your token expires before the next run:

```bash
node login.js --refresh
```

This uses your refresh token to get a new access token.

### Full Re-authentication

If both tokens expire or you want to start fresh:

```bash
# Delete the token file
rm .schwab_tokens.json

# Run login again
node login.js
```

## Troubleshooting

### "Failed to exchange code for token"

**Cause:** Redirect URI mismatch or invalid credentials

**Fix:**
1. Verify `SCHWAB_CLIENT_ID` and `SCHWAB_CLIENT_SECRET` are correct
2. Check Schwab app settings — redirect URI must be `http://localhost:8080/callback`
3. Delete `.schwab_tokens.json` and try again

### "No access token found"

**Cause:** Login script wasn't run or failed

**Fix:**
```bash
node login.js
```

### Browser doesn't open automatically

**Cause:** `open` package not installed (optional dependency)

**Fix:** Copy the URL printed to console and paste it in your browser manually

### "Invalid refresh token"

**Cause:** Refresh token expired (valid for 7 days only)

**Fix:**
```bash
rm .schwab_tokens.json
node login.js
```

### Token error during bot run

**Cause:** Access token expired and no valid refresh token

**Fix:**
```bash
node login.js --refresh
# or
node login.js  # for full re-auth
```

## Security Notes

⚠️ **Important:**
- `.schwab_tokens.json` contains your access token — **do not commit to git**
- `.env` contains credentials — **do not commit to git** (already in `.gitignore`)
- Refresh tokens are valid for 7 days — keep them secure
- If you think your token is compromised, invalidate the app in Schwab account settings

## Deployment to Server

Once tokens are working locally:

1. Copy `.env` and `.schwab_tokens.json` to server:
   ```bash
   scp .env claude@192.168.1.250:~/bots/SchwabStopLossBot/
   scp .schwab_tokens.json claude@192.168.1.250:~/bots/SchwabStopLossBot/
   ```

2. Test on server:
   ```bash
   ssh claude@192.168.1.250 "cd ~/bots/SchwabStopLossBot && node bot.js --once"
   ```

3. Add to crontab (runs every minute during market hours):
   ```bash
   * 13-21 * * 1-5 cd ~/bots/SchwabStopLossBot && /usr/bin/node bot.js --once >> ~/bots/logs/schwab-bot.log 2>&1
   ```

The bot will automatically refresh tokens as needed.

## Next Steps

- [README.md](./README.md) — Full documentation
- [QUICKSTART.md](./QUICKSTART.md) — Quick start guide
- Start the bot: `npm run once`
