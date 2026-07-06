"""
Entry signal generator — VWAP + EMA crossover + RSI for 0DTE options.

Signal:
  CALL  when:
    - price > VWAP
    - EMA9 > EMA21  AND  cross happened within last 5 bars (if USE_CROSS_RECENCY_FILTER)
    - 40 < RSI < 70
    - last bar volume > 5-bar avg volume  (if USE_VOLUME_FILTER)
    - SPY 15-min price > SPY 15-min EMA21  (higher-timeframe trend filter)
  PUT   when:
    - price < VWAP
    - EMA9 < EMA21  AND  cross happened within last 5 bars (if USE_CROSS_RECENCY_FILTER)
    - 30 < RSI < 60
    - last bar volume > 5-bar avg volume  (if USE_VOLUME_FILTER)
    - SPY 15-min price < SPY 15-min EMA21  (higher-timeframe trend filter)
  NONE  otherwise (no trade)

  USE_VOLUME_FILTER / USE_CROSS_RECENCY_FILTER (config.py, env-overridable) are
  both off by default -- 2026-07 train/test backtest validation showed both hurt
  out-of-sample P&L. Code stays in place so they can be flipped back on easily.
"""
import logging
from alpaca import get_5min_bars, get_recent_bars, get_latest_price
from config import USE_VOLUME_FILTER, USE_CROSS_RECENCY_FILTER

logger = logging.getLogger(__name__)

HTF_SYMBOL = 'SPY'      # higher-timeframe trend anchor
EMA_CROSS_LOOKBACK = 20  # crossover must be within this many bars (~1.5 hours)
VOL_AVG_PERIOD = 5      # rolling bars for volume check (avoids midday dip vs session avg)
RSI_CALL_RANGE = (40, 70)  # rsi_call_ok = RSI_CALL_RANGE[0] < rsi < RSI_CALL_RANGE[1]
RSI_PUT_RANGE  = (30, 60)  # rsi_put_ok  = RSI_PUT_RANGE[0] < rsi < RSI_PUT_RANGE[1]


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


def _recent_cross(ema9_series: list, ema21_series: list, direction: str, lookback: int) -> bool:
    """
    Returns True if a crossover in `direction` ('bull' or 'bear') occurred
    within the last `lookback` bars.
    """
    pairs = list(zip(ema9_series, ema21_series))
    # Need at least 2 valid points in the window
    window = [(a, b) for a, b in pairs[-(lookback + 1):] if a is not None and b is not None]
    if len(window) < 2:
        return False
    for i in range(1, len(window)):
        prev9, prev21 = window[i - 1]
        cur9,  cur21  = window[i]
        if direction == 'bull' and prev9 <= prev21 and cur9 > cur21:
            return True
        if direction == 'bear' and prev9 >= prev21 and cur9 < cur21:
            return True
    return False


def _htf_trend_bullish() -> bool | None:
    """
    Returns True if SPY's 15-min trend is bullish (price > EMA21),
    False if bearish, None if data unavailable.
    Fetches multi-session bars so this works from market open.
    """
    bars = get_recent_bars(HTF_SYMBOL, '15Min', limit=30)
    if len(bars) < 22:
        return None
    closes = [b['c'] for b in bars]
    ema21  = _ema(closes, 21)[-1]
    if ema21 is None:
        return None
    return closes[-1] > ema21


# ── Main signal function ───────────────────────────────────────────────────────

