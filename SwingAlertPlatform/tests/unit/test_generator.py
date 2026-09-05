from datetime import datetime, timezone

from app.alerts.generator import build_alert_body, build_alert_title
from app.scoring.engine import ScoreResult
from app.signals.base import Direction, SignalResult


def _score_result():
    return ScoreResult(
        score=5,
        classification="Buy",
        signals=[
            SignalResult("EMA13/21 Bull Cross", Direction.BULLISH, 1, "EMA13 crossed above EMA21"),
            SignalResult("RSI Weakness", Direction.BEARISH, 1, "RSI weakened below 70"),
        ],
    )


def test_title_includes_symbol_classification_and_score():
    title = build_alert_title("SPY", _score_result())
    assert "SPY" in title
    assert "Buy" in title
    assert "+5" in title


def test_body_includes_price_signals_support_resistance_and_timestamp():
    ts = datetime(2026, 7, 6, 14, 30, tzinfo=timezone.utc)
    body = build_alert_body("SPY", 512.34, _score_result(), support=505.0, resistance=520.0, ts=ts)

    assert "512.34" in body
    assert "EMA13/21 Bull Cross" in body
    assert "RSI Weakness" in body
    assert "505.00" in body
    assert "520.00" in body
    assert ts.isoformat() in body


def test_body_handles_missing_support_and_resistance():
    ts = datetime(2026, 7, 6, tzinfo=timezone.utc)
    body = build_alert_body("SPY", 100.0, _score_result(), support=None, resistance=None, ts=ts)
    assert "Support" not in body
    assert "Resistance" not in body


def test_body_reports_no_signals_when_none_fired():
    empty = ScoreResult(score=0, classification="Neutral", signals=[])
    ts = datetime(2026, 7, 6, tzinfo=timezone.utc)
    body = build_alert_body("SPY", 100.0, empty, support=None, resistance=None, ts=ts)
    assert "Signals: none" in body
