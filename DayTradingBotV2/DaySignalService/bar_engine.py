"""
Rolling in-memory bar buffers, fed by the streaming 1-minute bar channel and
aggregated into 5-minute/15-minute windows on demand.

Replaces DayTradingBot v1's REST-polled `get_recent_bars()`/`get_5min_bars()`:
those fetched a fresh multi-session (or session-only) window from Alpaca every
tick; here the window is built once at startup (`seed()`, from one REST call)
and then extended forever by the WebSocket stream (`add_bar()`), with 5m/15m
bars derived from the 1-minute buffer by time-bucketing rather than a second
REST call.

Bucketing note: ET market open (9:30 ET) is always HH:30 in UTC too (13:30 EDT
or 14:30 EST), and :30 is a multiple of 5, so bucketing by UTC minute-of-day
aligns to 5-min/15-min boundaries the same way regardless of DST -- no
timezone conversion needed.
"""
from collections import deque, OrderedDict
from datetime import datetime

MULTI_SESSION_MAXLEN = 1200  # ~20 hours of 1-min bars -- plenty for multi-session EMA/RSI/ADX warmup
SESSION_MAXLEN        = 600   # today's bars only, reset each new session


def _parse_t(t: str) -> datetime:
    return datetime.fromisoformat(t.replace('Z', '+00:00'))


def _normalize_bar(b: dict) -> dict:
    """
    Both the REST bar shape ({'t','o','h','l','c','v', ...}) and the streaming
    JSON bar message from ws_client.py ({'T':'b','S','o','h','l','c','v','t'})
    already carry these same short field names -- one normalizer covers both.
    """
    return {'t': b['t'], 'o': b['o'], 'h': b['h'], 'l': b['l'], 'c': b['c'], 'v': b['v']}


def _bucket_of(t_str: str, n: int) -> datetime:
    dt = _parse_t(t_str)
    return dt.replace(minute=(dt.minute // n) * n, second=0, microsecond=0)


def _aggregate(bars_1m: list, n: int, drop_partial: bool = True) -> list:
    """
    Group 1-min bars into n-minute bars by UTC-minute-of-day bucket (see module note).

    `drop_partial` discards the final bucket, which is still in progress -- its
    high/low/close only reflect the minutes seen so far. DayTradingBot v1
    computes its signal on completed 5-min bars only, so including a partial
    one here made the two engines disagree on the same instant. Callers that
    genuinely want the in-progress bucket can pass False.
    """
    buckets = OrderedDict()
    for b in bars_1m:
        key = _bucket_of(b['t'], n)
        buckets.setdefault(key, []).append(b)

    out = []
    for key, group in buckets.items():
        out.append({
            't': key.isoformat(),
            'o': group[0]['o'],
            'h': max(x['h'] for x in group),
            'l': min(x['l'] for x in group),
            'c': group[-1]['c'],
            'v': sum(x['v'] for x in group),
        })
    if drop_partial and out:
        out = out[:-1]
    return out


class BarEngine:
    """Per-symbol rolling 1-min bar buffers (multi-session + session-only)."""

    def __init__(self, symbols: list):
        self._bars = {s: deque(maxlen=MULTI_SESSION_MAXLEN) for s in symbols}
        self._session_bars = {s: deque(maxlen=SESSION_MAXLEN) for s in symbols}
        self._session_date = {s: None for s in symbols}
        self._last_bucket = {s: None for s in symbols}   # last 5-min bucket seen, for add_bar's rollover test

    def seed(self, symbol: str, multi_bars: list, session_bars: list):
        """One-time REST-fetched cold-start warm-up (see alpaca_market.py)."""
        for b in multi_bars:
            self._bars[symbol].append(_normalize_bar(b))
        for b in session_bars:
            nb = _normalize_bar(b)
            self._session_bars[symbol].append(nb)
            self._session_date[symbol] = nb['t'][:10]

    def add_bar(self, symbol: str, stream_bar: dict) -> bool:
        """
        Feed one streaming 1-min bar (raw JSON dict from ws_client.py). Returns
        True when this bar is the first of a NEW 5-minute bucket, i.e. the
        previous bucket has just closed and it's time to recompute.

        This used to be `len(session_bars) % 5 == 0`, which counts bars
        RECEIVED rather than clock position. The 1-min feed simply has no bar
        for a minute with no trades, so a single gap shifted the phase for the
        rest of the session and every later recompute fired mid-bucket, against
        a partial 5-min bar. Measured 2026-09-08 on one QQQ week: only 56% of
        recomputes landed on a completed bucket, which is why v2 and v1
        disagreed on the signal at the same timestamp despite identical bar
        data and identical maths. Keying off the bucket itself is immune to
        gaps.
        """
        b = _normalize_bar(stream_bar)
        self._bars[symbol].append(b)

        bar_date = b['t'][:10]
        if self._session_date[symbol] != bar_date:
            self._session_bars[symbol].clear()
            self._session_date[symbol] = bar_date
            self._last_bucket[symbol] = None
        self._session_bars[symbol].append(b)

        bucket = _bucket_of(b['t'], 5)
        prev = self._last_bucket[symbol]
        self._last_bucket[symbol] = bucket
        return prev is not None and bucket != prev

    def multi_5m(self, symbol: str, limit: int = 60) -> list:
        return _aggregate(list(self._bars[symbol]), 5)[-limit:]

    def session_5m(self, symbol: str, limit: int = 60) -> list:
        return _aggregate(list(self._session_bars[symbol]), 5)[-limit:]

    def multi_15m(self, symbol: str, limit: int = 30) -> list:
        return _aggregate(list(self._bars[symbol]), 15)[-limit:]
