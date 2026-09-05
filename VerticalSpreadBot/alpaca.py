"""Alpaca REST client — stocks + 0DTE vertical spreads (multi-leg options)."""
import requests
import logging
import os
import sys
from datetime import date, datetime, timezone, timedelta

# Add parent directory to path to enable imports from common
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import API_KEY, API_SECRET, BASE_URL, DATA_URL, SPREAD_WIDTH
from common.alpaca_client import AlpacaClient

logger = logging.getLogger(__name__)

LAST_ERROR_CODE = None   # tracks last API error code (e.g. PDT, approval level)

# Create shared client instance
_client = AlpacaClient(API_KEY, API_SECRET, BASE_URL, DATA_URL)

# ── Re-export common methods ────────────────────────────────────────────────

def _get(url, params=None):
    return _client._get(url, params)


def _post(url, body):
    return _client._post(url, body)


def get_account():
    return _client.get_account()


def get_cash():
    return _client.get_cash()


def get_position(symbol: str):
    return _client.get_position(symbol)


def list_positions():
    return _client.list_positions()


# ── Market data (underlying) ────────────────────────────────────────────────

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


# ── Options chain ────────────────────────────────────────────────────────────

def get_chain(symbol: str, opt_type: str, near_price: float, max_dte: int = 1) -> list[dict]:
    """Get options expiring within max_dte days, +/-10% strikes around spot (wide enough for both spread legs)."""
    today = date.today()
    exp_max = str(today + timedelta(days=max_dte))
    strike_min = near_price * 0.90
    strike_max = near_price * 1.10
    try:
        data = _get(f'{BASE_URL}/v2/options/contracts', params={
            'underlying_symbols': symbol,
            'expiration_date_gte': str(today),
            'expiration_date_lte': exp_max,
            'type': opt_type,
            'strike_price_gte': round(strike_min, 2),
            'strike_price_lte': round(strike_max, 2),
            'limit': 200,
        })
        return data.get('option_contracts', [])
    except Exception as e:
        logger.error(f'Chain error {symbol} {opt_type}: {e}')
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


def _mid_from_quote(quote: dict) -> float:
    bid = float(quote.get('bp', 0) or 0)
    ask = float(quote.get('ap', 0) or 0)
    if ask > 0 and bid > 0:
        return (ask + bid) / 2
    return ask or bid or 0.0


def find_vertical_spread(symbol: str, signal: str, spot_price: float, max_dte: int = None) -> dict | None:
    """
    Find a debit vertical spread near the money:
      CALL signal -> bull call spread: buy nearest strike >= spot, sell that + SPREAD_WIDTH
      PUT  signal -> bear put spread:  buy nearest strike <= spot, sell that - SPREAD_WIDTH
    Returns spread dict with both leg symbols and net debit, or None if no tradeable spread found.
    """
    max_dte = max_dte if max_dte is not None else 1
    opt_type = 'call' if signal == 'CALL' else 'put'
    contracts = get_chain(symbol, opt_type, spot_price, max_dte)
    if not contracts:
        logger.warning(f'No <={max_dte}DTE {opt_type} contracts found for {symbol}')
        return None

    soonest_exp = min(c['expiration_date'] for c in contracts)
    same_exp = [c for c in contracts if c['expiration_date'] == soonest_exp]
    by_strike = {float(c['strike_price']): c for c in same_exp}
    strikes = sorted(by_strike.keys())

    if signal == 'CALL':
        candidates = [s for s in strikes if s >= spot_price]
        long_strike = min(candidates) if candidates else None
        if long_strike is None:
            return None
        short_candidates = [s for s in strikes if s >= long_strike + SPREAD_WIDTH]
        short_strike = min(short_candidates) if short_candidates else None
    else:
        candidates = [s for s in strikes if s <= spot_price]
        long_strike = max(candidates) if candidates else None
        if long_strike is None:
            return None
        short_candidates = [s for s in strikes if s <= long_strike - SPREAD_WIDTH]
        short_strike = max(short_candidates) if short_candidates else None

    if short_strike is None:
        logger.warning(f'{symbol}: no strike {SPREAD_WIDTH} away from long leg {long_strike}')
        return None

    long_contract = by_strike[long_strike]
    short_contract = by_strike[short_strike]

    snaps = get_snapshots_by_symbols([long_contract['symbol'], short_contract['symbol']])
    long_quote = snaps.get(long_contract['symbol'], {}).get('latestQuote', {})
    short_quote = snaps.get(short_contract['symbol'], {}).get('latestQuote', {})

    long_ask = float(long_quote.get('ap', 0) or 0)
    short_bid = float(short_quote.get('bp', 0) or 0)
    if long_ask <= 0 or short_bid <= 0:
        logger.warning(f'{symbol}: illiquid legs (long_ask={long_ask}, short_bid={short_bid})')
        return None

    long_mid = _mid_from_quote(long_quote)
    short_mid = _mid_from_quote(short_quote)
    width = abs(short_strike - long_strike)
    net_debit = round(long_mid - short_mid, 2)

    if net_debit <= 0 or net_debit >= width:
        logger.warning(f'{symbol}: bad spread pricing (debit={net_debit}, width={width})')
        return None

    logger.info(
        f'{symbol} {opt_type.upper()} spread: long {long_contract["symbol"]}(${long_strike}) / '
        f'short {short_contract["symbol"]}(${short_strike}) | debit=${net_debit} width=${width}'
    )
    return {
        'underlying':   symbol,
        'type':         opt_type,
        'expiry':       soonest_exp,
        'long_symbol':  long_contract['symbol'],
        'short_symbol': short_contract['symbol'],
        'long_strike':  long_strike,
        'short_strike': short_strike,
        'width':        width,
        'net_debit':    net_debit,
        'max_profit':   round(width - net_debit, 2),
    }


