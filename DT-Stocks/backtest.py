#!/usr/bin/env python3
"""
DT-Stocks historical backtest. Replays the same 15m EMA21-trend + 5m
VWAP/EMA9/RSI/ADX signal as DayTradingBot's backtest.py (see signals.py,
copied verbatim), but simulates exits on the STOCK's own price path instead
of an option's -- there's no option contract to look up or price here at
all, since DT-Stocks trades the underlying directly. This is a real
simplification versus DayTradingBot/backtest.py, not a shortcut: the traded
instrument and the priced instrument are the same thing, so there's no
"options price path stands in for a leveraged derivative" step to get right.

Bars come from the shared backtesting-engine/ package (market_data.py) --
5-min bars for signal generation (matching signals.py's live behavior),
1-min bars for the exit-simulation walk (matching the live bot's per-minute
cron tick granularity). Both are independently cached per (symbol,
timeframe) -- see BACKTESTING_ENGINE_PLAN.md.

Usage:
  python backtest.py 2026-01-01 2026-09-07          - date range, all symbols
  python backtest.py 2026-01-01 2026-09-07 SPY QQQ  - date range, specific symbols
  python backtest.py 3                                - 3 months back from today, all symbols
"""
import os
import sys
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
BOTS_ROOT = os.path.dirname(HERE)
ENGINE_DIR = os.path.join(BOTS_ROOT, 'backtesting-engine')
sys.path.insert(0, BOTS_ROOT)
sys.path.insert(0, ENGINE_DIR)
sys.path.insert(0, HERE)

from market_data import get_bars
from config import (SYMBOLS, STOP_LOSS_PCT, PROFIT_TRAIL_TRIGGER, TRAIL_WIGGLE,
                    MAX_SAME_DIRECTION, MAX_DAILY_LOSS_PER_SYMBOL, MAX_DAILY_LOSS_TOTAL,
                    MAX_OPEN_EXPOSURE, EXPOSURE_TOLERANCE_PCT, MAX_POSITION_VALUE,
                    NO_NEW_ENTRY_TIME, FORCE_CLOSE_TIME, MIN_SHARE_PRICE, MAX_EMA_GAP_ATR)
from signals import _ema, _rsi, _vwap, _adx, _atr, RSI_CALL_RANGE, RSI_PUT_RANGE, ADX_MIN, ADX_PERIOD

ET = ZoneInfo('America/New_York')
_ENTRY_SLIP = 0.01   # small assumed stock-fill slippage -- much smaller than options' since spreads are tighter
_MIN_BARS   = 2 * ADX_PERIOD + 1
_NO_ENTRY   = NO_NEW_ENTRY_TIME
_FORCE_CLOSE = FORCE_CLOSE_TIME


# -- Time / grouping helpers (identical to DayTradingBot/backtest.py) -----------

def _et(bar: dict) -> datetime:
    return datetime.fromisoformat(bar['t'].replace('Z', '+00:00')).astimezone(ET)


def _group_by_day(bars: list) -> dict:
    by_day = defaultdict(list)
    for b in bars:
        t = _et(b).strftime('%H:%M')
        if '09:30' <= t <= '16:05':
            by_day[_et(b).strftime('%Y-%m-%d')].append(b)
    return dict(sorted(by_day.items()))


# -- HTF (own 15-min trend) — mirrors signals._htf_trend() ----------------------

def _build_htf_series(bars: list) -> tuple:
    by_day = _group_by_day(bars)
    times, closes = [], []
    for day_bars in by_day.values():
        for i in range(0, len(day_bars) - 2, 3):
            chunk = day_bars[i:i + 3]
            times.append(chunk[-1]['t'])
            closes.append(chunk[-1]['c'])
    return times, closes


def _htf_lookup_factory(bars: list):
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


# -- Signal (mirrors signals.get_signal() exactly) ------------------------------

