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

_BOTS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BOTS_ROOT)


def _load_credentials():
    """
    Find real Alpaca credentials for this package BEFORE common/alpaca_config.py
    reads them at import time.

    The docstring above warned this could silently end up with API_KEY=None; it
    did. Worse, DayTradingBotV2/backtest/run_backtest.py sets dummy values
    (ALPACA_API_KEY='backtest') on the premise that "every actual data/order
    call in a backtest goes through BacktestDataSource/SimulatedBroker instead,
    never the real credentialed client" -- which is false: options_data.py
    genuinely calls Alpaca for any contract not already on disk. The result
    (measured 2026-09-08) was that EVERY uncached contract lookup in a v2
    backtest 401'd, surfacing to the strategy as "no liquid ATM contract
    found": 162 of 194 rejected signals in a one-symbol/one-month run were
    this, not any risk rule. v1's backtest was unaffected because it runs from
    DayTradingBot/, whose config.py load_dotenv() does find that bot's .env.

    override=True is required: run_backtest.py's os.environ.setdefault() has
    usually already planted the dummy value by the time this module imports,
    and python-dotenv will not replace an existing environment variable
    without it. A real key already in the environment is preserved -- the
    dummy sentinel is the only value we deliberately overwrite.
    """
    current = os.environ.get('ALPACA_API_KEY')
    if current and current != 'backtest':
        return   # a real key is already set (live bot, CI secret, etc.) -- leave it alone

    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    # Market-data-scoped credentials are all this package needs (it only reads
    # bars/contracts/trades and never places an order), so any bot's .env works.
    for candidate in (
        os.path.join(_BOTS_ROOT, 'DayTradingBotV2', 'DaySignalService', '.env'),
        os.path.join(_BOTS_ROOT, 'DayTradingBot', '.env'),
    ):
        if os.path.exists(candidate):
            load_dotenv(dotenv_path=candidate, override=True)
            if os.environ.get('ALPACA_API_KEY', 'backtest') != 'backtest':
                return


_load_credentials()

from common.alpaca_client import AlpacaClient
from common.alpaca_config import API_KEY as _CFG_KEY, API_SECRET as _CFG_SECRET, BASE_URL, DATA_URL

# Read the environment directly rather than trusting alpaca_config's values.
# alpaca_config captures API_KEY/API_SECRET into module globals at ITS import
# time, and by the time this module loads inside a backtest, the orchestrator
# has usually already imported it (via the signal/execution services) with the
# dummy 'backtest' key in place -- so _load_credentials()'s override lands in
# os.environ but never reaches those frozen globals. os.environ is the live
# value and wins; alpaca_config remains the fallback for a normal import order.
API_KEY = os.environ.get('ALPACA_API_KEY') or _CFG_KEY
API_SECRET = os.environ.get('ALPACA_SECRET_KEY') or _CFG_SECRET

if not API_KEY or API_KEY == 'backtest':
    # Loud, not silent: without this the only symptom is every contract lookup
    # coming back empty, which reads like "the strategy found no trade" rather
    # than "the data layer is unauthenticated".
    print('[backtesting-engine] WARNING: no usable ALPACA_API_KEY found -- every '
          'uncached options/bars request will 401 and silently look like "no data".',
          file=sys.stderr)

client = AlpacaClient(API_KEY, API_SECRET, BASE_URL, DATA_URL)
