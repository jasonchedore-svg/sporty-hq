"""Pluggable odds providers plus CSV/JSON fixture importers."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx

from sporty_hq.models import Quote, normalize_book, normalize_market
from sporty_hq.odds_math import parse_american


def parse_commence(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class OddsProvider(Protocol):
    name: str

    def fetch_quotes(self) -> list[Quote]:
        ...


def quotes_from_odds_api_payload(payload: Any, source: str) -> list[Quote]:
    """Accept The Odds API v4 list (or ``{"events": [...]}`` / a single event)."""
    events: list[dict[str, Any]]
    if isinstance(payload, dict):
        if "events" in payload:
            events = list(payload["events"])
        elif "id" in payload and "bookmakers" in payload:
            events = [payload]
        else:
            raise ValueError("JSON fixture must be an Odds API event list or {events: [...]}")
    elif isinstance(payload, list):
        events = payload
    else:
        raise ValueError("JSON fixture must be a list or object")

    quotes: list[Quote] = []
    for event in events:
        event_id = str(event.get("id") or event.get("event_id") or "")
        if not event_id:
            raise ValueError("Each event needs an id")
        sport = str(event.get("sport_key") or event.get("sport") or "unknown")
        home = str(event.get("home_team") or event.get("home") or "")
        away = str(event.get("away_team") or event.get("away") or "")
        commence = parse_commence(event.get("commence_time") or event.get("commence_at"))
        for bookmaker in event.get("bookmakers") or []:
            book = normalize_book(str(bookmaker.get("key") or bookmaker.get("title") or ""))
            for market in bookmaker.get("markets") or []:
                try:
                    raw_key = str(market.get("key") or market.get("market") or "")
                    if any(tok in raw_key.lower() for tok in ("prop", "player", "period", "alternate")):
                        continue
                    market_name = normalize_market(raw_key)
                except ValueError:
                    continue
                for outcome in market.get("outcomes") or []:
                    quotes.append(
                        Quote(
                            event_id=event_id,
                            sport=sport,
                            commence_at=commence,
                            home_team=home,
                            away_team=away,
                            book=book,
                            market=market_name,
                            selection=str(outcome.get("name") or outcome.get("selection") or ""),
                            american_odds=parse_american(outcome.get("price", outcome.get("odds"))),
                            point=_optional_float(outcome.get("point")),
                            source=source,
                        )
                    )
    return quotes


def quotes_from_csv(path: Path, source: str | None = None) -> list[Quote]:
    quotes: list[Quote] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV {path} has no header")
        required = {
            "event_id",
            "sport",
            "commence_time",
            "home_team",
            "away_team",
            "book",
            "market",
            "selection",
            "price",
        }
        missing = required - {h.strip() for h in reader.fieldnames}
        if missing:
            raise ValueError(f"CSV missing columns: {sorted(missing)}")
        for row in reader:
            if not any((v or "").strip() for v in row.values()):
                continue
            quotes.append(
                Quote(
                    event_id=row["event_id"].strip(),
                    sport=row["sport"].strip(),
                    commence_at=parse_commence(row["commence_time"]),
                    home_team=row["home_team"].strip(),
                    away_team=row["away_team"].strip(),
                    book=normalize_book(row["book"]),
                    market=normalize_market(row["market"]),
                    selection=row["selection"].strip(),
                    american_odds=parse_american(row["price"]),
                    point=_optional_float(row.get("point")),
                    source=source or f"csv:{path.name}",
                )
            )
    return quotes


def quotes_from_json(path: Path, source: str | None = None) -> list[Quote]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return quotes_from_odds_api_payload(payload, source or f"json:{path.name}")


class FixtureProvider:
    """Load Odds API-shaped JSON or a flat CSV so demos need no paid keys."""

    name = "fixture"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"Fixture not found: {self.path}")

    def fetch_quotes(self) -> list[Quote]:
        suffix = self.path.suffix.lower()
        if suffix == ".csv":
            return quotes_from_csv(self.path, source=f"fixture:{self.path.name}")
        if suffix in {".json", ".jsonl"}:
            return quotes_from_json(self.path, source=f"fixture:{self.path.name}")
        raise ValueError(f"Unsupported fixture type {suffix} (use .json or .csv)")


class TheOddsApiProvider:
    """Optional live ingest. Requires THE_ODDS_API_KEY; never logged."""

    name = "theoddsapi"
    BASE = "https://api.the-odds-api.com/v4"

    def __init__(
        self,
        api_key: str,
        sports: list[str] | None = None,
        regions: str = "us,us2,eu",
        markets: str = "h2h,spreads,totals",
        timeout: float = 20.0,
    ) -> None:
        if not api_key:
            raise ValueError("THE_ODDS_API_KEY is not set")
        self._api_key = api_key
        self.sports = sports or [
            "baseball_mlb",
            "americanfootball_nfl",
            "americanfootball_ncaaf",
            "basketball_nba",
            "icehockey_nhl",
        ]
        # Hypothesis: Pinnacle is on the Odds API EU region; us+us2+eu so sharp vs FD retail can land.
        self.regions = regions
        self.markets = markets
        self.timeout = timeout

    def fetch_quotes(self) -> list[Quote]:
        quotes: list[Quote] = []
        with httpx.Client(timeout=self.timeout) as client:
            for sport in self.sports:
                response = client.get(
                    f"{self.BASE}/sports/{sport}/odds",
                    params={
                        "apiKey": self._api_key,
                        "regions": self.regions,
                        "markets": self.markets,
                        "oddsFormat": "american",
                    },
                )
                response.raise_for_status()
                quotes.extend(quotes_from_odds_api_payload(response.json(), source=self.name))
        return quotes


class SportsGameOddsProvider:
    """Payable live ingest. Requires SPORTSGAMEODDS_API_KEY; never logged."""

    name = "sportsgameodds"
    BASE = "https://api.sportsgameodds.com/v2"

    def __init__(self, api_key: str, leagues: str = "NFL,NBA,MLB,NCAAF,NHL", timeout: float = 20.0) -> None:
        if not api_key:
            raise ValueError("SPORTSGAMEODDS_API_KEY is not set")
        self._api_key = api_key
        self.leagues = leagues
        self.timeout = timeout

    def fetch_quotes(self) -> list[Quote]:
        from sporty_hq.cheap_history import quotes_from_sgo_payload

        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                f"{self.BASE}/events",
                params={"leagueID": self.leagues, "oddsAvailable": "true", "limit": "40"},
                headers={"x-api-key": self._api_key},
            )
            if response.status_code in {401, 403}:
                raise ValueError(
                    "SportsGameOdds rejected the key (401/403). HQ will not invent quotes."
                )
            response.raise_for_status()
            quotes = quotes_from_sgo_payload(response.json(), source=self.name)
        if not quotes:
            raise ValueError(
                "SportsGameOdds returned no mappable FanDuel/Pinnacle mains. HQ will not invent quotes."
            )
        return quotes


def load_provider(kind: str, path: Path | None = None, api_key: str | None = None) -> OddsProvider:
    kind = kind.strip().lower()
    if kind in {"fixture", "json", "csv", "file"}:
        if path is None:
            raise ValueError("--path is required for fixture ingest")
        return FixtureProvider(path)
    if kind in {"oddsapi", "theoddsapi", "the-odds-api"}:
        return TheOddsApiProvider(api_key=api_key or "")
    if kind in {"sportsgameodds", "sgo", "sports-game-odds"}:
        return SportsGameOddsProvider(api_key=api_key or "")
    raise ValueError(
        f"Unknown provider '{kind}' (use fixture, oddsapi, or sportsgameodds)"
    )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
