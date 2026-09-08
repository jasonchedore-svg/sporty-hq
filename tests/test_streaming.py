from pathlib import Path

import pytest

from sporty_hq.streaming import (
    FixtureReplayProvider,
    OpticOddsRealtimeProvider,
    OpticOddsWebsocketProvider,
    StreamingUnavailable,
    TheOddsApiWebsocketStub,
    iter_sse_events,
    load_stream_provider,
    opticodds_ws_url,
    quotes_from_opticodds_odds,
)
from tests.conftest import make_quote

REPO = Path(__file__).resolve().parents[1]
SSE = REPO / "fixtures" / "opticodds_sse.txt"
DEMO = REPO / "fixtures" / "demo_odds.json"


def test_sse_parser_maps_opticodds_odds() -> None:
    text = SSE.read_text(encoding="utf-8")
    events = list(iter_sse_events(text))
    kinds = [e["event"] for e in events]
    assert "connected" in kinds
    assert "odds" in kinds
    odds_event = next(e for e in events if e["event"] == "odds")
    quotes = quotes_from_opticodds_odds(odds_event["data"])
    books = {q.book for q in quotes}
    assert "fanduel" in books
    assert "pinnacle" in books
    yanks = [q for q in quotes if q.selection == "New York Yankees" and q.book == "fanduel"]
    assert yanks and yanks[0].american_odds == 165
    assert yanks[0].sport == "baseball_mlb"


def test_replay_emits_fixture_as_push_batch() -> None:
    provider = FixtureReplayProvider(DEMO)
    assert provider.transport == "replay"
    batches = list(provider.iter_quote_batches())
    assert len(batches) == 1
    assert any(q.book == "pinnacle" for q in batches[0])


def test_odds_api_websocket_stub_unavailable() -> None:
    stub = TheOddsApiWebsocketStub("dummy")
    assert stub.AVAILABLE is False
    with pytest.raises(StreamingUnavailable, match="no public WebSocket"):
        stub.fetch_quotes()


def test_load_stream_degrades_to_fixture_without_keys() -> None:
    provider, note = load_stream_provider(
        opticodds_key=None,
        odds_api_key=None,
        replay_path=None,
        fixture_fallback=DEMO,
    )
    assert provider.name == "stream-replay"
    assert "degrade" in note.lower()
    assert provider.fetch_quotes()


def test_ws_url_does_not_embed_api_key() -> None:
    url = opticodds_ws_url("baseball", ["FanDuel", "Pinnacle"], ["MLB"])
    assert url.startswith("wss://api.opticodds.com/")
    assert "key=" not in url.lower()
    assert "Pinnacle" in url
    assert "FanDuel" in url


def test_ws_provider_raises_when_connect_fails() -> None:
    def boom(url: str, key: str, timeout: float):
        assert "secret-key" not in url
        raise StreamingUnavailable("no ws")

    provider = OpticOddsWebsocketProvider("secret-key", ws_connect=boom, sports=["baseball"])
    with pytest.raises(StreamingUnavailable, match="no ws"):
        list(provider.iter_quote_batches())


def test_realtime_ws_first_falls_back_to_sse() -> None:
    class FakeWS:
        transport = "websocket"
        max_events = 40

        def iter_quote_batches(self):
            raise StreamingUnavailable("no ws")

    class FakeSSE:
        transport = "sse"
        max_events = 40

        def iter_quote_batches(self):
            yield [make_quote(book="fanduel"), make_quote(book="pinnacle")]

    provider = OpticOddsRealtimeProvider("k", ws=FakeWS(), sse=FakeSSE())
    batches = list(provider.iter_quote_batches())
    assert provider.transport == "sse"
    books = {q.book for q in batches[0]}
    assert "fanduel" in books and "pinnacle" in books


def test_load_stream_prefers_opticodds_over_oddsapi() -> None:
    provider, note = load_stream_provider(
        opticodds_key="dummy-optic",
        odds_api_key="dummy-oddsapi",
        replay_path=None,
        fixture_fallback=DEMO,
    )
    assert provider.name == "opticodds"
    assert "Odds API not used" in note or "not the payable path" in note
    assert "dummy-optic" not in note
    assert "dummy-oddsapi" not in note
