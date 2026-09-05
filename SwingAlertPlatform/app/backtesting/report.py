import json
import os
from datetime import datetime

import matplotlib

matplotlib.use("Agg")  # no display in the container — must be set before importing pyplot
import matplotlib.pyplot as plt

from app.backtesting.engine import TradeResult
from app.backtesting.metrics import BacktestMetrics
from app.config import get_settings


def _safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def generate_report(run_name: str, trades: list[TradeResult], metrics: BacktestMetrics) -> str:
    """Renders an equity-curve/drawdown PNG and a JSON summary alongside it. Returns the PNG path."""
    settings = get_settings()
    os.makedirs(settings.reports_dir, exist_ok=True)

    equity = [settings.backtest_initial_capital]
    for t in trades:
        equity.append(equity[-1] * (1 + t.pnl_pct))

    peak = equity[0]
    drawdown_pct = []
    for v in equity:
        peak = max(peak, v)
        drawdown_pct.append(-(peak - v) / peak * 100 if peak > 0 else 0.0)

    base_filename = f"{_safe_filename(run_name)}_{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    png_path = os.path.join(settings.reports_dir, f"{base_filename}.png")
    json_path = os.path.join(settings.reports_dir, f"{base_filename}.json")

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    ax1.plot(equity, color="tab:blue")
    ax1.set_title(f"{run_name} — Equity Curve")
    ax1.set_ylabel("Equity ($)")

    ax2.fill_between(range(len(drawdown_pct)), drawdown_pct, color="tab:red", alpha=0.5)
    ax2.set_title("Drawdown")
    ax2.set_ylabel("Drawdown (%)")
    ax2.set_xlabel("Trade #")

    fig.tight_layout()
    fig.savefig(png_path)
    plt.close(fig)

    summary = {
        "run_name": run_name,
        "trade_count": len(trades),
        "metrics": {
            "win_rate": metrics.win_rate,
            "cagr": metrics.cagr,
            "sharpe": metrics.sharpe,
            "sortino": metrics.sortino,
            "max_drawdown": metrics.max_drawdown,
            "profit_factor": metrics.profit_factor,
            "avg_hold_time_hours": metrics.avg_hold_time_hours,
        },
    }
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2)

    return png_path
