from sporty_hq.engine import ScanConfig, score_quotes
from sporty_hq.providers import quotes_from_csv, quotes_from_json
from sporty_hq.sports import sport_rank
from tests.conftest import DEMO_CSV, DEMO_JSON, make_quote


def test_fixture_json_produces_mlb_nfl_candidates() -> None:
    quotes = quotes_from_json(DEMO_JSON, source="test")
    cands = score_quotes(quotes, ScanConfig(min_edge=0.03))
    assert len(cands) >= 2
    assert all(c.edge_pct >= 3.0 - 1e-6 for c in cands)
    assert cands == sorted(
        cands, key=lambda c: (sport_rank(c.sport), -c.edge_pct, c.event_name, c.selection)
    )
    events = {c.event_id for c in cands}
    assert "mlb-2026-0908-nyy-bos" in events
    assert "nfl-2026-0910-kc-phi" in events
    assert "nba-2026-0908-offseason" not in events
    assert "soccer-illiquid-demo" not in events
    yanks = [c for c in cands if c.selection == "New York Yankees" and c.market == "ml"]
    assert yanks
    assert "consensus" in yanks[0].rationale.lower()
    assert yanks[0].book == "fanduel"


def test_min_edge_and_priority_order() -> None:
    quotes = quotes_from_json(DEMO_JSON, source="test")
    cands = score_quotes(quotes, ScanConfig(min_edge=0.03))
    nfl_mlb = [c for c in cands if sport_rank(c.sport) == 0]
    ncaaf = [c for c in cands if sport_rank(c.sport) == 1]
    if nfl_mlb and ncaaf:
        assert cands.index(nfl_mlb[0]) < cands.index(ncaaf[0])
    loose = score_quotes(quotes, ScanConfig(min_edge=-1.0))
    assert loose[0].edge_pct >= min(c.edge_pct for c in loose if sport_rank(c.sport) == sport_rank(loose[0].sport))


def test_csv_importer_scores() -> None:
    quotes = quotes_from_csv(DEMO_CSV, source="csv")
    cands = score_quotes(quotes, ScanConfig(min_edge=0.03))
    assert len(cands) == 1
    assert cands[0].selection == "Toronto Blue Jays"
    assert cands[0].edge_pct >= 3


def test_requires_consensus_book() -> None:
    quotes = [
        make_quote(book="fanduel", selection="New York Yankees", american_odds=150),
        make_quote(book="fanduel", selection="Boston Red Sox", american_odds=-170),
    ]
    assert score_quotes(quotes, ScanConfig(apply_calendar=False)) == []


def test_home_stub_bump_changes_fair() -> None:
    quotes = quotes_from_json(DEMO_JSON, source="test")
    base = score_quotes(quotes, ScanConfig(min_edge=-1.0, home_prob_bump=0.0))
    bumped = score_quotes(quotes, ScanConfig(min_edge=-1.0, home_prob_bump=0.05))
    yanks_base = next(c for c in base if c.selection == "New York Yankees" and c.market == "ml")
    yanks_bump = next(c for c in bumped if c.selection == "New York Yankees" and c.market == "ml")
    # Yankees are the away side; home bump on BOS should cut NY fair.
    assert yanks_bump.fair_prob < yanks_base.fair_prob
    assert "home bump" in yanks_bump.rationale