def _signal(bars: list, session_bars: list, htf: dict) -> dict:
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
    gap_ok      = ema_gap_atr is None or ema_gap_atr <= MAX_EMA_GAP_ATR
    htf_trend   = htf['trend']

    if htf_trend == 'bull' and price > vwap and ema9 > ema21 and ema9_rising and rsi_call_ok and adx_ok and gap_ok:
        out['signal'] = 'CALL'
    elif htf_trend == 'bear' and price < vwap and ema9 < ema21 and ema9_falling and rsi_put_ok and adx_ok and gap_ok:
        out['signal'] = 'PUT'
    return out


# -- Trade simulation (walks the STOCK's own 1-min price path) ------------------

def _simulate_stock(entry_fill: float, side: str, path_1min: list, entry_dt: datetime, day: str) -> dict:
    """
    Walk forward one minute at a time from entry (matching the live bot's
    1-minute cron), applying position_manager.check_and_update_stops'
    side-aware rules: a long's stop trails up below the high, a short's
    stop trails down above the low.
    """
    long_pos = side == 'long'
    shares_basis = 1   # per-share P&L; scaled to actual size in the portfolio pass
    best   = entry_fill
    stop   = entry_fill * (1 - STOP_LOSS_PCT) if long_pos else entry_fill * (1 + STOP_LOSS_PCT)
    trailing = False
    current = entry_fill

    # path_1min: list of {'t':.., 'c':..} 1-min bars for this day, sorted
    idx_by_time = {datetime.fromisoformat(b['t'].replace('Z', '+00:00')): b['c'] for b in path_1min}
    force_dt = datetime.strptime(f'{day} {_FORCE_CLOSE}', '%Y-%m-%d %H:%M').replace(tzinfo=ET).astimezone(timezone.utc)
    t = entry_dt.replace(second=0, microsecond=0) + timedelta(minutes=1)

    times_sorted = sorted(idx_by_time)
    import bisect

    def price_at(when):
        pos = bisect.bisect_right(times_sorted, when)
        return idx_by_time[times_sorted[pos - 1]] if pos else current

    while t <= force_dt:
        current = price_at(t)
        pct_gain = (current - entry_fill) / entry_fill if long_pos else (entry_fill - current) / entry_fill

        if t >= force_dt:
            reason = 'EOD'
            break

        favorable = current > best if long_pos else current < best
        if favorable:
            best = current
            if trailing:
                new_stop = current * (1 - TRAIL_WIGGLE) if long_pos else current * (1 + TRAIL_WIGGLE)
                improves = new_stop > stop if long_pos else new_stop < stop
                if improves:
                    stop = new_stop
        if not trailing and pct_gain >= PROFIT_TRAIL_TRIGGER:
            trailing = True
            stop = current * (1 - TRAIL_WIGGLE) if long_pos else current * (1 + TRAIL_WIGGLE)

        stop_hit = current <= stop if long_pos else current >= stop
        if stop_hit:
            reason = 'Trail' if trailing else 'Stop'
            break
        t += timedelta(minutes=1)
    else:
        reason = 'EOD'
        t = force_dt

    pnl_per_share = (current - entry_fill) if long_pos else (entry_fill - current)
    return {
        'exit': current, 'exit_time': t.strftime('%Y-%m-%dT%H:%M:%SZ'), 'reason': reason,
        'pnl_per_share': pnl_per_share,
        'return_pct': pnl_per_share / entry_fill * 100,
    }


# -- Portfolio pass: cross-symbol / cross-day risk rules ------------------------