def get_spread_value(long_symbol: str, short_symbol: str) -> float | None:
    """Current mark-to-market value of a vertical spread (what you'd net if closing now)."""
    snaps = get_snapshots_by_symbols([long_symbol, short_symbol])
    long_quote = snaps.get(long_symbol, {}).get('latestQuote', {})
    short_quote = snaps.get(short_symbol, {}).get('latestQuote', {})
    long_mid = _mid_from_quote(long_quote)
    short_mid = _mid_from_quote(short_quote)
    if long_mid <= 0 and short_mid <= 0:
        return None
    return round(long_mid - short_mid, 4)


# ── Orders (multi-leg) ───────────────────────────────────────────────────────

def open_vertical_spread(spread: dict, qty: int) -> dict | None:
    """Buy-to-open the long leg and sell-to-open the short leg as one atomic multi-leg order."""
    global LAST_ERROR_CODE
    limit_price = round(spread['net_debit'] + 0.02, 2)  # small buffer over mid to help fill
    body = {
        'order_class': 'mleg',
        'qty': str(qty),
        'type': 'limit',
        'time_in_force': 'day',
        'limit_price': str(limit_price),
        'legs': [
            {'symbol': spread['long_symbol'], 'side': 'buy', 'ratio_qty': '1', 'position_intent': 'buy_to_open'},
            {'symbol': spread['short_symbol'], 'side': 'sell', 'ratio_qty': '1', 'position_intent': 'sell_to_open'},
        ],
    }
    try:
        order = _post(f'{BASE_URL}/v2/orders', body)
        logger.info(f'OPEN spread order: {qty}x {spread["long_symbol"]}/{spread["short_symbol"]} '
                    f'@ debit ${limit_price} -> {order.get("id")}')
        LAST_ERROR_CODE = None
        return order
    except requests.HTTPError as e:
        try:
            LAST_ERROR_CODE = e.response.json().get('code')
        except Exception:
            LAST_ERROR_CODE = None
        logger.error(f'Open spread failed: {e}')
        return None
    except Exception as e:
        LAST_ERROR_CODE = None
        logger.error(f'Open spread failed: {e}')
        return None


def close_vertical_spread(long_symbol: str, short_symbol: str, qty: int, min_credit: float = 0.0) -> dict | None:
    """Sell-to-close the long leg and buy-to-close the short leg as one atomic multi-leg order."""
    limit_price = round(-min_credit, 2)  # negative = credit received, per Alpaca mleg convention
    body = {
        'order_class': 'mleg',
        'qty': str(qty),
        'type': 'limit',
        'time_in_force': 'day',
        'limit_price': str(limit_price),
        'legs': [
            {'symbol': long_symbol, 'side': 'sell', 'ratio_qty': '1', 'position_intent': 'sell_to_close'},
            {'symbol': short_symbol, 'side': 'buy', 'ratio_qty': '1', 'position_intent': 'buy_to_close'},
        ],
    }
    try:
        order = _post(f'{BASE_URL}/v2/orders', body)
        logger.info(f'CLOSE spread order: {qty}x {long_symbol}/{short_symbol} '
                    f'@ credit ${min_credit} -> {order.get("id")}')
        return order
    except Exception as e:
        logger.error(f'Close spread failed {long_symbol}/{short_symbol}: {e}')
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
