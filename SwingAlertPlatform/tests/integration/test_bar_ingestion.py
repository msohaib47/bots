from datetime import datetime, timezone

import responses

from app.data.alpaca_client import AlpacaDataClient
from app.data.bar_cache import sync_bars
from app.data.timeframes import Timeframe
from app.database.models import PriceBar


def _bar(ts: str, o=100.0, h=101.0, l=99.0, c=100.5, v=1000.0) -> dict:
    return {"t": ts, "o": o, "h": h, "l": l, "c": c, "v": v}


class FakeAlpacaClient:
    """Returns a fixed, scripted sequence of responses per call — avoids depending on real HTTP mocking
    for the higher-level de-dup behavior under test."""

    def __init__(self, responses_by_call: list[list[dict]]):
        self._responses = responses_by_call
        self.calls = 0

    def get_bars_since(self, symbol, timeframe, since, default_lookback_days=400):
        bars = self._responses[self.calls]
        self.calls += 1
        return bars


def test_sync_bars_inserts_new_bars(db_session):
    client = FakeAlpacaClient([[_bar("2026-07-06T13:30:00Z"), _bar("2026-07-06T13:35:00Z")]])

    inserted = sync_bars(db_session, client, "SPY", Timeframe.MIN_5)

    assert inserted == 2
    assert db_session.query(PriceBar).count() == 2


def test_sync_bars_is_idempotent_on_rerun(db_session):
    bars = [_bar("2026-07-06T13:30:00Z"), _bar("2026-07-06T13:35:00Z")]
    client = FakeAlpacaClient([bars, bars])  # simulate the API handing back the same window twice

    sync_bars(db_session, client, "SPY", Timeframe.MIN_5)
    inserted_second_run = sync_bars(db_session, client, "SPY", Timeframe.MIN_5)

    assert inserted_second_run == 0
    assert db_session.query(PriceBar).count() == 2


def test_sync_bars_only_appends_genuinely_new_bars(db_session):
    first = [_bar("2026-07-06T13:30:00Z"), _bar("2026-07-06T13:35:00Z")]
    second = [_bar("2026-07-06T13:35:00Z"), _bar("2026-07-06T13:40:00Z")]  # overlaps by one bar
    client = FakeAlpacaClient([first, second])

    sync_bars(db_session, client, "SPY", Timeframe.MIN_5)
    inserted_second_run = sync_bars(db_session, client, "SPY", Timeframe.MIN_5)

    assert inserted_second_run == 1
    assert db_session.query(PriceBar).count() == 3


def test_sync_bars_separates_by_symbol_and_timeframe(db_session):
    bars = [_bar("2026-07-06T13:30:00Z")]
    client = FakeAlpacaClient([bars, bars, bars])

    sync_bars(db_session, client, "SPY", Timeframe.MIN_5)
    sync_bars(db_session, client, "QQQ", Timeframe.MIN_5)
    sync_bars(db_session, client, "SPY", Timeframe.MIN_15)

    assert db_session.query(PriceBar).count() == 3


@responses.activate
def test_alpaca_client_requests_expected_timeframe_and_params():
    responses.add(
        responses.GET,
        "https://data.alpaca.markets/v2/stocks/SPY/bars",
        json={"bars": [_bar("2026-07-06T13:30:00Z")]},
        status=200,
    )

    client = AlpacaDataClient()
    bars = client.get_bars(
        "SPY",
        Timeframe.HOUR_4,
        start=datetime(2026, 7, 6, tzinfo=timezone.utc),
    )

    assert len(bars) == 1
    request = responses.calls[0].request
    assert "timeframe=4Hour" in request.url
    assert "feed=iex" in request.url
