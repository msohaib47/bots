from dataclasses import dataclass

from app.config import get_settings
from app.signals.base import SignalResult

SCORE_MIN = -12
SCORE_MAX = 12


@dataclass
class ScoreResult:
    score: int
    classification: str
    signals: list[SignalResult]


def classify(score: int) -> str:
    settings = get_settings()
    if score >= settings.score_strong_buy:
        return "Strong Buy"
    if score >= settings.score_buy:
        return "Buy"
    if score <= settings.score_strong_sell:
        return "Strong Sell"
    if score <= settings.score_sell:
        return "Sell"
    return "Neutral"


def score_signals(results: list[SignalResult]) -> ScoreResult:
    raw = sum(r.signed_weight for r in results)
    score = max(SCORE_MIN, min(SCORE_MAX, raw))
    return ScoreResult(score=score, classification=classify(score), signals=results)
