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
_OPT_CALL_SLEEP = 0.25   # raised from 0.05 on 2026-09-08 -- v2's more aggressive
                          # contract-reselection pattern was hitting Alpaca's
                          # rate limit hard enough to poison the contract cache
                          # (see _FetchFailed's docstring); more pacing per call
                          # reduces how often that happens in the first place


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


class _FetchFailed(Exception):
    """Raised when EVERY attempt to reach the API failed (network/rate-limit/etc.)
    -- distinct from a call that succeeded and legitimately found no contracts.
    Caught by get_option_contract(), which must NOT cache this outcome (see its
    docstring for why: caching a transient failure as a permanent None silently
    poisons every future lookup for that exact key, discovered 2026-09-08 after
    Alpaca's options-contracts endpoint got rate-limited during a heavy backtest
    run -- one nearby spot value succeeded, then dozens of nearly-identical
    requests in the same tight loop all failed and got cached as "no contract"
    forever, understating real trade opportunities in every run since)."""


def _fetch_option_chain(underlying: str, opt_type: str, spot: float, as_of: date,
                         max_dte: int) -> dict:
    """
    Fetches EVERY candidate contract in the strike window around `spot`, not just
    the single closest one. Queries both status=active and status=inactive since
    whether a contract has expired by now depends on when this runs, not `as_of`.

    Returns a chain entry: {'lo', 'hi', 'contracts': [{'symbol','strike','expiration'}]}
    where lo/hi are the spot bounds this fetch's strike window actually covers.

    Raises _FetchFailed if BOTH attempts raised an exception (so the caller
    knows this wasn't a genuine "no contracts in range" result) -- returns an
    entry with an empty `contracts` list only when a call actually completed
    and came back empty.
    """
    exp_max = str(as_of + timedelta(days=max_dte))
    strike_min, strike_max = spot * 0.95, spot * 1.05
    raw = []
    any_success = False
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
            any_success = True
            raw.extend(data.get('option_contracts', []))
        except Exception:
            # No pacing sleep on a failed request: _OPT_CALL_SLEEP exists to
            # stay under Alpaca's rate limit BETWEEN successful calls, and a
            # request that raised consumed no quota worth pacing. Sleeping here
            # turned a credential outage into 41s of dead wall time per
            # symbol-month (166 sleeps x 0.25s), on top of the failures
            # themselves -- measured 2026-09-08.
            continue
        time.sleep(_OPT_CALL_SLEEP)
        if raw:
            break

    if not raw and not any_success:
        raise _FetchFailed(f'{underlying} {opt_type} {as_of}: every request failed (rate-limited?)')

    return {
        'lo': strike_min,
        'hi': strike_max,
        'contracts': [{
            'symbol': c.get('symbol'),
            'strike': float(c.get('strike_price', 0)),
            'expiration': c.get('expiration_date'),
        } for c in raw],
    }


def _nearest_contract(contracts: list, spot: float) -> dict | None:
    """Nearest-expiry-then-closest-strike -- the exact selection rule
    _fetch_option_contract() used to apply server-side-then-locally, now applied
    purely locally against a cached candidate set."""
    if not contracts:
        return None
    return min(contracts, key=lambda c: (c['expiration'], abs(c['strike'] - spot)))


def _chain_key(opt_type: str, as_of) -> str:
    return f'{opt_type}|{as_of}|chain'


def _harvest_legacy_chain(cache: dict, opt_type: str, as_of) -> dict | None:
    """
    Rebuilds a chain entry from this cache file's OLD per-spot entries
    (`'{type}|{date}|{spot}'` -> one contract), so switching to the chain key
    doesn't throw away an already-warm cache and force a full re-fetch.

    Every legacy value is a contract that was genuinely selected at some spot
    that day, so the union of them is a valid candidate set, and the min/max
    spot those lookups were made at is a sound coverage window -- inside it,
    _nearest_contract() reproduces the same answer the old code returned.
    """
    prefix = f'{opt_type}|{as_of}|'
    by_symbol, spots = {}, []
    for k, v in cache.items():
        if not k.startswith(prefix) or k.endswith('|chain'):
            continue
        try:
            spots.append(float(k[len(prefix):]))
        except ValueError:
            continue
        if v:   # skip legacy None (genuine "no contract in range") entries
            by_symbol[v['symbol']] = {
                'symbol': v['symbol'], 'strike': v['strike'], 'expiration': v['expiration'],
            }
    if not spots:
        return None
    return {'lo': min(spots), 'hi': max(spots), 'contracts': list(by_symbol.values())}


