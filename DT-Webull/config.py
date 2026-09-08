"""
Config for DT-Webull -- a Webull-API variant of DayTradingBot (v1), same
trading rules, multi-account capable.

Unlike DayTradingBot (single Alpaca account, module-level credentials), this
bot can run against N Webull accounts in one process/cron tick. Configure via
WEBULL_ACCOUNTS (comma-separated account names) plus one block of
WEBULL_<NAME>_* vars per account:

  WEBULL_ACCOUNTS=main,live

  WEBULL_MAIN_APP_KEY=...
  WEBULL_MAIN_APP_SECRET=...
  WEBULL_MAIN_ACCOUNT_ID=...
  WEBULL_MAIN_BASE_URL=...      # optional, defaults to WEBULL_BASE_URL

  WEBULL_LIVE_APP_KEY=...
  WEBULL_LIVE_APP_SECRET=...
  WEBULL_LIVE_ACCOUNT_ID=...
  WEBULL_LIVE_BASE_URL=...      # optional, per-account override

Each account can point at a DIFFERENT host (WEBULL_<NAME>_BASE_URL) --
paper/sandbox credentials and real/production credentials are NOT
interchangeable across api.sandbox.webull.com vs. api.webull.com (confirmed
2026-09-07: same-shaped credentials 401'd against the wrong one of the two).
Added 2026-09-08 when a second, real/live-money account was introduced
alongside the existing paper one.

Each account gets its own state files (positions_<name>.json, trades_<name>.csv,
etc. -- see ACCOUNTS[name]['state_file'] and friends below), so accounts never
share or clobber each other's tracked positions.

All *trading-rule* thresholds below (MAX_CONTRACTS, STOP_LOSS_PCT, etc.) are
shared across every account -- same trading rules as DayTradingBot, per the
request that built this bot. If per-account rule overrides are ever wanted,
that's a deliberate future change, not implied by this file's current shape.
"""
import os
from dotenv import load_dotenv

load_dotenv()

from symbols import SYMBOLS  # edit symbols.py to change trading symbols

# ── Webull API endpoints ─────────────────────────────────────────────────────
# api.webull.com is the ONLY US host in the real SDK's endpoints.json (read
# from github.com/webull-inc/webull-openapi-python-sdk 2026-09-07) -- there is
# no separate sandbox subdomain. A live test against a guessed
# 'api.sandbox.webull.com' host returned a real (non-DNS-failure) 404 JSON
# response, but every account/trade path 404'd there; switching to the real
# host is what the actual SDK source uses. Paper vs. real trading is
# apparently distinguished by the account itself (the user's "paper account"
# credentials), not by a different API host.
WEBULL_BASE_URL  = os.getenv('WEBULL_BASE_URL', 'https://api.webull.com')
WEBULL_REGION_ID = os.getenv('WEBULL_REGION_ID', 'us')

# ── Multi-account credentials ────────────────────────────────────────────────

def _load_accounts() -> dict:
    names = [n.strip() for n in os.getenv('WEBULL_ACCOUNTS', '').split(',') if n.strip()]
    accounts = {}
    for name in names:
        prefix = f'WEBULL_{name.upper()}_'
        accounts[name] = {
            'app_key':    os.getenv(f'{prefix}APP_KEY', ''),
            'app_secret': os.getenv(f'{prefix}APP_SECRET', ''),
            'account_id': os.getenv(f'{prefix}ACCOUNT_ID', ''),
            'base_url':   os.getenv(f'{prefix}BASE_URL', WEBULL_BASE_URL),
            'state_file':        f'positions_{name}.json',
            'cooldown_file':     f'cooldowns_{name}.json',
            'daily_pnl_file':    f'daily_pnl_{name}.json',
            'pnl_history_file':  f'pnl_history_{name}.json',
            'trades_log':        f'trades_{name}.csv',
            'pdt_flag_file':     f'logs/pdt_blocked_{name}.flag',
        }
    return accounts


ACCOUNTS = _load_accounts()   # {name: {...}} -- empty until WEBULL_ACCOUNTS is set in .env

LOG_FILE = 'logs/daytrading.log'

# ── Trading-rule thresholds (byte-identical defaults to DayTradingBot/config.py) ──

MAX_CONTRACTS     = int(os.getenv('MAX_CONTRACTS', 4))
STOP_LOSS_PCT     = float(os.getenv('STOP_LOSS_PCT', 0.15))
PROFIT_TRAIL_TRIGGER = float(os.getenv('PROFIT_TRAIL_TRIGGER', 0.30))
TRAIL_WIGGLE      = float(os.getenv('TRAIL_WIGGLE', 0.10))
HALF_CLOSE_ENABLED    = os.getenv('HALF_CLOSE_ENABLED', 'false').lower() == 'true'
HALF_CLOSE_PROFIT_PCT = float(os.getenv('HALF_CLOSE_PROFIT_PCT', 0.50))

NO_NEW_ENTRY_TIME = os.getenv('NO_NEW_ENTRY_TIME', '12:00')  # ET
FORCE_CLOSE_TIME  = os.getenv('FORCE_CLOSE_TIME', '15:50')   # ET

COOLDOWN_MINUTES  = int(os.getenv('COOLDOWN_MINUTES', 30))

MAX_DAILY_LOSS_PER_SYMBOL = float(os.getenv('MAX_DAILY_LOSS_PER_SYMBOL', 100))
MAX_DAILY_LOSS_TOTAL      = float(os.getenv('MAX_DAILY_LOSS_TOTAL', 500))
MAX_SAME_DIRECTION        = int(os.getenv('MAX_SAME_DIRECTION', 4))
MAX_OPEN_EXPOSURE         = float(os.getenv('MAX_OPEN_EXPOSURE', 5000))
CASH_PER_TRADE_PCT        = float(os.getenv('CASH_PER_TRADE_PCT', 0.75))
EXPOSURE_TOLERANCE_PCT    = float(os.getenv('EXPOSURE_TOLERANCE_PCT', 0.10))
MAX_POSITIONS_PER_SYMBOL  = int(os.getenv('MAX_POSITIONS_PER_SYMBOL', 2))
MIN_CONTRACT_PRICE        = float(os.getenv('MIN_CONTRACT_PRICE', 0.20))

MAX_PREMIUM_PCT           = float(os.getenv('MAX_PREMIUM_PCT', 1.0))
MAX_EMA_GAP_ATR           = float(os.getenv('MAX_EMA_GAP_ATR', 1.2))
