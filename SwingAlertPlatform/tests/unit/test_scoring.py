from app.scoring.engine import SCORE_MAX, SCORE_MIN, classify, score_signals
from app.signals.base import Direction, SignalResult


def _result(direction: Direction, weight: int) -> SignalResult:
    return SignalResult(name="test-signal", direction=direction, weight=weight, detail="")


def test_score_sums_signed_weights():
    results = [_result(Direction.BULLISH, 2), _result(Direction.BULLISH, 1), _result(Direction.BEARISH, 1)]
    out = score_signals(results)
    assert out.score == 2  # +2 +1 -1


def test_score_clips_to_max():
    results = [_result(Direction.BULLISH, 2) for _ in range(10)]  # sums to 20, way over +12
    out = score_signals(results)
    assert out.score == SCORE_MAX


def test_score_clips_to_min():
    results = [_result(Direction.BEARISH, 2) for _ in range(10)]  # sums to -20, way under -12
    out = score_signals(results)
    assert out.score == SCORE_MIN


def test_score_with_no_signals_is_zero_and_neutral():
    out = score_signals([])
    assert out.score == 0
    assert out.classification == "Neutral"


def test_classify_boundaries_match_default_thresholds():
    assert classify(12) == "Strong Buy"
    assert classify(8) == "Strong Buy"
    assert classify(7) == "Buy"
    assert classify(3) == "Buy"
    assert classify(2) == "Neutral"
    assert classify(-2) == "Neutral"
    assert classify(-3) == "Sell"
    assert classify(-7) == "Sell"
    assert classify(-8) == "Strong Sell"
    assert classify(-12) == "Strong Sell"


def test_score_signals_preserves_the_fired_signal_list():
    results = [_result(Direction.BULLISH, 2)]
    out = score_signals(results)
    assert out.signals == results
