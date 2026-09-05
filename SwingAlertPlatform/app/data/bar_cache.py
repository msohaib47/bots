import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data.alpaca_client import AlpacaDataClient
from app.data.timeframes import Timeframe
from app.database.models import PriceBar

logger = logging.getLogger(__name__)


def _latest_cached_ts(db: Session, symbol: str, timeframe: Timeframe) -> datetime | None:
    stmt = (
        select(PriceBar.ts)
        .where(PriceBar.symbol == symbol, PriceBar.timeframe == timeframe.value)
        .order_by(PriceBar.ts.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def sync_bars(db: Session, client: AlpacaDataClient, symbol: str, timeframe: Timeframe) -> int:
    """Pull new bars for (symbol, timeframe) since the latest cached bar and append them.

    Returns the number of bars actually inserted (0 if already up to date or nothing came back).
    De-dup is belt-and-suspenders: we only request bars newer than what's cached, and the
    (symbol, timeframe, ts) unique constraint guards against any overlap or race.
    """
    since = _latest_cached_ts(db, symbol, timeframe)
    raw_bars = client.get_bars_since(symbol, timeframe, since)
    if not raw_bars:
        return 0

    # Guard against the API returning the boundary bar again.
    existing_ts = set()
    if since is not None:
        stmt = select(PriceBar.ts).where(
            PriceBar.symbol == symbol,
            PriceBar.timeframe == timeframe.value,
            PriceBar.ts >= since,
        )
        existing_ts = {row[0] for row in db.execute(stmt)}

    inserted = 0
    for bar in raw_bars:
        ts = datetime.fromisoformat(bar["t"].replace("Z", "+00:00")).astimezone(timezone.utc)
        if ts in existing_ts:
            continue
        row = PriceBar(
            symbol=symbol,
            timeframe=timeframe.value,
            ts=ts,
            open=bar["o"],
            high=bar["h"],
            low=bar["l"],
            close=bar["c"],
            volume=bar["v"],
        )
        db.add(row)
        try:
            db.flush()
            inserted += 1
            existing_ts.add(ts)
        except IntegrityError:
            db.rollback()
            logger.debug(f"{symbol} {timeframe.value} {ts}: duplicate bar, skipped")

    db.commit()
    logger.info(f"{symbol} {timeframe.value}: inserted {inserted} new bar(s)")
    return inserted
