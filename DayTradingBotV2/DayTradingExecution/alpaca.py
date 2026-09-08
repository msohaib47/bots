"""
Account-specific Alpaca REST wrapper for execution_service.py.

Trimmed from DayTradingBot/alpaca.py (v1) -- keeps only account reads and
order placement/closing. Market-data reads and contract lookup live in
DaySignalService instead (see PLAN.md) -- this module's only market-data
call is get_latest_price(), kept here purely for the CLOSE row's
underlying_price CSV column (best-effort, never blocks a close on failure,
same as v1).
"""
import logging
import requests

from config import BASE_URL, DATA_URL
from common.alpaca_client import AlpacaClient
from common.alpaca_config import API_KEY, API_SECRET

logger = logging.getLogger(__name__)

LAST_ERROR_CODE = None  # tracks last order-placement error code, for PDT detection

_client = AlpacaClient(API_KEY, API_SECRET, BASE_URL, DATA_URL)


def _get(url, params=None):
    return _client._get(url, params)


def _post(url, body):
    return _client._post(url, body)


def get_account() -> dict:
    return _client.get_account()


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


def get_snapshots_by_symbols(option_symbols: list[str]) -> dict:
    """Current mid/bid/ask for open option positions -- used by the stop/trailing/scale-out loop."""
    if not option_symbols:
        return {}
    try:
        data = _get(f'{DATA_URL}/v1beta1/options/snapshots', params={
            'symbols': ','.join(option_symbols),
        })
        return data.get('snapshots', {})
    except Exception as e:
        logger.error(f'Snapshot error: {e}')
        return {}


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
    """Close an option position (partial via qty, full via DELETE)."""
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
