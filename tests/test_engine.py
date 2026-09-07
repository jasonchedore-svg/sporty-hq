from sporty_hq.engine import ScanConfig, score_quotes
from sporty_hq.providers import quotes_from_csv, quotes_from_json
from tests.conftest import DEMO_CSV, DEMO_JSON, make_quote


def test_fixture_json_produces_candidates_above_3pct() -> None:
    quotes = quotes_from_json(DEMO_JSON, source="test")
    cands = score_quotes(quotes, ScanConfig(min_edge=0.03))
    assert len(cands) >= 2
    assert all(c.edge_pct >= 3.0 - 1e-6 for c in cands)
    assert cands == sorted(cands, key=lambda c: (-c.edge_pct, c.event_name, c.selection))
    events = {c.event_id for c in cands}
    assert "nhl-2026-1015-bos-tor" in events
    leafs = [c for c in cands if c.selection == "Toronto Maple Leafs" and c.market == "ml"]
    assert leafs
    assert "consensus" in leafs[0].rationale.lower()
    assert leafs[0].book == "fanduel"


def test_min_edge_filters_efficient_market() -> None:
    quotes = quotes_from_json(DEMO_JSON, source="test")
    # NFL BUF/MIA is tightly priced — should not appear at 3%
    cands = score_quotes(quotes, ScanConfig(min_edge=0.03))
    nfl = [c for c in cands if c.event_id == "nfl-2026-1012-buf-mia"]
    assert nfl == []
    loose = score_quotes(quotes, ScanConfig(min_edge=-1.0))
    assert loose[0].edge_pct >= loose[-1].edge_pct


def test_csv_importer_scores() -> None:
    quotes = quotes_from_csv(DEMO_CSV, source="csv")
    cands = score_quotes(quotes, ScanConfig(min_edge=0.03))
    assert len(cands) == 1
    assert cands[0].selection == "Toronto Maple Leafs"
    assert cands[0].edge_pct >= 3


def test_requires_consensus_book() -> None:
    quotes = [
        make_quote(book="fanduel", selection="Toronto Maple Leafs", american_odds=150),
        make_quote(book="fanduel", selection="Boston Bruins", american_odds=-170),
    ]
    assert score_quotes(quotes) == []


def test_home_stub_bump_changes_fair() -> None:
    quotes = quotes_from_json(DEMO_JSON, source="test")
    base = score_quotes(quotes, ScanConfig(min_edge=0.0, home_prob_bump=0.0))
    bumped = score_quotes(quotes, ScanConfig(min_edge=0.0, home_prob_bump=0.05))
    leafs_base = next(c for c in base if c.selection == "Toronto Maple Leafs" and c.market == "ml")
    leafs_bump = next(c for c in bumped if c.selection == "Toronto Maple Leafs" and c.market == "ml")
    assert leafs_bump.fair_prob > leafs_base.fair_prob
    assert "home bump" in leafs_bump.rationale
