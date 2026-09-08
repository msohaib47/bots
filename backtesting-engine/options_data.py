"""
Shared historical options data: contract selection (as-of-date ATM lookup) and
per-contract trade-price paths, extracted from DayTradingBot/backtest.py's proven
fetch/cache logic. Both are genuinely immutable once a trading day has closed, so this
is one of the two places (the other being the fetch/cache *machinery* for bars) where a
shared cache literally serves any number of bots/versions with zero duplicated API
calls -- unlike bars, there's no per-consumer resolution difference here.

Usage:
    from options_data import get_option_contract, get_option_price_path, price_at

    contract = get_option_contract('SPY', 'call', spot=750.0, as_of='2026-06-01')
    # -> {'symbol': 'SPY260601C00750000', 'strike': 750.0, 'expiration': '2026-06-01'} or None

    path = get_option_price_path(contract['symbol'], '2026-06-01', start_time='2026-06-01T13:00:00Z')
    price = price_at(path, some_datetime, fallback=None)

Requires a discoverable .env with ALPACA_API_KEY/ALPACA_SECRET_KEY (see _alpaca.py's
docstring -- this was not live-tested against the real API in the pass that wrote it,
for exactly that reason).
"""
import json
import os
import time
from datetime import datetime, timedelta, date

from _alpaca import client, BASE_URL, DATA_URL

CACHE_DIR            = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
CONTRACTS_CACHE_DIR  = os.path.join(CACHE_DIR, 'options_contracts')
TRADES_CACHE_DIR     = os.path.join(CACHE_DIR, 'options_trades')

_OPT_MAX_DTE    = 5      # matches DayTradingBot/alpaca.py's find_atm_contract live default
_OPT_CALL_SLEEP = 0.05   # gentle pacing across the extra API calls these lookups need


# -- Contract cache (keyed per-underlying, one JSON dict of all as-of-date lookups) --

def _contract_cache_path(underlying: str) -> str:
    return os.path.join(CONTRACTS_CACHE_DIR, f'{underlying}.json')


def load_contract_cache(underlying: str) -> dict:
    """Exposed (not just internal) so a caller doing many lookups for one underlying
    can load once, pass the dict into get_option_contract() repeatedly, and save once --
    matches DayTradingBot/backtest.py's per-symbol-per-backtest-run usage pattern."""
    p = _contract_cache_path(underlying)
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


def save_contract_cache(underlying: str, cache: dict):
    os.makedirs(CONTRACTS_CACHE_DIR, exist_ok=True)
    with open(_contract_cache_path(underlying), 'w') as f:
        json.dump(cache, f)


def _fetch_option_contract(underlying: str, opt_type: str, spot: float, as_of: date,
                            max_dte: int) -> dict | None:
    """
    Finds the nearest-expiry-then-closest-strike contract as of a historical date --
    the same selection DayTradingBot's live find_atm_contract() uses. Queries both
    status=active and status=inactive since whether a contract has expired by now
    depends on when this runs, not on `as_of`.
    """
    exp_max = str(as_of + timedelta(days=max_dte))
    strike_min, strike_max = spot * 0.95, spot * 1.05
    contracts = []
    for status in ('inactive', 'active'):
        try:
            data = client._get(f'{BASE_URL}/v2/options/contracts', params={
                'underlying_symbols': underlying,
                'expiration_date_gte': str(as_of),
                'expiration_date_lte': exp_max,
                'type': opt_type,
                'strike_price_gte': round(strike_min, 2),
                'strike_price_lte': round(strike_max, 2),
                'status': status,
                'limit': 100,
            })
            contracts.extend(data.get('option_contracts', []))
        except Exception:
            pass
        time.sleep(_OPT_CALL_SLEEP)
        if contracts:
            break

    if not contracts:
        return None
    contracts.sort(key=lambda c: (
        c.get('expiration_date', ''),
        abs(float(c.get('strike_price', 0)) - spot),
    ))
    c = contracts[0]
    return {
        'symbol': c.get('symbol'),
        'strike': float(c.get('strike_price', 0)),
        'expiration': c.get('expiration_date'),
    }


