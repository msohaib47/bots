"""
Pure signal/indicator math -- copied unchanged from DayTradingBot/signals.py
(v1), restructured to accept in-memory bar lists directly (mirrors how
DayTradingBot/backtest.py already decouples this same math from REST
fetching) instead of calling out to Alpaca itself. The Signal service's
bar_engine.py maintains the rolling buffers this operates on; live prices
come from the WebSocket stream, not from these functions making API calls.

Signal:
  CALL  when ALL of:
    15m: close > EMA21  AND  EMA21 rising
    5m:  price > VWAP
         EMA9 > EMA21
         EMA9 rising
         50 < RSI(14) < 70
         ADX(14) > 20
  PUT   when ALL of:
    15m: close < EMA21  AND  EMA21 falling
    5m:  price < VWAP
         EMA9 < EMA21
         EMA9 falling
         30 < RSI(14) < 50
         ADX(14) > 20
  NONE  otherwise (no trade)
"""
from config import MAX_EMA_GAP_ATR

RSI_CALL_RANGE = (50, 70)
RSI_PUT_RANGE  = (30, 50)
ADX_MIN        = 20
ADX_PERIOD     = 14
MIN_BARS       = 2 * ADX_PERIOD + 1  # bars needed for EMA21 + RSI14 + ADX14 warmup


# ── Indicator helpers (identical to signals.py v1) ──────────────────────────

def _ema(prices: list, period: int) -> list:
    if len(prices) < period:
        return [None] * len(prices)
    k = 2 / (period + 1)
    result = [None] * (period - 1)
    sma = sum(prices[:period]) / period
    result.append(sma)
    for p in prices[period:]:
        result.append(result[-1] * (1 - k) + p * k)
    return result


def _rsi(prices: list, period: int = 14) -> float | None:
    if len(prices) < period + 1:
        return None
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains  = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return round(100 - (100 / (1 + ag / al)), 2)


def _vwap(bars: list) -> float | None:
    """Compute VWAP from open-of-day (session-only) bars."""
    if not bars:
        return None
    total_pv = sum(((b['h'] + b['l'] + b['c']) / 3) * b['v'] for b in bars)
    total_v  = sum(b['v'] for b in bars)
    return total_pv / total_v if total_v > 0 else None


def _adx(highs: list, lows: list, closes: list, period: int = ADX_PERIOD) -> float | None:
    """Wilder's ADX. Needs at least 2*period+1 bars to produce a stable value."""
    n = len(closes)
    if n < 2 * period + 1:
        return None

    tr_list, plus_dm, minus_dm = [], [], []
    for i in range(1, n):
        up_move   = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
        tr_list.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))

    atr        = sum(tr_list[:period])
    plus_dm_s  = sum(plus_dm[:period])
    minus_dm_s = sum(minus_dm[:period])

    dx_list = []
    for i in range(period, len(tr_list)):
        atr        = atr - (atr / period) + tr_list[i]
        plus_dm_s  = plus_dm_s - (plus_dm_s / period) + plus_dm[i]
        minus_dm_s = minus_dm_s - (minus_dm_s / period) + minus_dm[i]

        plus_di  = 100 * (plus_dm_s / atr) if atr else 0
        minus_di = 100 * (minus_dm_s / atr) if atr else 0
        di_sum   = plus_di + minus_di
        dx = 100 * abs(plus_di - minus_di) / di_sum if di_sum else 0
        dx_list.append(dx)

    if len(dx_list) < period:
        return None
    adx = sum(dx_list[:period]) / period
    for dx in dx_list[period:]:
        adx = (adx * (period - 1) + dx) / period
    return round(adx, 2)


def _atr(highs: list, lows: list, closes: list, period: int = 14) -> float | None:
    """Wilder's ATR. Needs at least period+1 bars."""
    n = len(closes)
    if n < period + 1:
        return None
    tr_list = []
    for i in range(1, n):
        tr_list.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    atr = sum(tr_list[:period]) / period
    for tr in tr_list[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 4)


def htf_trend(bars_15m: list) -> dict:
    """
    Returns {'trend': 'bull'|'bear'|None, 'ema21': float|None, 'slope': float|None}
    given a list of 15-minute bars (most recent last).
    """
    out = {'trend': None, 'ema21': None, 'slope': None}
    if len(bars_15m) < 23:
        return out
    closes = [b['c'] for b in bars_15m]
    ema21s = _ema(closes, 21)
    ema21, ema21_prev = ema21s[-1], ema21s[-2]
    if ema21 is None or ema21_prev is None:
        return out
    out['ema21'] = round(ema21, 4)
    out['slope'] = round(ema21 - ema21_prev, 4)
    if closes[-1] > ema21 and ema21 > ema21_prev:
        out['trend'] = 'bull'
    elif closes[-1] < ema21 and ema21 < ema21_prev:
        out['trend'] = 'bear'
    return out


