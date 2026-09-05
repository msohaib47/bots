from datetime import datetime

import pandas as pd
from sqlalchemy.orm import Session

from app.backtesting.engine import BacktestConfig, run_backtest
from app.backtesting.metrics import compute_metrics
from app.backtesting.report import generate_report
from app.data.timeframes import Timeframe
from app.database.models import BacktestRun, BacktestTrade
from app.indicators.engine import load_frame


def _as_utc_timestamp(value: datetime) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def execute_backtest(
    db: Session,
    name: str,
    signal_name: str,
    symbol: str,
    timeframe: Timeframe,
    start_date: datetime,
    end_date: datetime,
    stop_loss_pct: float = 0.05,
    profit_target_pct: float = 0.10,
    max_hold_bars: int = 20,
) -> BacktestRun:
    df = load_frame(db, symbol, timeframe)
    # SQLite (used in tests) returns naive datetimes even from DateTime(timezone=True) columns;
    # Postgres (production) returns tz-aware ones — normalize both sides to UTC before comparing.
    ts_col = pd.to_datetime(df["ts"], utc=True)
    start_ts, end_ts = _as_utc_timestamp(start_date), _as_utc_timestamp(end_date)
    df = df[(ts_col >= start_ts) & (ts_col <= end_ts)].reset_index(drop=True)

    config = BacktestConfig(
        signal_name=signal_name,
        symbol=symbol,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        stop_loss_pct=stop_loss_pct,
        profit_target_pct=profit_target_pct,
        max_hold_bars=max_hold_bars,
    )
    trades = run_backtest(df, config)
    metrics = compute_metrics(trades, start_date, end_date)
    report_path = generate_report(name, trades, metrics)

    run = BacktestRun(
        name=name,
        strategy=signal_name,
        symbols=[symbol],
        timeframe=timeframe.value,
        start_date=start_date,
        end_date=end_date,
        params={
            "stop_loss_pct": stop_loss_pct,
            "profit_target_pct": profit_target_pct,
            "max_hold_bars": max_hold_bars,
        },
        win_rate=metrics.win_rate,
        cagr=metrics.cagr,
        sharpe=metrics.sharpe,
        sortino=metrics.sortino,
        max_drawdown=metrics.max_drawdown,
        profit_factor=metrics.profit_factor,
        avg_hold_time_hours=metrics.avg_hold_time_hours,
        report_path=report_path,
    )
    db.add(run)
    db.flush()

    for t in trades:
        db.add(
            BacktestTrade(
                backtest_run_id=run.id,
                symbol=t.symbol,
                entry_ts=t.entry_ts,
                entry_price=t.entry_price,
                exit_ts=t.exit_ts,
                exit_price=t.exit_price,
                pnl=t.pnl,
                pnl_pct=t.pnl_pct,
                hold_time_hours=(t.exit_ts - t.entry_ts).total_seconds() / 3600,
                exit_reason=t.exit_reason,
            )
        )
    db.commit()
    db.refresh(run)
    return run
