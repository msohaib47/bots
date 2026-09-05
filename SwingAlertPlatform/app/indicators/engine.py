import logging

import numpy as np
import pandas as pd
import pandas_ta as ta
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data.timeframes import Timeframe
from app.database.models import IndicatorValue, PriceBar

logger = logging.getLogger(__name__)

RVOL_PERIOD = 20
MIN_BARS_FOR_EMA200 = 200


def bars_to_frame(bars: list[PriceBar]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts": [b.ts for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        }
    )


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Given an ascending-by-ts OHLCV frame, return one indicator row per input bar.

    Bars without enough history for a given indicator (e.g. the first 200 bars for EMA200)
    get NaN for that column — same convention pandas-ta itself uses.
    """
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]
    all_nan = pd.Series(np.nan, index=df.index)

    # pandas-ta returns None (not a NaN-filled frame) when the series is too short for
    # the indicator's default window — normalize that to all-NaN columns instead.
    macd = ta.macd(close)
    bbands = ta.bbands(close, length=20)
    rvol = volume / volume.rolling(RVOL_PERIOD).mean()

    out = pd.DataFrame({"ts": df["ts"]})
    out["ema13"] = ta.ema(close, length=13)
    out["ema21"] = ta.ema(close, length=21)
    out["ema50"] = ta.ema(close, length=50)
    out["ema200"] = ta.ema(close, length=200)
    out["rsi14"] = ta.rsi(close, length=14)
    out["macd"] = macd["MACD_12_26_9"] if macd is not None else all_nan
    out["macd_signal"] = macd["MACDs_12_26_9"] if macd is not None else all_nan
    out["macd_hist"] = macd["MACDh_12_26_9"] if macd is not None else all_nan
    out["atr14"] = ta.atr(high, low, close, length=14)
    out["rvol"] = rvol
    out["obv"] = ta.obv(close, volume)
    out["bb_upper"] = bbands["BBU_20_2.0_2.0"] if bbands is not None else all_nan
    out["bb_mid"] = bbands["BBM_20_2.0_2.0"] if bbands is not None else all_nan
    out["bb_lower"] = bbands["BBL_20_2.0_2.0"] if bbands is not None else all_nan
    return out


def load_frame(db: Session, symbol: str, timeframe: Timeframe, limit: int | None = None) -> pd.DataFrame:
    """Load cached bars + indicators for (symbol, timeframe) as one ascending-by-ts frame,
    for signal evaluation / backtesting. Assumes sync_bars + sync_indicators have already run."""
    bars = (
        db.execute(
            select(PriceBar)
            .where(PriceBar.symbol == symbol, PriceBar.timeframe == timeframe.value)
            .order_by(PriceBar.ts.asc())
        )
        .scalars()
        .all()
    )
    indicators = (
        db.execute(
            select(IndicatorValue)
            .where(IndicatorValue.symbol == symbol, IndicatorValue.timeframe == timeframe.value)
            .order_by(IndicatorValue.ts.asc())
        )
        .scalars()
        .all()
    )

    bars_df = bars_to_frame(bars)
    indicator_cols = [
        "ema13", "ema21", "ema50", "ema200", "rsi14", "macd", "macd_signal", "macd_hist",
        "atr14", "rvol", "obv", "bb_upper", "bb_mid", "bb_lower",
    ]
    ind_df = pd.DataFrame(
        {"ts": [i.ts for i in indicators]} | {c: [getattr(i, c) for i in indicators] for c in indicator_cols}
    )

    merged = bars_df.merge(ind_df, on="ts", how="left")
    if limit:
        merged = merged.tail(limit).reset_index(drop=True)
    return merged


def sync_indicators(db: Session, symbol: str, timeframe: Timeframe) -> int:
    """Recompute indicators for every cached bar of (symbol, timeframe) and insert rows
    for any bar that doesn't have one yet. Indicator values for a given bar never change
    once enough history exists, so existing rows are left untouched (insert-only, like bars).
    """
    bars = (
        db.execute(
            select(PriceBar)
            .where(PriceBar.symbol == symbol, PriceBar.timeframe == timeframe.value)
            .order_by(PriceBar.ts.asc())
        )
        .scalars()
        .all()
    )
    if not bars:
        return 0

    existing_ts = set(
        db.execute(
            select(IndicatorValue.ts).where(
                IndicatorValue.symbol == symbol, IndicatorValue.timeframe == timeframe.value
            )
        ).scalars()
    )

    df = bars_to_frame(bars)
    computed = compute_indicators(df)

    inserted = 0
    for _, row in computed.iterrows():
        if row["ts"] in existing_ts:
            continue
        values = {k: (None if pd.isna(v) else float(v)) for k, v in row.items() if k != "ts"}
        db.add(IndicatorValue(symbol=symbol, timeframe=timeframe.value, ts=row["ts"], **values))
        try:
            db.flush()
            inserted += 1
            existing_ts.add(row["ts"])
        except IntegrityError:
            db.rollback()
            logger.debug(f"{symbol} {timeframe.value} {row['ts']}: duplicate indicator row, skipped")

    db.commit()
    logger.info(f"{symbol} {timeframe.value}: inserted {inserted} new indicator row(s)")
    return inserted