def _apply_portfolio_rules(all_trades: list) -> dict:
    order = {s: i for i, s in enumerate(SYMBOLS)}
    cands = sorted((t for t in all_trades if t.get('pnl') is not None),
                   key=lambda t: (t['entry_time'], order.get(t['symbol'], 99)))

    events = []
    open_pos = []   # (exit_time_str, side, cost)
    daily = {'date': None, 'total': 0.0, 'per_symbol': defaultdict(float)}
    cum = peak = 0.0
    cap = MAX_OPEN_EXPOSURE * (1 + EXPOSURE_TOLERANCE_PCT)
    stats = Counter()
    sized_down = 0
    peak_exposure = 0.0

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
        return sum(cost for exit_t, _, cost in open_pos if exit_t > t)

    for tr in cands:
        t = tr['entry_time']
        _realize_through(t)
        if daily['date'] != tr['date']:
            daily['date'], daily['total'] = tr['date'], 0.0
            daily['per_symbol'] = defaultdict(float)
        open_pos[:] = [p for p in open_pos if p[0] > t]

        per_share = tr['entry_fill']
        max_by_value = int(MAX_POSITION_VALUE / per_share)
        exposure = _exposure_at(t)
        room = cap - exposure
        max_by_room = int(room / per_share) if room > 0 else 0
        qty = min(max_by_value, max_by_room)

        skip = None
        if daily['total'] <= -MAX_DAILY_LOSS_TOTAL:
            skip = 'SKIP_DAILY_TOTAL'
        elif daily['per_symbol'][tr['symbol']] <= -MAX_DAILY_LOSS_PER_SYMBOL:
            skip = 'SKIP_DAILY_SYMBOL'
        elif sum(1 for _, s, _ in open_pos if s == tr['side']) >= MAX_SAME_DIRECTION:
            skip = 'SKIP_SAME_DIR'
        elif qty < 1:
            skip = 'SKIP_EXPOSURE'

        if skip:
            tr['skipped_pnl'] = tr['pnl']
            tr['pnl'] = None
            tr['reason'] = skip
            stats[skip] += 1
            continue

        if qty != tr.get('shares', qty):
            tr['shares'] = qty
            tr['pnl'] = tr['pnl_per_share'] * qty
            sized_down += 1

        cost = per_share * qty
        open_pos.append((tr['exit_time'], tr['side'], cost))
        peak_exposure = max(peak_exposure, exposure + cost)
        events.append((tr['exit_time'], tr['date'], tr['symbol'], tr['pnl']))

    _realize_through('9999')
    return {'skips': stats, 'sized_down': sized_down, 'peak_exposure': peak_exposure,
            'final_cum': cum, 'peak': peak}


# -- Per-symbol backtest ---------------------------------------------------------

def backtest_symbol(symbol: str, start_date: str, end_date: str) -> list:
    sys.stdout.write(f'  {symbol:<6}  ')
    sys.stdout.flush()

    lookback_start = (date.fromisoformat(start_date) - timedelta(days=15)).isoformat()
    try:
        bars5 = get_bars(symbol, '5Min', start_date=lookback_start, end_date=end_date, verbose=False)
        bars1 = get_bars(symbol, '1Min', start_date=start_date, end_date=end_date, verbose=False)
    except Exception as e:
        print(f'ERROR: {e}')
        return []

    if not bars5 or not bars1:
        print('no data')
        return []

    by_day5 = _group_by_day(bars5)
    by_day1 = _group_by_day(bars1)
    print(f'{len(bars5):>5} 5m bars  {len(bars1):>6} 1m bars  {len(by_day5):>3} days')

    htf_lookup = _htf_lookup_factory(bars5)

    import bisect
    all_times5 = [b['t'] for b in bars5]

    trades = []
    for day, day_bars in by_day5.items():
        if day < start_date or day > end_date:
            continue
        day_1min = by_day1.get(day, [])
        if not day_1min:
            continue

        for i in range(len(day_bars)):
            if _et(day_bars[i]).strftime('%H:%M') >= _NO_ENTRY:
                break

            session_window = day_bars[:i + 1]
            g_idx = bisect.bisect_right(all_times5, day_bars[i]['t'])
            window = bars5[max(0, g_idx - 60):g_idx]

            htf = htf_lookup(day_bars[i]['t'])
            sig_out = _signal(window, session_window, htf)
            sig = sig_out['signal']
            if sig == 'NONE':
                continue

            entry = day_bars[i]['c']
            if entry < MIN_SHARE_PRICE:
                continue
            entry_time = day_bars[i]['t']
            entry_dt = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
            side = 'long' if sig == 'CALL' else 'short'
            entry_fill = round(entry + _ENTRY_SLIP, 4) if side == 'long' else round(entry - _ENTRY_SLIP, 4)

            result = _simulate_stock(entry_fill, side, day_1min, entry_dt, day)

            trades.append({
                'date': day, 'symbol': symbol, 'side': side,
                'entry': entry, 'entry_fill': entry_fill, 'entry_time': entry_time,
                'rsi': sig_out['rsi'], 'adx': sig_out['adx'], 'atr': sig_out['atr'],
                'ema_gap_atr': sig_out['ema_gap_atr'],
                'ema9': sig_out['ema9'], 'ema21': sig_out['ema21'], 'vwap': sig_out['vwap'],
                'htf_ema21': htf['ema21'], 'htf_slope': htf['slope'],
                'shares': 1, 'pnl_per_share': result['pnl_per_share'],
                'pnl': result['pnl_per_share'], 'return_pct': result['return_pct'],
                **result,
            })
            break   # one entry per symbol per day

    return trades


