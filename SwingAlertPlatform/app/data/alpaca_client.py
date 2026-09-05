import logging
from datetime import datetime, timedelta, timezone

import requests

from app.config import get_settings
from app.data.timeframes import ALPACA_TIMEFRAME, Timeframe

logger = logging.getLogger(__name__)


class AlpacaDataClient:
    """Read-only Alpaca market-data wrapper. This platform never places orders."""

    def __init__(self) -> None:
        settings = get_settings()
        self.data_url = settings.alpaca_data_url
        self.headers = {
            "APCA-API-KEY-ID": settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
        }

    def get_bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        limit: int = 1000,
    ) -> list[dict]:
        """Fetch bars for `symbol` at `timeframe` starting from `start` (inclusive), oldest first."""
        params = {
            "timeframe": ALPACA_TIMEFRAME[timeframe],
            "start": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": limit,
            "adjustment": "raw",
            "feed": "iex",
        }
        try:
            r = requests.get(
                f"{self.data_url}/v2/stocks/{symbol}/bars",
                headers=self.headers,
                params=params,
                timeout=15,
            )
            r.raise_for_status()
            return r.json().get("bars", []) or []
        except Exception as e:
            logger.error(f"Failed to fetch {timeframe.value} bars for {symbol}: {e}")
            return []

    def get_bars_since(
        self,
        symbol: str,
        timeframe: Timeframe,
        since: datetime | None,
        default_lookback_days: int = 400,
    ) -> list[dict]:
        """Fetch bars newer than `since` (or a default lookback window if no cached bars exist yet)."""
        start = since + timedelta(seconds=1) if since else datetime.now(timezone.utc) - timedelta(days=default_lookback_days)
        return self.get_bars(symbol, timeframe, start)
