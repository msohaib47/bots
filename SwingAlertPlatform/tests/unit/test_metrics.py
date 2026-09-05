from datetime import datetime, timedelta, timezone

import pytest

from app.backtesting.engine import TradeResult
from app.backtesting.metrics import compute_metrics

START = datetime(2020, 1, 1, tzinfo=timezone.utc)
END = START + timedelta(days=365.25)  # exactly 1.0 year, so CAGR == total return


def _trade(entry_price, exit_price, hold_hours) -> TradeResult:
    entry_ts = START
    exit_ts = entry_ts + timedelta(hours=hold_hours)
    return TradeResult(
        symbol="TEST", entry_ts=entry_ts, entry_price=entry_price, exit_ts=exit_ts,
        exit_price=exit_price, exit_reason="signal_exit", hold_bars=1,
    )


def _three_trades() -> list[TradeResult]:
    # +10% (win, 10 days), -10% (loss, 5 days), -5% (loss, 3 days) — entry always at 100
    # so dollar pnl equals the percentage directly, keeping gross profit/loss easy to hand-check.
    return [
        _trade(100, 110, 240),
        _trade(100, 90, 120),
        _trade(100, 95, 72),
    ]


def test_win_rate_and_profit_factor():
    metrics = compute_metrics(_three_trades(), START, END)
    assert metrics.win_rate == pytest.approx(1 / 3)
    assert metrics.profit_factor == pytest.approx(10 / 15)  # gross profit 10 vs gross loss 15


def test_average_hold_time_hours():
    metrics = compute_metrics(_three_trades(), START, END)
    assert metrics.avg_hold_time_hours == pytest.approx(144.0)  # (240+120+72)/3


def test_cagr_matches_compounded_equity_over_exactly_one_year():
    # equity: 10000 -> 11000 -> 9900 -> 9405; over exactly 1 year CAGR == total return
    metrics = compute_metrics(_three_trades(), START, END, initial_capital=10000.0)
    assert metrics.cagr == pytest.approx(-0.0595)


def test_max_drawdown_from_the_post_win_peak():
    # peak hits 11000 after trade 1; final equity 9405 -> drawdown (11000-9405)/11000
    metrics = compute_metrics(_three_trades(), START, END)
    assert metrics.max_drawdown == pytest.approx(0.145)


def test_sharpe_and_sortino_match_hand_calculated_values():
    metrics = compute_metrics(_three_trades(), START, END)
    assert metrics.sharpe == pytest.approx(-0.2773500981126145, rel=1e-9)
    assert metrics.sortino == pytest.approx(-0.8164965809277258, rel=1e-9)


def test_all_winning_trades_gives_none_profit_factor_not_infinity():
    trades = [_trade(100, 110, 24), _trade(100, 105, 24)]
    metrics = compute_metrics(trades, START, END)
    assert metrics.profit_factor is None


def test_no_variance_in_returns_gives_none_sharpe():
    trades = [_trade(100, 110, 24), _trade(100, 110, 24)]  # identical returns -> zero std
    metrics = compute_metrics(trades, START, END)
    assert metrics.sharpe is None


def test_no_losing_trades_gives_none_sortino():
    trades = [_trade(100, 110, 24), _trade(100, 105, 24)]
    metrics = compute_metrics(trades, START, END)
    assert metrics.sortino is None


def test_empty_trade_list_returns_all_none():
    metrics = compute_metrics([], START, END)
    assert metrics.win_rate is None
    assert metrics.cagr is None
    assert metrics.sharpe is None
    assert metrics.sortino is None
    assert metrics.max_drawdown is None
    assert metrics.profit_factor is None
    assert metrics.avg_hold_time_hours is None
