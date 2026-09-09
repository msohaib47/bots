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
# 4 -> 10 on 2026-09-08 to match DayTradingBot v1's MAX_CONTRACTS_PER_SYMBOL
# (v1 renamed MAX_CONTRACTS and merged its per-symbol position cap into it the
# same day). At a $200 seed with 75%-of-cash sizing this dominates compounding:
# the two engines' win rates matched within 2.4 points over Aug-Sep, but v1
# finished 40x higher almost entirely on per-trade size.
MAX_CONTRACTS         = int(os.getenv('MAX_CONTRACTS', 10))
# Defaults raised 2026-09-08 to match what DayTradingBot v1 ACTUALLY runs.
# v1's config.py declares 0.15/0.10 too, but its .env overrides them to
# 0.30/0.15 -- and a v2 backtest never sees any .env (its CWD is the run
# directory), so it silently ran a 2x tighter stop and a 1.5x tighter trail
# than the live bot it is supposed to model. That single difference decided
# most exits: on QQQ 2026-08-03 the same contract, entered at the same time
# and price, exited at 1.95 under a 10% wiggle but rode to 3.13 under 15%.
STOP_LOSS_PCT         = float(os.getenv('STOP_LOSS_PCT', 0.30))
PROFIT_TRAIL_TRIGGER  = float(os.getenv('PROFIT_TRAIL_TRIGGER', 0.30))
TRAIL_WIGGLE          = float(os.getenv('TRAIL_WIGGLE', 0.15))
HALF_CLOSE_ENABLED    = os.getenv('HALF_CLOSE_ENABLED', 'false').lower() == 'true'
HALF_CLOSE_PROFIT_PCT = float(os.getenv('HALF_CLOSE_PROFIT_PCT', 0.50))
COOLDOWN_MINUTES      = int(os.getenv('COOLDOWN_MINUTES', 15))   # 30 -> 15, matches v1

# Risk limits (per-account -- see PLAN.md's still-open fixed-$ vs %-of-equity question)
MAX_DAILY_LOSS_PER_SYMBOL = float(os.getenv('MAX_DAILY_LOSS_PER_SYMBOL', 100))
MAX_DAILY_LOSS_TOTAL      = float(os.getenv('MAX_DAILY_LOSS_TOTAL', 500))

# Opt-in %-of-equity alternative to the two fixed-$ caps above -- unset (None) by
# default, meaning zero behavior change for the live daemons unless these env
# vars are explicitly set. When set, sizing_service.py computes the effective
# cap as pct * current portfolio_value instead of the fixed dollar amount.
# Added 2026-09-08 to test whether the $100/$500 fixed caps (originally sized
# for the ~$10k account) were the actual bottleneck starving a small account
# (e.g. Start-at-200) of recovery trades after early losses -- see
# BACKTESTING_ENGINE_PLAN.md / TRADING_RULES.md for the backtest findings that
# prompted this. Ratios below (1% / 5%) match the original $100/$500-on-$10k
# proportions, just re-based to track current equity instead of staying fixed.
DAILY_LOSS_PCT_PER_SYMBOL = os.environ.get('DAILY_LOSS_PCT_PER_SYMBOL')
DAILY_LOSS_PCT_PER_SYMBOL = float(DAILY_LOSS_PCT_PER_SYMBOL) if DAILY_LOSS_PCT_PER_SYMBOL else None
DAILY_LOSS_PCT_TOTAL = os.environ.get('DAILY_LOSS_PCT_TOTAL')
DAILY_LOSS_PCT_TOTAL = float(DAILY_LOSS_PCT_TOTAL) if DAILY_LOSS_PCT_TOTAL else None
MAX_SAME_DIRECTION        = int(os.getenv('MAX_SAME_DIRECTION', 4))
MAX_OPEN_EXPOSURE         = float(os.getenv('MAX_OPEN_EXPOSURE', 5000))
CASH_PER_TRADE_PCT        = float(os.getenv('CASH_PER_TRADE_PCT', 0.75))
EXPOSURE_TOLERANCE_PCT    = float(os.getenv('EXPOSURE_TOLERANCE_PCT', 0.10))
# 2 -> 1 on 2026-09-08, matching v1's merged cap: a symbol never holds a second
# CONCURRENT position, it just holds up to MAX_CONTRACTS in the one. Re-entry
# after that position closes is unaffected (and still gated by COOLDOWN_MINUTES),
# which is what "allow re-entry" means for both engines -- neither stacks.
MAX_POSITIONS_PER_SYMBOL  = int(os.getenv('MAX_POSITIONS_PER_SYMBOL', 1))
MIN_CONTRACT_PRICE        = float(os.getenv('MIN_CONTRACT_PRICE', 0.20))
MAX_PREMIUM_PCT           = float(os.getenv('MAX_PREMIUM_PCT', 1.0))

# Entries allowed all day (no cutoff) as of 2026-09-08, matching DayTradingBot
# v1/config.py: the earlier 12:00 ET cutoff was re-tested against an August/
# $200-seed backtest with MAX_EMA_GAP_ATR/MAX_PREMIUM_PCT in place and found
# to cost real money at a small account size -- afternoon trades that would
# have compounded the account (and unlocked larger later sizing) were being
# skipped outright. v1 saw 80->134 trades, win rate 30.0%->41.0%, total P&L
# $4,550->$13,347 over the same August window from this change alone. This
# default had drifted stale (still '12:00'/'15:50') since v1's fix landed --
# fixed 2026-09-08 to keep v2 in sync with v1's live strategy.
NO_NEW_ENTRY_TIME = os.getenv('NO_NEW_ENTRY_TIME', '15:58')  # ET
FORCE_CLOSE_TIME  = os.getenv('FORCE_CLOSE_TIME', '15:58')   # ET

STATE_FILE       = 'positions.json'
COOLDOWN_FILE    = 'cooldowns.json'
DAILY_PNL_FILE   = 'daily_pnl.json'
PNL_HISTORY_FILE = 'pnl_history.json'
TRADES_LOG       = 'trades.csv'
LOG_DIR          = 'logs'
