import pandas as pd

from app.signals.base import Direction, Signal, SignalResult
from app.signals.registry import register_signal

RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70


@register_signal
class RsiRecovery(Signal):
    name = "RSI Recovery"
    weight = 1

    def evaluate(self, df, context=None):
        if len(df) < 2:
            return None
        prev, curr = df["rsi14"].iloc[-2], df["rsi14"].iloc[-1]
        if pd.isna(prev) or pd.isna(curr):
            return None
        if prev <= RSI_OVERSOLD < curr:
            return SignalResult(
                self.name, Direction.BULLISH, self.weight,
                f"RSI recovered above {RSI_OVERSOLD} ({prev:.1f} -> {curr:.1f})",
            )
        return None


@register_signal
class RsiWeakness(Signal):
    name = "RSI Weakness"
    weight = 1

    def evaluate(self, df, context=None):
        if len(df) < 2:
            return None
        prev, curr = df["rsi14"].iloc[-2], df["rsi14"].iloc[-1]
        if pd.isna(prev) or pd.isna(curr):
            return None
        if prev >= RSI_OVERBOUGHT > curr:
            return SignalResult(
                self.name, Direction.BEARISH, self.weight,
                f"RSI weakened below {RSI_OVERBOUGHT} ({prev:.1f} -> {curr:.1f})",
            )
        return None