def get_signal(symbol: str) -> dict:
    """
    Returns:
        {
            'signal':      'CALL' | 'PUT' | 'NONE',
            'price':       float,
            'vwap':        float,
            'ema9':        float,
            'ema21':       float,
            'rsi':         float,
            'htf_bullish': bool | None,
            'vol_ok':      bool,
            'cross_recent': bool,
            'reason':      str,
        }
    """
    result = {
        'signal': 'NONE', 'price': None, 'vwap': None,
        'ema9': None, 'ema21': None, 'rsi': None,
        'htf_bullish': None, 'vol_ok': False, 'cross_recent': False,
        'reason': '',
    }

    bars5 = get_5min_bars(symbol, limit=60)
    if len(bars5) < 22:
        result['reason'] = f'Not enough 5min bars ({len(bars5)})'
        return result

    closes  = [b['c'] for b in bars5]
    volumes = [b['v'] for b in bars5]
    price   = closes[-1]

    vwap   = _vwap(bars5)
    ema9s  = _ema(closes, 9)
    ema21s = _ema(closes, 21)
    ema9   = ema9s[-1]
    ema21  = ema21s[-1]
    rsi    = _rsi(closes, 14)

    # Volume: last bar vs 20-bar average
    vol_avg = sum(volumes[-VOL_AVG_PERIOD - 1:-1]) / VOL_AVG_PERIOD if len(volumes) >= VOL_AVG_PERIOD + 1 else None
    vol_ok  = volumes[-1] > vol_avg if vol_avg else False

    # HTF trend
    htf_bullish = _htf_trend_bullish()

    result.update({
        'price': price,
        'vwap':  vwap,
        'ema9':  round(ema9, 4) if ema9 else None,
        'ema21': round(ema21, 4) if ema21 else None,
        'rsi':   rsi,
        'htf_bullish': htf_bullish,
        'vol_ok': vol_ok,
    })

    if None in (vwap, ema9, ema21, rsi):
        result['reason'] = 'Indicators not ready'
        return result

    above_vwap  = price > vwap
    below_vwap  = price < vwap
    bullish_ema = ema9 > ema21
    bearish_ema = ema9 < ema21

    bull_cross = _recent_cross(ema9s, ema21s, 'bull', EMA_CROSS_LOOKBACK)
    bear_cross = _recent_cross(ema9s, ema21s, 'bear', EMA_CROSS_LOOKBACK)

    # RSI bands widened back from 45-60/40-55 — backtest showed the tighter
    # bands cut trade volume without improving win rate or P&L (see 2026-07-05
    # ablation: widening was strictly better across all three metrics).
    rsi_call_ok = RSI_CALL_RANGE[0] < rsi < RSI_CALL_RANGE[1]
    rsi_put_ok  = RSI_PUT_RANGE[0] < rsi < RSI_PUT_RANGE[1]

    # Volume and cross-recency filters are gated by config toggles (both off by
    # default -- see module docstring). vol_ok/bull_cross/bear_cross are still
    # computed above and reported in `result` either way, for diagnostics.
    vol_gate       = vol_ok if USE_VOLUME_FILTER else True
    bull_cross_gate = bull_cross if USE_CROSS_RECENCY_FILTER else True
    bear_cross_gate = bear_cross if USE_CROSS_RECENCY_FILTER else True

    if above_vwap and bullish_ema and bull_cross_gate and rsi_call_ok and vol_gate and htf_bullish is True:
        result['signal']      = 'CALL'
        result['cross_recent'] = bull_cross
        result['reason'] = (
            f'price({price:.2f})>VWAP({vwap:.2f}), bull cross <{EMA_CROSS_LOOKBACK}bars, '
            f'RSI={rsi}, vol_ok, SPY HTF bullish'
        )
    elif below_vwap and bearish_ema and bear_cross_gate and rsi_put_ok and vol_gate and htf_bullish is False:
        result['signal']      = 'PUT'
        result['cross_recent'] = bear_cross
        result['reason'] = (
            f'price({price:.2f})<VWAP({vwap:.2f}), bear cross <{EMA_CROSS_LOOKBACK}bars, '
            f'RSI={rsi}, vol_ok, SPY HTF bearish'
        )
    else:
        filters = []
        if USE_VOLUME_FILTER and not vol_ok:
            filters.append('low volume')
        if USE_CROSS_RECENCY_FILTER and not bull_cross and not bear_cross:
            filters.append(f'no recent cross (>{EMA_CROSS_LOOKBACK}bars)')
        if not rsi_call_ok and not rsi_put_ok:
            filters.append(f'RSI={rsi} out of range')
        if htf_bullish is None:
            filters.append('HTF data unavailable')
        result['reason'] = f'No signal — {", ".join(filters) or "conditions not aligned"} | price={price:.2f} VWAP={vwap:.2f} RSI={rsi}'

    return result
