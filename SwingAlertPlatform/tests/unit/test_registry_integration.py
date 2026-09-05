import numpy as np
import pandas as pd

import app.signals  # noqa: F401 — populates SIGNAL_REGISTRY as a side effect of import
from app.indicators.engine import compute_indicators
from app.scoring.engine import SCORE_MAX, SCORE_MIN, score_signals
from app.signals.registry import SIGNAL_REGISTRY, evaluate_all


def test_all_15_spec_signals_are_registered():
    expected = {
        "EMA13/21 Bull Cross",
        "EMA13/21 Bear Cross",
        "EMA50/200 Golden Cross",
        "EMA50/200 Death Cross",
        "MACD Bull Cross",
        "MACD Bear Cross",
        "MACD Above Zero",
        "RSI Recovery",
        "RSI Weakness",
        "Support Bounce",
        "Resistance Breakout",
        "New 52 Week High",
        "Relative Strength vs SPY",
        "Volatility Squeeze Breakout",
        "High Relative Volume",
    }
    assert expected <= SIGNAL_REGISTRY.keys()


def test_end_to_end_pipeline_stays_within_score_bounds():
    rng = np.random.default_rng(42)
    n = 260
    ts = pd.date_range("2025-01-01", periods=n, freq="D")
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)))
    df = pd.DataFrame(
        {
            "ts": ts,
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + rng.random(n),
            "low": close - rng.random(n),
            "close": close,
            "volume": rng.integers(1000, 5000, n).astype(float),
        }
    )
    merged = pd.concat([df, compute_indicators(df).drop(columns=["ts"])], axis=1)

    results = evaluate_all(merged, context={"spy_df": df})
    score = score_signals(results)

    assert SCORE_MIN <= score.score <= SCORE_MAX
    assert score.classification in {"Strong Buy", "Buy", "Neutral", "Sell", "Strong Sell"}
