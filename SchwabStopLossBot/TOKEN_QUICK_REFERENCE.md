# Schwab Access Token — Quick Reference

## 1️⃣ Get Client ID & Secret (One-time)

1. Go to [Schwab Developer Portal](https://developer.schwab.com)
2. Sign in → **Apps** → **Create a new app**
3. Set redirect URI to: `http://localhost:8080/callback` (exact match!)
4. Copy: **Client ID** and **Client Secret**

## 2️⃣ Configure .env

```bash
cd Z:\work\bots\SchwabStopLossBot
cp .env.example .env
```

Edit `.env`:
```env
SCHWAB_CLIENT_ID=<paste_client_id_here>
SCHWAB_CLIENT_SECRET=<paste_client_secret_here>
SCHWAB_ACCOUNT_NUMBER=<your_account_number>
```

## 3️⃣ Run Login Script

```bash
npm install
node login.js
```

**What happens:**
- Browser opens to Schwab login
- You authorize the app
- Access token is saved automatically

**Output:**
```
✅ Authentication successful!
   Access Token: eyJhbGciOiJIUzI1Ni...
   Expires: 2025-06-11T15:30:00.000Z
   Refresh Token: Saved (valid for 7 days)
```

## 4️⃣ Done! ✅

Your `.env` now has:
- `SCHWAB_ACCESS_TOKEN` (auto-filled)
- `SCHWAB_REFRESH_TOKEN` (auto-filled)

## Verify It Worked

```bash
node login.js --status
npm run dry-run
```

## Common Issues

| Problem | Solution |
|---------|----------|
| Browser doesn't open | Copy URL from console, paste manually |
| "Invalid credentials" | Check Client ID/Secret in .env |
| "Redirect URI mismatch" | Verify Schwab app settings → exact match to `http://localhost:8080/callback` |
| "Token expired" | Run `node login.js --refresh` |
| Completely expired | Run `node login.js` again for fresh auth |

## Token Lifetime

- **Access Token:** 30 minutes (auto-refreshes)
- **Refresh Token:** 7 days (refreshed each time)

No manual refresh needed during bot operation — it's automatic!

## Files Generated

After successful login:
- `.env` — Updated with access/refresh tokens
- `.schwab_tokens.json` — Backup of tokens (do NOT commit to git)

Both files are in `.gitignore` for security.

## Detailed Setup

→ See [OAUTH_SETUP.md](./OAUTH_SETUP.md) for complete guide