# ── Main signal function ─────────────────────────────────────────────────────

def compute_signal(bars_5m: list, session_bars_5m: list, bars_15m: list) -> dict:
    """
    `bars_5m`: multi-session rolling window (most recent last) -- EMA/RSI/ADX/ATR
               warm up across prior sessions, same as signals.get_signal() v1.
    `session_bars_5m`: today-only 5-min bars, for VWAP (resets each session).
    `bars_15m`: multi-session rolling window of 15-min bars, for the HTF filter.

    Returns the same dict shape as signals.get_signal() v1.
    """
    result = {
        'signal': 'NONE', 'price': None, 'vwap': None,
        'ema9': None, 'ema21': None, 'rsi': None, 'adx': None, 'atr': None,
        'ema_gap_atr': None, 'htf_trend': None, 'htf_ema21': None, 'htf_slope': None,
        'reason': '',
    }

    if len(bars_5m) < MIN_BARS:
        result['reason'] = f'Not enough 5min bars ({len(bars_5m)})'
        return result

    highs  = [b['h'] for b in bars_5m]
    lows   = [b['l'] for b in bars_5m]
    closes = [b['c'] for b in bars_5m]
    price  = closes[-1]

    vwap   = _vwap(session_bars_5m)
    ema9s  = _ema(closes, 9)
    ema21s = _ema(closes, 21)
    ema9, ema9_prev = ema9s[-1], ema9s[-2]
    ema21           = ema21s[-1]
    rsi = _rsi(closes, 14)
    adx = _adx(highs, lows, closes, ADX_PERIOD)
    atr = _atr(highs, lows, closes, 14)

    htf = htf_trend(bars_15m)
    trend = htf['trend']

    ema_gap_atr = round(abs(ema9 - ema21) / atr, 3) if (ema9 is not None and ema21 is not None and atr) else None

    result.update({
        'price': price,
        'vwap':  vwap,
        'ema9':  round(ema9, 4) if ema9 is not None else None,
        'ema21': round(ema21, 4) if ema21 is not None else None,
        'rsi':   rsi,
        'adx':   adx,
        'atr':   atr,
        'ema_gap_atr': ema_gap_atr,
        'htf_trend': trend,
        'htf_ema21': htf['ema21'],
        'htf_slope': htf['slope'],
    })

    if None in (vwap, ema9, ema9_prev, ema21, rsi, adx):
        result['reason'] = 'Indicators not ready'
        return result

    ema9_rising  = ema9 > ema9_prev
    ema9_falling = ema9 < ema9_prev

    rsi_call_ok = RSI_CALL_RANGE[0] < rsi < RSI_CALL_RANGE[1]
    rsi_put_ok  = RSI_PUT_RANGE[0] < rsi < RSI_PUT_RANGE[1]
    adx_ok      = adx > ADX_MIN
    gap_ok      = ema_gap_atr is None or ema_gap_atr <= MAX_EMA_GAP_ATR

    call_ok = (
        trend == 'bull' and price > vwap and ema9 > ema21 and ema9_rising
        and rsi_call_ok and adx_ok and gap_ok
    )
    put_ok = (
        trend == 'bear' and price < vwap and ema9 < ema21 and ema9_falling
        and rsi_put_ok and adx_ok and gap_ok
    )

    if call_ok:
        result['signal'] = 'CALL'
        result['reason'] = (
            f'15m bull (EMA21 rising), price({price:.2f})>VWAP({vwap:.2f}), '
            f'EMA9>EMA21 rising, RSI={rsi}, ADX={adx}, gap={ema_gap_atr} ATR'
        )
    elif put_ok:
        result['signal'] = 'PUT'
        result['reason'] = (
            f'15m bear (EMA21 falling), price({price:.2f})<VWAP({vwap:.2f}), '
            f'EMA9<EMA21 falling, RSI={rsi}, ADX={adx}, gap={ema_gap_atr} ATR'
        )
    else:
        filters = []
        if trend is None:
            filters.append('no clear 15m trend')
        if not adx_ok:
            filters.append(f'ADX={adx} <= {ADX_MIN}')
        if not rsi_call_ok and not rsi_put_ok:
            filters.append(f'RSI={rsi} out of range')
        if not gap_ok:
            filters.append(f'EMA gap={ema_gap_atr} ATR > {MAX_EMA_GAP_ATR} (extended)')
        result['reason'] = (
            f'No signal - {", ".join(filters) or "conditions not aligned"} | '
            f'price={price:.2f} VWAP={vwap:.2f} RSI={rsi} ADX={adx} gap={ema_gap_atr}'
        )

    return result
