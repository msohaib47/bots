"""Alpaca REST client — stocks + 0DTE options."""
import requests
import logging
import os
import sys
from datetime import date, datetime, timezone, timedelta

# Add parent directory to path to enable imports from common
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import API_KEY, API_SECRET, BASE_URL, DATA_URL
from common.alpaca_client import AlpacaClient

logger = logging.getLogger(__name__)

LAST_ERROR_CODE = None   # tracks last API error code for PDT detection

# Create shared client instance
_client = AlpacaClient(API_KEY, API_SECRET, BASE_URL, DATA_URL)

# ── Re-export common methods for backward compatibility ────────────────────────

def _get(url, params=None):
    """Delegate to shared client."""
    return _client._get(url, params)


def _post(url, body):
    """Delegate to shared client."""
    return _client._post(url, body)


def get_account():
    return _client.get_account()


def get_cash():
    return _client.get_cash()


def get_position(symbol: str):
    return _client.get_position(symbol)


def list_positions():
    return _client.list_positions()


# ── Market data ────────────────────────────────────────────────────────────────

def get_latest_price(symbol: str) -> float | None:
    try:
        data = _get(f'{DATA_URL}/v2/stocks/{symbol}/quotes/latest')
        q = data.get('quote', {})
        ask, bid = float(q.get('ap', 0) or 0), float(q.get('bp', 0) or 0)
        if ask and bid:
            return (ask + bid) / 2
        return ask or bid or None
    except Exception as e:
        logger.error(f'Price error {symbol}: {e}')
        return None


def get_intraday_bars(symbol: str, timeframe: str = '5Min', limit: int = 100) -> list[dict]:
    """Fetch intraday bars from market open today."""
    now = datetime.now(timezone.utc)
    # Market opens 9:30 ET = 13:30 UTC
    today = now.date()
    start = f'{today}T13:30:00Z'
    try:
        data = _get(f'{DATA_URL}/v2/stocks/{symbol}/bars', params={
            'timeframe': timeframe,
            'start': start,
            'limit': limit,
            'adjustment': 'raw',
            'feed': 'iex',
        })
        return data.get('bars', [])
    except Exception as e:
        logger.error(f'Bars error {symbol}: {e}')
        return []


def get_1min_bars(symbol: str, limit: int = 60) -> list[dict]:
    return get_intraday_bars(symbol, '1Min', limit)


def get_5min_bars(symbol: str, limit: int = 50) -> list[dict]:
    return get_intraday_bars(symbol, '5Min', limit)


def get_recent_bars(symbol: str, timeframe: str = '15Min', limit: int = 30) -> list[dict]:
    """Fetch the most recent N bars regardless of day — for multi-session indicators."""
    try:
        start = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        data = _get(f'{DATA_URL}/v2/stocks/{symbol}/bars', params={
            'timeframe': timeframe,
            'limit': limit,
            'adjustment': 'raw',
            'feed': 'iex',
            'start': start,
        })
        return data.get('bars', [])
    except Exception as e:
        logger.error(f'Recent bars error {symbol} {timeframe}: {e}')
        return []


# ── Options ────────────────────────────────────────────────────────────────────

def get_0dte_chain(symbol: str, opt_type: str, near_price: float, max_dte: int = 5) -> list[dict]:
    """Get options expiring within max_dte days, nearest expiry first then ATM."""
    today = date.today()
    exp_max = str(today + timedelta(days=max_dte))
    # Search +/- 5% of current price for strikes
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
        # Sort: nearest expiry first, then closest strike to spot
        return sorted(contracts, key=lambda c: (
            c.get('expiration_date', ''),
            abs(float(c.get('strike_price', 0)) - near_price),
        ))
    except Exception as e:
        logger.error(f'Near-DTE chain error {symbol} {opt_type}: {e}')
        return []


def get_snapshots_by_symbols(option_symbols: list[str]) -> dict:
    """Look up snapshots directly by OCC symbol — most reliable method."""
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


def find_atm_contract(symbol: str, opt_type: str, spot_price: float, max_dte: int = 5) -> dict | None:
    """Find the best ATM contract within max_dte days with a tradeable spread."""
    contracts = get_0dte_chain(symbol, opt_type, spot_price, max_dte=max_dte)
    if not contracts:
        logger.warning(f'No <={max_dte}DTE {opt_type} contracts found for {symbol}')
        return None

    # Look up snapshots directly by the exact symbols found — avoids missing ATM quotes
    syms = [c.get('symbol', '') for c in contracts if c.get('symbol')]
    snapshots = get_snapshots_by_symbols(syms)

    for contract in contracts:
        sym    = contract.get('symbol', '')
        strike = float(contract.get('strike_price', 0))
        snap   = snapshots.get(sym, {})
        quote  = snap.get('latestQuote', {})
        bid    = float(quote.get('bp', 0) or 0)
        ask    = float(quote.get('ap', 0) or 0)

        # Accept ask-only quotes (common for 0DTE in paper trading)
        if ask <= 0:
            continue
        if ask > 20:
            continue  # skip deep ITM expensive contracts
        if ask < 0.05:
            continue  # too cheap / near worthless

        # Use mid if bid exists, otherwise use ask
        mid = (bid + ask) / 2 if bid > 0 else ask
        spread_ok = (ask <= bid * 2.5) if bid > 0 else True

        if not spread_ok:
            continue

        logger.info(f'ATM contract: {sym} strike={strike} bid={bid} ask={ask} mid={mid:.2f}')
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


# ── Orders ────────────────────────────────────────────────────────────────────

def buy_option(contract: dict, qty: int) -> dict | None:
    """Buy option contracts at limit (mid price)."""
    global LAST_ERROR_CODE
    limit_price = round(contract['mid'] + 0.01, 2)
    body = {
        'symbol': contract['symbol'],
        'qty': str(qty),
        'side': 'buy',
        'type': 'limit',
        'limit_price': str(limit_price),
        'time_in_force': 'day',
    }
    try:
        order = _post(f'{BASE_URL}/v2/orders', body)
        logger.info(f'BUY order: {qty}x {contract["symbol"]} @ ${limit_price} -> {order.get("id")}')
        LAST_ERROR_CODE = None
        return order
    except requests.HTTPError as e:
        try:
            LAST_ERROR_CODE = e.response.json().get('code')
        except Exception:
            LAST_ERROR_CODE = None
        logger.error(f'Buy order failed {contract["symbol"]}: {e}')
        return None
    except Exception as e:
        LAST_ERROR_CODE = None
        logger.error(f'Buy order failed {contract["symbol"]}: {e}')
        return None


def close_option_position(symbol: str, qty: int = None) -> dict | None:
    """Close an option position (market order to sell)."""
    try:
        if qty:
            body = {
                'symbol': symbol,
                'qty': str(qty),
                'side': 'sell',
                'type': 'market',
                'time_in_force': 'day',
            }
            return _post(f'{BASE_URL}/v2/orders', body)
        else:
            r = requests.delete(f'{BASE_URL}/v2/positions/{symbol}', headers=_client.headers, timeout=15)
            if r.ok:
                return r.json()
            return None
    except Exception as e:
        logger.error(f'Close position failed {symbol}: {e}')
        return None


def get_order(order_id: str) -> dict | None:
    try:
        return _get(f'{BASE_URL}/v2/orders/{order_id}')
    except Exception:
        return None


def cancel_order(order_id: str):
    try:
        requests.delete(f'{BASE_URL}/v2/orders/{order_id}', headers=_client.headers, timeout=10)
    except Exception:
        pass
