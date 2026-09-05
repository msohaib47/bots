import pandas as pd

from app.signals.base import Signal, SignalResult

SIGNAL_REGISTRY: dict[str, type[Signal]] = {}


def register_signal(cls: type[Signal]) -> type[Signal]:
    SIGNAL_REGISTRY[cls.name] = cls
    return cls


def all_signals() -> list[Signal]:
    return [cls() for cls in SIGNAL_REGISTRY.values()]


def evaluate_all(df: pd.DataFrame, context: dict | None = None) -> list[SignalResult]:
    """Run every registered signal against the last bar of `df`, collecting whichever fire."""
    results = []
    for signal in all_signals():
        result = signal.evaluate(df, context)
        if result is not None:
            results.append(result)
    return results
