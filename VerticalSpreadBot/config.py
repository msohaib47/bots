import os
import sys
from dotenv import load_dotenv

# Add parent directory to path to enable imports from common
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

# Import shared Alpaca configuration from common module
from common.alpaca_config import API_KEY, API_SECRET, BASE_URL, DATA_URL

SYMBOLS = [s.strip() for s in os.getenv('SYMBOLS', 'SPY,QQQ').split(',')]

SPREAD_WIDTH        = float(os.getenv('SPREAD_WIDTH', 2.0))          # $ distance between long and short strike
MAX_DTE             = int(os.getenv('MAX_DTE', 1))                    # 0-1 DTE
MAX_CONTRACTS       = int(os.getenv('MAX_CONTRACTS', 1))              # spread units per trade
MAX_DEBIT_PER_TRADE = float(os.getenv('MAX_DEBIT_PER_TRADE', 150.0))  # max $ paid to open one spread (post 100x multiplier)
PROFIT_TARGET_PCT   = float(os.getenv('PROFIT_TARGET_PCT', 0.50))     # close at this % of max possible profit
STOP_LOSS_PCT       = float(os.getenv('STOP_LOSS_PCT', 0.50))         # close if spread value drops this % below entry debit

NO_NEW_ENTRY_TIME = os.getenv('NO_NEW_ENTRY_TIME', '15:45')  # ET
FORCE_CLOSE_TIME  = os.getenv('FORCE_CLOSE_TIME', '15:50')   # ET

STATE_FILE  = 'positions.json'
LOG_FILE    = 'logs/verticalspread.log'
TRADES_LOG  = 'trades.csv'
