from datetime import datetime, timedelta, timezone

from app.alerts.dedup import is_in_cooldown, make_dedup_key
from app.database.models import Alert


def _make_alert(dedup_key: str, created_at: datetime) -> Alert:
    return Alert(
        symbol="SPY",
        timeframe="1Day",
        ts=created_at,
        score=8,
        classification="Strong Buy",
        signals_fired=[],
        price=500.0,
        dedup_key=dedup_key,
        created_at=created_at,
    )


def test_make_dedup_key_is_stable_for_same_inputs():
    key1 = make_dedup_key("SPY", "1Day", "Strong Buy")
    key2 = make_dedup_key("SPY", "1Day", "Strong Buy")
    assert key1 == key2


def test_make_dedup_key_differs_by_classification():
    assert make_dedup_key("SPY", "1Day", "Buy") != make_dedup_key("SPY", "1Day", "Strong Buy")


def test_not_in_cooldown_when_no_prior_alert(db_session):
    assert is_in_cooldown(db_session, "SPY:1Day:Strong Buy") is False


def test_in_cooldown_right_after_an_alert(db_session):
    key = make_dedup_key("SPY", "1Day", "Strong Buy")
    db_session.add(_make_alert(key, datetime.now(timezone.utc)))
    db_session.commit()

    assert is_in_cooldown(db_session, key) is True


def test_not_in_cooldown_after_window_expires(db_session):
    key = make_dedup_key("SPY", "1Day", "Strong Buy")
    old_alert_time = datetime.now(timezone.utc) - timedelta(minutes=241)  # just past the 240-min default cooldown
    db_session.add(_make_alert(key, old_alert_time))
    db_session.commit()

    assert is_in_cooldown(db_session, key) is False


def test_cooldown_is_scoped_to_the_exact_dedup_key(db_session):
    db_session.add(_make_alert(make_dedup_key("SPY", "1Day", "Strong Buy"), datetime.now(timezone.utc)))
    db_session.commit()

    assert is_in_cooldown(db_session, make_dedup_key("SPY", "1Day", "Buy")) is False
    assert is_in_cooldown(db_session, make_dedup_key("QQQ", "1Day", "Strong Buy")) is False
