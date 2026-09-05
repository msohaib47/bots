from datetime import datetime, timezone
from unittest.mock import patch

from app.alerts.dispatcher import dispatch_alert
from app.database.models import Alert, AlertDelivery, User
from app.scoring.engine import ScoreResult
from app.signals.base import Direction, SignalResult


def _score_result(score=8, classification="Strong Buy"):
    return ScoreResult(
        score=score,
        classification=classification,
        signals=[SignalResult("EMA50/200 Golden Cross", Direction.BULLISH, 2, "golden cross")],
    )


@patch("app.alerts.dispatcher.ntfy_client.send", return_value=(True, 200, None))
def test_dispatch_creates_alert_and_delivery_rows(mock_send, db_session):
    alert = dispatch_alert(
        db_session, "SPY", "1Day", datetime.now(timezone.utc), 500.0, _score_result(), 490.0, 510.0, []
    )

    assert alert is not None
    assert db_session.query(Alert).count() == 1
    deliveries = db_session.query(AlertDelivery).all()
    assert len(deliveries) == 1  # just the global topic, no subscribed users
    assert deliveries[0].status == "sent"


@patch("app.alerts.dispatcher.ntfy_client.send", return_value=(True, 200, None))
def test_dispatch_delivers_to_global_and_each_subscribed_user(mock_send, db_session):
    user1 = User(username="alice", ntfy_topic="swing-alerts-alice")
    user2 = User(username="bob", ntfy_topic="swing-alerts-bob")
    db_session.add_all([user1, user2])
    db_session.commit()

    dispatch_alert(
        db_session, "SPY", "1Day", datetime.now(timezone.utc), 500.0, _score_result(), 490.0, 510.0, [user1, user2]
    )

    topics = {d.target_topic for d in db_session.query(AlertDelivery).all()}
    assert topics == {"swing-alerts-global", "swing-alerts-alice", "swing-alerts-bob"}


@patch("app.alerts.dispatcher.ntfy_client.send", return_value=(False, 500, "server error"))
def test_dispatch_records_failed_delivery_status(mock_send, db_session):
    dispatch_alert(
        db_session, "SPY", "1Day", datetime.now(timezone.utc), 500.0, _score_result(), 490.0, 510.0, []
    )
    delivery = db_session.query(AlertDelivery).one()
    assert delivery.status == "failed"
    assert delivery.response_code == 500
    assert delivery.error_message == "server error"


@patch("app.alerts.dispatcher.ntfy_client.send", return_value=(True, 200, None))
def test_dispatch_is_suppressed_within_cooldown(mock_send, db_session):
    ts = datetime.now(timezone.utc)
    first = dispatch_alert(db_session, "SPY", "1Day", ts, 500.0, _score_result(), 490.0, 510.0, [])
    second = dispatch_alert(db_session, "SPY", "1Day", ts, 501.0, _score_result(), 490.0, 510.0, [])

    assert first is not None
    assert second is None
    assert db_session.query(Alert).count() == 1
    assert mock_send.call_count == 1  # second call never reached ntfy at all


@patch("app.alerts.dispatcher.ntfy_client.send", return_value=(True, 200, None))
def test_dispatch_not_suppressed_for_a_different_classification(mock_send, db_session):
    ts = datetime.now(timezone.utc)
    dispatch_alert(db_session, "SPY", "1Day", ts, 500.0, _score_result(8, "Strong Buy"), 490.0, 510.0, [])
    second = dispatch_alert(db_session, "SPY", "1Day", ts, 501.0, _score_result(3, "Buy"), 490.0, 510.0, [])

    assert second is not None
    assert db_session.query(Alert).count() == 2
