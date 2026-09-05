import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.database.base import Base
from app.main import app


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_create_user(client):
    r = client.post("/users", json={"username": "alice"})
    assert r.status_code == 201
    body = r.json()
    assert body["username"] == "alice"
    assert body["is_active"] is True


def test_create_user_duplicate_username_conflicts(client):
    client.post("/users", json={"username": "alice"})
    r = client.post("/users", json={"username": "alice"})
    assert r.status_code == 409


def test_list_users(client):
    client.post("/users", json={"username": "alice"})
    client.post("/users", json={"username": "bob"})
    r = client.get("/users")
    assert r.status_code == 200
    assert {u["username"] for u in r.json()} == {"alice", "bob"}


def test_get_user_not_found(client):
    r = client.get("/users/999")
    assert r.status_code == 404


def test_add_symbol_to_user(client):
    user = client.post("/users", json={"username": "alice"}).json()
    r = client.post("/symbols", json={"user_id": user["id"], "symbol": "spy"})
    assert r.status_code == 201
    assert r.json()["symbol"] == "SPY"


def test_add_symbol_for_missing_user_404s(client):
    r = client.post("/symbols", json={"user_id": 999, "symbol": "SPY"})
    assert r.status_code == 404


def test_list_symbols_for_user(client):
    user = client.post("/users", json={"username": "alice"}).json()
    client.post("/symbols", json={"user_id": user["id"], "symbol": "SPY"})
    client.post("/symbols", json={"user_id": user["id"], "symbol": "QQQ"})

    r = client.get("/symbols", params={"user_id": user["id"]})
    assert r.status_code == 200
    assert {s["symbol"] for s in r.json()} == {"SPY", "QQQ"}


def test_remove_symbol(client):
    user = client.post("/users", json={"username": "alice"}).json()
    client.post("/symbols", json={"user_id": user["id"], "symbol": "SPY"})

    r = client.delete("/symbols/SPY", params={"user_id": user["id"]})
    assert r.status_code == 204

    remaining = client.get("/symbols", params={"user_id": user["id"]}).json()
    assert remaining == []


def test_remove_symbol_not_found(client):
    user = client.post("/users", json={"username": "alice"}).json()
    r = client.delete("/symbols/SPY", params={"user_id": user["id"]})
    assert r.status_code == 404
