"""
Entry signal generator — VWAP + EMA + RSI + ADX + 15-min trend confirmation,
for 0DTE options. (ChatGPT-Upgrades strategy, replaces the old VWAP/EMA/RSI
+ static HTF filter.)

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
import logging
from alpaca import get_5min_bars, get_recent_bars
from config import MAX_EMA_GAP_ATR

logger = logging.getLogger(__name__)

RSI_CALL_RANGE = (50, 70)  # rsi_call_ok = RSI_CALL_RANGE[0] < rsi < RSI_CALL_RANGE[1]
RSI_PUT_RANGE  = (30, 50)  # rsi_put_ok  = RSI_PUT_RANGE[0] < rsi < RSI_PUT_RANGE[1]
ADX_MIN        = 20
ADX_PERIOD     = 14


# ── Indicator helpers ──────────────────────────────────────────────────────────

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
    """Compute VWAP from open-of-day bars."""
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

    # Wilder smoothing, seeded with a simple sum over the first `period` values
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


def _htf_trend(symbol: str) -> dict:
    """
    Returns {'trend': 'bull'|'bear'|None, 'ema21': float|None, 'slope': float|None}
    for the symbol's own 15-min timeframe. 'bull' requires close > EMA21 AND EMA21
    rising; 'bear' requires close < EMA21 AND EMA21 falling; else trend is None
    (no clear trend or data unavailable). Fetches multi-session bars so this works
    from market open. `slope` is EMA21 - EMA21_prev (raw units), reported even
    when the direction doesn't clear the 'bull'/'bear' bar, for diagnostics.
    """
    out = {'trend': None, 'ema21': None, 'slope': None}
    bars = get_recent_bars(symbol, '15Min', limit=30)
    if len(bars) < 23:
        return out
    closes = [b['c'] for b in bars]
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


# ── Main signal function ───────────────────────────────────────────────────────

def get_signal(symbol: str) -> dict:
    """
    Returns:
        {
            'signal':    'CALL' | 'PUT' | 'NONE',
            'price':     float,
            'vwap':      float,
            'ema9':      float,
            'ema21':     float,
            'rsi':       float,
            'adx':       float,
            'atr':       float,
            'htf_trend': 'bull' | 'bear' | None,
            'htf_ema21': float,   # 15-min EMA21
            'htf_slope': float,   # 15-min EMA21 - EMA21_prev
            'reason':    str,
        }
    """
    result = {
        'signal': 'NONE', 'price': None, 'vwap': None,
        'ema9': None, 'ema21': None, 'rsi': None, 'adx': None, 'atr': None,
        'htf_trend': None, 'htf_ema21': None, 'htf_slope': None, 'reason': '',
    }

    # EMA/RSI/ADX/ATR warm up from prior sessions (like _htf_trend does for 15m) --
    # session-only bars need ~2.4 hours to accumulate the 29 bars ADX needs, which
    # otherwise blocks any signal until ~11:55 ET every single day.
    bars5 = get_recent_bars(symbol, '5Min', limit=60)
    if len(bars5) < 2 * ADX_PERIOD + 1:
        result['reason'] = f'Not enough 5min bars ({len(bars5)})'
        return result

    # VWAP stays session-only (its definition resets each session at the open).
    session_bars5 = get_5min_bars(symbol, limit=60)

    highs   = [b['h'] for b in bars5]
    lows    = [b['l'] for b in bars5]
    closes  = [b['c'] for b in bars5]
    price   = closes[-1]

    vwap   = _vwap(session_bars5)
    ema9s  = _ema(closes, 9)
    ema21s = _ema(closes, 21)
    ema9, ema9_prev   = ema9s[-1], ema9s[-2]
    ema21             = ema21s[-1]
    rsi    = _rsi(closes, 14)
    adx    = _adx(highs, lows, closes, ADX_PERIOD)
    atr    = _atr(highs, lows, closes, 14)

    htf = _htf_trend(symbol)
    htf_trend = htf['trend']

    # How extended the 5-min trend already is, in ATRs. Large gap = we'd be chasing.
    ema_gap_atr = round(abs(ema9 - ema21) / atr, 3) if (ema9 is not None and ema21 is not None and atr) else None

    result.update({
        'price': price,
        'vwap':  vwap,
        'ema9':  round(ema9, 4) if ema9 else None,
        'ema21': round(ema21, 4) if ema21 else None,
        'rsi':   rsi,
        'adx':   adx,
        'atr':   atr,
        'ema_gap_atr': ema_gap_atr,
        'htf_trend': htf_trend,
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
    gap_ok      = ema_gap_atr is None or ema_gap_atr <= MAX_EMA_GAP_ATR   # no ATR -> don't veto

    call_ok = (
        htf_trend == 'bull'
        and price > vwap
        and ema9 > ema21
        and ema9_rising
        and rsi_call_ok
        and adx_ok
        and gap_ok
    )
    put_ok = (
        htf_trend == 'bear'
        and price < vwap
        and ema9 < ema21
        and ema9_falling
        and rsi_put_ok
        and adx_ok
        and gap_ok
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
        if htf_trend is None:
            filters.append('no clear 15m trend')
        if not adx_ok:
            filters.append(f'ADX={adx} <= {ADX_MIN}')
        if not rsi_call_ok and not rsi_put_ok:
            filters.append(f'RSI={rsi} out of range')
        if not gap_ok:
            filters.append(f'EMA gap={ema_gap_atr} ATR > {MAX_EMA_GAP_ATR} (extended)')
        result['reason'] = f'No signal — {", ".join(filters) or "conditions not aligned"} | price={price:.2f} VWAP={vwap:.2f} RSI={rsi} ADX={adx} gap={ema_gap_atr}'

    return result
