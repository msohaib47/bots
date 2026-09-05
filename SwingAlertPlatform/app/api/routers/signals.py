from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import app.signals  # noqa: F401 — populates SIGNAL_REGISTRY as a side effect of import
from app.api.deps import get_db
from app.api.schemas import SignalEvaluationOut, SignalOut
from app.data.timeframes import Timeframe
from app.indicators.engine import load_frame
from app.scoring.engine import score_signals
from app.signals.library.price_levels import support_resistance
from app.signals.registry import evaluate_all

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("/{symbol}", response_model=SignalEvaluationOut)
def get_signals(symbol: str, timeframe: Timeframe = Timeframe.DAY_1, db: Session = Depends(get_db)):
    symbol = symbol.upper()
    df = load_frame(db, symbol, timeframe)
    if df.empty:
        raise HTTPException(404, f"No cached bars for {symbol} {timeframe.value} yet")

    spy_df = load_frame(db, "SPY", timeframe) if symbol != "SPY" else None
    context = {"spy_df": spy_df} if spy_df is not None and not spy_df.empty else None

    results = evaluate_all(df, context)
    score_result = score_signals(results)
    support, resistance = support_resistance(df)
    curr = df.iloc[-1]

    return SignalEvaluationOut(
        symbol=symbol,
        timeframe=timeframe.value,
        ts=curr["ts"],
        price=float(curr["close"]),
        score=score_result.score,
        classification=score_result.classification,
        signals=[SignalOut(name=s.name, direction=s.direction.value, weight=s.weight, detail=s.detail) for s in results],
        support=support,
        resistance=resistance,
    )
