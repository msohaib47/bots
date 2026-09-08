"""
Market-data-only Alpaca REST wrapper for the Signal service.

Trimmed from DayTradingBot/alpaca.py (v1) -- keeps only the account-independent
market-data reads. No order placement, no account/position endpoints belong
here; those live in DayTradingExecution's alpaca.py instead.

Used only for cold-start warm-up (see bar_engine.py / signal_service.py) --
steady-state price data comes from the WebSocket stream, not these REST calls.
"""
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from config import DATA_URL

ET = ZoneInfo('America/New_York')
from common.alpaca_client import AlpacaClient
from common.alpaca_config import API_KEY, API_SECRET, BASE_URL

logger = logging.getLogger(__name__)

_client = AlpacaClient(API_KEY, API_SECRET, BASE_URL, DATA_URL)


def _get(url, params=None):
    return _client._get(url, params)


def get_recent_bars(symbol: str, timeframe: str = '1Min', limit: int = 600) -> list[dict]:
    """Fetch the most recent N bars regardless of day -- used once at startup
    to seed the in-memory rolling buffers before the WS stream takes over."""
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


def get_session_bars(symbol: str, timeframe: str = '1Min', limit: int = 600) -> list[dict]:
    """
    Fetch today-session-only bars -- used to seed the VWAP-only buffer at startup.
    "Today" is the US/Eastern trading day, not the UTC calendar date -- unlike
    v1 (a cron script gated to market hours, where this distinction never
    surfaced), this service runs continuously and can be started/restarted at
    any hour, including after UTC midnight while the ET trading day is still
    "yesterday" from the server's perspective.
    """
    today = datetime.now(ET).date()
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
        logger.error(f'Session bars error {symbol} {timeframe}: {e}')
        return []
