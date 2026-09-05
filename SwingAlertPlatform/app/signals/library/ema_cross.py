from app.signals._utils import crossed
from app.signals.base import Direction, Signal, SignalResult
from app.signals.registry import register_signal


@register_signal
class Ema1321BullCross(Signal):
    name = "EMA13/21 Bull Cross"
    weight = 1

    def evaluate(self, df, context=None):
        if crossed(df, "ema13", "ema21") == "bull":
            return SignalResult(self.name, Direction.BULLISH, self.weight, "EMA13 crossed above EMA21")
        return None


@register_signal
class Ema1321BearCross(Signal):
    name = "EMA13/21 Bear Cross"
    weight = 1

    def evaluate(self, df, context=None):
        if crossed(df, "ema13", "ema21") == "bear":
            return SignalResult(self.name, Direction.BEARISH, self.weight, "EMA13 crossed below EMA21")
        return None


@register_signal
class Ema50200GoldenCross(Signal):
    name = "EMA50/200 Golden Cross"
    weight = 2

    def evaluate(self, df, context=None):
        if crossed(df, "ema50", "ema200") == "bull":
            return SignalResult(self.name, Direction.BULLISH, self.weight, "EMA50 crossed above EMA200 (golden cross)")
        return None


@register_signal
class Ema50200DeathCross(Signal):
    name = "EMA50/200 Death Cross"
    weight = 2

    def evaluate(self, df, context=None):
        if crossed(df, "ema50", "ema200") == "bear":
            return SignalResult(self.name, Direction.BEARISH, self.weight, "EMA50 crossed below EMA200 (death cross)")
        return None
