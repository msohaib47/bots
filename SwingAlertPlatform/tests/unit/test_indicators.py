import numpy as np
import pandas as pd

from app.indicators.engine import MIN_BARS_FOR_EMA200, compute_indicators


def _synthetic_frame(n=260, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2025-01-01", periods=n, freq="D")
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)))
    high = close + rng.random(n)
    low = close - rng.random(n)
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(rng.integers(1000, 5000, n).astype(float))
    return pd.DataFrame({"ts": ts, "open": open_, "high": high, "low": low, "close": close, "volume": volume})


def test_compute_indicators_returns_one_row_per_bar():
    df = _synthetic_frame(n=260)
    out = compute_indicators(df)
    assert len(out) == len(df)
    assert list(out["ts"]) == list(df["ts"])


def test_ema200_is_nan_until_enough_history_then_populated():
    df = _synthetic_frame(n=260)
    out = compute_indicators(df)
    assert out["ema200"].iloc[: MIN_BARS_FOR_EMA200 - 1].isna().all()
    assert out["ema200"].iloc[MIN_BARS_FOR_EMA200:].notna().all()


def test_rsi_stays_within_bounds():
    df = _synthetic_frame(n=260)
    out = compute_indicators(df)
    rsi = out["rsi14"].dropna()
    assert (rsi >= 0).all() and (rsi <= 100).all()


def test_bollinger_bands_are_ordered():
    df = _synthetic_frame(n=260)
    out = compute_indicators(df)
    valid = out.dropna(subset=["bb_upper", "bb_mid", "bb_lower"])
    assert (valid["bb_upper"] >= valid["bb_mid"]).all()
    assert (valid["bb_mid"] >= valid["bb_lower"]).all()


def test_rvol_is_near_one_for_constant_volume():
    df = _synthetic_frame(n=60)
    df["volume"] = 2000.0
    out = compute_indicators(df)
    rvol = out["rvol"].dropna()
    assert np.allclose(rvol, 1.0, atol=1e-9)


def test_obv_increases_on_up_days_and_decreases_on_down_days():
    ts = pd.date_range("2025-01-01", periods=4, freq="D")
    df = pd.DataFrame(
        {
            "ts": ts,
            "open": [100, 101, 100, 99],
            "high": [101, 102, 101, 100],
            "low": [99, 100, 98, 97],
            "close": [100, 101, 99, 98],  # up, down, down
            "volume": [1000.0, 1000.0, 1000.0, 1000.0],
        }
    )
    out = compute_indicators(df)
    obv = out["obv"]
    # obv.iloc[0] is pandas-ta's NaN seed row (no prior close to compare against yet)
    assert pd.isna(obv.iloc[0])
    assert obv.iloc[1] == 1000.0  # up day: +volume
    assert obv.iloc[2] < obv.iloc[1]  # down day: -volume
    assert obv.iloc[3] < obv.iloc[2]  # down day: -volume
