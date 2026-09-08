"""Odds Arcade brief schema + juice $100 compare."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sporty_hq.brief import (
    CADENCE,
    OPEN_LINE_MISSING_HINT,
    build_brief,
    pick_juice_explainer,
    render_markdown,
)
from sporty_hq.odds_math import juice_compare_to_win
from sporty_hq.providers import quotes_from_json
from sporty_hq.storage import Store
from tests.conftest import DEMO_JSON, make_quote

SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "brief.schema.json"

LINE_MOVE_REQUIRED = [
    "sport",
    "event",
    "market",
    "open_line",
    "open_price",
    "current_line",
    "current_price",
    "delta",
    "why_hint",
    "as_of",
    "example",
]
JUICE_REQUIRED = [
    "event",
    "market",
    "selection",
    "better_price",
    "worse_price",
    "risk_to_win_100_better",
    "risk_to_win_100_worse",
    "diff_usd",
    "as_of",
    "example",
]
PACK_REQUIRED = [
    "sport",
    "event",
    "market",
    "current_line",
    "current_price",
    "as_of",
    "example",
]
RESULTS_REQUIRED = PACK_REQUIRED + ["close_line", "close_price", "beat_close", "note"]


def _schema_required(def_name: str) -> list[str]:
    raw = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return list(raw["$defs"][def_name]["required"])


def test_schema_file_lists_locked_fields() -> None:
    assert _schema_required("line_move") == LINE_MOVE_REQUIRED
    assert _schema_required("juice_explainer") == JUICE_REQUIRED
    assert _schema_required("challenge_game") == PACK_REQUIRED
    assert "close_line" in _schema_required("challenge_result")
    assert "beat_close" in _schema_required("challenge_result")
    assert "note" in _schema_required("challenge_result")


def test_fixture_brief_schema_shape() -> None:
    quotes = quotes_from_json(DEMO_JSON, source="test")
    brief = build_brief(quotes, include_pack=True, include_closes=True)
    payload = brief.to_json_dict()
    assert payload["kind"] == "odds_arcade_brief"
    assert payload["cadence"] == CADENCE
    assert "does not place bets" in payload["disclaimer"].lower()
    move = payload["line_move_of_the_day"]
    juice = payload["juice_explainer"]
    for key in LINE_MOVE_REQUIRED:
        assert key in move, key
    for key in JUICE_REQUIRED:
        assert key in juice, key
    assert isinstance(move["example"], bool)
    assert isinstance(juice["example"], bool)
    assert isinstance(move["delta"], (int, float))
    assert isinstance(move["open_price"], int)
    assert isinstance(move["current_price"], int)
    assert isinstance(juice["better_price"], int)
    assert isinstance(juice["worse_price"], int)
    # Single fixture snapshot has no historical open.
    assert move["example"] is True
    assert OPEN_LINE_MISSING_HINT.split("—")[0].strip() in move["why_hint"]
    pack = payload["close_challenge_pack"]
    assert 2 <= len(pack) <= 3
    for game in pack:
        for key in PACK_REQUIRED:
            assert key in game, key
        assert game["example"] is False
    results = payload["close_challenge_results"]
    assert results
    for row in results:
        for key in RESULTS_REQUIRED:
            assert key in row, key
        assert isinstance(row["example"], bool)
        assert isinstance(row["beat_close"], (bool, type(None)))


def test_juice_explainer_math_matches_fixture_quotes() -> None:
    quotes = quotes_from_json(DEMO_JSON, source="test")
    juice = pick_juice_explainer(quotes, as_of="2026-09-08T13:00:00+00:00")
    assert juice.example is False
    assert "Arsenal" not in juice.event
    assert "Chelsea" not in juice.event
    better, worse, risk_b, risk_w, diff = juice_compare_to_win(
        juice.better_price, juice.worse_price
    )
    assert juice.better_price == better
    assert juice.worse_price == worse
    assert juice.better_price >= juice.worse_price
    assert juice.risk_to_win_100_better == risk_b
    assert juice.risk_to_win_100_worse == risk_w
    assert juice.diff_usd == diff
    assert juice.diff_usd == round(
        juice.risk_to_win_100_worse - juice.risk_to_win_100_better, 2
    )
    assert juice.diff_usd > 0
    # Classic -110 vs -120 juice: $10 extra to win $100.
    b, w, rb, rw, d = juice_compare_to_win(-110, -120)
    assert (b, w, rb, rw, d) == (-110, -120, 110.0, 120.0, 10.0)


def test_historical_open_is_not_example(store: Store) -> None:
    commence = datetime(2026, 9, 11, 0, 20, tzinfo=timezone.utc)
    opened = [
        make_quote(
            event_id="nfl-2026-0910-kc-phi",
            sport="americanfootball_nfl",
            commence_at=commence,
            home_team="Philadelphia Eagles",
            away_team="Kansas City Chiefs",
            book="fanduel",
            market="spread",
            selection="Philadelphia Eagles",
            american_odds=115,
            point=3.5,
        ),
        make_quote(
            event_id="nfl-2026-0910-kc-phi",
            sport="americanfootball_nfl",
            commence_at=commence,
            home_team="Philadelphia Eagles",
            away_team="Kansas City Chiefs",
            book="fanduel",
            market="spread",
            selection="Kansas City Chiefs",
            american_odds=-135,
            point=-3.5,
        ),
    ]
    current = [
        make_quote(
            event_id="nfl-2026-0910-kc-phi",
            sport="americanfootball_nfl",
            commence_at=commence,
            home_team="Philadelphia Eagles",
            away_team="Kansas City Chiefs",
            book="fanduel",
            market="spread",
            selection="Philadelphia Eagles",
            american_odds=-110,
            point=2.5,
        ),
        make_quote(
            event_id="nfl-2026-0910-kc-phi",
            sport="americanfootball_nfl",
            commence_at=commence,
            home_team="Philadelphia Eagles",
            away_team="Kansas City Chiefs",
            book="fanduel",
            market="spread",
            selection="Kansas City Chiefs",
            american_odds=-110,
            point=-2.5,
        ),
        make_quote(
            event_id="nfl-2026-0910-kc-phi",
            sport="americanfootball_nfl",
            commence_at=commence,
            home_team="Philadelphia Eagles",
            away_team="Kansas City Chiefs",
            book="draftkings",
            market="spread",
            selection="Philadelphia Eagles",
            american_odds=-108,
            point=2.5,
        ),
        make_quote(
            event_id="nfl-2026-0910-kc-phi",
            sport="americanfootball_nfl",
            commence_at=commence,
            home_team="Philadelphia Eagles",
            away_team="Kansas City Chiefs",
            book="draftkings",
            market="spread",
            selection="Kansas City Chiefs",
            american_odds=-112,
            point=-2.5,
        ),
    ]
    store.insert_quotes("open", "test", opened)
    store.insert_quotes("now", "test", current)
    history = store.quotes_chronological()
    brief = build_brief(current, history=history)
    move = brief.line_move_of_the_day
    assert move.example is False
    assert move.event == "Kansas City Chiefs @ Philadelphia Eagles"
    assert move.open_line in {3.5, -3.5}
    assert move.current_line in {2.5, -2.5}
    assert abs(move.delta) == 1.0
    assert "Example open" not in move.why_hint


def test_markdown_mentions_no_bets() -> None:
    quotes = quotes_from_json(DEMO_JSON, source="test")
    text = render_markdown(build_brief(quotes, include_pack=True))
    assert "Odds Arcade" in text
    assert "does not place bets" in text.lower()
    assert "Juice explainer" in text
    assert "Close-challenge pack" in text