# -- Report ----------------------------------------------------------------------

def _report(all_trades: list, portfolio: dict = None):
    if not all_trades:
        print('  No trades simulated.\n')
        return

    taken = [t for t in all_trades if t.get('pnl') is not None]
    by_sym = defaultdict(list)
    for t in taken:
        by_sym[t['symbol']].append(t)

    W = 74
    print(f'\n{"="*W}')
    print(f'  DT-Stocks Backtest')
    print(f'  Signal: 15m EMA21 trend + 5m VWAP/EMA9/RSI/ADX (same as DayTradingBot)')
    print(f'{"-"*W}')
    print(f'  {"Symbol":<8} {"N":>5} {"Win%":>6} {"AvgW%":>7} {"AvgL%":>7} '
          f'{"Net%":>7}  {"Stop":>4} {"Trail":>5} {"EOD":>4}')
    print(f'  {"-"*62}')

    total_n, total_wins = 0, 0
    grand_pnl = 0.0
    for sym in sorted(by_sym):
        tt = by_sym[sym]
        wins   = [x for x in tt if x['return_pct'] > 0]
        losses = [x for x in tt if x['return_pct'] <= 0]
        stops  = sum(1 for x in tt if x['reason'] == 'Stop')
        trails = sum(1 for x in tt if x['reason'] == 'Trail')
        eods   = sum(1 for x in tt if x['reason'] == 'EOD')
        n      = len(tt)
        wp     = len(wins) / n * 100 if n else 0
        aw     = sum(x['return_pct'] for x in wins)   / len(wins)   if wins   else 0.0
        al     = sum(x['return_pct'] for x in losses) / len(losses) if losses else 0.0
        net    = sum(x['return_pct'] for x in tt) / n if n else 0.0
        pnl    = sum(x['pnl'] for x in tt)
        grand_pnl += pnl
        print(f'  {sym:<8} {n:>5} {wp:>5.1f}% {aw:>+6.2f}% {al:>+6.2f}% '
              f'{net:>+6.2f}%  {stops:>4} {trails:>5} {eods:>4}   (${pnl:+,.2f})')
        total_n += n
        total_wins += len(wins)

    ow = total_wins / total_n * 100 if total_n else 0
    print(f'  {"-"*62}')
    print(f'  {"TOTAL":<8} {total_n:>5} {ow:>5.1f}%  {"":>6}   {"":>6}  {"":>7}  '
          f'{"":>4} {"":>5} {"":>4}   (${grand_pnl:+,.2f})')

    longs  = sum(1 for t in taken if t['side'] == 'long')
    shorts = sum(1 for t in taken if t['side'] == 'short')
    print(f'\n  Direction:   {longs} LONG  /  {shorts} SHORT')
    print(f'  Data:        {len(all_trades)} signals; '
          f'{len(all_trades) - len(taken)} blocked by risk rules, {len(taken)} taken')
    print(f'  Note:        1 share/trade in the per-symbol scan above -- portfolio-level')
    print(f'               sizing (shares, exposure) applied separately, see below.')

    if portfolio:
        sk = portfolio['skips']
        print(f'\n  Risk rules:  same-direction cap ({MAX_SAME_DIRECTION}) blocked {sk["SKIP_SAME_DIR"]}; '
              f'daily total cap (${MAX_DAILY_LOSS_TOTAL:.0f}) blocked {sk["SKIP_DAILY_TOTAL"]}; '
              f'daily per-symbol cap (${MAX_DAILY_LOSS_PER_SYMBOL:.0f}) blocked {sk["SKIP_DAILY_SYMBOL"]}')
        print(f'  Exposure:    MAX_OPEN_EXPOSURE ${MAX_OPEN_EXPOSURE:,.0f} (+{EXPOSURE_TOLERANCE_PCT:.0%} tolerance): '
              f'peak ${portfolio["peak_exposure"]:,.0f}; {portfolio["sized_down"]} sized, '
              f'{sk["SKIP_EXPOSURE"]} skipped entirely')
        print(f'  P&L (sized): final ${portfolio["final_cum"]:+,.0f}, peak ${portfolio["peak"]:+,.0f}')
    print()


