"""
ATM option contract lookup -- copied from DayTradingBot/alpaca.py (v1)
`get_0dte_chain`/`get_snapshots_by_symbols`/`find_atm_contract`, unchanged.

Centralized here (account-independent) per PLAN.md's "Inter-process
transport" design: the Signal service looks up the ATM contract once per
symbol, the moment a CALL/PUT signal fires, and includes it in the published
`signal.<SYMBOL>` payload -- so N accounts' sizing services don't each
redundantly hit Alpaca's options-chain/snapshot endpoints for the same data
every time the same signal fires.
"""
import logging
from datetime import date, timedelta

from config import BASE_URL, DATA_URL
from alpaca_market import _get

logger = logging.getLogger(__name__)

MAX_DTE = 5  # matches v1's alpaca.find_atm_contract default


def get_0dte_chain(symbol: str, opt_type: str, near_price: float, max_dte: int = MAX_DTE) -> list[dict]:
    """Get options expiring within max_dte days, nearest expiry first then ATM."""
    today = date.today()
    exp_max = str(today + timedelta(days=max_dte))
    strike_min = near_price * 0.95
    strike_max = near_price * 1.05
    try:
        data = _get(f'{BASE_URL}/v2/options/contracts', params={
            'underlying_symbols': symbol,
            'expiration_date_gte': str(today),
            'expiration_date_lte': exp_max,
            'type': opt_type,
            'strike_price_gte': round(strike_min, 2),
            'strike_price_lte': round(strike_max, 2),
            'limit': 100,
        })
        contracts = data.get('option_contracts', [])
        return sorted(contracts, key=lambda c: (
            c.get('expiration_date', ''),
            abs(float(c.get('strike_price', 0)) - near_price),
        ))
    except Exception as e:
        logger.error(f'Near-DTE chain error {symbol} {opt_type}: {e}')
        return []


def get_snapshots_by_symbols(option_symbols: list[str]) -> dict:
    """Look up snapshots directly by OCC symbol -- most reliable method."""
    if not option_symbols:
        return {}
    try:
        data = _get(f'{DATA_URL}/v1beta1/options/snapshots', params={
            'symbols': ','.join(option_symbols),
        })
        return data.get('snapshots', {})
    except Exception as e:
        logger.error(f'Direct snapshot error: {e}')
        return {}


def find_atm_contract(symbol: str, opt_type: str, spot_price: float, max_dte: int = MAX_DTE) -> dict | None:
    """Find the best ATM contract within max_dte days with a tradeable spread."""
    contracts = get_0dte_chain(symbol, opt_type, spot_price, max_dte=max_dte)
    if not contracts:
        logger.warning(f'No <={max_dte}DTE {opt_type} contracts found for {symbol}')
        return None

    syms = [c.get('symbol', '') for c in contracts if c.get('symbol')]
    snapshots = get_snapshots_by_symbols(syms)

    for contract in contracts:
        sym    = contract.get('symbol', '')
        strike = float(contract.get('strike_price', 0))
        snap   = snapshots.get(sym, {})
        quote  = snap.get('latestQuote', {})
        bid    = float(quote.get('bp', 0) or 0)
        ask    = float(quote.get('ap', 0) or 0)

        if ask <= 0:
            continue
        if ask > 20:
            continue  # skip deep ITM expensive contracts
        if ask < 0.05:
            continue  # too cheap / near worthless

        mid = (bid + ask) / 2 if bid > 0 else ask
        spread_ok = (ask <= bid * 2.5) if bid > 0 else True
        if not spread_ok:
            continue

        return {
            'symbol': sym,
            'strike': strike,
            'bid': bid,
            'ask': ask,
            'mid': mid,
            'type': opt_type,
            'underlying': symbol,
            'expiry': contract.get('expiration_date', str(date.today())),
        }

    logger.warning(f'No liquid {opt_type} contracts near {spot_price} for {symbol}')
    return None
