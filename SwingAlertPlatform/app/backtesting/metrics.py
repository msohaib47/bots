import math
from dataclasses import dataclass
from datetime import datetime

from app.backtesting.engine import TradeResult


@dataclass
class BacktestMetrics:
    win_rate: float | None
    cagr: float | None
    sharpe: float | None
    sortino: float | None
    max_drawdown: float | None
    profit_factor: float | None
    avg_hold_time_hours: float | None


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _max_drawdown(equity: list[float]) -> float:
    peak = equity[0]
    max_dd = 0.0
    for value in equity:
        peak = max(peak, value)
        drawdown = (peak - value) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, drawdown)
    return max_dd


def compute_metrics(
    trades: list[TradeResult],
    start_date: datetime,
    end_date: datetime,
    initial_capital: float = 10000.0,
) -> BacktestMetrics:
    if not trades:
        return BacktestMetrics(None, None, None, None, None, None, None)

    returns = [t.pnl_pct for t in trades]
    win_rate = len([r for r in returns if r > 0]) / len(trades)

    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
    # No losing trades makes the ratio undefined rather than infinite — None keeps the API response JSON-safe.
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    hold_hours = [(t.exit_ts - t.entry_ts).total_seconds() / 3600 for t in trades]
    avg_hold_time_hours = sum(hold_hours) / len(hold_hours)

    # Equity curve: compound trade-by-trade in execution order (one position open at a time).
    equity = [initial_capital]
    for r in returns:
        equity.append(equity[-1] * (1 + r))
    max_drawdown = _max_drawdown(equity)

    # total_seconds() (not .days, which truncates) so sub-day-precision windows still get an accurate CAGR/Sharpe
    years = max((end_date - start_date).total_seconds() / (86400 * 365.25), 1 / 365.25)
    final_equity = equity[-1]
    cagr = (final_equity / initial_capital) ** (1 / years) - 1 if final_equity > 0 else -1.0

    trades_per_year = len(trades) / years
    mean_return = sum(returns) / len(returns)
    std_return = _std(returns)
    sharpe = (mean_return / std_return) * math.sqrt(trades_per_year) if std_return > 0 else None

    downside_returns = [r for r in returns if r < 0]
    downside_std = _std(downside_returns) if len(downside_returns) >= 2 else 0.0
    sortino = (mean_return / downside_std) * math.sqrt(trades_per_year) if downside_std > 0 else None

    return BacktestMetrics(
        win_rate=win_rate,
        cagr=cagr,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_drawdown,
        profit_factor=profit_factor,
        avg_hold_time_hours=avg_hold_time_hours,
    )
