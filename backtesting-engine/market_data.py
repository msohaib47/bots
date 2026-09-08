"""
Shared historical equity/ETF bar data: fetch (paginated, rate-limit-friendly) + local
JSON cache, extracted from DayTradingBot/backtest.py's proven fetch/cache logic so any
bot's backtest can call one function instead of reimplementing this.

Cached per (symbol, timeframe) pair -- a request for 5Min bars and a request for 1Min
bars on the same symbol are cached separately, since Alpaca aggregates server-side per
timeframe and there's no local resampling here (see BACKTESTING_ENGINE_PLAN.md's "one
download, use for both" caveat: this is deliberate, not an oversight -- bar resampling
with correct session-boundary alignment is exactly the kind of thing that's caused a
real bug in this codebase before).

Usage:
    from market_data import get_bars
    bars = get_bars('SPY', '5Min', start_date='2026-06-01', end_date='2026-09-01')
    # bars: [{'t': '2026-06-01T13:30:00Z', 'o':.., 'h':.., 'l':.., 'c':.., 'v':..}, ...]
    # sorted oldest first, timestamps are Alpaca's raw ISO-8601 UTC strings.

Requires a discoverable .env with ALPACA_API_KEY/ALPACA_SECRET_KEY (see _alpaca.py's
docstring -- this was not live-tested against the real API in the pass that wrote it,
for exactly that reason).
"""
import json
import os
from datetime import datetime, timedelta, timezone, date

from _alpaca import client, DATA_URL

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache', 'bars')
_PAGE_LIMIT = 10000   # Alpaca's max bars per page


# -- Cache helpers ----------------------------------------------------------------

def _cache_path(symbol: str, timeframe: str) -> str:
    safe_tf = timeframe.replace('/', '_')
    return os.path.join(CACHE_DIR, f'{symbol}_{safe_tf}.json')


def _load_cache(symbol: str, timeframe: str) -> list:
    p = _cache_path(symbol, timeframe)
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return json.load(f)


def _save_cache(symbol: str, timeframe: str, bars: list):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(symbol, timeframe), 'w') as f:
        json.dump(bars, f)


def _missing_ranges(cached: list, start: date, end: date) -> list:
    """
    Return [(from_date, to_date)] for ranges outside what the cache already covers.
    Gaps are checked only at the edges (before cache start, after cache end) to avoid
    re-fetching weekends/holidays inside an already-covered range, which have no bars
    by design, not because of a gap.
    """
    if not cached:
        return [(start, end)]

    cache_min = date.fromisoformat(min(b['t'][:10] for b in cached))
    cache_max = date.fromisoformat(max(b['t'][:10] for b in cached))

    ranges = []
    if start < cache_min:
        ranges.append((start, cache_min - timedelta(days=1)))
    # Only fetch the tail if there are at least 2 calendar days since cache_max --
    # avoids re-fetching "today" (possibly weekend/pre-open, no data yet) on every call.
    if end > cache_max + timedelta(days=1):
        ranges.append((cache_max + timedelta(days=1), end))
    return ranges


# -- Raw fetch (no cache) ----------------------------------------------------------

def _fetch_range(symbol: str, timeframe: str, from_date: date, to_date: date, feed: str) -> list:
    """Fetch bars via Alpaca's bars endpoint for a date range (paginated)."""
    start = datetime.combine(from_date, datetime.min.time(), tzinfo=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    end   = datetime.combine(to_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    bars = []
    page_token = None
    while True:
        params = {
            'timeframe': timeframe, 'start': start, 'end': end,
            'limit': _PAGE_LIMIT, 'adjustment': 'raw', 'feed': feed,
        }
        if page_token:
            params['page_token'] = page_token
        try:
            data = client._get(f'{DATA_URL}/v2/stocks/{symbol}/bars', params=params)
        except Exception:
            break
        for b in (data.get('bars') or []):
            bars.append({'t': b['t'], 'o': b['o'], 'h': b['h'], 'l': b['l'], 'c': b['c'], 'v': b['v']})
        page_token = data.get('next_page_token')
        if not page_token:
            break
    return bars


# -- Public API ---------------------------------------------------------------------

def get_bars(symbol: str, timeframe: str = '5Min', start_date=None, end_date=None,
             months: int = None, feed: str = 'iex', verbose: bool = True) -> list:
    """
    Return cached + freshly-fetched bars for `symbol` at `timeframe`, covering
    [start_date, end_date] (inclusive, both as date objects or 'YYYY-MM-DD' strings).

    If start_date is omitted, `months` (default 3) of lookback from end_date (default
    today) is used instead -- mirroring DayTradingBot v1's original convenience default.

    Only date-range gaps not already in the local cache are fetched from Alpaca; a
    repeated call over an already-cached range hits disk only.
    """
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)
    end_date = end_date or datetime.now(timezone.utc).date()
    if start_date is None:
        start_date = end_date - timedelta(days=(months or 3) * 31)

    cached = _load_cache(symbol, timeframe)
    gaps = _missing_ranges(cached, start_date, end_date)

    new_count = 0
    if gaps:
        new_bars = []
        for g_from, g_to in gaps:
            fetched = _fetch_range(symbol, timeframe, g_from, g_to, feed)
            new_bars.extend(fetched)
            new_count += len(fetched)

        if new_bars:
            combined = cached + new_bars
            seen, merged = set(), []
            for b in sorted(combined, key=lambda x: x['t']):
                if b['t'] not in seen:
                    seen.add(b['t'])
                    merged.append(b)
            _save_cache(symbol, timeframe, merged)
            all_bars = merged
        else:
            all_bars = cached
    else:
        all_bars = cached

    if verbose:
        cached_note = f'+{new_count} new' if new_count else 'cached'
        print(f'  {symbol} {timeframe}: {len(all_bars)} bars total ({cached_note})')

    start_str, end_str = start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')
    return [b for b in all_bars if start_str <= b['t'][:10] <= end_str]