def get_option_contract(underlying: str, opt_type: str, spot: float, as_of,
                         max_dte: int = _OPT_MAX_DTE, cache: dict = None) -> dict | None:
    """
    Cached as-of-date ATM-ish contract lookup. Pass `cache` (from load_contract_cache())
    if you're doing many lookups for the same underlying and want to save the round
    trip to disk on every call -- otherwise this loads/saves the cache file itself.

    Caches the whole candidate CHAIN per (type, day) rather than one contract per
    (type, day, exact spot). The old per-spot key was effectively never reused --
    spot drifts every bar, so `round(spot, 4)` made a distinct key almost every
    call, and a caller sweeping a day intraday re-hit Alpaca continuously.
    Profiled 2026-09-08: 176 live requests during a supposedly-cached one-symbol/
    one-month backtest, 70% of its total wall time (fresh TLS handshake plus a
    mandatory _OPT_CALL_SLEEP pause each). Caching the chain instead means one
    fetch per (symbol, type, day) -- matching how often DayTradingBot v1's
    backtest looks one up -- and every subsequent spot resolves locally.

    This is NOT an approximation: selection is still nearest-expiry-then-
    closest-strike over the real contract list, so it returns exactly what the
    per-spot version returned, just without the network round trip. (Contrast
    the CONTRACT_REUSE_BAND_PCT hack removed from
    DayTradingBotV2/backtest/backtest_market_data.py the same day, which DID
    change the answer by reusing a stale strike.)

    A transient fetch failure (e.g. rate-limited) is NOT cached -- only a call
    that actually completed gets its result written to disk. See _FetchFailed's
    docstring for why: caching a failure as a permanent None silently poisons
    every future lookup for that key forever.
    """
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)
    owns_cache = cache is None
    if owns_cache:
        cache = load_contract_cache(underlying)

    key = _chain_key(opt_type, as_of)
    entry = cache.get(key) or _harvest_legacy_chain(cache, opt_type, as_of)

    # Re-fetch when there's nothing cached yet, or when spot has wandered outside
    # the strike window the cached chain actually covers (so the truly-closest
    # strike could be one we never fetched).
    if entry is None or not (entry['lo'] <= spot <= entry['hi']):
        try:
            fresh = _fetch_option_chain(underlying, opt_type, spot, as_of, max_dte)
        except _FetchFailed:
            # Not cached -- next call retries instead of staying poisoned. Fall
            # back to whatever we already had rather than losing a usable answer.
            return _nearest_contract(entry['contracts'], spot) if entry else None
        if entry:
            merged = {c['symbol']: c for c in entry['contracts']}
            merged.update({c['symbol']: c for c in fresh['contracts']})
            entry = {'lo': min(entry['lo'], fresh['lo']),
                     'hi': max(entry['hi'], fresh['hi']),
                     'contracts': list(merged.values())}
        else:
            entry = fresh
        cache[key] = entry
        if owns_cache:
            save_contract_cache(underlying, cache)

    return _nearest_contract(entry['contracts'], spot)


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


_PATH_MEMO: dict = {}   # (option_symbol, day) -> (times, prices), see get_option_price_path


def get_option_price_path(option_symbol: str, day: str, start_time: str = None) -> tuple:
    """
    Cached (times, prices) trade tape for one contract on one day, from `start_time`
    (default the day's market open, 13:30 UTC) through the session close. Immutable
    historical data once the day has closed -- a repeated call for the same
    (option_symbol, day) never re-hits Alpaca once cached, regardless of `start_time`
    (the cache always covers from market open, so any later start_time is a subset).

    Memoized in-process on top of the disk cache: a caller checking stops every
    simulated minute asks for the same contract's path hundreds of times, and
    re-reading plus re-JSON-parsing that file each time is pure waste (profiled
    2026-09-08 at 37s of a 236s backtest -- 315 loads where 21 distinct paths
    existed). DayTradingBot v1's backtest.py has always kept an equivalent
    per-run `path_cache` dict; this brings the shared engine to parity so every
    consumer gets it. Safe to hold for a whole process: closed-day trade tapes
    never change.
    """
    start_time = start_time or f'{day}T13:30:00Z'
    memo_key = (option_symbol, day)
    if memo_key in _PATH_MEMO:
        return _PATH_MEMO[memo_key]
    path = _load_path_cache(option_symbol, day)
    if path is None:
        path = _fetch_option_path(option_symbol, day, f'{day}T13:30:00Z')
        _save_path_cache(option_symbol, day, path)
    _PATH_MEMO[memo_key] = path
    return path


def price_at(path: tuple, when: datetime, fallback: float = None) -> float | None:
    """Last traded price at or before `when` -- what a live mid-quote would roughly show."""
    import bisect
    times, prices = path
    idx = bisect.bisect_right(times, when)
    return prices[idx - 1] if idx else fallback
