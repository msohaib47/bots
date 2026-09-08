#!/usr/bin/env python3
"""
DayTradingBot historical backtest.
Replays the 15m EMA21-trend + 5m VWAP/EMA9/RSI/ADX signal on historical 5-min bars.
Uses Alpaca's IEX feed (same data vendor/feed the live bot trades against) --
gives 24+ months of intraday history, vs. yfinance's ~60-day cap, and removes
any discrepancy between backtested and live signal prices.
Bars are cached in cache_alpaca/<SYMBOL>.json; only missing days are fetched.
Option contract lookups are cached in cache_alpaca/options_contracts/<SYMBOL>.json
and option trade-price paths in cache_alpaca/options_trades/<OPT_SYMBOL>__<DAY>.json --
both are immutable historical data once a trading day has closed, so a re-run over
a previously-backtested date range hits disk instead of Alpaca's options endpoints
(which are the slow part: paginated per-contract fetches with rate-limit sleeps).

Usage:
  python backtest.py           - 3 months, all symbols
  python backtest.py 6         - 6 months, all symbols
  python backtest.py 12 SPY QQQ - 12 months, specific symbols only
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alpaca
from config import (SYMBOLS, BASE_URL, DATA_URL, STOP_LOSS_PCT, PROFIT_TRAIL_TRIGGER, TRAIL_WIGGLE,
                    MAX_CONTRACTS_PER_SYMBOL, HALF_CLOSE_PROFIT_PCT, HALF_CLOSE_ENABLED,
                    MAX_DAILY_LOSS_PER_SYMBOL, MAX_DAILY_LOSS_TOTAL,
                    MAX_SAME_DIRECTION, MAX_OPEN_EXPOSURE, EXPOSURE_TOLERANCE_PCT,
                    NO_NEW_ENTRY_TIME, FORCE_CLOSE_TIME, MAX_PREMIUM_PCT, MAX_EMA_GAP_ATR,
                    CASH_PER_TRADE_PCT, COOLDOWN_MINUTES)
from signals import _ema, _rsi, _vwap, _adx, _atr, RSI_CALL_RANGE, RSI_PUT_RANGE, ADX_MIN, ADX_PERIOD

ET       = ZoneInfo('America/New_York')
# Separate from the old yfinance cache (cache/) -- different vendor, don't mix bar sources.
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache_alpaca')

# Symbols Alpaca's IEX feed doesn't serve as equities
_SKIP = {'SPX'}

# Exits are simulated on the OPTION price path (real historical option trades,
# sampled once per minute like the live cron tick), applying position_manager's
# actual rules -- STOP_LOSS_PCT / PROFIT_TRAIL_TRIGGER / TRAIL_WIGGLE /
# HALF_CLOSE_PROFIT_PCT straight from config.py. The old underlying-move
# approximation (a 1.5%-underlying stop standing in for a 30%-premium stop) is gone:
# it produced 9 stops in 192 trades while 75 of them lost >80% of premium, i.e. it
# bore no relation to what the live bot would actually have done (2026-09-05).
_ENTRY_SLIP  = 0.01    # live bot assumes fill at mid + $0.01 (bot.py)

_NO_ENTRY    = NO_NEW_ENTRY_TIME   # from config.py / .env / env, same as the live bot
_FORCE_CLOSE = FORCE_CLOSE_TIME
_MIN_BARS    = 2 * ADX_PERIOD + 1   # bars needed for EMA21 + RSI14 + ADX14 warmup
_PAGE_LIMIT  = 10000   # Alpaca max bars per page

# Alpaca's historical options API has trade prices but NO historical bid/ask
# (confirmed 2026-09-05: /v1beta1/options/quotes 404s for any historical range),
# so the option price path is built from last-trade prices, standing in for the
# mid quote the live bot reads.
_OPT_MAX_DTE       = 5      # matches alpaca.find_atm_contract's live default
_OPT_CALL_SLEEP    = 0.05   # gentle pacing across the ~3 extra API calls/trade


# -- Cache helpers ---------------------------------------------------------------

def _cache_path(symbol: str) -> str:
    return os.path.join(CACHE_DIR, f'{symbol}.json')

def _load_cache(symbol: str) -> list:
    p = _cache_path(symbol)
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return json.load(f)

def _save_cache(symbol: str, bars: list):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(symbol), 'w') as f:
        json.dump(bars, f)


# -- Option contract + trade-price caches (immutable historical data once a
#    trading day has closed -- persisted so repeated backtest runs over the
#    same range never re-hit Alpaca's slow, paginated options endpoints) -----

CONTRACTS_CACHE_DIR = os.path.join(CACHE_DIR, 'options_contracts')
TRADES_CACHE_DIR    = os.path.join(CACHE_DIR, 'options_trades')


def _contract_cache_path(underlying: str) -> str:
    return os.path.join(CONTRACTS_CACHE_DIR, f'{underlying}.json')


def _load_contract_cache(underlying: str) -> dict:
    p = _contract_cache_path(underlying)
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


def _save_contract_cache(underlying: str, cache: dict):
    os.makedirs(CONTRACTS_CACHE_DIR, exist_ok=True)
    with open(_contract_cache_path(underlying), 'w') as f:
        json.dump(cache, f)


def _cached_historical_option_contract(cache: dict, underlying: str, opt_type: str,
                                        spot: float, as_of: date):
    """Same return shape as _historical_option_contract, memoized to `cache`
    (loaded/saved once per symbol by the caller) keyed on the inputs that
    actually determine the chosen contract."""
    key = f'{opt_type}|{as_of}|{round(spot, 4)}'
    if key in cache:
        return cache[key]
    result = _historical_option_contract(underlying, opt_type, spot, as_of)
    cache[key] = result
    return result


def _option_trades_cache_path(option_symbol: str, day: str) -> str:
    safe = option_symbol.replace('/', '_')
    return os.path.join(TRADES_CACHE_DIR, f'{safe}__{day}.json')


def _load_option_path_cache(option_symbol: str, day: str):
    p = _option_trades_cache_path(option_symbol, day)
    if not os.path.exists(p):
        return None
    with open(p) as f:
        data = json.load(f)
    times = [datetime.fromisoformat(t) for t in data['times']]
    return times, data['prices']


def _save_option_path_cache(option_symbol: str, day: str, path: tuple):
    os.makedirs(TRADES_CACHE_DIR, exist_ok=True)
    times, prices = path
    with open(_option_trades_cache_path(option_symbol, day), 'w') as f:
        json.dump({'times': [t.isoformat() for t in times], 'prices': prices}, f)

def _missing_ranges(cached: list, start: date, end: date) -> list:
    """
    Return [(from_date, to_date)] for ranges outside what the cache already covers.
    Gaps are checked only at the edges (before cache start, after cache end) to
    avoid re-fetching weekends/holidays that have no bars by design.
    """
    if not cached:
        return [(start, end)]

    cache_min = date.fromisoformat(min(b['t'][:10] for b in cached))
    cache_max = date.fromisoformat(max(b['t'][:10] for b in cached))

    ranges = []
    if start < cache_min:
        ranges.append((start, cache_min - timedelta(days=1)))
    # Only fetch the tail if there are at least 2 calendar days since cache_max —
    # avoids fetching the current day (possibly weekend/pre-open) which has no data yet.
    if end > cache_max + timedelta(days=1):
        ranges.append((cache_max + timedelta(days=1), end))
    return ranges


# -- Alpaca fetch (raw, no cache) -------------------------------------------------

def _alpaca_fetch_range(symbol: str, from_date: date, to_date: date) -> list:
    """Fetch 5-min bars via Alpaca's IEX feed for a date range (paginated)."""
    start = datetime.combine(from_date, datetime.min.time(), tzinfo=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    end   = datetime.combine(to_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    bars = []
    page_token = None
    while True:
        params = {
            'timeframe': '5Min', 'start': start, 'end': end,
            'limit': _PAGE_LIMIT, 'adjustment': 'raw', 'feed': 'iex',
        }
        if page_token:
            params['page_token'] = page_token
        try:
            data = alpaca._get(f'{DATA_URL}/v2/stocks/{symbol}/bars', params=params)
        except Exception:
            break
        for b in (data.get('bars') or []):
            bars.append({'t': b['t'], 'o': b['o'], 'h': b['h'], 'l': b['l'], 'c': b['c'], 'v': b['v']})
        page_token = data.get('next_page_token')
        if not page_token:
            break
    return bars


# -- Cached fetch ----------------------------------------------------------------

def _fetch(symbol: str, months: int) -> tuple:
    """
    Return (bars, new_bar_count) for the requested period.
    Loads cached bars, fetches only missing date ranges from Alpaca's IEX feed,
    updates the cache file, and returns bars filtered to [start, today].
    """
    cached     = _load_cache(symbol)
    today      = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=months * 31)

    gaps = _missing_ranges(cached, start_date, today)

    new_count = 0
    if gaps:
        new_bars = []
        for g_from, g_to in gaps:
            fetched = _alpaca_fetch_range(symbol, g_from, g_to)
            new_bars.extend(fetched)
            new_count += len(fetched)

        if new_bars:
            combined = cached + new_bars
            seen, merged = set(), []
            for b in sorted(combined, key=lambda x: x['t']):
                if b['t'] not in seen:
                    seen.add(b['t'])
                    merged.append(b)
            _save_cache(symbol, merged)
            all_bars = merged
        else:
            all_bars = cached
    else:
        all_bars = cached

    start_str = start_date.strftime('%Y-%m-%d')
    return [b for b in all_bars if b['t'][:10] >= start_str], new_count


# -- Time helpers ----------------------------------------------------------------

def _et(bar: dict) -> datetime:
    return datetime.fromisoformat(bar['t'].replace('Z', '+00:00')).astimezone(ET)

def _group_by_day(bars: list) -> dict:
    by_day = defaultdict(list)
    for b in bars:
        t = _et(b).strftime('%H:%M')
        if '09:30' <= t <= '16:05':
            by_day[_et(b).strftime('%Y-%m-%d')].append(b)
    return dict(sorted(by_day.items()))


# -- HTF (own 15-min trend) — mirrors signals._htf_trend() ---------------------

def _build_htf_series(bars: list) -> tuple:
    """
    Aggregate a symbol's own 5-min market-hours bars into 15-min bars (day-local
    chunks of 3, matching how Alpaca's 15Min timeframe aligns to the 9:30 open)
    and return (sorted timestamps, sorted closes) for lookup.
    """
    by_day = _group_by_day(bars)
    times, closes = [], []
    for day_bars in by_day.values():
        for i in range(0, len(day_bars) - 2, 3):
            chunk = day_bars[i:i + 3]
            times.append(chunk[-1]['t'])
            closes.append(chunk[-1]['c'])
    return times, closes


def _htf_lookup_factory(bars: list):
    """Returns a function: cutoff timestamp -> {'trend','ema21','slope'} (own 15-min trend as of that time)."""
    import bisect
    times, closes = _build_htf_series(bars)

    def lookup(cutoff_t: str) -> dict:
        out = {'trend': None, 'ema21': None, 'slope': None}
        idx = bisect.bisect_right(times, cutoff_t)
        window = closes[max(0, idx - 30):idx]
        if len(window) < 23:
            return out
        ema21s = _ema(window, 21)
        ema21, ema21_prev = ema21s[-1], ema21s[-2]
        if ema21 is None or ema21_prev is None:
            return out
        out['ema21'] = round(ema21, 4)
        out['slope'] = round(ema21 - ema21_prev, 4)
        if window[-1] > ema21 and ema21 > ema21_prev:
            out['trend'] = 'bull'
        elif window[-1] < ema21 and ema21 < ema21_prev:
            out['trend'] = 'bear'
        return out

    return lookup


# -- Signal (mirrors signals.get_signal() exactly, using signals.py's own helpers) --

def _signal(bars: list, session_bars: list, htf: dict) -> dict:
    """
    Returns {'signal': 'CALL'|'PUT'|'NONE', 'rsi','adx','atr','ema9','ema21','vwap','price', ...}.
    `bars` is the multi-session window (EMA/RSI/ADX/ATR warm up across prior
    sessions, matching signals.get_signal()'s use of get_recent_bars); `session_bars`
    is today-only, for VWAP (which resets each session).
    """
    out = {'signal': 'NONE', 'rsi': None, 'adx': None, 'atr': None,
           'ema9': None, 'ema21': None, 'vwap': None, 'price': None, 'ema_gap_atr': None}
    if len(bars) < _MIN_BARS:
        return out
    highs  = [b['h'] for b in bars]
    lows   = [b['l'] for b in bars]
    closes = [b['c'] for b in bars]

    vwap   = _vwap(session_bars)
    ema9s  = _ema(closes, 9)
    ema21s = _ema(closes, 21)
    ema9, ema9_prev = ema9s[-1], ema9s[-2]
    ema21  = ema21s[-1]
    rsi    = _rsi(closes, 14)
    adx    = _adx(highs, lows, closes, ADX_PERIOD)
    atr    = _atr(highs, lows, closes, 14)
    price  = closes[-1]

    ema_gap_atr = round(abs(ema9 - ema21) / atr, 3) if (ema9 is not None and ema21 is not None and atr) else None

    out.update({
        'rsi': rsi, 'adx': adx, 'atr': atr, 'ema_gap_atr': ema_gap_atr,
        'ema9': round(ema9, 4) if ema9 is not None else None,
        'ema21': round(ema21, 4) if ema21 is not None else None,
        'vwap': vwap, 'price': price,
    })

    if None in (vwap, ema9, ema9_prev, ema21, rsi, adx):
        return out

    ema9_rising  = ema9 > ema9_prev
    ema9_falling = ema9 < ema9_prev
    rsi_call_ok = RSI_CALL_RANGE[0] < rsi < RSI_CALL_RANGE[1]
    rsi_put_ok  = RSI_PUT_RANGE[0] < rsi < RSI_PUT_RANGE[1]
    adx_ok      = adx > ADX_MIN
    gap_ok      = ema_gap_atr is None or ema_gap_atr <= MAX_EMA_GAP_ATR   # mirrors signals.py
    htf_trend   = htf['trend']

    if htf_trend == 'bull' and price > vwap and ema9 > ema21 and ema9_rising and rsi_call_ok and adx_ok and gap_ok:
        out['signal'] = 'CALL'
    elif htf_trend == 'bear' and price < vwap and ema9 < ema21 and ema9_falling and rsi_put_ok and adx_ok and gap_ok:
        out['signal'] = 'PUT'
    return out


# -- Historical option contract + price lookup (real strike/expiry/trade price) --

def _historical_option_contract(underlying: str, opt_type: str, spot: float, as_of: date) -> dict | None:
    """
    Finds the contract the live bot would have picked (nearest expiry within
    _OPT_MAX_DTE days, closest strike to spot) as of a historical date.
    Queries both status=active and status=inactive since whether a contract has
    already expired depends on when this runs, not on `as_of`.
    """
    exp_max = str(as_of + timedelta(days=_OPT_MAX_DTE))
    strike_min, strike_max = spot * 0.95, spot * 1.05
    contracts = []
    # Try 'inactive' first -- every backtest date is historical, so the contract
    # has almost always already expired by the time this runs; only fall back to
    # 'active' (an extra call) when that comes up empty, e.g. a very recent date
    # whose contract hasn't technically expired yet.
    for status in ('inactive', 'active'):
        try:
            data = alpaca._get(f'{BASE_URL}/v2/options/contracts', params={
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


def _option_path(option_symbol: str, day: str, start_t: str) -> list:
    """
    Every trade for the contract from start_t through the session close, as a
    sorted list of (utc_datetime, price). Paginated via page_token -- 0DTE
    contracts print thousands of ticks/hour, so a single-page fetch silently
    truncates to the first ~30-40 minutes (confirmed 2026-09-05).
    """
    end_t = f'{day}T20:05:00Z'
    out, page_token = [], None
    while True:
        params = {'symbols': option_symbol, 'start': start_t, 'end': end_t,
                  'limit': 10000, 'sort': 'asc'}
        if page_token:
            params['page_token'] = page_token
        try:
            data = alpaca._get(f'{DATA_URL}/v1beta1/options/trades', params=params)
        except Exception:
            break
        for t in (data.get('trades') or {}).get(option_symbol, []):
            out.append((datetime.fromisoformat(t['t'].replace('Z', '+00:00')), float(t['p'])))
        page_token = data.get('next_page_token')
        time.sleep(_OPT_CALL_SLEEP)
        if not page_token:
            break
    out.sort(key=lambda x: x[0])
    # Parallel lists so _price_at can bisect without rebuilding a key list per call
    return [p[0] for p in out], [p[1] for p in out]


def _price_at(path: tuple, when: datetime, fallback: float | None) -> float | None:
    """Last traded price at or before `when` (what a live mid-quote would roughly show)."""
    import bisect
    times, prices = path
    idx = bisect.bisect_right(times, when)
    return prices[idx - 1] if idx else fallback


# -- Trade simulation ------------------------------------------------------------

def _simulate_option(entry_fill: float, path: list, entry_dt: datetime, day: str) -> dict:
    """
    Walk forward one minute at a time from entry (the live bot is a 1-minute cron)
    applying position_manager.check_and_update_stops' rules on the option price:
      - fixed stop at entry * (1 - STOP_LOSS_PCT)
      - trailing stop activates at +PROFIT_TRAIL_TRIGGER, trails TRAIL_WIGGLE below high-water
      - scale-out: sell contracts//2 once at +HALF_CLOSE_PROFIT_PCT, remainder keeps trailing
      - force close at _FORCE_CLOSE ET
    Same tick ordering as the live code: high-water -> trail activation -> scale-out -> stop check.
    """
    qty        = MAX_CONTRACTS_PER_SYMBOL
    high       = entry_fill
    stop       = entry_fill * (1 - STOP_LOSS_PCT)
    trailing   = False
    half_done  = False
    partial    = None   # (time, price, qty, pnl)
    current    = entry_fill

    force_dt = datetime.strptime(f'{day} {_FORCE_CLOSE}', '%Y-%m-%d %H:%M').replace(tzinfo=ET).astimezone(timezone.utc)
    t = entry_dt.replace(second=0, microsecond=0) + timedelta(minutes=1)

    while t <= force_dt:
        current = _price_at(path, t, current)
        gain    = (current - entry_fill) / entry_fill

        if t >= force_dt:
            reason = 'EOD'
            break

        if current > high:
            high = current
            if trailing:
                stop = max(stop, current * (1 - TRAIL_WIGGLE))
        if not trailing and gain >= PROFIT_TRAIL_TRIGGER:
            trailing = True
            stop = current * (1 - TRAIL_WIGGLE)
        if HALF_CLOSE_ENABLED and not half_done and gain >= HALF_CLOSE_PROFIT_PCT:
            half_done = True
            half_qty = qty // 2
            if half_qty >= 1:
                partial = (t, current, half_qty, (current - entry_fill) * half_qty * 100)
                qty -= half_qty
        if current <= stop:
            reason = 'Trail' if trailing else 'Stop'
            break
        t += timedelta(minutes=1)
    else:
        reason = 'EOD'
        t = force_dt

    final_pnl = (current - entry_fill) * qty * 100
    gross     = final_pnl + (partial[3] if partial else 0.0)
    return {
        'exit': current, 'exit_time': t.strftime('%Y-%m-%dT%H:%M:%SZ'), 'reason': reason,
        'final_qty': qty,
        'partial_time':  partial[0].strftime('%Y-%m-%dT%H:%M:%SZ') if partial else None,
        'partial_price': partial[1] if partial else None,
        'partial_qty':   partial[2] if partial else None,
        'partial_pnl':   partial[3] if partial else None,
        'gross_pnl': gross,
        'opt_return_pct': gross / (entry_fill * MAX_CONTRACTS_PER_SYMBOL * 100) * 100,
    }


# -- Portfolio pass: the live bot's cross-symbol / cross-day risk rules -----------

# Dynamic-exposure experiment (not a live config value -- backtest-only, opt in
# via --dynamic-exposure): below DYNAMIC_EXPOSURE_THRESHOLD equity, cap stays at
# the live fixed MAX_OPEN_EXPOSURE; once equity exceeds that threshold, the cap
# switches to DYNAMIC_EXPOSURE_PCT of *current* equity instead. STARTING_EQUITY
# matches the real live paper account's approximate balance at the time this was
# asked (~$10k, see TRADING_RULES.md's 2026-09-05 deploy log).
STARTING_EQUITY           = 10000.0
DYNAMIC_EXPOSURE_THRESHOLD = 10000.0
DYNAMIC_EXPOSURE_PCT      = 0.50


def _apply_portfolio_rules(all_trades: list, dynamic_exposure: bool = False,
                            starting_cash: float | None = None) -> dict:
    """
    Replays accepted entries chronologically the way bot.py would see them:
      - MAX_SAME_DIRECTION concurrent positions per direction (symbols evaluated in
        SYMBOLS order within the same minute, like the live loop)
      - MAX_DAILY_LOSS_TOTAL / MAX_DAILY_LOSS_PER_SYMBOL on realized P&L so far today
      - Exposure cap (+ EXPOSURE_TOLERANCE_PCT): total premium tied up in open
        positions; a new entry is sized down to what fits (P&L scaled pro rata --
        exact when the scale-out is off, approximate otherwise) and skipped if not
        even 1 contract fits. Fixed at MAX_OPEN_EXPOSURE unless `dynamic_exposure`
        is set, in which case the cap becomes DYNAMIC_EXPOSURE_PCT of current
        equity (STARTING_EQUITY + cumulative realized P&L so far) once that
        equity exceeds DYNAMIC_EXPOSURE_THRESHOLD -- see constants above.
      - Cash-based sizing (CASH_PER_TRADE_PCT), when `starting_cash` is given:
        mirrors bot.py's live `qty = min(MAX_CONTRACTS_PER_SYMBOL, floor(cash*CASH_PER_TRADE_PCT/cost),
        floor(exposure_room/cost))` -- cash tracked as
        starting_cash + cumulative realized P&L so far - cost basis of currently
        open positions (uncommitted cash), same definition Alpaca's own `cash`
        account field uses. Without `starting_cash` this constraint is skipped
        entirely (the original behavior, sizing purely off MAX_CONTRACTS_PER_SYMBOL/exposure/
        position-count, which is fine for an account large enough that cash never
        actually binds -- see TRADING_RULES.md's 2026-09-05 deploy log).
    Marks rejected candidates with reason='SKIP_<rule>' and gross_pnl=None.
    Not modelled: post-stop cool-down (bot.py blocks re-entry on a symbol for
    COOLDOWN_MINUTES after a stop-loss; backtest_symbol() re-enters as soon as
    a signal fires again once the prior position has closed, with no cooldown
    delay), PDT flag.
    """
    order = {s: i for i, s in enumerate(SYMBOLS)}
    cands = sorted((t for t in all_trades if t.get('gross_pnl') is not None),
                   key=lambda t: (t['entry_time'], order.get(t['symbol'], 99)))

    events = []           # (time_str, date, symbol, pnl) realizations from accepted trades
    open_pos = []         # (exit_time_str, direction, cost, partial_time, partial_cost)
    daily = {'date': None, 'total': 0.0, 'per_symbol': defaultdict(float)}
    cum = peak = 0.0
    fixed_cap = MAX_OPEN_EXPOSURE * (1 + EXPOSURE_TOLERANCE_PCT)
    stats = Counter()
    sized_down = 0
    peak_exposure = 0.0
    peak_equity = STARTING_EQUITY
    dynamic_kicked_in_at = None   # first trade date the equity-based cap actually applied
    min_cash_seen = starting_cash if starting_cash is not None else None

    def _cap_for_equity(equity: float) -> float:
        if dynamic_exposure and equity > DYNAMIC_EXPOSURE_THRESHOLD:
            return equity * DYNAMIC_EXPOSURE_PCT * (1 + EXPOSURE_TOLERANCE_PCT)
        return fixed_cap

    def _realize_through(t):
        nonlocal cum, peak
        events.sort()
        while events and events[0][0] <= t:
            _, d, sym, pnl = events.pop(0)
            if daily['date'] != d:
                daily['date'], daily['total'] = d, 0.0
                daily['per_symbol'] = defaultdict(float)
            daily['total'] += pnl
            daily['per_symbol'][sym] += pnl
            cum += pnl
            peak = max(peak, cum)

    def _exposure_at(t):
        # entry cost of everything still open at t; a half-closed position counts at its remaining size
        total = 0.0
        for exit_t, _, cost, p_t, p_cost in open_pos:
            if exit_t > t:
                total += cost - (p_cost if (p_t and p_t <= t) else 0.0)
        return total

    for tr in cands:
        t = tr['entry_time']
        _realize_through(t)
        if daily['date'] != tr['date']:
            daily['date'], daily['total'] = tr['date'], 0.0
            daily['per_symbol'] = defaultdict(float)
        open_pos[:] = [p for p in open_pos if p[0] > t]

        equity = STARTING_EQUITY + cum
        peak_equity = max(peak_equity, equity)
        cap = _cap_for_equity(equity)
        if dynamic_exposure and equity > DYNAMIC_EXPOSURE_THRESHOLD and dynamic_kicked_in_at is None:
            dynamic_kicked_in_at = tr['date']

        per_contract = tr['option_entry_fill'] * 100
        exposure = _exposure_at(t)
        room = cap - exposure
        fit = int(room / per_contract) if room > 0 else 0
        qty = min(tr['quantity'], fit)

        cash_fit = None
        if starting_cash is not None:
            cash_available = starting_cash + cum - exposure
            min_cash_seen = min(min_cash_seen, cash_available)
            cash_fit = int(cash_available * CASH_PER_TRADE_PCT / per_contract) if cash_available > 0 else 0
            qty = min(qty, cash_fit)

        skip = None
        if daily['total'] <= -MAX_DAILY_LOSS_TOTAL:
            skip = 'SKIP_DAILY_TOTAL'
        elif daily['per_symbol'][tr['symbol']] <= -MAX_DAILY_LOSS_PER_SYMBOL:
            skip = 'SKIP_DAILY_SYMBOL'
        elif sum(1 for _, d, *_ in open_pos if d == tr['direction']) >= MAX_SAME_DIRECTION:
            skip = 'SKIP_SAME_DIR'
        elif starting_cash is not None and cash_fit is not None and cash_fit < 1:
            skip = 'SKIP_CASH'
        elif qty < 1:
            skip = 'SKIP_EXPOSURE'

        if skip:
            tr['skipped_pnl'] = tr['gross_pnl']
            tr['gross_pnl'] = tr['net_pnl'] = None
            tr['reason'] = skip
            stats[skip] += 1
            continue

        if qty < tr['quantity']:
            scale = qty / tr['quantity']
            tr['gross_pnl'] = tr['net_pnl'] = tr['gross_pnl'] * scale
            if tr.get('partial_pnl'):
                tr['partial_pnl'] *= scale
            tr['quantity'] = qty
            tr['sized_down'] = True
            sized_down += 1

        cost = per_contract * qty
        partial_cost = per_contract * tr['partial_qty'] * (qty / MAX_CONTRACTS_PER_SYMBOL) if tr.get('partial_qty') else 0.0
        open_pos.append((tr['exit_time'], tr['direction'], cost, tr.get('partial_time'), partial_cost))
        peak_exposure = max(peak_exposure, exposure + cost)

        if tr.get('partial_time'):
            events.append((tr['partial_time'], tr['date'], tr['symbol'], tr['partial_pnl']))
            final_pnl = tr['gross_pnl'] - tr['partial_pnl']
        else:
            final_pnl = tr['gross_pnl']
        events.append((tr['exit_time'], tr['date'], tr['symbol'], final_pnl))

    _realize_through('9999')
    return {'skips': stats, 'sized_down': sized_down, 'peak_exposure': peak_exposure,
            'final_cum': cum, 'peak': peak, 'dynamic_exposure': dynamic_exposure,
            'peak_equity': peak_equity, 'dynamic_kicked_in_at': dynamic_kicked_in_at,
            'fixed_cap': fixed_cap, 'starting_cash': starting_cash,
            'ending_cash': (starting_cash + cum) if starting_cash is not None else None,
            'min_cash_seen': min_cash_seen}


# -- Per-symbol backtest ---------------------------------------------------------

def backtest_symbol(symbol: str, months: int, start_date: str | None = None, end_date: str | None = None) -> list:
    sys.stdout.write(f'  {symbol:<6}  ')
    sys.stdout.flush()

    try:
        bars, new_count = _fetch(symbol, months)
    except Exception as e:
        print(f'ERROR: {e}')
        return []

    if not bars:
        print('no data')
        return []

    by_day = _group_by_day(bars)
    cached_note = f'+{new_count} new' if new_count else 'cached'
    print(f'{len(bars):>5} bars  {len(by_day):>3} days  ({cached_note})')

    # HTF filter anchors on the symbol's own 15-min trend (matches signals.py's
    # self-anchored _htf_trend())
    htf_lookup = _htf_lookup_factory(bars)

    # EMA/RSI/ADX/ATR warm up from prior sessions (matches signals.get_signal()'s
    # get_recent_bars(limit=60) -- otherwise a naive within-day-only window blocks
    # any signal until ~2.4 hours after each day's open). Global bisect gives the
    # most recent 60 bars as of any timestamp, spanning across day boundaries.
    import bisect
    all_times = [b['t'] for b in bars]

    trades = []
    contract_cache = _load_contract_cache(symbol)
    path_cache: dict = {}   # option_symbol -> path; in-memory reuse when the premium filter rejects and we keep scanning
    for day, day_bars in by_day.items():
        if start_date and day < start_date:
            continue   # earlier days are only fetched for indicator/HTF warmup -- don't simulate (or price) them
        if end_date and day > end_date:
            break

        day_times = [b['t'] for b in day_bars]
        i = 0
        # Post-stop-loss cool-down (mirrors position_manager.set_cooldown/
        # is_in_cooldown/update_cooldown_reset): blocks re-entry on this symbol
        # until either COOLDOWN_MINUTES passes or the stopped-out direction's
        # setup breaks down (EMA9/EMA21 flips, or a VWAP-side cross), whichever
        # comes first. Reset fresh each day rather than carried from the prior
        # day's dict entry -- equivalent in practice, since COOLDOWN_MINUTES is
        # far shorter than the real-time gap between one day's close and the
        # next day's open, so a cross-day cooldown would always have expired
        # by the next session anyway.
        cooldown = None   # {'until': datetime, 'direction': 'call'|'put', 'reset_seen': bool, 'vwap_side': str|None}
        while i < len(day_bars):
            if _et(day_bars[i]).strftime('%H:%M') >= _NO_ENTRY:
                break

            session_window = day_bars[:i + 1]   # today-only, for VWAP
            g_idx  = bisect.bisect_right(all_times, day_bars[i]['t'])
            window = bars[max(0, g_idx - 60):g_idx]   # multi-session, for EMA/RSI/ADX/ATR

            htf = htf_lookup(day_bars[i]['t'])
            sig_out = _signal(window, session_window, htf)
            bar_dt_utc = datetime.fromisoformat(day_bars[i]['t'].replace('Z', '+00:00'))

            if cooldown is not None:
                if not cooldown['reset_seen']:
                    price, vwap, ema9, ema21 = sig_out['price'], sig_out['vwap'], sig_out['ema9'], sig_out['ema21']
                    if None not in (price, vwap, ema9, ema21):
                        current_side = 'above' if price > vwap else 'below'
                        prev_side = cooldown['vwap_side']
                        if cooldown['direction'] == 'call':
                            trend_broken = ema9 <= ema21
                            crossed = prev_side == 'above' and current_side == 'below'
                        else:
                            trend_broken = ema9 >= ema21
                            crossed = prev_side == 'below' and current_side == 'above'
                        cooldown['vwap_side'] = current_side
                        if trend_broken or crossed:
                            cooldown['reset_seen'] = True
                still_cooling = (not cooldown['reset_seen']) and (bar_dt_utc < cooldown['until'])
                if still_cooling:
                    i += 1
                    continue
                cooldown = None

            sig = sig_out['signal']
            if sig == 'NONE':
                i += 1
                continue

            entry      = day_bars[i]['c']
            entry_time = day_bars[i]['t']
            entry_dt   = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
            forward    = day_bars[i + 1:]
            if not forward:
                break

            opt_type = 'call' if sig == 'CALL' else 'put'
            as_of    = date.fromisoformat(day)
            contract = _cached_historical_option_contract(contract_cache, symbol, opt_type, entry, as_of)

            # Option path fetched once per (contract, day) from a fixed early point
            # (8:00 ET -- safely before any 0DTE contract starts trading, so it
            # covers any need_start regardless of entry time) through the close;
            # exits are simulated on this, not the underlying. Cached to disk since
            # this is immutable historical data -- a re-run over the same range
            # never re-fetches it.
            path = ([], [])
            opt_entry_mid = None
            if contract:
                opt_symbol = contract['symbol']
                path = path_cache.get(opt_symbol)
                if path is None:
                    path = _load_option_path_cache(opt_symbol, day)
                    if path is None:
                        path = _option_path(opt_symbol, day, f'{day}T13:00:00Z')
                        _save_option_path_cache(opt_symbol, day, path)
                    path_cache[opt_symbol] = path
                opt_entry_mid = _price_at(path, entry_dt, None)

            base = {
                'date': day, 'symbol': symbol, 'direction': sig,
                'entry': entry, 'entry_time': entry_time,
                'option_symbol': contract['symbol'] if contract else None,
                'expiration': contract['expiration'] if contract else None,
                'strike': contract['strike'] if contract else None,
                'option_entry_mid': opt_entry_mid,
                'quantity': MAX_CONTRACTS_PER_SYMBOL,
                'rsi': sig_out['rsi'], 'adx': sig_out['adx'], 'atr': sig_out['atr'],
                'ema_gap_atr': sig_out['ema_gap_atr'],
                'ema9': sig_out['ema9'], 'ema21': sig_out['ema21'], 'vwap': sig_out['vwap'],
                'htf_ema21': htf['ema21'], 'htf_slope': htf['slope'],
            }
            empty = {'option_entry_fill': None, 'exit': None, 'exit_time': None, 'gross_pnl': None,
                     'net_pnl': None, 'opt_return_pct': None, 'return': 0.0, 'underlying_exit': None}

            if opt_entry_mid is None:
                trades.append({**base, **empty, 'reason': 'UNPRICED'})
                i += 1   # can't price this attempt -- move to the next bar and keep trying, don't give up on the day
                continue

            # Premium filter (mirrors bot.py): too expensive vs spot -> skip this bar but
            # keep scanning, exactly like the live loop re-evaluating on the next tick.
            prem_pct = (opt_entry_mid + _ENTRY_SLIP) / entry * 100
            if prem_pct > MAX_PREMIUM_PCT:
                if not any(t['date'] == day and t['reason'] == 'FILTER_PREMIUM' for t in trades):
                    trades.append({**base, **empty, 'option_entry_fill': round(opt_entry_mid + _ENTRY_SLIP, 4),
                                   'reason': 'FILTER_PREMIUM'})   # record the first rejection of the day only
                i += 1
                continue

            entry_fill = round(opt_entry_mid + _ENTRY_SLIP, 4)
            result = _simulate_option(entry_fill, path, entry_dt, day)

            # Underlying price at the (option-determined) exit time, for the Und% table
            exit_dt = datetime.fromisoformat(result['exit_time'].replace('Z', '+00:00'))
            und_exit = next((b['c'] for b in reversed(forward)
                             if datetime.fromisoformat(b['t'].replace('Z', '+00:00')) <= exit_dt), entry)
            und_ret = (und_exit - entry) / entry if sig == 'CALL' else (entry - und_exit) / entry

            trades.append({
                **base,
                'option_entry_fill': entry_fill,
                'net_pnl': result['gross_pnl'],   # no commissions modeled (paper trading)
                'return': und_ret, 'underlying_exit': und_exit,
                **result,
            })

            # Cool-down after a (non-trailing) stop-loss -- mirrors bot.py calling
            # set_cooldown() only when `not trailing`. A trailing-stop or EOD exit
            # starts no cool-down, same as live.
            if result['reason'] == 'Stop':
                cooldown = {
                    'until': exit_dt + timedelta(minutes=COOLDOWN_MINUTES),
                    'direction': opt_type, 'reset_seen': False, 'vwap_side': None,
                }

            # Re-entry allowed once this position closes (matches bot.py: a symbol
            # can never have more than one open position at a time, sized up to
            # MAX_CONTRACTS_PER_SYMBOL, but is free to re-enter immediately after
            # closing -- no "one trade per symbol per day" limit). Resume scanning
            # from the first bar at/after this trade's exit time, not the very next
            # bar, so the position is never treated as still open past its own close.
            i = max(bisect.bisect_right(day_times, result['exit_time']), i + 1)

    _save_contract_cache(symbol, contract_cache)
    return trades


# -- Report ----------------------------------------------------------------------

def _report(all_trades: list, months: int, portfolio: dict | None = None):
    if not all_trades:
        print('  No trades simulated.\n')
        return

    # Only trades the live bot would actually have taken count toward the tables;
    # UNPRICED (no option data) and SKIP_* (blocked by a risk rule) are reported separately.
    taken  = [t for t in all_trades if t.get('gross_pnl') is not None]
    by_sym = defaultdict(list)
    for t in taken:
        by_sym[t['symbol']].append(t)

    priced  = taken
    missing = sum(1 for t in all_trades if t['reason'] == 'UNPRICED')

    W = 74

    # -- % table (underlying-move based, direction-agnostic) --
    print(f'\n{"="*W}')
    print(f'  DayTradingBot Backtest - {months} Month{"s" if months > 1 else ""}')
    print(f'  Signal: 15m EMA21 trend + 5m VWAP/EMA9/RSI/ADX')
    print(f'{"-"*W}')
    print(f'  {"Symbol":<8} {"N":>5} {"Win%":>6} {"AvgW%":>7} {"AvgL%":>7} '
          f'{"Und%":>7}  {"Stop":>4} {"Trail":>5} {"EOD":>4}')
    print(f'  {"-"*62}')

    total_n, total_wins, total_ret = 0, 0, 0.0

    for sym in sorted(by_sym):
        tt     = by_sym[sym]
        wins   = [x for x in tt if x['return'] > 0]
        losses = [x for x in tt if x['return'] <= 0]
        stops  = sum(1 for x in tt if x['reason'] == 'Stop')
        trails = sum(1 for x in tt if x['reason'] == 'Trail')
        eods   = sum(1 for x in tt if x['reason'] == 'EOD')
        n      = len(tt)
        wp     = len(wins) / n * 100
        aw     = sum(x['return'] for x in wins)   / len(wins)   * 100 if wins   else 0.0
        al     = sum(x['return'] for x in losses) / len(losses) * 100 if losses else 0.0
        net    = sum(x['return'] for x in tt) / n * 100
        print(f'  {sym:<8} {n:>5} {wp:>5.1f}% {aw:>+6.2f}% {al:>+6.2f}% '
              f'{net:>+6.2f}%  {stops:>4} {trails:>5} {eods:>4}')
        total_n    += n
        total_wins += len(wins)
        total_ret  += sum(x['return'] for x in tt)

    ow = total_wins / total_n * 100 if total_n else 0
    on = total_ret  / total_n * 100 if total_n else 0
    print(f'  {"-"*62}')
    print(f'  {"TOTAL":<8} {total_n:>5} {ow:>5.1f}%  {"":>6}   {"":>6}  {on:>+6.2f}%')

    # -- $ table: real historical option prices, exits simulated on the option path --
    print(f'\n  Dollar P&L - real historical option trade prices; exits simulated on the')
    half = f'half-close @ +{HALF_CLOSE_PROFIT_PCT:.0%}' if HALF_CLOSE_ENABLED else 'half-close off'
    print(f'  option price with the live rules (stop {STOP_LOSS_PCT:.0%}, trail @ +{PROFIT_TRAIL_TRIGGER:.0%} / '
          f'{TRAIL_WIGGLE:.0%} wiggle, {half}).')
    print(f'  Entry fill = last trade + ${_ENTRY_SLIP:.2f}. Quantity = {MAX_CONTRACTS_PER_SYMBOL} contracts/trade. '
          f'No entries after {_NO_ENTRY} ET, force-close {_FORCE_CLOSE} ET.')
    print(f'  {"Symbol":<8} {"AvgEntry":>10} {"AvgW$":>8} {"AvgL$":>8} {"Avg$/tr":>9} {"Total P&L":>11} {"N taken":>9}')
    print(f'  {"-"*68}')

    grand_pnl = 0.0
    for sym in sorted(by_sym):
        tt = [x for x in by_sym[sym] if x.get('gross_pnl') is not None]
        if not tt:
            print(f'  {sym:<8}  (no trades taken)')
            continue
        wins   = [x for x in tt if x['gross_pnl'] > 0]
        losses = [x for x in tt if x['gross_pnl'] <= 0]
        avg_entry = sum(x['option_entry_fill'] for x in tt) / len(tt)
        avg_win   = sum(x['gross_pnl'] for x in wins)   / len(wins)   if wins   else 0.0
        avg_loss  = sum(x['gross_pnl'] for x in losses) / len(losses) if losses else 0.0
        avg_trade = sum(x['gross_pnl'] for x in tt) / len(tt)
        total     = sum(x['gross_pnl'] for x in tt)
        grand_pnl += total
        print(f'  {sym:<8} {avg_entry:>9.2f}  {avg_win:>+7.2f}  {avg_loss:>+7.2f} '
              f'{avg_trade:>+8.2f}  {total:>+10.2f} {len(tt):>9}')

    print(f'  {"-"*68}')
    print(f'  {"TOTAL":<8} {"":>10}  {"":>8}  {"":>8} {"":>9}  {grand_pnl:>+10.2f} {len(priced):>9}')
    print(f'{"="*W}')

    calls  = sum(1 for t in taken if t['direction'] == 'CALL')
    puts   = sum(1 for t in taken if t['direction'] == 'PUT')
    stops  = sum(1 for t in taken if t['reason'] == 'Stop')
    trails = sum(1 for t in taken if t['reason'] == 'Trail')
    eods   = sum(1 for t in taken if t['reason'] == 'EOD')
    halves = sum(1 for t in taken if t.get('partial_time'))

    print(f'\n  Direction:   {calls} CALL  /  {puts} PUT')
    half_note = f'   (+{halves} half-closes at +{HALF_CLOSE_PROFIT_PCT:.0%})' if HALF_CLOSE_ENABLED else ''
    print(f'  Exit:        {stops} stop-loss  /  {trails} trailing-stop  /  {eods} EOD{half_note}')
    print(f'  Days:        {len(set(t["date"] for t in taken))} trading days with a trade')
    prem_filtered = sum(1 for t in all_trades if t['reason'] == 'FILTER_PREMIUM')
    print(f'  Data:        {len(all_trades)} signals; {missing} unpriced (no historical option data), '
          f'{prem_filtered} symbol-days with a MAX_PREMIUM_PCT={MAX_PREMIUM_PCT:.2f}% rejection (may have entered later), '
          f'{len(all_trades) - missing - prem_filtered - len(taken)} blocked by risk rules, {len(taken)} taken')
    print(f'  Filters:     MAX_EMA_GAP_ATR={MAX_EMA_GAP_ATR} (applied inside the signal), MAX_PREMIUM_PCT={MAX_PREMIUM_PCT:.2f}%')

    if portfolio:
        sk = portfolio['skips']
        print(f'\n  Risk rules:  same-direction cap ({MAX_SAME_DIRECTION}) blocked {sk["SKIP_SAME_DIR"]}; '
              f'daily total cap (${MAX_DAILY_LOSS_TOTAL:.0f}) blocked {sk["SKIP_DAILY_TOTAL"]}; '
              f'daily per-symbol cap (${MAX_DAILY_LOSS_PER_SYMBOL:.0f}) blocked {sk["SKIP_DAILY_SYMBOL"]}')
        blocked_pnl = sum(t.get('skipped_pnl') or 0 for t in all_trades if t['reason'].startswith('SKIP_'))
        print(f'               P&L the blocked trades would have made: ${blocked_pnl:+,.0f}')
        exp_pnl = sum(t.get('skipped_pnl') or 0 for t in all_trades if t['reason'] == 'SKIP_EXPOSURE')
        if portfolio.get('dynamic_exposure'):
            print(f'  Exposure:    DYNAMIC -- fixed ${MAX_OPEN_EXPOSURE:,.0f}(+{EXPOSURE_TOLERANCE_PCT:.0%}) while equity <= '
                  f'${DYNAMIC_EXPOSURE_THRESHOLD:,.0f}, then {DYNAMIC_EXPOSURE_PCT:.0%} of current equity(+{EXPOSURE_TOLERANCE_PCT:.0%}) above it '
                  f'(starting equity ${STARTING_EQUITY:,.0f}); peak open premium ${portfolio["peak_exposure"]:,.0f}, peak equity ${portfolio["peak_equity"]:,.0f}; '
                  f'{portfolio["sized_down"]} entries sized down, {sk["SKIP_EXPOSURE"]} skipped entirely (worth ${exp_pnl:+,.0f} at full size)')
            if portfolio.get('dynamic_kicked_in_at'):
                print(f'               dynamic cap first took over on {portfolio["dynamic_kicked_in_at"]} (equity crossed ${DYNAMIC_EXPOSURE_THRESHOLD:,.0f})')
            else:
                print(f'               equity never crossed ${DYNAMIC_EXPOSURE_THRESHOLD:,.0f} -- ran on the fixed cap the whole period')
        else:
            print(f'  Exposure:    MAX_OPEN_EXPOSURE ${MAX_OPEN_EXPOSURE:,.0f} (+{EXPOSURE_TOLERANCE_PCT:.0%} tolerance = '
                  f'${MAX_OPEN_EXPOSURE * (1 + EXPOSURE_TOLERANCE_PCT):,.0f}): peak open premium ${portfolio["peak_exposure"]:,.0f}; '
                  f'{portfolio["sized_down"]} entries sized down, {sk["SKIP_EXPOSURE"]} skipped entirely (worth ${exp_pnl:+,.0f} at full size)')
        if portfolio.get('starting_cash') is not None:
            cash_pnl = sum(t.get('skipped_pnl') or 0 for t in all_trades if t['reason'] == 'SKIP_CASH')
            print(f'  Cash sizing: CASH_PER_TRADE_PCT={CASH_PER_TRADE_PCT:.0%} of available cash per entry '
                  f'(starting cash ${portfolio["starting_cash"]:,.0f}); {sk["SKIP_CASH"]} entries skipped entirely for lack of cash '
                  f'(worth ${cash_pnl:+,.0f} at full size); lowest available cash seen ${portfolio["min_cash_seen"]:,.0f}')
            print(f'  Cash:        starting ${portfolio["starting_cash"]:,.2f}, ending ${portfolio["ending_cash"]:,.2f} '
                  f'({portfolio["ending_cash"] - portfolio["starting_cash"]:+,.2f})')
        # Max drawdown of cumulative realized P&L, for reference (no rule acts on it)
        print(f'  P&L curve:   final ${portfolio["final_cum"]:+,.0f}, peak ${portfolio["peak"]:+,.0f}')
    print()


# -- Trade-level CSV export (entry/exit price + time per trade) ------------------

TRADES_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backtest_trades.csv')

_CSV_COLUMNS = [
    'date', 'symbol', 'direction',
    'underlying_entry', 'underlying_exit',
    'option_symbol', 'expiration', 'strike',
    'option_entry_mid', 'option_entry_fill', 'option_exit_price',
    'partial_time', 'partial_price', 'partial_qty', 'partial_pnl', 'final_qty',
    'quantity', 'sized_down', 'gross_pnl', 'net_pnl', 'return_pct', 'skipped_pnl',
    'entry_time', 'exit_time', 'exit_reason',
    'rsi', 'adx', 'ema9', 'ema21', 'vwap', 'htf_ema21', 'htf_slope', 'atr', 'ema_gap_atr',
]


def _r(v, nd=4):
    return round(v, nd) if isinstance(v, (int, float)) else v


def _export_trades_csv(all_trades: list, path: str = TRADES_CSV):
    import csv as _csv
    with open(path, 'w', newline='') as f:
        w = _csv.writer(f)
        w.writerow(_CSV_COLUMNS)
        for t in sorted(all_trades, key=lambda x: (x['date'], x['symbol'])):
            w.writerow([
                t['date'], t['symbol'], t['direction'],
                _r(t['entry'], 2), _r(t.get('underlying_exit'), 2),
                t.get('option_symbol'), t.get('expiration'), _r(t.get('strike'), 2),
                _r(t.get('option_entry_mid'), 2), _r(t.get('option_entry_fill'), 2), _r(t.get('exit'), 2),
                t.get('partial_time') or '', _r(t.get('partial_price'), 2), t.get('partial_qty') or '',
                _r(t.get('partial_pnl'), 2), t.get('final_qty') or '',
                t.get('quantity'), 'Y' if t.get('sized_down') else '',
                _r(t.get('gross_pnl'), 2), _r(t.get('net_pnl'), 2), _r(t.get('opt_return_pct'), 3),
                _r(t.get('skipped_pnl'), 2),
                t.get('entry_time', ''), t.get('exit_time', '') or '', t['reason'],
                _r(t.get('rsi'), 2), _r(t.get('adx'), 2), _r(t.get('ema9'), 4), _r(t.get('ema21'), 4),
                _r(t.get('vwap'), 4), _r(t.get('htf_ema21'), 4), _r(t.get('htf_slope'), 4), _r(t.get('atr'), 4),
                _r(t.get('ema_gap_atr'), 3),
            ])
    print(f'  Per-trade detail (underlying + option prices, indicators) written to {path}\n')


# -- Entry point -----------------------------------------------------------------

def run_backtest(months: int = None, symbols: list = None, start_date: str = None, end_date: str = None,
                  dynamic_exposure: bool = False, starting_cash: float | None = None):
    """
    start_date (YYYY-MM-DD), when given, filters the reported/exported trades to
    that date onward -- months is auto-computed to fetch enough lookback to cover
    it (with a small buffer for HTF/indicator warmup) unless months is also given.
    end_date (YYYY-MM-DD, inclusive) stops the simulation early, e.g. for a holdout window.
    dynamic_exposure: backtest-only experiment, see _apply_portfolio_rules' docstring
    and the STARTING_EQUITY/DYNAMIC_EXPOSURE_THRESHOLD/DYNAMIC_EXPOSURE_PCT constants
    above it -- does NOT change the live MAX_OPEN_EXPOSURE config value.
    starting_cash: when given, applies real CASH_PER_TRADE_PCT-based sizing against
    a tracked cash balance (see _apply_portfolio_rules) instead of the default
    behavior of sizing purely off MAX_CONTRACTS_PER_SYMBOL/exposure/position-count. Use this to
    simulate a small account (e.g. the real Start-at-200 balance) where cash is
    actually the binding sizing constraint, not just exposure/contract caps.
    """
    if start_date:
        span_days = (datetime.now(timezone.utc).date() - date.fromisoformat(start_date)).days
        computed  = max(1, min(24, -(-span_days // 31) + 1))  # ceil(days/31) + 1 buffer month
        months    = months or computed

    requested = symbols or SYMBOLS
    skipped   = [s for s in requested if s in _SKIP]
    run_syms  = [s for s in requested if s not in _SKIP]

    if skipped:
        print(f'  Skipping (no equity data): {", ".join(skipped)}')
    if not run_syms:
        print('  No symbols to backtest.')
        return

    range_desc = f'{start_date} to {end_date or "today"}' if start_date else f'{months} month{"s" if months > 1 else ""}'
    print(f'\nBacktest: {range_desc} | {", ".join(run_syms)}\n')

    all_trades: list = []
    for sym in run_syms:
        all_trades.extend(backtest_symbol(sym, months, start_date, end_date))

    if start_date:
        all_trades = [t for t in all_trades if t['date'] >= start_date]
    if end_date:
        all_trades = [t for t in all_trades if t['date'] <= end_date]

    portfolio = _apply_portfolio_rules(all_trades, dynamic_exposure=dynamic_exposure, starting_cash=starting_cash)
    _report(all_trades, months, portfolio)
    _export_trades_csv(all_trades)


if __name__ == '__main__':
    args   = sys.argv[1:]
    dyn    = '--dynamic-exposure' in args
    args   = [a for a in args if a != '--dynamic-exposure']
    cash   = None
    if '--cash' in args:
        i = args.index('--cash')
        try:
            cash = float(args[i + 1])
        except (IndexError, ValueError):
            print('Error: --cash requires a numeric value, e.g. --cash 200')
            sys.exit(1)
        args = args[:i] + args[i + 2:]
    months = 3
    syms   = None
    start  = None
    end    = None
    if args:
        try:
            if '-' in args[0]:  # YYYY-MM-DD start date [YYYY-MM-DD end date]
                start = date.fromisoformat(args[0]).isoformat()
                months = None
                rest = args[1:]
                if rest and '-' in rest[0]:
                    end = date.fromisoformat(rest[0]).isoformat()
                    rest = rest[1:]
                syms = rest or None
            else:
                months = int(args[0])
                if not 1 <= months <= 24:
                    raise ValueError('months must be 1-24')
                syms = args[1:] or None
        except ValueError as e:
            print(f'Error: {e}')
            print('Usage: python backtest.py [1-24 | YYYY-MM-DD [YYYY-MM-DD]] [symbol ...] [--dynamic-exposure] [--cash N]')
            sys.exit(1)
    run_backtest(months, syms, start_date=start, end_date=end, dynamic_exposure=dyn, starting_cash=cash)
