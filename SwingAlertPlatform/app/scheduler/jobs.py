import logging

from sqlalchemy.orm import Session

import app.signals  # noqa: F401 — populates SIGNAL_REGISTRY as a side effect of import
from app.alerts.dispatcher import dispatch_alert
from app.config import get_settings
from app.data.alpaca_client import AlpacaDataClient
from app.data.bar_cache import sync_bars
from app.data.timeframes import ALL_TIMEFRAMES, Timeframe
from app.database.base import SessionLocal
from app.indicators.engine import load_frame, sync_indicators
from app.scoring.engine import score_signals
from app.signals.library.price_levels import support_resistance
from app.signals.registry import evaluate_all
from app.users.service import all_active_symbols, users_subscribed_to

logger = logging.getLogger(__name__)


def run_cycle() -> None:
    """The recurring job: sync bars + indicators for every symbol worth tracking, then
    evaluate signals and alert for every symbol someone actually cares about."""
    db = SessionLocal()
    try:
        settings = get_settings()
        client = AlpacaDataClient()

        watched = all_active_symbols(db) | set(settings.default_symbol_list)
        sync_universe = watched | {"SPY"}  # SPY is always synced — every relative-strength check needs it

        logger.info(f"Scheduler cycle: syncing {len(sync_universe)} symbol(s) — {sorted(sync_universe)}")
        for symbol in sync_universe:
            for timeframe in ALL_TIMEFRAMES:
                try:
                    sync_bars(db, client, symbol, timeframe)
                    sync_indicators(db, symbol, timeframe)
                except Exception:
                    logger.exception(f"{symbol} {timeframe.value}: sync failed")

        logger.info(f"Scheduler cycle: evaluating {len(watched)} watched symbol(s)")
        for symbol in watched:
            for timeframe in ALL_TIMEFRAMES:
                try:
                    _evaluate_and_alert(db, symbol, timeframe)
                except Exception:
                    logger.exception(f"{symbol} {timeframe.value}: evaluation failed")
    finally:
        db.close()


def _evaluate_and_alert(db: Session, symbol: str, timeframe: Timeframe) -> None:
    df = load_frame(db, symbol, timeframe)
    if df.empty:
        return

    spy_df = load_frame(db, "SPY", timeframe) if symbol != "SPY" else None
    context = {"spy_df": spy_df} if spy_df is not None and not spy_df.empty else None

    results = evaluate_all(df, context)
    score_result = score_signals(results)
    if score_result.classification == "Neutral":
        return

    support, resistance = support_resistance(df)
    curr = df.iloc[-1]
    subscribed = users_subscribed_to(db, symbol)
    dispatch_alert(
        db, symbol, timeframe.value, curr["ts"], float(curr["close"]), score_result, support, resistance, subscribed
    )
