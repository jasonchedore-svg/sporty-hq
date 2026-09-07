from sporty_hq.models import Bet, Candidate
from sporty_hq.odds_math import clv_pct, settle_pnl
from sporty_hq.storage import utcnow
from tests.conftest import make_quote


def test_quote_and_bet_roundtrip(store) -> None:
    q = make_quote()
    store.insert_quotes("batch1", "test", [q])
    assert store.latest_batch_id() == "batch1"
    loaded = store.quotes_for_batch("batch1")
    assert len(loaded) == 1
    assert loaded[0].selection == q.selection
    assert loaded[0].american_odds == 150


def test_candidates_replace(store) -> None:
    now = utcnow()
    cand = Candidate(
        event_id="e1",
        sport="nhl",
        event_name="BOS @ TOR",
        commence_at=now,
        market="ml",
        selection="Toronto Maple Leafs",
        book="fanduel",
        american_odds=165,
        decimal_odds=2.65,
        fair_prob=0.4,
        implied_prob=0.377,
        edge_pct=6.5,
        juice_pct=4.8,
        rationale="test",
        consensus_books=["draftkings"],
    )
    stored = store.replace_candidates("batch1", [cand])
    assert stored[0].id == 1
    assert store.get_candidate(1).edge_pct == 6.5
    store.replace_candidates("batch2", [])
    assert store.list_candidates() == []


def test_settle_persists_clv(store) -> None:
    now = utcnow()
    bet = Bet(
        id="deadbeef",
        logged_at=now,
        event_id="e1",
        event_name="BOS @ TOR",
        sport="nhl",
        commence_at=now,
        market="ml",
        selection="Toronto Maple Leafs",
        odds_at_bet=165,
        stake=25,
    )
    store.insert_bet(bet)
    loaded = store.get_bet("deadbeef")
    assert loaded is not None and loaded.is_open
    loaded.result = "win"
    loaded.close_odds = 148
    loaded.clv_pct = round(clv_pct(165, 148), 2)
    loaded.pnl = settle_pnl("win", 25, 165)
    loaded.settled_at = utcnow()
    store.update_bet(loaded)
    again = store.get_bet("deadbeef")
    assert again is not None
    assert again.result == "win"
    assert again.clv_pct and again.clv_pct > 0
    assert again.pnl == 41.25
    assert store.open_bets() == []