TRADES_CSV = os.path.join(HERE, 'backtest_trades.csv')
_CSV_COLUMNS = ['date', 'symbol', 'side', 'entry', 'entry_fill', 'exit', 'shares', 'pnl',
                'return_pct', 'entry_time', 'exit_time', 'reason',
                'rsi', 'adx', 'ema9', 'ema21', 'vwap', 'htf_ema21', 'htf_slope', 'atr', 'ema_gap_atr']


def _r(v, nd=4):
    return round(v, nd) if isinstance(v, (int, float)) else v


def _export_trades_csv(all_trades: list, path: str = TRADES_CSV):
    import csv as _csv
    with open(path, 'w', newline='') as f:
        w = _csv.writer(f)
        w.writerow(_CSV_COLUMNS)
        for t in sorted(all_trades, key=lambda x: (x['date'], x['symbol'])):
            w.writerow([
                t['date'], t['symbol'], t['side'], _r(t['entry'], 2), _r(t['entry_fill'], 2),
                _r(t.get('exit'), 2), t.get('shares'), _r(t.get('pnl'), 2), _r(t.get('return_pct'), 3),
                t['entry_time'], t.get('exit_time', ''), t.get('reason', ''),
                _r(t.get('rsi'), 2), _r(t.get('adx'), 2), _r(t.get('ema9'), 4), _r(t.get('ema21'), 4),
                _r(t.get('vwap'), 4), _r(t.get('htf_ema21'), 4), _r(t.get('htf_slope'), 4),
                _r(t.get('atr'), 4), _r(t.get('ema_gap_atr'), 3),
            ])
    print(f'  Per-trade detail written to {path}\n')


def run_backtest(start_date: str, end_date: str, symbols: list = None):
    run_syms = symbols or SYMBOLS
    print(f'\nDT-Stocks Backtest: {start_date} to {end_date} | {", ".join(run_syms)}\n')

    all_trades = []
    for sym in run_syms:
        all_trades.extend(backtest_symbol(sym, start_date, end_date))

    portfolio = _apply_portfolio_rules(all_trades)
    _report(all_trades, portfolio)
    _export_trades_csv(all_trades)


if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        print('Usage: python backtest.py START END [symbol ...]  |  python backtest.py MONTHS [symbol ...]')
        sys.exit(1)
    if '-' in args[0]:
        start = args[0]
        end = args[1] if len(args) > 1 and '-' in args[1] else datetime.now(timezone.utc).date().isoformat()
        syms = args[2:] if len(args) > 1 and '-' in args[1] else args[1:]
        syms = syms or None
    else:
        months = int(args[0])
        end = datetime.now(timezone.utc).date().isoformat()
        start = (datetime.now(timezone.utc).date() - timedelta(days=months * 31)).isoformat()
        syms = args[1:] or None
    run_backtest(start, end, syms)
