import os
import sys
from dotenv import load_dotenv

# Add the repo root to path so `common` resolves regardless of CWD
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Explicit dotenv_path (not the bare load_dotenv() form) -- see
# DayTradingExecution/config.py for why: find_dotenv()'s default search walks
# up from the __main__ script's own directory, not the process CWD. This
# service's .env happens to live alongside signal_service.py so the bare form
# isn't actually broken here today, but pinning it explicitly avoids relying
# on that coincidence.
load_dotenv(dotenv_path=os.path.join(os.getcwd(), '.env'))

# Market-data-only Alpaca credentials -- this service places no orders and
# reads no account state, so any account's credentials work here (see
# PLAN.md's "Open items": whether Alpaca offers a data-scoped key type is
# still unverified; for now this just reuses one account's key pair).
from common.alpaca_config import API_KEY, API_SECRET, BASE_URL, DATA_URL

from symbols import SYMBOLS  # edit symbols.py to change the traded universe

# Strategy/signal-quality constants (copied from DayTradingBot/config.py v1 --
# these are account-independent, so they belong with the signal service, not
# per-account sizing config)
MAX_EMA_GAP_ATR    = float(os.getenv('MAX_EMA_GAP_ATR', 1.2))
MIN_CONTRACT_PRICE = float(os.getenv('MIN_CONTRACT_PRICE', 0.20))
MAX_PREMIUM_PCT    = float(os.getenv('MAX_PREMIUM_PCT', 1.0))
NO_NEW_ENTRY_TIME  = os.getenv('NO_NEW_ENTRY_TIME', '12:00')  # ET
FORCE_CLOSE_TIME   = os.getenv('FORCE_CLOSE_TIME', '15:50')   # ET

LOG_FILE = 'logs/signal_service.log'
