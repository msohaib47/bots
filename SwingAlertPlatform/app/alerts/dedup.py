from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import Alert


def make_dedup_key(symbol: str, timeframe: str, classification: str) -> str:
    return f"{symbol}:{timeframe}:{classification}"


def is_in_cooldown(db: Session, dedup_key: str) -> bool:
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.alert_cooldown_minutes)
    stmt = select(Alert.id).where(Alert.dedup_key == dedup_key, Alert.created_at >= cutoff).limit(1)
    return db.execute(stmt).first() is not None
