import os
import sys
from dotenv import load_dotenv

# Add parent directory to path to enable imports from common
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

from common.alpaca_config import API_KEY, API_SECRET, BASE_URL, DATA_URL
from symbols import SYMBOLS  # edit symbols.py to change trading symbols

# ── Trade-management knobs ──────────────────────────────────────────────────
# DELIBERATELY different defaults than DayTradingBot's config.py: those
# (STOP_LOSS_PCT=30%, PROFIT_TRAIL_TRIGGER=30%) are tuned for OPTION PREMIUM
# moves -- a leveraged instrument where a <1% underlying move can swing the
# premium 30%+. Applied directly to a STOCK's own price, a 30% stop would
# almost never fire (stocks rarely move 30% intraday), leaving positions
# essentially unprotected.
#
# Tuned 2026-09-08 via a backtest sweep (Jan 1 - Sep 7 2026, 8 symbols) --
# 1.5%/3.0%/1.5% beat every other combination tried on both win rate and
# total P&L (+$1,885 vs. the original 1%/2%/1% guess's +$1,235). See .env's
# comment for the full sweep results.
STOP_LOSS_PCT         = float(os.getenv('STOP_LOSS_PCT', 0.015))   # exit if down 1.5%
PROFIT_TRAIL_TRIGGER  = float(os.getenv('PROFIT_TRAIL_TRIGGER', 0.03))  # start trailing at +3%
TRAIL_WIGGLE          = float(os.getenv('TRAIL_WIGGLE', 0.015))     # stop = high * (1 - 1.5%)
HALF_CLOSE_ENABLED    = os.getenv('HALF_CLOSE_ENABLED', 'false').lower() == 'true'
HALF_CLOSE_PROFIT_PCT = float(os.getenv('HALF_CLOSE_PROFIT_PCT', 0.03))

NO_NEW_ENTRY_TIME = os.getenv('NO_NEW_ENTRY_TIME', '12:00')  # ET
FORCE_CLOSE_TIME  = os.getenv('FORCE_CLOSE_TIME', '15:50')   # ET

COOLDOWN_MINUTES  = int(os.getenv('COOLDOWN_MINUTES', 30))

# ── Risk limits ──────────────────────────────────────────────────────────────
MAX_DAILY_LOSS_PER_SYMBOL = float(os.getenv('MAX_DAILY_LOSS_PER_SYMBOL', 150))
MAX_DAILY_LOSS_TOTAL      = float(os.getenv('MAX_DAILY_LOSS_TOTAL', 500))
MAX_SAME_DIRECTION        = int(os.getenv('MAX_SAME_DIRECTION', 4))   # max concurrent longs (or shorts)
MAX_POSITIONS_PER_SYMBOL  = int(os.getenv('MAX_POSITIONS_PER_SYMBOL', 1))

# Sizing is dollar-based, not contract-count-based -- a stock trade's
# "quantity" is shares, so there's no small fixed cap like MAX_CONTRACTS that
# makes sense here the way it did for options.
MAX_POSITION_VALUE     = float(os.getenv('MAX_POSITION_VALUE', 2000))   # $ per single entry, hard cap
MAX_OPEN_EXPOSURE      = float(os.getenv('MAX_OPEN_EXPOSURE', 8000))    # $ across all open positions
CASH_PER_TRADE_PCT     = float(os.getenv('CASH_PER_TRADE_PCT', 0.20))   # max share of cash spent per entry
EXPOSURE_TOLERANCE_PCT = float(os.getenv('EXPOSURE_TOLERANCE_PCT', 0.10))
MIN_SHARE_PRICE        = float(os.getenv('MIN_SHARE_PRICE', 1.0))       # skip penny-stock-tier symbols

MAX_EMA_GAP_ATR = float(os.getenv('MAX_EMA_GAP_ATR', 1.2))

STATE_FILE       = 'positions.json'
COOLDOWN_FILE    = 'cooldowns.json'
DAILY_PNL_FILE   = 'daily_pnl.json'
PNL_HISTORY_FILE = 'pnl_history.json'
LOG_FILE         = 'logs/dtstocks.log'
TRADES_LOG       = 'trades.csv'
