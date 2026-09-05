import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.backtesting.engine import TradeResult
from app.backtesting.metrics import compute_metrics
from app.backtesting.report import generate_report
from app.config import Settings


def _trades():
    ts = datetime.now(timezone.utc)
    return [
        TradeResult("TEST", ts, 100, ts + timedelta(days=1), 110, "signal_exit", 1),
        TradeResult("TEST", ts, 100, ts + timedelta(days=1), 95, "stop_loss", 1),
    ]


def test_generate_report_writes_png_and_json(tmp_path):
    settings = Settings(reports_dir=str(tmp_path))
    with patch("app.backtesting.report.get_settings", return_value=settings):
        trades = _trades()
        metrics = compute_metrics(trades, datetime.now(timezone.utc) - timedelta(days=30), datetime.now(timezone.utc))
        png_path = generate_report("my-strategy-test", trades, metrics)

    assert os.path.exists(png_path)
    assert png_path.endswith(".png")

    json_path = png_path.replace(".png", ".json")
    assert os.path.exists(json_path)
    with open(json_path) as f:
        summary = json.load(f)
    assert summary["run_name"] == "my-strategy-test"
    assert summary["trade_count"] == 2
    assert summary["metrics"]["win_rate"] == 0.5


def test_generate_report_sanitizes_unsafe_characters_in_filename(tmp_path):
    settings = Settings(reports_dir=str(tmp_path))
    with patch("app.backtesting.report.get_settings", return_value=settings):
        trades = _trades()
        metrics = compute_metrics(trades, datetime.now(timezone.utc) - timedelta(days=30), datetime.now(timezone.utc))
        png_path = generate_report("my strategy/weird:name", trades, metrics)

    assert os.path.exists(png_path)
    basename = os.path.basename(png_path)
    assert "/" not in basename and ":" not in basename
