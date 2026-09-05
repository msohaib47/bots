import pandas as pd

from app.signals._utils import crossed
from app.signals.base import Direction, Signal, SignalResult
from app.signals.registry import register_signal


@register_signal
class MacdBullCross(Signal):
    name = "MACD Bull Cross"
    weight = 1

    def evaluate(self, df, context=None):
        if crossed(df, "macd", "macd_signal") == "bull":
            return SignalResult(self.name, Direction.BULLISH, self.weight, "MACD crossed above its signal line")
        return None


@register_signal
class MacdBearCross(Signal):
    name = "MACD Bear Cross"
    weight = 1

    def evaluate(self, df, context=None):
        if crossed(df, "macd", "macd_signal") == "bear":
            return SignalResult(self.name, Direction.BEARISH, self.weight, "MACD crossed below its signal line")
        return None


@register_signal
class MacdAboveZero(Signal):
    """Trend-confirmation signal — bullish only when true, per spec (no 'MACD Below Zero' counterpart)."""

    name = "MACD Above Zero"
    weight = 1

    def evaluate(self, df, context=None):
        curr = df.iloc[-1]
        if pd.isna(curr["macd"]):
            return None
        if curr["macd"] > 0:
            return SignalResult(self.name, Direction.BULLISH, self.weight, f"MACD={curr['macd']:.3f} above zero")
        return None
