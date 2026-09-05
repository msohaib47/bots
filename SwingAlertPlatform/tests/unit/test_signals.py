import numpy as np
import pandas as pd
import pytest

from app.signals.base import Direction
from app.signals.library.ema_cross import Ema1321BearCross, Ema1321BullCross, Ema50200DeathCross, Ema50200GoldenCross
from app.signals.library.macd import MacdAboveZero, MacdBearCross, MacdBullCross
from app.signals.library.price_levels import New52WeekHigh, ResistanceBreakout, SupportBounce
from app.signals.library.relative_strength import RelativeStrengthVsSpy
from app.signals.library.rsi import RsiRecovery, RsiWeakness
from app.signals.library.volatility import VolatilitySqueezeBreakout
from app.signals.library.volume import HighRelativeVolume


def _base_frame(n: int, **overrides) -> pd.DataFrame:
    ts = pd.date_range("2025-01-01", periods=n, freq="D")
    df = pd.DataFrame(
        {
            "ts": ts,
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.0] * n,
            "volume": [1000.0] * n,
            "ema13": [np.nan] * n,
            "ema21": [np.nan] * n,
            "ema50": [np.nan] * n,
            "ema200": [np.nan] * n,
            "rsi14": [np.nan] * n,
            "macd": [np.nan] * n,
            "macd_signal": [np.nan] * n,
            "macd_hist": [np.nan] * n,
            "atr14": [np.nan] * n,
            "rvol": [np.nan] * n,
            "obv": [np.nan] * n,
            "bb_upper": [np.nan] * n,
            "bb_mid": [np.nan] * n,
            "bb_lower": [np.nan] * n,
        }
    )
    for col, values in overrides.items():
        df[col] = values
    return df


@pytest.mark.parametrize(
    "signal_cls, fast_col, slow_col, direction",
    [
        (Ema1321BullCross, "ema13", "ema21", Direction.BULLISH),
        (Ema1321BearCross, "ema13", "ema21", Direction.BEARISH),
        (Ema50200GoldenCross, "ema50", "ema200", Direction.BULLISH),
        (Ema50200DeathCross, "ema50", "ema200", Direction.BEARISH),
    ],
)
def test_ema_cross_fires_on_actual_cross(signal_cls, fast_col, slow_col, direction):
    df = _base_frame(2)
    if direction == Direction.BULLISH:
        df.loc[0, [fast_col, slow_col]] = [9.0, 10.0]
        df.loc[1, [fast_col, slow_col]] = [11.0, 10.0]
    else:
        df.loc[0, [fast_col, slow_col]] = [11.0, 10.0]
        df.loc[1, [fast_col, slow_col]] = [9.0, 10.0]

    result = signal_cls().evaluate(df)

    assert result is not None
    assert result.direction == direction


def test_ema_cross_does_not_fire_without_a_cross():
    df = _base_frame(2)
    df.loc[0, ["ema13", "ema21"]] = [11.0, 10.0]
    df.loc[1, ["ema13", "ema21"]] = [12.0, 10.0]  # stayed above, no cross

    assert Ema1321BullCross().evaluate(df) is None


def test_macd_bull_cross_fires():
    df = _base_frame(2)
    df.loc[0, ["macd", "macd_signal"]] = [-0.5, 0.0]
    df.loc[1, ["macd", "macd_signal"]] = [0.5, 0.0]

    result = MacdBullCross().evaluate(df)
    assert result is not None
    assert result.direction == Direction.BULLISH


def test_macd_bear_cross_fires():
    df = _base_frame(2)
    df.loc[0, ["macd", "macd_signal"]] = [0.5, 0.0]
    df.loc[1, ["macd", "macd_signal"]] = [-0.5, 0.0]

    result = MacdBearCross().evaluate(df)
    assert result is not None
    assert result.direction == Direction.BEARISH


