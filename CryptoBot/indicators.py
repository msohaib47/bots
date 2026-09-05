# Technical indicators: EMA, RSI, signal generation.


def ema(prices, period):
    if len(prices) < period:
        return [None] * len(prices)
    k = 2 / (period + 1)
    result = [None] * (period - 1)
    sma = sum(prices[:period]) / period
    result.append(sma)
    for price in prices[period:]:
        result.append(result[-1] * (1 - k) + price * k)
    return result


def rsi(prices, period=14):
    if len(prices) < period + 1:
        return [None] * len(prices)
    result = [None] * period
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def calc(ag, al):
        if al == 0:
            return 100.0
        return 100 - (100 / (1 + ag / al))

    result.append(calc(avg_gain, avg_loss))
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        result.append(calc(avg_gain, avg_loss))
    return result


def compute_signals(bars, ema_fast, ema_slow, rsi_period, rsi_buy_max, rsi_sell_min, bars_1h=None):
    closes = [float(b['c']) for b in bars]
    ema_f = ema(closes, ema_fast)
    ema_s = ema(closes, ema_slow)
    rsi_v = rsi(closes, rsi_period)

    def last_two(lst):
        vals = [v for v in lst if v is not None]
        return (vals[-2], vals[-1]) if len(vals) >= 2 else (None, None)

    ef_prev, ef_curr = last_two(ema_f)
    es_prev, es_curr = last_two(ema_s)
    rsi_curr = next((v for v in reversed(rsi_v) if v is not None), None)
    price = closes[-1]

    # 1H RSI for entry timing — only block BUY if data is available
    rsi_1h = None
    if bars_1h and len(bars_1h) >= rsi_period + 1:
        closes_1h = [float(b['c']) for b in bars_1h]
        rsi_1h_series = rsi(closes_1h, rsi_period)
        rsi_1h = next((v for v in reversed(rsi_1h_series) if v is not None), None)
        if rsi_1h is not None:
            rsi_1h = round(rsi_1h, 2)

    if None in (ef_prev, ef_curr, es_prev, es_curr, rsi_curr):
        return {'signal': 'HOLD', 'price': price, 'ema_fast': ef_curr,
                'ema_slow': es_curr, 'rsi': rsi_curr, 'rsi_1h': rsi_1h, 'cross': None}

    was_above = ef_prev > es_prev
    is_above  = ef_curr > es_curr
    cross = None
    if not was_above and is_above:
        cross = 'golden'
    elif was_above and not is_above:
        cross = 'death'

    uptrend   = ef_curr > es_curr
    downtrend = ef_curr < es_curr

    # 1H RSI < 50 = intraday not overbought; fall back to allowing entry if 1H data unavailable
    entry_ok = (rsi_1h is None) or (rsi_1h < 50)

    if uptrend and rsi_curr < rsi_buy_max and entry_ok:
        signal = 'BUY'
    elif downtrend or rsi_curr > rsi_sell_min:
        signal = 'SELL'
    else:
        signal = 'HOLD'

    return {
        'signal':   signal,
        'price':    price,
        'ema_fast': round(ef_curr, 2),
        'ema_slow': round(es_curr, 2),
        'rsi':      round(rsi_curr, 2),
        'rsi_1h':   rsi_1h,
        'cross':    cross,
    }
