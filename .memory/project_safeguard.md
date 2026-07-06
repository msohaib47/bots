---
name: safeguard-bots
description: Stop-loss guardian bots — RobinhoodSafeGuard (Python) and SchwabStopLossBot (Node.js)
metadata:
  type: project
---

**Location:** `Z:\work\bots\RobinhoodSafeGuard\`

**Purpose:** Every minute during market hours, checks all open LONG option positions on Robinhood. Places and trails stop-loss orders automatically.

**Stop logic:**
- `stop = mark − min(mark × 25%, $20)`
- Below $80 mark: 25% rule. Above $80: $20 cap kicks in.
- Trailing: tracks highest price seen per position in `guard_state.json`; stop only ever moves up
- Quantity mismatch (partial manual close) → cancels and replaces stop with correct qty

**Order type:** Stop-limit sell-to-close, GTC. Limit = stop × 0.90 (10% slippage buffer).

**DRY_RUN=true** by default. To enable live orders:
`ssh claude@192.168.1.250 "echo 'DRY_RUN=false' >> ~/bots/RobinhoodSafeGuard/.env"`

**Cron:** `* 13-20 * * 1-5` (every minute Mon-Fri, 13:00-20:00 UTC = 9am-4pm ET)

**Key files:**
- `bot.py` — `run()` and `print_status()` entry points
- `robinhood.py` — API wrapper for option positions and stop orders
- `config.py` — STOP_LOSS_PCT=0.25, STOP_LOSS_MAX=20.0 (overridable via .env)
- `guard_state.json` — persists highest_price and stop_order_id per option_id

---

## SchwabStopLossBot (Node.js)

**Location:** `Z:\work\bots\SchwabStopLossBot\`

**Purpose:** Every minute during market hours, checks all open LONG option positions on Schwab. Calculates tiered trailing stops and places/updates stop orders automatically.

**Stop tiers (per contract mark price):**
- `< $100`: $10 margin or 25% (whichever is larger)
- `$100–$200`: $15 margin
- `> $200`: $25 margin

**Auth:** OAuth 2.0. Tokens saved in `.schwab_tokens.json` and `.env`.
- Login (run locally on Windows, needs browser): `npm run login`
- Token refresh is automatic; refresh tokens expire after ~7 days
- Re-auth if refresh expires: delete `.schwab_tokens.json`, then `npm run login`
- Schwab API uses **encrypted account hash**, not raw account number — `resolveAccountHash()` in `schwab.js` fetches it automatically on startup via `GET /trader/v1/accounts/accountNumbers`

**DRY_RUN=true** by default. To go live: set `DRY_RUN=false` in `.env`.

**Cron:** `* 13-20 * * 1-5` via `~/schwabbot.sh` → `logs/schwab.log`

**Key files:**
- `bot.js` — main entry point; `--once`, `--status`, `--reset` flags
- `schwab.js` — Schwab API wrapper (positions, quotes, orders, token refresh)
- `login.js` — interactive OAuth flow; `--status`, `--refresh` flags
- `position-manager.js` — stop price tiers, state persistence
- `guard_state.json` — persists highest prices and stop order IDs per position
- `.schwab_tokens.json` — OAuth token cache (not committed to git)

**Node.js version required:** 18+ (ES modules). Server runs Node 22.
