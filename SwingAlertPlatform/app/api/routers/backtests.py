from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import BacktestCreate, BacktestRunDetailOut, BacktestRunOut
from app.backtesting.service import execute_backtest
from app.data.timeframes import Timeframe
from app.database.models import BacktestRun
from app.signals.registry import SIGNAL_REGISTRY

router = APIRouter(prefix="/backtests", tags=["backtests"])


@router.post("", response_model=BacktestRunDetailOut, status_code=201)
def create_backtest(payload: BacktestCreate, db: Session = Depends(get_db)):
    if payload.signal_name not in SIGNAL_REGISTRY:
        raise HTTPException(400, f"Unknown signal '{payload.signal_name}'. Valid: {sorted(SIGNAL_REGISTRY.keys())}")
    try:
        timeframe = Timeframe(payload.timeframe)
    except ValueError:
        raise HTTPException(400, f"Unknown timeframe '{payload.timeframe}'")

    run = execute_backtest(
        db,
        name=payload.name,
        signal_name=payload.signal_name,
        symbol=payload.symbol.upper(),
        timeframe=timeframe,
        start_date=payload.start_date,
        end_date=payload.end_date,
        stop_loss_pct=payload.stop_loss_pct,
        profit_target_pct=payload.profit_target_pct,
        max_hold_bars=payload.max_hold_bars,
    )
    return run


@router.get("", response_model=list[BacktestRunOut])
def list_backtests(db: Session = Depends(get_db)):
    return db.query(BacktestRun).order_by(BacktestRun.created_at.desc()).all()


@router.get("/{run_id}", response_model=BacktestRunDetailOut)
def get_backtest(run_id: int, db: Session = Depends(get_db)):
    run = db.get(BacktestRun, run_id)
    if not run:
        raise HTTPException(404, "Backtest run not found")
    return run
