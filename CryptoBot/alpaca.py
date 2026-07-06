"""Alpaca REST client for crypto trading."""
import requests
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

# Add parent directory to path to enable imports from common
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import API_KEY, API_SECRET, BASE_URL, DATA_URL, BARS_NEEDED
from common.alpaca_client import AlpacaClient

logger = logging.getLogger(__name__)

# Create shared client instance
_client = AlpacaClient(API_KEY, API_SECRET, BASE_URL, DATA_URL)

# ── Re-export common methods for backward compatibility ────────────────────────

def _get(url, params=None):
    """Delegate to shared client."""
    return _client._get(url, params)


def _post(url, body):
    """Delegate to shared client."""
    return _client._post(url, body)


def get_account() -> dict:
    return _client.get_account()


def get_cash() -> float:
    return _client.get_cash()


def get_portfolio_value() -> float:
    return _client.get_portfolio_value()


def get_position(symbol: str) -> dict | None:
    """symbol like 'BTC/USD' -> converts to 'BTCUSD' for position lookup."""
    sym = symbol.replace('/', '')
    try:
        return _get(f'{BASE_URL}/v2/positions/{sym}')
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return None
        raise


def list_positions() -> list[dict]:
    return _get(f'{BASE_URL}/v2/positions')


# ── Market Data ───────────────────────────────────────────────────────────────

def get_daily_bars(symbol: str, days: int = None) -> list[dict]:
    """Fetch daily OHLCV bars for a crypto symbol. Returns list sorted oldest first."""
    days = days or BARS_NEEDED
    start = (datetime.now(timezone.utc) - timedelta(days=days + 5)).strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        data = _get(f'{DATA_URL}/v1beta3/crypto/us/bars', params={
            'symbols': symbol,
            'timeframe': '1Day',
            'start': start,
            'limit': days + 5,
        })
        bars = data.get('bars', {}).get(symbol, [])
        return sorted(bars, key=lambda b: b['t'])
    except Exception as e:
        logger.error(f'Failed to fetch bars for {symbol}: {e}')
        return []


def get_4h_bars(symbol: str, limit: int = 60) -> list[dict]:
    """Fetch 4-hour OHLCV bars for a crypto symbol. Returns list sorted oldest first."""
    start = (datetime.now(timezone.utc) - timedelta(days=15)).strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        data = _get(f'{DATA_URL}/v1beta3/crypto/us/bars', params={
            'symbols': symbol,
            'timeframe': '4Hour',
            'start': start,
            'limit': limit,
        })
        bars = data.get('bars', {}).get(symbol, [])
        return sorted(bars, key=lambda b: b['t'])
    except Exception as e:
        logger.error(f'Failed to fetch 4h bars for {symbol}: {e}')
        return []


def get_latest_price(symbol: str) -> float | None:
    try:
        data = _get(f'{DATA_URL}/v1beta3/crypto/us/latest/bars', params={'symbols': symbol})
        bar = data.get('bars', {}).get(symbol, {})
        return float(bar.get('c', 0)) or None
    except Exception as e:
        logger.error(f'Failed to get price for {symbol}: {e}')
        return None


# ── Orders ────────────────────────────────────────────────────────────────────

def place_market_order(symbol: str, side: str, notional: float = None, qty: float = None) -> dict | None:
    """
    Place a crypto market order.
    symbol: 'BTC/USD'
    side: 'buy' or 'sell'
    notional: dollar amount (e.g. 500.0)  -- preferred for buys
    qty: coin quantity                     -- preferred for sells
    """
    sym = symbol.replace('/', '')
    body = {
        'symbol': sym,
        'side': side,
        'type': 'market',
        'time_in_force': 'gtc',  # crypto uses GTC
    }
    if notional:
        body['notional'] = str(round(notional, 2))
    elif qty:
        body['qty'] = str(qty)
    else:
        logger.error('Must specify notional or qty')
        return None

    try:
        order = _post(f'{BASE_URL}/v2/orders', body)
        logger.info(f'Order placed: {side.upper()} {symbol} notional=${notional or ""} qty={qty or ""} -> {order.get("id")}')
        return order
    except Exception as e:
        logger.error(f'Order failed {side} {symbol}: {e}')
        return None


def close_position(symbol: str) -> dict | None:
    """Close entire position for a symbol."""
    sym = symbol.replace('/', '')
    try:
        r = requests.delete(f'{BASE_URL}/v2/positions/{sym}', headers=_client.headers, timeout=15)
        if r.status_code == 200:
            logger.info(f'Position closed: {symbol}')
            return r.json()
        elif r.status_code == 404:
            logger.info(f'No position to close for {symbol}')
            return None
        else:
            logger.error(f'Close position failed {symbol}: {r.status_code} {r.text}')
            return None
    except Exception as e:
        logger.error(f'Close position error {symbol}: {e}')
        return None


def get_1h_bars(symbol: str, limit: int = 50) -> list[dict]:
    start = (datetime.now(timezone.utc) - timedelta(days=3)).strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        data = _get(f'{DATA_URL}/v1beta3/crypto/us/bars', params={
            'symbols': symbol,
            'timeframe': '1Hour',
            'start': start,
            'limit': limit,
        })
        bars = data.get('bars', {}).get(symbol, [])
        return sorted(bars, key=lambda b: b['t'])
    except Exception as e:
        logger.error(f'Failed to fetch 1h bars for {symbol}: {e}')
        return []
