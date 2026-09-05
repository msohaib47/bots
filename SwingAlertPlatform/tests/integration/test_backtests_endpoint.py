from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.config import Settings
from app.database.base import Base
from app.database.models import PriceBar, IndicatorValue
from app.main import app

N = 260


@pytest.fixture
def client(tmp_path):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db

    test_settings = Settings(reports_dir=str(tmp_path))
    with patch("app.backtesting.report.get_settings", return_value=test_settings):
        try:
            yield TestClient(app), session
        finally:
            app.dependency_overrides.clear()
            session.close()


def _seed_trending_symbol(session, symbol: str):
    rng = np.random.default_rng(7)
    ts = [datetime.now(timezone.utc) - timedelta(days=N - i) for i in range(N)]
    # Oscillate so EMA13/21 actually crosses back and forth a few times, giving the
    # backtest something to trade rather than a single monotonic trend.
    close = pd.Series(100 + 10 * np.sin(np.linspace(0, 6 * np.pi, N)) + rng.normal(0, 0.3, N))
    df = pd.DataFrame(
        {
            "ts": ts,
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": rng.integers(1000, 5000, N).astype(float),
        }
    )
    for _, row in df.iterrows():
        session.add(
            PriceBar(
                symbol=symbol, timeframe="1Day", ts=row["ts"], open=row["open"], high=row["high"],
                low=row["low"], close=row["close"], volume=row["volume"],
            )
        )
    session.commit()

    from app.indicators.engine import compute_indicators

    indicators = compute_indicators(df)
    for _, row in indicators.iterrows():
        values = {k: (None if pd.isna(v) else float(v)) for k, v in row.items() if k != "ts"}
        session.add(IndicatorValue(symbol=symbol, timeframe="1Day", ts=row["ts"], **values))
    session.commit()
    return ts[0], ts[-1]


def test_create_backtest_runs_and_returns_metrics(client):
    test_client, session = client
    start, end = _seed_trending_symbol(session, "SPY")

    r = test_client.post(
        "/backtests",
        json={
            "name": "ema-cross-test",
            "signal_name": "EMA13/21 Bull Cross",
            "symbol": "SPY",
            "timeframe": "1Day",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["strategy"] == "EMA13/21 Bull Cross"
    assert body["symbols"] == ["SPY"]
    assert "trades" in body
    assert body["report_path"] is not None


def test_create_backtest_rejects_unknown_signal(client):
    test_client, session = client
    start, end = _seed_trending_symbol(session, "SPY")

    r = test_client.post(
        "/backtests",
        json={
            "name": "bad-signal",
            "signal_name": "Not A Real Signal",
            "symbol": "SPY",
            "timeframe": "1Day",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
    )
    assert r.status_code == 400


def test_create_backtest_rejects_unknown_timeframe(client):
    test_client, session = client
    start, end = _seed_trending_symbol(session, "SPY")

    r = test_client.post(
        "/backtests",
        json={
            "name": "bad-timeframe",
            "signal_name": "EMA13/21 Bull Cross",
            "symbol": "SPY",
            "timeframe": "3Week",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
    )
    assert r.status_code == 400


def test_list_and_get_backtest(client):
    test_client, session = client
    start, end = _seed_trending_symbol(session, "SPY")

    created = test_client.post(
        "/backtests",
        json={
            "name": "listable-run",
            "signal_name": "EMA13/21 Bull Cross",
            "symbol": "SPY",
            "timeframe": "1Day",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
    ).json()

    listed = test_client.get("/backtests").json()
    assert any(r["id"] == created["id"] for r in listed)

    detail = test_client.get(f"/backtests/{created['id']}")
    assert detail.status_code == 200
    assert detail.json()["name"] == "listable-run"


def test_get_missing_backtest_404s(client):
    test_client, _ = client
    r = test_client.get("/backtests/999")
    assert r.status_code == 404
