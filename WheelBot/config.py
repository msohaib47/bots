import os
import sys
from dotenv import load_dotenv

# Add parent directory to path to enable imports from common
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

# Import shared Alpaca configuration from common module
from common.alpaca_config import API_KEY, API_SECRET, BASE_URL, DATA_URL

TICKERS    = [t.strip() for t in os.getenv('TICKERS', 'QBTS,RIOT,CIFR,CLSK').split(',')]

CSP_OTM_PCT = float(os.getenv('CSP_OTM_PCT', 0.07))   # sell put 7% below spot
CC_OTM_PCT  = float(os.getenv('CC_OTM_PCT', 0.07))    # sell call 7% above cost basis

DTE_MIN = int(os.getenv('DTE_MIN', 14))
DTE_MAX = int(os.getenv('DTE_MAX', 35))

STATE_FILE = 'wheel_state.json'
LOG_FILE   = 'logs/wheel.log'
TRADES_LOG = 'wheel_trades.csv'
