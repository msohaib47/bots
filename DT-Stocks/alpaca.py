"""Alpaca REST client -- equities only. DT-Stocks trades shares directly
(long AND short, matching DayTradingBot's bidirectional CALL/PUT design),
not 0DTE option contracts -- so there's no options-chain/contract-lookup
code here at all, unlike DayTradingBot/alpaca.py."""
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

_client = AlpacaClient(API_KEY, API_SECRET, BASE_URL, DATA_URL)


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


# ── Market data (identical to DayTradingBot/alpaca.py) ──────────────────────

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


def get_1min_bars(symbol: str, limit: int = 60) -> list[dict]:
    return get_intraday_bars(symbol, '1Min', limit)


def get_5min_bars(symbol: str, limit: int = 50) -> list[dict]:
    return get_intraday_bars(symbol, '5Min', limit)


def get_recent_bars(symbol: str, timeframe: str = '15Min', limit: int = 30) -> list[dict]:
    """Fetch the most recent N bars regardless of day -- for multi-session indicators."""
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


# ── Equity orders ────────────────────────────────────────────────────────────
# Replaces DayTradingBot/alpaca.py's buy_option/close_option_position -- this
# trades the underlying directly. `side` here is the ALPACA order side
# ('buy'/'sell'), not the position's own long/short direction -- opening a
# long is 'buy', opening a short is 'sell' (short-sell to open); closing a
# long is 'sell', closing a short (buying to cover) is 'buy'. position_manager.py
# tracks which is which; this module just executes whatever side it's told.

def buy_stock(symbol: str, qty: int, side: str = 'buy') -> dict | None:
    """Market order to open a position -- 'buy' for long, 'sell' for short-sell-to-open."""
    global LAST_ERROR_CODE
    body = {
        'symbol': symbol,
        'qty': str(qty),
        'side': side,
        'type': 'market',
        'time_in_force': 'day',
    }
    try:
        order = _post(f'{BASE_URL}/v2/orders', body)
        logger.info(f'{side.upper()} order: {qty}x {symbol} -> {order.get("id")}')
        LAST_ERROR_CODE = None
        return order
    except requests.HTTPError as e:
        try:
            LAST_ERROR_CODE = e.response.json().get('code')
        except Exception:
            LAST_ERROR_CODE = None
        logger.error(f'{side.upper()} order failed {symbol}: {e}')
        return None
    except Exception as e:
        LAST_ERROR_CODE = None
        logger.error(f'{side.upper()} order failed {symbol}: {e}')
        return None


def close_stock_position(symbol: str, qty: int = None, closing_side: str = 'sell') -> dict | None:
    """
    Close a position. Full close (qty=None) uses Alpaca's DELETE shortcut,
    which correctly handles long or short automatically. A PARTIAL close
    (scale-out, off by default via HALF_CLOSE_ENABLED) needs the caller to
    say which side actually closes it -- 'sell' to reduce a long, 'buy' to
    reduce a short (buying back part of the borrowed shares).
    """
    try:
        if qty:
            body = {
                'symbol': symbol,
                'qty': str(qty),
                'side': closing_side,
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
