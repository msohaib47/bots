import pandas as pd

from app.signals.base import Direction, Signal, SignalResult
from app.signals.registry import register_signal

RVOL_THRESHOLD = 2.0


@register_signal
class HighRelativeVolume(Signal):
    name = "High Relative Volume"
    weight = 2

    def evaluate(self, df, context=None):
        if len(df) < 2:
            return None
        curr, prev = df.iloc[-1], df.iloc[-2]
        if pd.isna(curr["rvol"]) or curr["rvol"] < RVOL_THRESHOLD:
            return None
        if curr["close"] > prev["close"]:
            return SignalResult(self.name, Direction.BULLISH, self.weight, f"RVOL={curr['rvol']:.1f}x on an up move")
        if curr["close"] < prev["close"]:
            return SignalResult(self.name, Direction.BEARISH, self.weight, f"RVOL={curr['rvol']:.1f}x on a down move")
        return None
