"""
Entry signal generator for RobinhoodDayTradingBot.
Strategy: VWAP + EMA crossover + RSI on 5-minute bars.

Signal:
  BUY   when price > VWAP  AND  EMA9 > EMA21  AND  40 < RSI < 70
  SELL  when price < VWAP  AND  EMA9 < EMA21  AND  30 < RSI < 60
  HOLD  otherwise
"""
import logging
import robinhood

logger = logging.getLogger(__name__)


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
    if not bars:
        return None
    total_pv = sum(((b['h'] + b['l'] + b['c']) / 3) * b['v'] for b in bars)
    total_v  = sum(b['v'] for b in bars)
    return total_pv / total_v if total_v > 0 else None


def get_signal(symbol: str) -> dict:
    """
    Returns:
        {
            'signal':  'BUY' | 'SELL' | 'HOLD',
            'price':   float,
            'vwap':    float,
            'ema9':    float,
            'ema21':   float,
            'rsi':     float,
            'reason':  str,
        }
    """
    result = {'signal': 'HOLD', 'price': None, 'vwap': None,
              'ema9': None, 'ema21': None, 'rsi': None, 'reason': ''}

    bars = robinhood.get_5min_bars(symbol, limit=60)
    if len(bars) < 10:
        result['reason'] = f'Not enough bars ({len(bars)})'
        return result

    closes = [b['c'] for b in bars]
    price  = closes[-1]
    vwap   = _vwap(bars)
    ema9   = _ema(closes, 9)[-1]
    ema21  = _ema(closes, 21)[-1]
    rsi    = _rsi(closes, 14)

    result.update({
        'price': price,
        'vwap':  vwap,
        'ema9':  round(ema9, 4) if ema9 else None,
        'ema21': round(ema21, 4) if ema21 else None,
        'rsi':   rsi,
    })

    if None in (vwap, ema9, ema21, rsi):
        result['reason'] = 'Indicators not ready'
        return result

    if price > vwap and ema9 > ema21 and 40 < rsi < 70:
        result['signal'] = 'BUY'
        result['reason'] = f'price({price:.2f})>VWAP({vwap:.2f}), EMA9>EMA21, RSI={rsi}'
    elif price < vwap and ema9 < ema21 and 30 < rsi < 60:
        result['signal'] = 'SELL'
        result['reason'] = f'price({price:.2f})<VWAP({vwap:.2f}), EMA9<EMA21, RSI={rsi}'
    else:
        result['reason'] = f'No clear signal -- price={price:.2f} VWAP={vwap:.2f} RSI={rsi}'

    return result
