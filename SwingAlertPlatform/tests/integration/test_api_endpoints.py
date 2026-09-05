from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.database.base import Base
from app.database.models import Alert, PriceBar
from app.indicators.engine import compute_indicators
from app.database.models import IndicatorValue
from app.main import app


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine)
    session = session_local()

    def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app), session
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_health_endpoint(client):
    test_client, _ = client
    r = test_client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_signals_endpoint_404_when_no_data(client):
    test_client, _ = client
    r = test_client.get("/signals/SPY?timeframe=1Day")
    assert r.status_code == 404


def test_signals_endpoint_returns_evaluation_once_seeded(client):
    test_client, session = client
    _seed_bars_and_indicators(session, "SPY", n=260)

    r = test_client.get("/signals/SPY?timeframe=1Day")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "SPY"
    assert body["timeframe"] == "1Day"
    assert -12 <= body["score"] <= 12
    assert body["classification"] in {"Strong Buy", "Buy", "Neutral", "Sell", "Strong Sell"}


def test_alerts_endpoint_empty_initially(client):
    test_client, _ = client
    r = test_client.get("/alerts")
    assert r.status_code == 200
    assert r.json() == []


def test_alerts_endpoint_returns_seeded_alerts_filtered_by_symbol(client):
    test_client, session = client
    session.add(
        Alert(
            symbol="SPY", timeframe="1Day", ts=datetime.now(timezone.utc), score=8,
            classification="Strong Buy", signals_fired=[], price=500.0, dedup_key="SPY:1Day:Strong Buy",
        )
    )
    session.add(
        Alert(
            symbol="QQQ", timeframe="1Day", ts=datetime.now(timezone.utc), score=-8,
            classification="Strong Sell", signals_fired=[], price=400.0, dedup_key="QQQ:1Day:Strong Sell",
        )
    )
    session.commit()

    r = test_client.get("/alerts", params={"symbol": "SPY"})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["symbol"] == "SPY"


def _seed_bars_and_indicators(session, symbol: str, n: int):
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(0)
    ts = [datetime.now(timezone.utc) - timedelta(days=n - i) for i in range(n)]
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)))
    df = pd.DataFrame(
        {
            "ts": ts,
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + rng.random(n),
            "low": close - rng.random(n),
            "close": close,
            "volume": rng.integers(1000, 5000, n).astype(float),
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

    indicators = compute_indicators(df)
    for _, row in indicators.iterrows():
        values = {k: (None if pd.isna(v) else float(v)) for k, v in row.items() if k != "ts"}
        session.add(IndicatorValue(symbol=symbol, timeframe="1Day", ts=row["ts"], **values))
    session.commit()
