"""
scraper.py — congressional trade disclosures via Financial Modeling Prep.

Switched from QuiverQuant (2026-08-18) — QuiverQuant's live congress-trading
endpoint needs a paid subscription (~$30/mo) and was never actually wired up
with a key (401 on every fetch since 2026-05-29). FMP has the same data
(sourced from the same official Senate/House disclosure filings) with a free
tier: 250 calls/day, `limit` capped at 25/page on free. This bot only needs
the newest page each run (new trades are caught well before they'd scroll
past the first 25 most-recent disclosures across all of Congress), so it
stays well under the free-tier cap even at a 5-minute cron interval.

FMP's dedicated by-name/by-symbol filter endpoints
(senate-trading-by-name, senate-trading?symbol=...) 404 on the free tier —
undocumented (their docs pages 403 automated fetches) whether that's a
missing plan tier or a different param name. Filtering client-side against
the plain senate-latest/house-latest feed sidesteps that entirely.

Normalizes FMP's field names to the same shape the rest of this bot already
expects (Representative/Ticker/Transaction/TransactionDate/Range/TickerType)
so bot.py, tracker.py, and make_trade_id() didn't need to change at all.
"""

import requests
import logging
from config import FMP_BASE_URL, FMP_API_KEY, TARGET_POLITICIAN

logger = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json',
}


def _fetch_latest(endpoint: str) -> list[dict]:
    if not FMP_API_KEY:
        logger.error('FMP_API_KEY not set in .env — cannot fetch trades.')
        return []
    try:
        r = requests.get(
            f'{FMP_BASE_URL}/{endpoint}',
            params={'page': 0, 'limit': 25, 'apikey': FMP_API_KEY},
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            logger.error(f'Unexpected response from {endpoint}: {data}')
            return []
        return data
    except Exception as e:
        logger.error(f'Failed to fetch {endpoint}: {e}')
        return []


def _normalize(raw: dict) -> dict:
    """Map an FMP senate-latest/house-latest record to the field names the
    rest of this bot expects (originally QuiverQuant's schema)."""
    full_name = f"{raw.get('firstName', '')} {raw.get('lastName', '')}".strip()
    return {
        'Representative': full_name,
        'Ticker': raw.get('symbol', ''),
        'Transaction': raw.get('type', ''),
        'TransactionDate': raw.get('transactionDate', ''),
        'Range': raw.get('amount', ''),
        'TickerType': raw.get('assetType', 'ST'),
    }


def fetch_politician_trades(politician: str = None) -> list[dict]:
    """Fetch recent congressional trades from FMP. Returns list of trade dicts
    in the same shape the rest of the bot already expects."""
    politician = politician or TARGET_POLITICIAN

    all_raw = _fetch_latest('senate-latest') + _fetch_latest('house-latest')
    trades = [
        _normalize(t) for t in all_raw
        if f"{t.get('firstName', '')} {t.get('lastName', '')}".strip().lower() == politician.lower()
    ]
    logger.info(f'Fetched {len(trades)} trades for {politician} (checked {len(all_raw)} recent disclosures)')
    return trades


def parse_amount_midpoint(range_str: str) -> float:
    """Convert '$1,001 - $15,000' style ranges to a midpoint dollar value."""
    if not range_str:
        return 1000.0
    import re
    nums = re.findall(r'[\d,]+', range_str.replace('$', ''))
    nums = [float(n.replace(',', '')) for n in nums]
    if len(nums) == 2:
        return (nums[0] + nums[1]) / 2
    if len(nums) == 1:
        return nums[0]
    return 1000.0


def make_trade_id(trade: dict) -> str:
    """Unique ID for a trade to prevent duplicate execution."""
    return f"{trade['Representative']}_{trade['TransactionDate']}_{trade['Ticker']}_{trade['Transaction']}"