def get_option_contract(underlying: str, opt_type: str, spot: float, as_of,
                         max_dte: int = _OPT_MAX_DTE, cache: dict = None) -> dict | None:
    """
    Cached as-of-date ATM-ish contract lookup. Pass `cache` (from load_contract_cache())
    if you're doing many lookups for the same underlying and want to save the round
    trip to disk on every call -- otherwise this loads/saves the cache file itself.
    """
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)
    owns_cache = cache is None
    if owns_cache:
        cache = load_contract_cache(underlying)

    key = f'{opt_type}|{as_of}|{round(spot, 4)}'
    if key not in cache:
        cache[key] = _fetch_option_contract(underlying, opt_type, spot, as_of, max_dte)
        if owns_cache:
            save_contract_cache(underlying, cache)
    return cache[key]


# -- Option trade-price path cache (one file per contract-symbol + day) -------------

def _trades_cache_path(option_symbol: str, day: str) -> str:
    safe = option_symbol.replace('/', '_')
    return os.path.join(TRADES_CACHE_DIR, f'{safe}__{day}.json')


def _load_path_cache(option_symbol: str, day: str):
    p = _trades_cache_path(option_symbol, day)
    if not os.path.exists(p):
        return None
    with open(p) as f:
        data = json.load(f)
    times = [datetime.fromisoformat(t) for t in data['times']]
    return times, data['prices']


def _save_path_cache(option_symbol: str, day: str, path: tuple):
    os.makedirs(TRADES_CACHE_DIR, exist_ok=True)
    times, prices = path
    with open(_trades_cache_path(option_symbol, day), 'w') as f:
        json.dump({'times': [t.isoformat() for t in times], 'prices': prices}, f)


def _fetch_option_path(option_symbol: str, day: str, start_t: str) -> tuple:
    """
    Every trade for the contract from start_t through the session close, as parallel
    (sorted_datetimes, prices) lists for price_at()'s bisect lookup. Paginated -- 0DTE
    contracts print thousands of ticks/hour, so a single-page fetch silently truncates
    to the first ~30-40 minutes (confirmed against the real API on 2026-09-05).
    """
    end_t = f'{day}T20:05:00Z'
    out, page_token = [], None
    while True:
        params = {'symbols': option_symbol, 'start': start_t, 'end': end_t,
                  'limit': 10000, 'sort': 'asc'}
        if page_token:
            params['page_token'] = page_token
        try:
            data = client._get(f'{DATA_URL}/v1beta1/options/trades', params=params)
        except Exception:
            break
        for t in (data.get('trades') or {}).get(option_symbol, []):
            out.append((datetime.fromisoformat(t['t'].replace('Z', '+00:00')), float(t['p'])))
        page_token = data.get('next_page_token')
        time.sleep(_OPT_CALL_SLEEP)
        if not page_token:
            break
    out.sort(key=lambda x: x[0])
    return [p[0] for p in out], [p[1] for p in out]


def get_option_price_path(option_symbol: str, day: str, start_time: str = None) -> tuple:
    """
    Cached (times, prices) trade tape for one contract on one day, from `start_time`
    (default the day's market open, 13:30 UTC) through the session close. Immutable
    historical data once the day has closed -- a repeated call for the same
    (option_symbol, day) never re-hits Alpaca once cached, regardless of `start_time`
    (the cache always covers from market open, so any later start_time is a subset).
    """
    start_time = start_time or f'{day}T13:30:00Z'
    path = _load_path_cache(option_symbol, day)
    if path is None:
        path = _fetch_option_path(option_symbol, day, f'{day}T13:30:00Z')
        _save_path_cache(option_symbol, day, path)
    return path


def price_at(path: tuple, when: datetime, fallback: float = None) -> float | None:
    """Last traded price at or before `when` -- what a live mid-quote would roughly show."""
    import bisect
    times, prices = path
    idx = bisect.bisect_right(times, when)
    return prices[idx - 1] if idx else fallback
