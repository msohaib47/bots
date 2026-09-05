from datetime import datetime

from app.scoring.engine import ScoreResult


def build_alert_title(symbol: str, score_result: ScoreResult) -> str:
    return f"{symbol}: {score_result.classification} (score {score_result.score:+d})"


def build_alert_body(
    symbol: str,
    price: float,
    score_result: ScoreResult,
    support: float | None,
    resistance: float | None,
    ts: datetime,
) -> str:
    lines = [f"Price: ${price:.2f}"]

    if score_result.signals:
        lines.append("Signals:")
        for s in score_result.signals:
            sign = "+" if s.signed_weight > 0 else "-"
            lines.append(f"  {sign}{abs(s.signed_weight)} {s.name} — {s.detail}")
    else:
        lines.append("Signals: none")

    if support is not None:
        lines.append(f"Support: ${support:.2f}")
    if resistance is not None:
        lines.append(f"Resistance: ${resistance:.2f}")

    lines.append(f"Time: {ts.isoformat()}")
    return "\n".join(lines)
