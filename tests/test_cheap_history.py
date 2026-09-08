"""Cheap-feed historical pull: Odds API / SportsGameOdds. Never invent closes."""

from __future__ import annotations

import pytest

from sporty_hq.cheap_history import (
    HistoricalUnavailable,
    fetch_odds_api_historical_closes,
    fetch_sportsgameodds_historical_closes,
    quotes_from_sgo_payload,
)


def test_odds_api_history_pairs_fanduel_vs_pinnacle() -> None:
    commence = "2025-09-02T23:00:00Z"
    event = {
        "id": "e1",
        "home_team": "Boston Red Sox",
        "away_team": "New York Yankees",
        "commence_time": commence,
        "bookmakers": [
            {
                "key": "fanduel",
                "last_update": "2025-09-02T20:00:00Z",
                "markets": [
                    {"key": "h2h", "outcomes": [{"name": "New York Yankees", "price": 165}]},
                ],
            },
            {
                "key": "pinnacle",
                "last_update": "2025-09-02T22:55:00Z",
                "markets": [
                    {"key": "h2h", "outcomes": [{"name": "New York Yankees", "price": 148}]},
                ],
            },
        ],
    }

    def fetch(url: str, params=None, headers=None):
        if "/scores" in url:
            return [{"id": "e1", "completed": True, "commence_time": commence}]
        return {"data": [event]}

    rows = fetch_odds_api_historical_closes("k", sports=("baseball_mlb",), fetch=fetch)
    assert rows
    assert rows[0].posted_feed == "oddsapi"
    assert rows[0].posted_book == "fanduel"
    assert rows[0].close_book == "pinnacle"
    assert rows[0].posted_odds == 165
    assert rows[0].close_odds == 148
    assert rows[0].close_kind == "true_close"


def test_odds_api_history_without_pinnacle_uses_provider_close() -> None:
    commence = "2025-09-02T23:00:00Z"
    event = {
        "id": "e1",
        "home_team": "Boston Red Sox",
        "away_team": "New York Yankees",
        "commence_time": commence,
        "bookmakers": [
            {
                "key": "fanduel",
                "last_update": "2025-09-02T20:00:00Z",
                "markets": [
                    {"key": "h2h", "outcomes": [{"name": "New York Yankees", "price": 165}]},
                ],
            },
        ],
    }

    def fetch(url: str, params=None, headers=None):
        if "/scores" in url:
            return [{"id": "e1", "completed": True, "commence_time": commence}]
        return {"data": [event]}

    rows = fetch_odds_api_historical_closes("k", sports=("baseball_mlb",), fetch=fetch)
    assert rows[0].close_book == "fanduel"
    assert rows[0].close_odds == 165


def test_odds_api_empty_does_not_invent() -> None:
    def fetch(url: str, params=None, headers=None):
        if "/scores" in url:
            return []
        return {"data": []}

    with pytest.raises(HistoricalUnavailable, match="will not invent"):
        fetch_odds_api_historical_closes("k", sports=("baseball_mlb",), fetch=fetch)


def test_odds_api_unauthorized_does_not_invent() -> None:
    def fetch(url: str, params=None, headers=None):
        raise HistoricalUnavailable(
            "this key cannot read historical odds. Export snapshots to --path. "
            "HQ will not invent closes."
        )

    with pytest.raises(HistoricalUnavailable, match="will not invent"):
        fetch_odds_api_historical_closes("k", sports=("baseball_mlb",), fetch=fetch)


def test_sgo_quotes_and_history_map_fanduel() -> None:
    payload = {
        "data": [
            {
                "eventID": "sgo-1",
                "leagueID": "MLB",
                "status": {"startsAt": "2025-09-02T23:00:00Z"},
                "teams": {
                    "home": {"names": {"long": "Boston Red Sox"}},
                    "away": {"names": {"long": "New York Yankees"}},
                },
                "odds": {
                    "points-away-game-ml-away": {
                        "oddID": "points-away-game-ml-away",
                        "betTypeID": "ml",
                        "periodID": "game",
                        "sideID": "away",
                        "byBookmaker": {
                            "fanduel": {"openOdds": 165, "closeOdds": 160, "odds": 160},
                            "pinnacle": {"closeOdds": 148, "odds": 148},
                        },
                    }
                },
            }
        ]
    }
    quotes = quotes_from_sgo_payload(payload)
    assert quotes
    assert {q.book for q in quotes} >= {"fanduel", "pinnacle"}

    def fetch(url: str, params=None, headers=None):
        return payload

    rows = fetch_sportsgameodds_historical_closes("k", fetch=fetch)
    assert rows
    assert rows[0].posted_feed == "sportsgameodds"
    assert rows[0].close_book == "pinnacle"
    assert rows[0].posted_odds == 165
    assert rows[0].close_odds == 148
