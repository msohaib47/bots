import os
import sys
from dotenv import load_dotenv

# Add parent directory to path to enable imports from common
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

# Import shared Alpaca configuration from common module
from common.alpaca_config import API_KEY, API_SECRET, BASE_URL, DATA_URL

from symbols import SYMBOLS  # edit symbols.py to change trading symbols
MAX_CONTRACTS     = int(os.getenv('MAX_CONTRACTS', 2))
STOP_LOSS_PCT     = float(os.getenv('STOP_LOSS_PCT', 0.30))       # exit if down 30%
PROFIT_TRAIL_TRIGGER = float(os.getenv('PROFIT_TRAIL_TRIGGER', 0.50))  # start trailing at +50%
TRAIL_WIGGLE      = float(os.getenv('TRAIL_WIGGLE', 0.15))         # stop = high * (1 - 10%)

NO_NEW_ENTRY_TIME = os.getenv('NO_NEW_ENTRY_TIME', '15:45')  # ET
FORCE_CLOSE_TIME  = os.getenv('FORCE_CLOSE_TIME', '15:50')   # ET

STATE_FILE  = 'positions.json'
LOG_FILE    = 'logs/daytrading.log'
TRADES_LOG  = 'trades.csv'
