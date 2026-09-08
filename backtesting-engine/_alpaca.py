"""
Internal: one shared Alpaca REST client for this package's own data-fetch modules
(market_data.py, options_data.py). Not meant to be imported by any bot -- bots keep
using their own alpaca.py wrappers for live trading; this is backtesting-only.

Credentials come from common/alpaca_config.py, same as every bot. Note: python-dotenv's
bare load_dotenv() (called inside alpaca_config.py) searches upward from the __main__
script's own directory, not the process's CWD -- confirmed the hard way in
DayTradingBotV2/PLAN.md. If nothing under this package's parent directories has a
loadable .env with ALPACA_API_KEY/ALPACA_SECRET_KEY, API_KEY/API_SECRET below will be
None and every request will 401. See BACKTESTING_ENGINE_PLAN.md's Phase 1 notes --
this was not live-tested against the real API for exactly this reason.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.alpaca_client import AlpacaClient
from common.alpaca_config import API_KEY, API_SECRET, BASE_URL, DATA_URL

client = AlpacaClient(API_KEY, API_SECRET, BASE_URL, DATA_URL)
