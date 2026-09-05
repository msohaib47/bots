"""Alpaca REST client for stocks and options."""
import requests
import logging
import os
import sys
from datetime import date, timedelta

# Add parent directory to path to enable imports from common
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import API_KEY, API_SECRET, BASE_URL, DATA_URL, DTE_MIN, DTE_MAX
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


def get_account():
    return _client.get_account()


def get_cash() -> float:
    return _client.get_cash()


def get_position(ticker: str) -> dict | None:
    try:
        return _get(f'{BASE_URL}/v2/positions/{ticker}')
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return None
        raise


def list_positions() -> list[dict]:
    try:
        return _get(f'{BASE_URL}/v2/positions')
    except Exception:
        return []


# ── Account (extended) ─────────────────────────────────────────────────────────

def get_buying_power() -> float:
    return float(get_account().get('buying_power', 0))


def get_options_buying_power() -> float:
    return float(get_account().get('options_buying_power', 0))


# ── Stocks ─────────────────────────────────────────────────────────────────────

def get_stock_price(ticker: str) -> float | None:
    try:
        data = _get(f'{DATA_URL}/v2/stocks/{ticker}/quotes/latest')
        q = data.get('quote', {})
        ask, bid = q.get('ap', 0), q.get('bp', 0)
        if ask and bid:
            return (ask + bid) / 2
        if ask:
            return ask
        if bid:
            return bid
    except Exception:
        pass
    try:
        data = _get(f'{DATA_URL}/v2/stocks/{ticker}/bars/latest')
        return float(data.get('bar', {}).get('c', 0)) or None
    except Exception as e:
        logger.error(f'Could not get price for {ticker}: {e}')
        return None


def get_option_chain(ticker: str, opt_type: str, strike_min=None, strike_max=None) -> list[dict]:
    """Fetch option contracts for a ticker. opt_type: 'put' or 'call'."""
    today = date.today()
    params = {
        'underlying_symbols': ticker,
        'expiration_date_gte': str(today + timedelta(days=DTE_MIN)),
        'expiration_date_lte': str(today + timedelta(days=DTE_MAX)),
        'type': opt_type,
        'limit': 100,
    }
    if strike_min:
        params['strike_price_gte'] = strike_min
    if strike_max:
        params['strike_price_lte'] = strike_max
    try:
        data = _get(f'{BASE_URL}/v2/options/contracts', params=params)
        return data.get('option_contracts', [])
    except Exception as e:
        logger.error(f'Option chain error for {ticker} {opt_type}: {e}')
        return []


def get_option_snapshots(ticker: str, opt_type: str) -> dict:
    """Get bid/ask/greeks for options. Returns {symbol: snapshot}."""
    today = date.today()
    params = {
        'expiration_date_gte': str(today + timedelta(days=DTE_MIN)),
        'expiration_date_lte': str(today + timedelta(days=DTE_MAX)),
        'type': opt_type,
        'limit': 200,
    }
    try:
        data = _get(f'{DATA_URL}/v1beta1/options/snapshots/{ticker}', params=params)
        return data.get('snapshots', {})
    except Exception as e:
        logger.error(f'Snapshot error for {ticker}: {e}')
        return {}


def get_option_contract(symbol: str) -> dict | None:
    """Get details for a specific option contract by OCC symbol."""
    try:
        return _get(f'{BASE_URL}/v2/options/contracts/{symbol}')
    except Exception:
        return None


def get_order(order_id: str) -> dict | None:
    try:
        return _get(f'{BASE_URL}/v2/orders/{order_id}')
    except Exception:
        return None


def list_open_orders() -> list[dict]:
    try:
        return _get(f'{BASE_URL}/v2/orders', params={'status': 'open', 'limit': 100})
    except Exception:
        return []


def cancel_order(order_id: str) -> bool:
    try:
        requests.delete(f'{BASE_URL}/v2/orders/{order_id}', headers=_client.headers, timeout=10)
        return True
    except Exception:
        return False


# ── Order placement ────────────────────────────────────────────────────────────

def place_option_order(symbol: str, side: str, qty: int, limit_price: float) -> dict | None:
    """
    Place a limit order for an option contract.
    side: 'buy' or 'sell'
    symbol: OCC option symbol e.g. 'QBTS260620P00028000'
    limit_price: per-contract price (will be multiplied by 100 at settlement)
    """
    body = {
        'symbol': symbol,
        'qty': str(qty),
        'side': side,
        'type': 'limit',
        'limit_price': str(round(limit_price, 2)),
        'time_in_force': 'day',
    }
    try:
        order = _post(f'{BASE_URL}/v2/orders', body)
        logger.info(f'Option order placed: {side} {qty}x {symbol} @ ${limit_price:.2f} -> {order.get("id")}')
        return order
    except Exception as e:
        logger.error(f'Failed to place option order {symbol}: {e}')
        return None
