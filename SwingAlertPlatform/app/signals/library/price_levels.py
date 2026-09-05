import pandas as pd

from app.signals.base import Direction, Signal, SignalResult
from app.signals.registry import register_signal

SR_LOOKBACK = 20
BOUNCE_TOLERANCE_PCT = 0.01
# ~1 trading year of bars. On sub-daily timeframes this approximates a 252-bar high
# rather than a literal calendar 52 weeks — there's no daily-only fast path here.
NEW_HIGH_LOOKBACK = 252


def support_resistance(df: pd.DataFrame, lookback: int = SR_LOOKBACK) -> tuple[float | None, float | None]:
    """Rolling swing support/resistance from the `lookback` bars *before* the current one,
    so the current bar can be tested against a level it didn't itself contribute to."""
    if len(df) < lookback + 1:
        return None, None
    window = df.iloc[-(lookback + 1) : -1]
    return float(window["low"].min()), float(window["high"].max())


@register_signal
class SupportBounce(Signal):
    name = "Support Bounce"
    weight = 1

    def evaluate(self, df, context=None):
        support, _ = support_resistance(df)
        if support is None:
            return None
        curr = df.iloc[-1]
        touched = curr["low"] <= support * (1 + BOUNCE_TOLERANCE_PCT)
        bounced = curr["close"] > support
        if touched and bounced:
            return SignalResult(self.name, Direction.BULLISH, self.weight, f"Bounced off support ${support:.2f}")
        return None


@register_signal
class ResistanceBreakout(Signal):
    name = "Resistance Breakout"
    weight = 1

    def evaluate(self, df, context=None):
        _, resistance = support_resistance(df)
        if resistance is None:
            return None
        curr = df.iloc[-1]
        if curr["close"] > resistance:
            return SignalResult(self.name, Direction.BULLISH, self.weight, f"Broke above resistance ${resistance:.2f}")
        return None


@register_signal
class New52WeekHigh(Signal):
    name = "New 52 Week High"
    weight = 2

    def evaluate(self, df, context=None):
        if len(df) < 2:
            return None
        window = df.iloc[-(NEW_HIGH_LOOKBACK + 1) : -1] if len(df) > NEW_HIGH_LOOKBACK else df.iloc[:-1]
        if window.empty:
            return None
        prior_high = window["high"].max()
        curr = df.iloc[-1]
        if curr["close"] > prior_high:
            return SignalResult(
                self.name, Direction.BULLISH, self.weight,
                f"New high ${curr['close']:.2f} (prior {NEW_HIGH_LOOKBACK}-bar high ${prior_high:.2f})",
            )
        return None
