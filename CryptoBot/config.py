import os
import sys
from dotenv import load_dotenv

# Add parent directory to path to enable imports from common
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

# Import shared Alpaca configuration from common module
from common.alpaca_config import API_KEY, API_SECRET, BASE_URL, DATA_URL

SYMBOLS    = [s.strip() for s in os.getenv('SYMBOLS', 'BTC/USD').split(',')]

EMA_FAST        = int(os.getenv('EMA_FAST', 9))
EMA_SLOW        = int(os.getenv('EMA_SLOW', 21))
RSI_PERIOD      = int(os.getenv('RSI_PERIOD', 14))
RSI_BUY_MAX     = float(os.getenv('RSI_BUY_MAX', 65))    # don't buy if RSI above this
RSI_SELL_MIN    = float(os.getenv('RSI_SELL_MIN', 72))   # sell if RSI above this

MAX_POSITION_PCT = float(os.getenv('MAX_POSITION_PCT', 0.40))
TRADE_AMOUNT_USD = float(os.getenv('TRADE_AMOUNT_USD', 500))

TRADES_LOG = 'trades_log.csv'
LOG_FILE   = 'logs/crypto_bot.log'
BARS_NEEDED = 60   # days of history to fetch for indicators
