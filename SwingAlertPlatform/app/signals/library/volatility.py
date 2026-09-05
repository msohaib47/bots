import pandas as pd

from app.signals.base import Direction, Signal, SignalResult
from app.signals.registry import register_signal

SQUEEZE_LOOKBACK = 20
SQUEEZE_PERCENTILE = 0.20  # bandwidth must have been in the lowest 20% of the lookback window to count as "squeezed"


@register_signal
class VolatilitySqueezeBreakout(Signal):
    name = "Volatility Squeeze Breakout"
    weight = 2

    def evaluate(self, df, context=None):
        if len(df) < SQUEEZE_LOOKBACK + 2:
            return None
        bandwidth = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
        if bandwidth.iloc[-2:].isna().any():
            return None

        window = bandwidth.iloc[-(SQUEEZE_LOOKBACK + 1) : -1]
        if window.isna().any():
            return None
        was_squeezed = bandwidth.iloc[-2] <= window.quantile(SQUEEZE_PERCENTILE)
        if not was_squeezed:
            return None

        curr = df.iloc[-1]
        if pd.isna(curr["bb_upper"]) or pd.isna(curr["bb_lower"]):
            return None
        if curr["close"] > curr["bb_upper"]:
            return SignalResult(self.name, Direction.BULLISH, self.weight, "Breakout above upper band after a volatility squeeze")
        if curr["close"] < curr["bb_lower"]:
            return SignalResult(self.name, Direction.BEARISH, self.weight, "Breakdown below lower band after a volatility squeeze")
        return None
