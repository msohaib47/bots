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
# Raised again 4 -> 10 on 2026-09-08, alongside the symbols.py swap to the 4-symbol
# META/GOOG/MSFT/SPY set: an Aug1-Sep8/$200-seed backtest on that set showed this
# was the dominant lever, not MAX_OPEN_EXPOSURE -- $16,400 at 4 contracts/$5k
# exposure -> $27,876 at 10 contracts/$5k exposure (exposure cap left unchanged;
# raising it further to $10k/unlimited only added another ~$2,600 on top, since
# exposure rarely bound in this combo -- peak usage stayed under $5,500 even at
# 10 contracts). See DAYTRADING_RULES.md for the full comparison sweep.
# Renamed from MAX_CONTRACTS on 2026-09-08 and merged with the old
# MAX_POSITIONS_PER_SYMBOL cap (previously a separate "max 2 concurrent
# positions per underlying" limit): per explicit user intent, a symbol never
# gets a second concurrent position at all now (see the single `if
# pm.count_positions_for(state, symbol) >= 1` check in bot.py) -- this is now
# purely "how many contracts can that one position hold."
MAX_CONTRACTS_PER_SYMBOL = int(os.getenv('MAX_CONTRACTS_PER_SYMBOL', 10))
STOP_LOSS_PCT     = float(os.getenv('STOP_LOSS_PCT', 0.15))       # exit if down 15%
PROFIT_TRAIL_TRIGGER = float(os.getenv('PROFIT_TRAIL_TRIGGER', 0.30))  # start trailing at +30%
TRAIL_WIGGLE      = float(os.getenv('TRAIL_WIGGLE', 0.10))         # stop = high * (1 - 10%)
# Scale-out (sell half the contracts at +HALF_CLOSE_PROFIT_PCT) -- OFF by default:
# 2026-09-05 option-path backtest (Jun-Sep, SPY/QQQ/IWM) showed it cost ~$400 on
# +$2.3k with no drawdown benefit once the 12:00 entry cutoff below is in place.
HALF_CLOSE_ENABLED    = os.getenv('HALF_CLOSE_ENABLED', 'false').lower() == 'true'
HALF_CLOSE_PROFIT_PCT = float(os.getenv('HALF_CLOSE_PROFIT_PCT', 0.50))

# Entries allowed all day (no cutoff) as of 2026-09-08: the earlier 12:00 ET
# cutoff (justified by a pre-entry-quality-filter Jun-Sep backtest) was
# re-tested against an August/$200-seed backtest with MAX_EMA_GAP_ATR/
# MAX_PREMIUM_PCT in place and found to cost real money at a small account
# size -- 41 of 121 signal-days were being cash-blocked, but only because
# afternoon trades that would have compounded the account (and unlocked
# larger later sizing) were being skipped outright. Removing the cutoff:
# 80->134 trades taken, win rate 30.0%->41.0%, total P&L $4,550->$13,347 over
# the same August window, every symbol but QQQ/INTC improved. Force-close
# moved to 15:58 (was 15:50) to match -- still comfortably before the 16:00
# ET close, just no longer redundant with an earlier no-new-entries cutoff.
NO_NEW_ENTRY_TIME = os.getenv('NO_NEW_ENTRY_TIME', '15:58')  # ET
FORCE_CLOSE_TIME  = os.getenv('FORCE_CLOSE_TIME', '15:58')   # ET

# Cool-down after a stop-loss (not a trailing/profit stop) fires for a symbol --
# blocks new entries on that underlying until the window passes, and the next
# signal check after that is a fresh evaluation (no carried-over state).
COOLDOWN_MINUTES  = int(os.getenv('COOLDOWN_MINUTES', 15))

# Risk limits
MAX_DAILY_LOSS_PER_SYMBOL = float(os.getenv('MAX_DAILY_LOSS_PER_SYMBOL', 100))  # $ realized loss/day/symbol
MAX_DAILY_LOSS_TOTAL      = float(os.getenv('MAX_DAILY_LOSS_TOTAL', 500))       # $ realized loss/day, all symbols
MAX_SAME_DIRECTION        = int(os.getenv('MAX_SAME_DIRECTION', 4))             # max concurrent CALLs (or PUTs) open at once, across all symbols -- raised from 2 on 2026-09-05, see MAX_CONTRACTS_PER_SYMBOL note above
# Max premium tied up in open positions at any moment (sum of entry_cost x contracts x 100).
# A new entry is sized down to whatever fits under the cap (+ tolerance), and skipped if
# not even 1 contract fits. This is an exposure limit, not a P&L circuit breaker.
MAX_OPEN_EXPOSURE         = float(os.getenv('MAX_OPEN_EXPOSURE', 5000))  # raised from $2,000 on 2026-09-05
# Max share of current cash a single entry may spend (was a hard-coded 25% -- relaxed to 75%
# on 2026-09-05: with small balances 25% couldn't afford even one contract on most signals).
# MAX_OPEN_EXPOSURE above still caps the total premium across all open positions.
CASH_PER_TRADE_PCT        = float(os.getenv('CASH_PER_TRADE_PCT', 0.75))
EXPOSURE_TOLERANCE_PCT    = float(os.getenv('EXPOSURE_TOLERANCE_PCT', 0.10))     # allow up to cap x 1.10
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
