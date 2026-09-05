from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.database.models import Alert, IndicatorValue, PriceBar
from app.data.timeframes import Timeframe
from app.scheduler.jobs import _evaluate_and_alert
from app.users import service

N = 25  # comfortably past every signal's lookback (20) except New-52-Week-High's 252, which just uses all-but-last


def _seed_engineered_strong_buy(db, symbol: str, n: int = N):
    """Bars 0..n-2 are flat/neutral. The last bar is engineered so *every* bullish signal
    the framework can detect fires simultaneously — golden cross, bull cross, MACD cross +
    above zero, RSI recovery, resistance breakout, new high, vol-squeeze breakout, high RVOL —
    guaranteeing a deterministic Strong Buy rather than hoping a random walk happens to trigger one.
    """
    ts = [datetime.now(timezone.utc) - timedelta(days=n - i) for i in range(n)]

    for i in range(n):
        last = i == n - 1
        second_last = i == n - 2
        db.add(
            PriceBar(
                symbol=symbol, timeframe="1Day", ts=ts[i],
                open=100.0, high=111.0 if last else 101.0, low=109.0 if last else 99.0,
                close=110.0 if last else 100.0,
                volume=5000.0 if last else 1000.0,
            )
        )
        db.add(
            IndicatorValue(
                symbol=symbol, timeframe="1Day", ts=ts[i],
                ema13=105.0 if last else (99.0 if second_last else 100.0),
                ema21=100.0,
                ema50=105.0 if last else (99.0 if second_last else 100.0),
                ema200=100.0,
                rsi14=35.0 if last else (28.0 if second_last else 50.0),
                macd=0.5 if last else (-0.5 if second_last else 0.0),
                macd_signal=0.0,
                macd_hist=0.0,
                atr14=1.0,
                rvol=3.0 if last else 1.0,
                obv=1000.0,
                bb_upper=101.0,
                bb_mid=100.0,
                bb_lower=99.0,
            )
        )
    db.commit()


@patch("app.alerts.dispatcher.ntfy_client.send", return_value=(True, 200, None))
def test_evaluate_and_alert_dispatches_deterministic_strong_buy(mock_send, db_session):
    _seed_engineered_strong_buy(db_session, "SPY")

    _evaluate_and_alert(db_session, "SPY", Timeframe.DAY_1)

    alert = db_session.query(Alert).one()
    assert alert.classification == "Strong Buy"
    assert alert.score == 12  # clipped — the engineered bar fires well past the +12 ceiling
    fired_names = {s["name"] for s in alert.signals_fired}
    assert "EMA50/200 Golden Cross" in fired_names
    assert "EMA13/21 Bull Cross" in fired_names
    assert "MACD Bull Cross" in fired_names
    assert "RSI Recovery" in fired_names
    mock_send.assert_called()


@patch("app.alerts.dispatcher.ntfy_client.send", return_value=(True, 200, None))
def test_evaluate_and_alert_noop_on_empty_data(mock_send, db_session):
    _evaluate_and_alert(db_session, "NOPE", Timeframe.DAY_1)
    assert db_session.query(Alert).count() == 0
    mock_send.assert_not_called()


@patch("app.alerts.dispatcher.ntfy_client.send", return_value=(True, 200, None))
def test_evaluate_and_alert_notifies_subscribed_users(mock_send, db_session):
    alice = service.create_user(db_session, "alice", ntfy_topic="swing-alerts-alice")
    service.add_symbol(db_session, alice.id, "SPY")
    _seed_engineered_strong_buy(db_session, "SPY")

    _evaluate_and_alert(db_session, "SPY", Timeframe.DAY_1)

    delivered_topics = {call.args[0] for call in mock_send.call_args_list}
    assert "swing-alerts-alice" in delivered_topics
    assert "swing-alerts-global" in delivered_topics


@patch("app.alerts.dispatcher.ntfy_client.send", return_value=(True, 200, None))
def test_evaluate_and_alert_second_call_suppressed_by_cooldown(mock_send, db_session):
    _seed_engineered_strong_buy(db_session, "SPY")

    _evaluate_and_alert(db_session, "SPY", Timeframe.DAY_1)
    _evaluate_and_alert(db_session, "SPY", Timeframe.DAY_1)  # same bar, same classification

    assert db_session.query(Alert).count() == 1
