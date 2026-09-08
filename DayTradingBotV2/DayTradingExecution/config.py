"""
Account/risk config for sizing_service.py + execution_service.py.

Run with CWD set to a specific account's thin directory (e.g.
DayTradingBotV2/DayTradingBot-Start200/) -- every *_FILE path below resolves
relative to it, matching the same CWD-relative pattern DayTradingBot v1
already relies on (see PLAN.md's "Directory layout" section), so the exact
same code runs correctly for every account without modification, just a
different working directory per account.

IMPORTANT: `load_dotenv()` with no path argument does NOT search the process's
CWD by default -- it walks up from the `__main__` script's own directory
(here, DayTradingExecution/, a *sibling* of the account directories, not an
ancestor), so the bare form silently finds no .env at all when this script is
invoked the way the real wrapper scripts do (`python
.../DayTradingExecution/sizing_service.py` after `cd`-ing into the account
directory) -- confirmed by a real test run 2026-09-06, where it silently fell
through to no credentials at all rather than raising. `dotenv_path` is passed
explicitly below specifically to force CWD-relative resolution instead.

ACCOUNT_NAME identifies this account for ZMQ port lookup (shared/ports.py)
and topic naming -- set via env var (the cron/systemd wrapper script sets
it) or defaults to the CWD's directory name.
"""
import os
import sys
from dotenv import load_dotenv

# Add the repo root to path so `common` resolves regardless of CWD
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

load_dotenv(dotenv_path=os.path.join(os.getcwd(), '.env'))

from common.alpaca_config import API_KEY, API_SECRET, BASE_URL, DATA_URL

ACCOUNT_NAME = os.environ.get('ACCOUNT_NAME') or os.path.basename(os.getcwd())

# Trade-management knobs (position-level mechanics; values are per-account via .env)
MAX_CONTRACTS         = int(os.getenv('MAX_CONTRACTS', 4))
STOP_LOSS_PCT         = float(os.getenv('STOP_LOSS_PCT', 0.15))
PROFIT_TRAIL_TRIGGER  = float(os.getenv('PROFIT_TRAIL_TRIGGER', 0.30))
TRAIL_WIGGLE          = float(os.getenv('TRAIL_WIGGLE', 0.10))
HALF_CLOSE_ENABLED    = os.getenv('HALF_CLOSE_ENABLED', 'false').lower() == 'true'
HALF_CLOSE_PROFIT_PCT = float(os.getenv('HALF_CLOSE_PROFIT_PCT', 0.50))
COOLDOWN_MINUTES      = int(os.getenv('COOLDOWN_MINUTES', 30))

# Risk limits (per-account -- see PLAN.md's still-open fixed-$ vs %-of-equity question)
MAX_DAILY_LOSS_PER_SYMBOL = float(os.getenv('MAX_DAILY_LOSS_PER_SYMBOL', 100))
MAX_DAILY_LOSS_TOTAL      = float(os.getenv('MAX_DAILY_LOSS_TOTAL', 500))
MAX_SAME_DIRECTION        = int(os.getenv('MAX_SAME_DIRECTION', 4))
MAX_OPEN_EXPOSURE         = float(os.getenv('MAX_OPEN_EXPOSURE', 5000))
CASH_PER_TRADE_PCT        = float(os.getenv('CASH_PER_TRADE_PCT', 0.75))
EXPOSURE_TOLERANCE_PCT    = float(os.getenv('EXPOSURE_TOLERANCE_PCT', 0.10))
MAX_POSITIONS_PER_SYMBOL  = int(os.getenv('MAX_POSITIONS_PER_SYMBOL', 2))
MIN_CONTRACT_PRICE        = float(os.getenv('MIN_CONTRACT_PRICE', 0.20))
MAX_PREMIUM_PCT           = float(os.getenv('MAX_PREMIUM_PCT', 1.0))

NO_NEW_ENTRY_TIME = os.getenv('NO_NEW_ENTRY_TIME', '12:00')  # ET
FORCE_CLOSE_TIME  = os.getenv('FORCE_CLOSE_TIME', '15:50')   # ET

STATE_FILE       = 'positions.json'
COOLDOWN_FILE    = 'cooldowns.json'
DAILY_PNL_FILE   = 'daily_pnl.json'
PNL_HISTORY_FILE = 'pnl_history.json'
TRADES_LOG       = 'trades.csv'
LOG_DIR          = 'logs'