def test_macd_above_zero_fires_bullish_only():
    df = _base_frame(1, macd=[0.3])
    result = MacdAboveZero().evaluate(df)
    assert result is not None
    assert result.direction == Direction.BULLISH

    df_neg = _base_frame(1, macd=[-0.3])
    assert MacdAboveZero().evaluate(df_neg) is None


def test_rsi_recovery_fires_on_cross_above_30():
    df = _base_frame(2, rsi14=[28.0, 32.0])
    result = RsiRecovery().evaluate(df)
    assert result is not None
    assert result.direction == Direction.BULLISH


def test_rsi_weakness_fires_on_cross_below_70():
    df = _base_frame(2, rsi14=[72.0, 68.0])
    result = RsiWeakness().evaluate(df)
    assert result is not None
    assert result.direction == Direction.BEARISH


def test_rsi_recovery_does_not_fire_if_already_above_30():
    df = _base_frame(2, rsi14=[35.0, 40.0])
    assert RsiRecovery().evaluate(df) is None


def test_support_bounce_fires_when_low_touches_support_and_closes_back_above():
    n = 22
    df = _base_frame(n)
    df.loc[: n - 2, "low"] = 95.0  # prior 20 bars form support at 95
    df.loc[n - 1, "low"] = 94.8  # touches just below support
    df.loc[n - 1, "close"] = 96.0  # but closes back above it

    result = SupportBounce().evaluate(df)
    assert result is not None
    assert result.direction == Direction.BULLISH


def test_resistance_breakout_fires_when_close_exceeds_prior_high():
    n = 22
    df = _base_frame(n)
    df.loc[: n - 2, "high"] = 105.0  # prior 20 bars form resistance at 105
    df.loc[n - 1, "close"] = 106.0

    result = ResistanceBreakout().evaluate(df)
    assert result is not None
    assert result.direction == Direction.BULLISH


def test_new_52_week_high_fires_above_prior_max_close():
    n = 30
    df = _base_frame(n)
    df.loc[: n - 2, "high"] = 110.0
    df.loc[n - 1, "close"] = 111.0

    result = New52WeekHigh().evaluate(df)
    assert result is not None
    assert result.direction == Direction.BULLISH


def test_relative_strength_bullish_when_outperforming_spy():
    n = 25
    df = _base_frame(n)
    df["close"] = np.linspace(100, 110, n)  # +10%
    spy_df = _base_frame(n)
    spy_df["close"] = np.linspace(100, 101, n)  # +1%

    result = RelativeStrengthVsSpy().evaluate(df, context={"spy_df": spy_df})
    assert result is not None
    assert result.direction == Direction.BULLISH


def test_relative_strength_returns_none_without_context():
    df = _base_frame(25)
    assert RelativeStrengthVsSpy().evaluate(df, context=None) is None


def test_volatility_squeeze_breakout_fires_above_upper_band():
    n = 23
    df = _base_frame(n)
    df["bb_mid"] = 100.0
    df["bb_upper"] = 102.0  # wide bands for the lookback window (bandwidth 4%)
    df["bb_lower"] = 98.0
    df.loc[n - 2, ["bb_upper", "bb_lower"]] = [100.2, 99.8]  # squeeze right before breakout (bandwidth 0.4%)
    df.loc[n - 1, "close"] = 103.0  # breaks above the (still-wide, pre-squeeze-update) upper band

    result = VolatilitySqueezeBreakout().evaluate(df)
    assert result is not None
    assert result.direction == Direction.BULLISH


def test_high_relative_volume_direction_follows_price_move():
    df = _base_frame(2, rvol=[1.0, 3.0])
    df.loc[0, "close"] = 100.0
    df.loc[1, "close"] = 105.0  # up move on high volume

    result = HighRelativeVolume().evaluate(df)
    assert result is not None
    assert result.direction == Direction.BULLISH


def test_high_relative_volume_does_not_fire_below_threshold():
    df = _base_frame(2, rvol=[1.0, 1.5])
    assert HighRelativeVolume().evaluate(df) is None
