import logging

import requests

from app.config import get_settings

logger = logging.getLogger(__name__)

# (priority, ntfy emoji tag) per classification
PRIORITY_BY_CLASSIFICATION = {
    "Strong Buy": ("urgent", "rocket"),
    "Buy": ("high", "chart_increasing"),
    "Neutral": ("default", "left_right_arrow"),
    "Sell": ("high", "chart_decreasing"),
    "Strong Sell": ("urgent", "rotating_light"),
}


def send(topic: str, title: str, body: str, classification: str) -> tuple[bool, int | None, str | None]:
    """POST an alert to an ntfy topic. Returns (success, http_status_code, error_message)."""
    settings = get_settings()
    priority, emoji = PRIORITY_BY_CLASSIFICATION.get(classification, ("default", "bell"))
    url = f"{settings.ntfy_base_url}/{topic}"
    try:
        r = requests.post(
            url,
            data=body.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": emoji},
            timeout=10,
        )
        if r.ok:
            return True, r.status_code, None
        return False, r.status_code, r.text[:500]
    except Exception as e:
        logger.error(f"ntfy delivery failed for topic {topic}: {e}")
        return False, None, str(e)
