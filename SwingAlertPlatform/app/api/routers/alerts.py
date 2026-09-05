from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import AlertOut
from app.database.models import Alert

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertOut])
def list_alerts(
    symbol: str | None = None,
    user_id: int | None = None,
    since: datetime | None = None,
    limit: int = Query(50, le=500),
    db: Session = Depends(get_db),
):
    stmt = select(Alert).order_by(Alert.created_at.desc()).limit(limit)
    if symbol:
        stmt = stmt.where(Alert.symbol == symbol.upper())
    if user_id is not None:
        stmt = stmt.where(Alert.user_id == user_id)
    if since is not None:
        stmt = stmt.where(Alert.created_at >= since)
    return db.execute(stmt).scalars().all()
