from pathlib import Path

import pytest

from sporty_hq.streaming import (
    FixtureReplayProvider,
    StreamingUnavailable,
    TheOddsApiWebsocketStub,
    iter_sse_events,
    load_stream_provider,
    quotes_from_opticodds_odds,
)

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
