import requests
import logging
from config import QUIVER_API_URL, TARGET_POLITICIAN

logger = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json',
}


def fetch_politician_trades(politician: str = None) -> list[dict]:
    """Fetch recent congressional trades from QuiverQuant. Returns list of trade dicts."""
    politician = politician or TARGET_POLITICIAN
    try:
        r = requests.get(QUIVER_API_URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
        all_trades = r.json()
        trades = [t for t in all_trades if t.get('Representative', '').lower() == politician.lower()]
        logger.info(f'Fetched {len(trades)} trades for {politician} (total pool: {len(all_trades)})')
        return trades
    except Exception as e:
        logger.error(f'Failed to fetch trades: {e}')
        return []


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
