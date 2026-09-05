from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.backtesting.engine import BacktestConfig, run_backtest
from app.data.timeframes import Timeframe


def _make_df(rows: list[dict]) -> pd.DataFrame:
    ts = [datetime.now(timezone.utc) + timedelta(days=i) for i in range(len(rows))]
    df = pd.DataFrame(rows)
    df.insert(0, "ts", ts)
    df["high"] = df.get("high", df["close"] + 1)
    df["low"] = df.get("low", df["close"] - 1)
    df["volume"] = df.get("volume", 1000.0)
    return df


def _config(**overrides) -> BacktestConfig:
    defaults = dict(
        signal_name="EMA13/21 Bull Cross",
        symbol="TEST",
        timeframe=Timeframe.DAY_1,
        start_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2030, 1, 1, tzinfo=timezone.utc),
        stop_loss_pct=0.05,
        profit_target_pct=0.10,
        max_hold_bars=20,
    )
    defaults.update(overrides)
    return BacktestConfig(**defaults)


def test_entry_executes_at_next_bars_open_not_the_signal_bar():
    df = _make_df(
        [
            {"open": 100, "close": 100, "ema13": 99, "ema21": 100},
            {"open": 100, "close": 100, "ema13": 99, "ema21": 100},
            {"open": 100, "close": 101, "ema13": 105, "ema21": 100},  # bull cross detected here
            {"open": 102, "close": 102, "ema13": 105, "ema21": 100},  # entry executes here, at open
            {"open": 100, "close": 100, "ema13": 105, "ema21": 100},
        ]
    )
    # generous thresholds so nothing exits before the loop ends — isolates entry timing only
    trades = run_backtest(df, _config(stop_loss_pct=0.9, profit_target_pct=0.9, max_hold_bars=100))

    assert len(trades) == 1
    assert trades[0].entry_price == 102  # bar 3's open, not bar 2's close where the cross was detected


def test_stop_loss_exit_executes_at_the_following_bars_open():
    df = _make_df(
        [
            {"open": 100, "close": 100, "ema13": 99, "ema21": 100},
            {"open": 100, "close": 100, "ema13": 99, "ema21": 100},
            {"open": 100, "close": 100, "ema13": 105, "ema21": 100},  # bull cross
            {"open": 100, "close": 100, "ema13": 105, "ema21": 100},  # entry at open=100
            {"open": 100, "close": 90, "ema13": 105, "ema21": 100},  # -10% close breaches -5% stop
            {"open": 89, "close": 89, "ema13": 105, "ema21": 100},  # exit executes here at open
        ]
    )
    trades = run_backtest(df, _config(stop_loss_pct=0.05))

    assert len(trades) == 1
    assert trades[0].exit_reason == "stop_loss"
    assert trades[0].entry_price == 100
    assert trades[0].exit_price == 89
    assert trades[0].hold_bars == 2


def test_profit_target_exit_executes_at_the_following_bars_open():
    df = _make_df(
        [
            {"open": 100, "close": 100, "ema13": 99, "ema21": 100},
            {"open": 100, "close": 100, "ema13": 99, "ema21": 100},
            {"open": 100, "close": 100, "ema13": 105, "ema21": 100},  # bull cross
            {"open": 100, "close": 100, "ema13": 105, "ema21": 100},  # entry at open=100
            {"open": 100, "close": 112, "ema13": 105, "ema21": 100},  # +12% close breaches +10% target
            {"open": 113, "close": 113, "ema13": 105, "ema21": 100},  # exit executes here at open
        ]
    )
    trades = run_backtest(df, _config(profit_target_pct=0.10))

    assert len(trades) == 1
    assert trades[0].exit_reason == "profit_target"
    assert trades[0].exit_price == 113


def test_max_hold_exit_realizes_exactly_max_hold_bars_of_holding_time():
    rows = [{"open": 100, "close": 100, "ema13": 99, "ema21": 100}] * 2
    rows.append({"open": 100, "close": 100, "ema13": 105, "ema21": 100})  # bull cross at index 2
    rows += [{"open": 100, "close": 100, "ema13": 105, "ema21": 100}] * 5  # entry at index 3, holds flat
    df = _make_df(rows)

    trades = run_backtest(df, _config(max_hold_bars=3, stop_loss_pct=0.5, profit_target_pct=0.5))

    assert len(trades) == 1
    assert trades[0].exit_reason == "max_hold"
    assert trades[0].hold_bars == 3


def test_open_position_closes_at_final_bar_when_backtest_ends():
    df = _make_df(
        [
            {"open": 100, "close": 100, "ema13": 99, "ema21": 100},
            {"open": 100, "close": 100, "ema13": 99, "ema21": 100},
            {"open": 100, "close": 100, "ema13": 105, "ema21": 100},  # bull cross
            {"open": 100, "close": 105, "ema13": 105, "ema21": 100},  # entry at open=100
        ]
    )
    trades = run_backtest(df, _config(stop_loss_pct=0.5, profit_target_pct=0.5, max_hold_bars=100))

    assert len(trades) == 1
    assert trades[0].exit_reason == "end_of_backtest"
    assert trades[0].exit_price == 105  # last bar's close, since there's no "next bar" to open at


def test_no_trades_when_signal_never_fires():
    df = _make_df([{"open": 100, "close": 100, "ema13": 100, "ema21": 100}] * 10)
    trades = run_backtest(df, _config())
    assert trades == []


def test_unknown_signal_name_raises():
    df = _make_df([{"open": 100, "close": 100, "ema13": 100, "ema21": 100}] * 3)
    with pytest.raises(ValueError):
        run_backtest(df, _config(signal_name="Not A Real Signal"))


def test_one_directional_signal_never_produces_a_signal_exit_reason():
    """A key design constraint: no Signal subclass can return the opposite direction from what
    it's named for, so 'signal_exit' should never appear as an exit_reason — every trade closes
    via stop_loss, profit_target, max_hold, or end_of_backtest instead."""
    df = _make_df(
        [
            {"open": 100, "close": 100, "ema13": 99, "ema21": 100},
            {"open": 100, "close": 100, "ema13": 99, "ema21": 100},
            {"open": 100, "close": 100, "ema13": 105, "ema21": 100},  # bull cross
        ]
        + [{"open": 100, "close": 100, "ema13": 95, "ema21": 100}] * 5  # would-be "bear cross" afterward
    )
    trades = run_backtest(df, _config(max_hold_bars=3))

    assert all(t.exit_reason != "signal_exit" for t in trades)
