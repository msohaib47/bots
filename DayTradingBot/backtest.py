#!/usr/bin/env python3
"""
DayTradingBot historical backtest.
Replays the VWAP + EMA9/21 + RSI signal on historical 5-min bars.
Uses Alpaca's IEX feed (same data vendor/feed the live bot trades against) --
gives 24+ months of intraday history, vs. yfinance's ~60-day cap, and removes
any discrepancy between backtested and live signal prices.
Bars are cached in cache_alpaca/<SYMBOL>.json; only missing days are fetched.

Usage:
  python backtest.py           - 3 months, all symbols
  python backtest.py 6         - 6 months, all symbols
  python backtest.py 12 SPY QQQ - 12 months, specific symbols only
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alpaca
from config import (SYMBOLS, DATA_URL, STOP_LOSS_PCT, PROFIT_TRAIL_TRIGGER, TRAIL_WIGGLE,
                     USE_VOLUME_FILTER, USE_CROSS_RECENCY_FILTER)
from signals import (_ema, _rsi, _vwap, _recent_cross,
                      EMA_CROSS_LOOKBACK, VOL_AVG_PERIOD,
                      RSI_CALL_RANGE, RSI_PUT_RANGE)

# Local copies, not direct references -- mirror config.py's live values by default
# (so a plain `python backtest.py` run matches live behavior), but ad-hoc ablation
# scripts can flip these two module-level flags to explore filter combinations
# without touching production .env/config.py:
#   import backtest; backtest.BT_USE_VOLUME_FILTER = True
BT_USE_VOLUME_FILTER        = USE_VOLUME_FILTER
BT_USE_CROSS_RECENCY_FILTER = USE_CROSS_RECENCY_FILTER

ET       = ZoneInfo('America/New_York')
# Separate from the old yfinance cache (cache/) -- different vendor, don't mix bar sources.
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache_alpaca')

# Symbols Alpaca's IEX feed doesn't serve as equities
_SKIP = {'SPX'}

# Underlying-price thresholds that approximate the bot's options stop/trail config
# (config.py's STOP_LOSS_PCT/PROFIT_TRAIL_TRIGGER/TRAIL_WIGGLE, all option-premium %).
# There's no historical option-premium series to simulate against directly, so these
# ratios (empirically calibrated once, see 2026-07 backtest notes) convert an option-%
# threshold into its underlying-move equivalent. Derived from config.py so they can't
# drift out of sync with the live bot's actual settings again.
_STOP_RATIO   = 0.05   # STOP_LOSS_PCT        30% -> 1.5% underlying
_TRAIL_RATIO  = 0.04   # PROFIT_TRAIL_TRIGGER 50% -> 2.0% underlying
_WIGGLE_RATIO = 0.07   # TRAIL_WIGGLE         10% -> 0.7% underlying
_STOP    = STOP_LOSS_PCT        * _STOP_RATIO
_TRAIL   = PROFIT_TRAIL_TRIGGER * _TRAIL_RATIO
_WIGGLE  = TRAIL_WIGGLE         * _WIGGLE_RATIO

_LEVERAGE    = 4.0     # estimated 4x for ATM 0DTE (display only)
_NO_ENTRY    = '15:45'
_FORCE_CLOSE = '15:50'
_MIN_BARS    = 25      # bars needed for EMA21 + RSI14 warmup
_PAGE_LIMIT  = 10000   # Alpaca max bars per page


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


# -- HTF (own 15-min trend) — mirrors signals._htf_trend_bullish() -------------

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
    """Returns a function: cutoff timestamp -> bool|None (own 15-min trend as of that time)."""
    import bisect
    times, closes = _build_htf_series(bars)

    def lookup(cutoff_t: str) -> bool | None:
        idx = bisect.bisect_right(times, cutoff_t)
        window = closes[max(0, idx - 30):idx]
        if len(window) < 22:
            return None
        ema21 = _ema(window, 21)[-1]
        if ema21 is None:
            return None
        return window[-1] > ema21

    return lookup


# -- Signal (mirrors signals.get_signal() exactly, using signals.py's own helpers) --

def _signal(bars: list, htf_bullish: bool | None) -> str:
    if len(bars) < _MIN_BARS:
        return 'NONE'
    closes  = [b['c'] for b in bars]
    volumes = [b['v'] for b in bars]

    vwap   = _vwap(bars)
    ema9s  = _ema(closes, 9)
    ema21s = _ema(closes, 21)
    ema9, ema21 = ema9s[-1], ema21s[-1]
    rsi    = _rsi(closes, 14)
    if None in (vwap, ema9, ema21, rsi):
        return 'NONE'
    price = closes[-1]

    vol_avg = sum(volumes[-VOL_AVG_PERIOD - 1:-1]) / VOL_AVG_PERIOD if len(volumes) >= VOL_AVG_PERIOD + 1 else None
    vol_ok  = volumes[-1] > vol_avg if vol_avg else False
    vol_gate = vol_ok if BT_USE_VOLUME_FILTER else True

    bull_cross = _recent_cross(ema9s, ema21s, 'bull', EMA_CROSS_LOOKBACK)
    bear_cross = _recent_cross(ema9s, ema21s, 'bear', EMA_CROSS_LOOKBACK)
    bull_cross_gate = bull_cross if BT_USE_CROSS_RECENCY_FILTER else True
    bear_cross_gate = bear_cross if BT_USE_CROSS_RECENCY_FILTER else True

    rsi_call_ok = RSI_CALL_RANGE[0] < rsi < RSI_CALL_RANGE[1]
    rsi_put_ok  = RSI_PUT_RANGE[0] < rsi < RSI_PUT_RANGE[1]

    if price > vwap and ema9 > ema21 and bull_cross_gate and rsi_call_ok and vol_gate and htf_bullish is True:
        return 'CALL'
    if price < vwap and ema9 < ema21 and bear_cross_gate and rsi_put_ok and vol_gate and htf_bullish is False:
        return 'PUT'
    return 'NONE'


# -- Trade simulation ------------------------------------------------------------

def _simulate(direction: str, entry: float, forward: list) -> dict:
    """Walk forward bars applying stop/trail on the underlying price."""
    best  = entry
    stop  = entry * (1 - _STOP) if direction == 'CALL' else entry * (1 + _STOP)
    trail = False
    exit_p, reason = entry, 'EOD'

    for bar in forward:
        p  = bar['c']
        et = _et(bar).strftime('%H:%M')

        if et >= _FORCE_CLOSE:
            exit_p, reason = p, 'EOD'
            break

        if direction == 'CALL':
            if p > best:
                best = p
                if trail:
                    stop = max(stop, best * (1 - _WIGGLE))
            if not trail and (p - entry) / entry >= _TRAIL:
                trail = True
                stop  = best * (1 - _WIGGLE)
            if p <= stop:
                exit_p, reason = p, 'Trail' if trail else 'Stop'
                break
        else:  # PUT
            if p < best:
                best = p
                if trail:
                    stop = min(stop, best * (1 + _WIGGLE))
            if not trail and (entry - p) / entry >= _TRAIL:
                trail = True
                stop  = best * (1 + _WIGGLE)
            if p >= stop:
                exit_p, reason = p, 'Trail' if trail else 'Stop'
                break
    else:
        exit_p = forward[-1]['c'] if forward else entry
        reason = 'EOD'

    ret = (exit_p - entry) / entry if direction == 'CALL' else (entry - exit_p) / entry
    return {'return': ret, 'reason': reason}


# -- Per-symbol backtest ---------------------------------------------------------

def backtest_symbol(symbol: str, months: int) -> list:
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
    # self-anchored _htf_trend_bullish() -- see 2026-08-10 note in signals.py)
    htf_lookup = _htf_lookup_factory(bars)

    trades = []
    for day, day_bars in by_day.items():
        for i in range(_MIN_BARS, len(day_bars)):
            if _et(day_bars[i]).strftime('%H:%M') >= _NO_ENTRY:
                break

            # Match live bot: uses first 60 bars from open (get_5min_bars limit=60)
            window = day_bars[:min(i + 1, 60)]
            htf_bullish = htf_lookup(day_bars[i]['t'])
            sig = _signal(window, htf_bullish)
            if sig == 'NONE':
                continue

            entry   = day_bars[i]['c']
            forward = day_bars[i + 1:]
            if not forward:
                break

            result = _simulate(sig, entry, forward)
            trades.append({'date': day, 'symbol': symbol, 'direction': sig, 'entry': entry, **result})
            break  # one entry per symbol per day

    return trades


# -- Report ----------------------------------------------------------------------

# Estimated P&L for 1 ATM 0DTE contract: underlying_move * delta * 100 shares
# delta=0.5 (ATM), so: entry_price * return * 0.5 * 100
def _opt_pnl(trade: dict) -> float:
    return trade['entry'] * trade['return'] * 0.5 * 100


def _report(all_trades: list, months: int):
    if not all_trades:
        print('  No trades simulated.\n')
        return

    by_sym = defaultdict(list)
    for t in all_trades:
        by_sym[t['symbol']].append(t)

    W = 74

    # -- % table --
    print(f'\n{"="*W}')
    print(f'  DayTradingBot Backtest - {months} Month{"s" if months > 1 else ""}')
    print(f'  Signal: VWAP + EMA9/21 + RSI  |  Est. options leverage: {_LEVERAGE:.0f}x')
    print(f'{"-"*W}')
    print(f'  {"Symbol":<8} {"N":>5} {"Win%":>6} {"AvgW%":>7} {"AvgL%":>7} '
          f'{"Und%":>7} {"Opt%":>8}  {"Stop":>4} {"Trail":>5} {"EOD":>4}')
    print(f'  {"-"*68}')

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
        opt    = net * _LEVERAGE
        print(f'  {sym:<8} {n:>5} {wp:>5.1f}% {aw:>+6.2f}% {al:>+6.2f}% '
              f'{net:>+6.2f}% {opt:>+7.1f}%  {stops:>4} {trails:>5} {eods:>4}')
        total_n    += n
        total_wins += len(wins)
        total_ret  += sum(x['return'] for x in tt)

    ow = total_wins / total_n * 100 if total_n else 0
    on = total_ret  / total_n * 100 if total_n else 0
    oe = on * _LEVERAGE
    print(f'  {"-"*68}')
    print(f'  {"TOTAL":<8} {total_n:>5} {ow:>5.1f}%  {"":>6}   {"":>6}  {on:>+6.2f}% {oe:>+7.1f}%')

    # -- $ table (1 contract per trade, delta=0.5) --
    print(f'\n  Dollar estimates - 1 ATM contract per trade (delta=0.5, premium ~ 0.5% of price)')
    print(f'  {"Symbol":<8} {"AvgEntry":>10} {"AvgW$":>8} {"AvgL$":>8} {"Avg$/tr":>9} {"Total P&L":>11}')
    print(f'  {"-"*58}')

    grand_pnl = 0.0
    for sym in sorted(by_sym):
        tt     = by_sym[sym]
        wins   = [x for x in tt if x['return'] > 0]
        losses = [x for x in tt if x['return'] <= 0]
        avg_entry = sum(x['entry'] for x in tt) / len(tt)
        avg_win   = sum(_opt_pnl(x) for x in wins)   / len(wins)   if wins   else 0.0
        avg_loss  = sum(_opt_pnl(x) for x in losses) / len(losses) if losses else 0.0
        avg_trade = sum(_opt_pnl(x) for x in tt) / len(tt)
        total     = sum(_opt_pnl(x) for x in tt)
        grand_pnl += total
        print(f'  {sym:<8} {avg_entry:>9.2f}  {avg_win:>+7.2f}  {avg_loss:>+7.2f} '
              f'{avg_trade:>+8.2f}  {total:>+10.2f}')

    print(f'  {"-"*58}')
    print(f'  {"TOTAL":<8} {"":>10}  {"":>8}  {"":>8} {"":>9}  {grand_pnl:>+10.2f}')
    print(f'{"="*W}')

    calls  = sum(1 for t in all_trades if t['direction'] == 'CALL')
    puts   = sum(1 for t in all_trades if t['direction'] == 'PUT')
    stops  = sum(1 for t in all_trades if t['reason'] == 'Stop')
    trails = sum(1 for t in all_trades if t['reason'] == 'Trail')
    eods   = sum(1 for t in all_trades if t['reason'] == 'EOD')

    print(f'\n  Direction:   {calls} CALL  /  {puts} PUT')
    print(f'  Exit:        {stops} stop-loss  /  {trails} trailing-stop  /  {eods} EOD')
    print(f'  Days:        {len(set(t["date"] for t in all_trades))} trading days covered')
    print(f'  $ = entry_price * return * 0.5 * 100  (ATM delta=0.5, 1 contract=100 shares)')
    print()


# -- Entry point -----------------------------------------------------------------

def run_backtest(months: int, symbols: list = None):
    requested = symbols or SYMBOLS
    skipped   = [s for s in requested if s in _SKIP]
    run_syms  = [s for s in requested if s not in _SKIP]

    if skipped:
        print(f'  Skipping (no equity data): {", ".join(skipped)}')
    if not run_syms:
        print('  No symbols to backtest.')
        return

    print(f'\nBacktest: {months} month{"s" if months > 1 else ""} | {", ".join(run_syms)}\n')

    all_trades: list = []
    for sym in run_syms:
        all_trades.extend(backtest_symbol(sym, months))
    _report(all_trades, months)


if __name__ == '__main__':
    args   = sys.argv[1:]
    months = 3
    syms   = None
    if args:
        try:
            months = int(args[0])
            if not 1 <= months <= 24:
                raise ValueError('months must be 1-24')
            syms = args[1:] or None
        except ValueError as e:
            print(f'Error: {e}')
            print('Usage: python backtest.py [1-24] [symbol ...]')
            sys.exit(1)
    run_backtest(months, syms)
