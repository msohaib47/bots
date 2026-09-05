import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.alerts import ntfy_client
from app.alerts.dedup import is_in_cooldown, make_dedup_key
from app.alerts.generator import build_alert_body, build_alert_title
from app.config import get_settings
from app.database.models import Alert, AlertDelivery, User
from app.scoring.engine import ScoreResult

logger = logging.getLogger(__name__)


def dispatch_alert(
    db: Session,
    symbol: str,
    timeframe: str,
    ts: datetime,
    price: float,
    score_result: ScoreResult,
    support: float | None,
    resistance: float | None,
    subscribed_users: list[User],
) -> Alert | None:
    """Persist + deliver an alert for (symbol, timeframe) to the global topic and every
    subscribed user's topic, unless an identical (symbol, timeframe, classification) alert
    already fired within the cooldown window. Returns the persisted Alert, or None if suppressed.
    """
    settings = get_settings()
    dedup_key = make_dedup_key(symbol, timeframe, score_result.classification)
    if is_in_cooldown(db, dedup_key):
        logger.info(f"{symbol} {timeframe}: alert suppressed (cooldown active for {dedup_key})")
        return None

    title = build_alert_title(symbol, score_result)
    body = build_alert_body(symbol, price, score_result, support, resistance, ts)

    alert = Alert(
        user_id=None,  # this Alert row represents the global/shared evaluation; deliveries fan out per-user below
        symbol=symbol,
        timeframe=timeframe,
        ts=ts,
        score=score_result.score,
        classification=score_result.classification,
        signals_fired=[
            {"name": s.name, "direction": s.direction.value, "weight": s.weight, "detail": s.detail}
            for s in score_result.signals
        ],
        price=price,
        support=support,
        resistance=resistance,
        dedup_key=dedup_key,
    )
    db.add(alert)
    db.flush()

    topics = {settings.ntfy_global_topic} | {
        u.ntfy_topic or f"{settings.ntfy_user_topic_prefix}{u.username}" for u in subscribed_users
    }
    for topic in topics:
        success, status_code, error = ntfy_client.send(topic, title, body, score_result.classification)
        db.add(
            AlertDelivery(
                alert_id=alert.id,
                channel="ntfy",
                target_topic=topic,
                status="sent" if success else "failed",
                response_code=status_code,
                error_message=error,
            )
        )

    db.commit()
    logger.info(
        f"{symbol} {timeframe}: alert dispatched to {len(topics)} topic(s) — "
        f"{score_result.classification} ({score_result.score:+d})"
    )
    return alert
