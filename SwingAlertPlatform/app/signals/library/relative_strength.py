from app.signals.base import Direction, Signal, SignalResult
from app.signals.registry import register_signal

RS_LOOKBACK = 20
RS_THRESHOLD = 0.02  # 2 percentage points of out/under-performance vs SPY over the lookback


@register_signal
class RelativeStrengthVsSpy(Signal):
    name = "Relative Strength vs SPY"
    weight = 2

    def evaluate(self, df, context=None):
        if not context or context.get("spy_df") is None:
            return None
        spy_df = context["spy_df"]
        if len(df) < RS_LOOKBACK + 1 or len(spy_df) < RS_LOOKBACK + 1:
            return None

        symbol_return = df["close"].iloc[-1] / df["close"].iloc[-(RS_LOOKBACK + 1)] - 1
        spy_return = spy_df["close"].iloc[-1] / spy_df["close"].iloc[-(RS_LOOKBACK + 1)] - 1
        spread = symbol_return - spy_return

        if spread > RS_THRESHOLD:
            return SignalResult(
                self.name, Direction.BULLISH, self.weight,
                f"Outperforming SPY by {spread:+.1%} over {RS_LOOKBACK} bars",
            )
        if spread < -RS_THRESHOLD:
            return SignalResult(
                self.name, Direction.BEARISH, self.weight,
                f"Underperforming SPY by {spread:+.1%} over {RS_LOOKBACK} bars",
            )
        return None
