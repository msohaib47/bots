import os
import sys
from dotenv import load_dotenv

# Add parent directory to path to enable imports from common
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

# Import shared Alpaca configuration from common module
from common.alpaca_config import API_KEY, API_SECRET, BASE_URL, DATA_URL

from symbols import SYMBOLS  # edit symbols.py to change trading symbols
# Raised 2 -> 4 on 2026-09-05 (with MAX_SAME_DIRECTION below) after a $200-seed backtest
# comparison: roughly doubles total return in both a 5-month and an August-only window at
# the same overall drawdown %, at the cost of a deeper single-month drawdown (Aug: -66% -> -78%).
MAX_CONTRACTS     = int(os.getenv('MAX_CONTRACTS', 4))
STOP_LOSS_PCT     = float(os.getenv('STOP_LOSS_PCT', 0.15))       # exit if down 15%
PROFIT_TRAIL_TRIGGER = float(os.getenv('PROFIT_TRAIL_TRIGGER', 0.30))  # start trailing at +30%
TRAIL_WIGGLE      = float(os.getenv('TRAIL_WIGGLE', 0.10))         # stop = high * (1 - 10%)
# Scale-out (sell half the contracts at +HALF_CLOSE_PROFIT_PCT) -- OFF by default:
# 2026-09-05 option-path backtest (Jun-Sep, SPY/QQQ/IWM) showed it cost ~$400 on
# +$2.3k with no drawdown benefit once the 12:00 entry cutoff below is in place.
HALF_CLOSE_ENABLED    = os.getenv('HALF_CLOSE_ENABLED', 'false').lower() == 'true'
HALF_CLOSE_PROFIT_PCT = float(os.getenv('HALF_CLOSE_PROFIT_PCT', 0.50))

# No new entries after 12:00 ET (was 15:45): entries after noon were net negative
# in the same backtest (19 trades, -$347, PF 0.76) and the change lowered max
# drawdown. This bot scalps ~15-min holds off the morning range.
NO_NEW_ENTRY_TIME = os.getenv('NO_NEW_ENTRY_TIME', '12:00')  # ET
FORCE_CLOSE_TIME  = os.getenv('FORCE_CLOSE_TIME', '15:50')   # ET

# Cool-down after a stop-loss (not a trailing/profit stop) fires for a symbol --
# blocks new entries on that underlying until the window passes, and the next
# signal check after that is a fresh evaluation (no carried-over state).
COOLDOWN_MINUTES  = int(os.getenv('COOLDOWN_MINUTES', 30))

# Risk limits
MAX_DAILY_LOSS_PER_SYMBOL = float(os.getenv('MAX_DAILY_LOSS_PER_SYMBOL', 100))  # $ realized loss/day/symbol
MAX_DAILY_LOSS_TOTAL      = float(os.getenv('MAX_DAILY_LOSS_TOTAL', 500))       # $ realized loss/day, all symbols
MAX_SAME_DIRECTION        = int(os.getenv('MAX_SAME_DIRECTION', 4))             # max concurrent CALLs (or PUTs) open at once -- raised from 2 on 2026-09-05, see MAX_CONTRACTS note above
# Max premium tied up in open positions at any moment (sum of entry_cost x contracts x 100).
# A new entry is sized down to whatever fits under the cap (+ tolerance), and skipped if
# not even 1 contract fits. This is an exposure limit, not a P&L circuit breaker.
MAX_OPEN_EXPOSURE         = float(os.getenv('MAX_OPEN_EXPOSURE', 5000))  # raised from $2,000 on 2026-09-05
# Max share of current cash a single entry may spend (was a hard-coded 25% -- relaxed to 75%
# on 2026-09-05: with small balances 25% couldn't afford even one contract on most signals).
# MAX_OPEN_EXPOSURE above still caps the total premium across all open positions.
CASH_PER_TRADE_PCT        = float(os.getenv('CASH_PER_TRADE_PCT', 0.75))
EXPOSURE_TOLERANCE_PCT    = float(os.getenv('EXPOSURE_TOLERANCE_PCT', 0.10))     # allow up to cap x 1.10
MAX_POSITIONS_PER_SYMBOL  = int(os.getenv('MAX_POSITIONS_PER_SYMBOL', 2))       # max open positions per underlying
MIN_CONTRACT_PRICE        = float(os.getenv('MIN_CONTRACT_PRICE', 0.20))       # skip contracts quoted under $0.20/share ($20/contract)

# Entry-quality filters (added 2026-09-05 from loser analysis of the Jun-Sep option-path backtest):
#  - MAX_PREMIUM_PCT: skip a contract whose mid is more than this % of spot. Premium >1% of
#    spot (high IV / not really ATM) won 42% with PF 0.68 vs 70% / PF 3.5 under 0.5%.
#  - MAX_EMA_GAP_ATR: veto the signal when |EMA9 - EMA21| / ATR(14) on 5-min bars exceeds
#    this -- the move is already extended ("chasing"); such entries won 44% with PF 0.81.
MAX_PREMIUM_PCT           = float(os.getenv('MAX_PREMIUM_PCT', 1.0))
MAX_EMA_GAP_ATR           = float(os.getenv('MAX_EMA_GAP_ATR', 1.2))

STATE_FILE      = 'positions.json'
COOLDOWN_FILE   = 'cooldowns.json'
DAILY_PNL_FILE  = 'daily_pnl.json'
PNL_HISTORY_FILE = 'pnl_history.json'   # lifetime realized P&L + high-water mark (status display only)
LOG_FILE        = 'logs/daytrading.log'
TRADES_LOG      = 'trades.csv'
