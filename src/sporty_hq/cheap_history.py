"""Historical closes from payable cheap feeds. Never invent prices or OpticOdds data.

Odds API historical is a paid snapshot API (quota-heavy, daysFrom scores ≤ 3).
SportsGameOdds historical (finalized + includeOpenCloseOdds) needs a Pro-class plan.
Gaps stay gaps. Missing Pinnacle stays a liability, not a fake close.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from sporty_hq.backtest import HistoricalClose
from sporty_hq.models import Quote, normalize_book, normalize_market
from sporty_hq.odds_math import parse_american

FetchJson = Callable[[str, dict[str, Any] | None, dict[str, str] | None], Any]

DEFAULT_SPORTS = (
    "baseball_mlb",
    "americanfootball_nfl",
    "americanfootball_ncaaf",
)
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SGO_BASE = "https://api.sportsgameodds.com/v2"
MAX_EVENTS = 20
MAX_SNAPSHOTS = 12
POSTED_LAG = timedelta(hours=3)
CLOSE_LAG = timedelta(minutes=5)


class HistoricalUnavailable(RuntimeError):
    """Provider refused history or returned nothing usable. Do not invent."""


def _utc(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _httpx_fetch(base_headers: dict[str, str] | None = None) -> FetchJson:
    def fetch(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        merged = dict(base_headers or {})
        if headers:
            merged.update(headers)
        with httpx.Client(timeout=25.0) as client:
            response = client.get(url, params=params or {}, headers=merged)
            if response.status_code in {401, 403}:
                raise HistoricalUnavailable(
                    f"{url.split('?')[0]} returned {response.status_code} — this key cannot "
                    "read historical odds. Export snapshots to --path. HQ will not invent closes."
                )
            if response.status_code == 422:
                raise HistoricalUnavailable(
                    "Historical request rejected (plan or date). Export snapshots to --path. "
                    "HQ will not invent closes."
                )
            response.raise_for_status()
            return response.json()

    return fetch


def fetch_odds_api_historical_closes(
    api_key: str,
    *,
    sports: tuple[str, ...] | list[str] = DEFAULT_SPORTS,
    days_from: int = 3,
    fetch: FetchJson | None = None,
) -> list[HistoricalClose]:
    """Pair FanDuel posted vs Pinnacle (or FanDuel) close from Odds API snapshots.

    Scores only cover ``daysFrom`` ≤ 3. That is a known thin-history cost, not a
    fake season. Historical snapshots require a paid Odds API plan.
    """
    if not api_key:
        raise HistoricalUnavailable("THE_ODDS_API_KEY is not set. HQ will not invent Odds API history.")
    getter = fetch or _httpx_fetch()
    rows: list[HistoricalClose] = []
    snapshot_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def snapshot(sport: str, when: datetime) -> list[dict[str, Any]]:
        key = (sport, _iso(when))
        if key in snapshot_cache:
            return snapshot_cache[key]
        if len(snapshot_cache) >= MAX_SNAPSHOTS:
            snapshot_cache[key] = []
            return []
        payload = getter(
            f"{ODDS_API_BASE}/historical/sports/{sport}/odds",
            {
                "apiKey": api_key,
                "regions": "us,eu",
                "markets": "h2h,spreads,totals",
                "oddsFormat": "american",
                "bookmakers": "fanduel,pinnacle",
                "date": _iso(when),
            },
            None,
        )
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        events = list(data) if isinstance(data, list) else []
        snapshot_cache[key] = events
        return events

    for sport in sports:
        scores = getter(
            f"{ODDS_API_BASE}/sports/{sport}/scores",
            {"apiKey": api_key, "daysFrom": max(1, min(int(days_from), 3))},
            None,
        )
        events = scores if isinstance(scores, list) else list((scores or {}).get("data") or [])
        completed = [e for e in events if e.get("completed") and e.get("id") and e.get("commence_time")]
        for event in completed[:MAX_EVENTS]:
            commence = _utc(event.get("commence_time"))
            if commence is None:
                continue
            posted_snap = snapshot(sport, commence - POSTED_LAG)
            close_snap = snapshot(sport, commence - CLOSE_LAG)
            posted_event = _event_by_id(posted_snap, str(event["id"]))
            close_event = _event_by_id(close_snap, str(event["id"]))
            if not posted_event or not close_event:
                continue
            rows.extend(
                _pairs_from_odds_api_events(
                    posted_event,
                    close_event,
                    sport=sport,
                    commence=commence,
                    source_feed="oddsapi",
                )
            )
    if not rows:
        raise HistoricalUnavailable(
            "Odds API returned no FanDuel posted/close pairs (scores only cover ~3 days, "
            "and historical snapshots are a paid add-on). Pass --path to an Odds API export. "
            "HQ will not invent a season of closes."
        )
    return rows


def fetch_sportsgameodds_historical_closes(
    api_key: str,
    *,
    leagues: str = "NFL,NBA,MLB,NCAAF",
    fetch: FetchJson | None = None,
) -> list[HistoricalClose]:
    """Finalized SGO events with includeOpenCloseOdds. Skip unmapped books/markets."""
    if not api_key:
        raise HistoricalUnavailable(
            "SPORTSGAMEODDS_API_KEY is not set. HQ will not invent SportsGameOdds history."
        )
    getter = fetch or _httpx_fetch({"x-api-key": api_key})
    payload = getter(
        f"{SGO_BASE}/events",
        {
            "leagueID": leagues,
            "finalized": "true",
            "includeOpenCloseOdds": "true",
            "limit": str(MAX_EVENTS),
        },
        {"x-api-key": api_key},
    )
    events = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(events, list):
        raise HistoricalUnavailable(
            "SportsGameOdds historical response was not a list of events. "
            "Pass --path. HQ will not invent closes."
        )
    rows: list[HistoricalClose] = []
    for event in events:
        rows.extend(_pairs_from_sgo_event(event))
    if not rows:
        raise HistoricalUnavailable(
            "SportsGameOdds returned events but no FanDuel open/close pairs we could map "
            "(historical + includeOpenCloseOdds needs a Pro-class plan). Pass --path. "
            "HQ will not invent closes."
        )
    return rows


def quotes_from_sgo_payload(payload: Any, source: str = "sportsgameodds") -> list[Quote]:
    """Live/upcoming SGO events → Quote rows. Skip anything we cannot map."""
    events = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(events, list):
        return []
    quotes: list[Quote] = []
    for event in events:
        event_id = str(event.get("eventID") or event.get("id") or "")
        if not event_id:
            continue
        sport = _sgo_sport(event)
        teams = event.get("teams") or {}
        home = _team_name(teams.get("home") or event.get("homeTeam") or {})
        away = _team_name(teams.get("away") or event.get("awayTeam") or {})
        commence = _utc(event.get("status", {}).get("startsAt") or event.get("startTime") or event.get("startsAt"))
        if commence is None:
            continue
        odds = event.get("odds") or {}
        if isinstance(odds, dict):
            items = odds.values()
        elif isinstance(odds, list):
            items = odds
        else:
            continue
        for odd in items:
            market = _sgo_market(odd)
            if not market:
                continue
            selection = _sgo_selection(odd, home=home, away=away)
            by_book = odd.get("byBookmaker") or {}
            if not isinstance(by_book, dict):
                continue
            for book_id, book_odd in by_book.items():
                book = normalize_book(str(book_id))
                price = _sgo_price(book_odd, ("odds", "currentOdds", "price"))
                if price is None:
                    continue
                quotes.append(
                    Quote(
                        event_id=event_id,
                        sport=sport,
                        commence_at=commence,
                        home_team=home,
                        away_team=away,
                        book=book,
                        market=market,
                        selection=selection,
                        american_odds=price,
                        point=_optional_float(book_odd.get("spread") or book_odd.get("overUnder") or odd.get("spread")),
                        source=source,
                    )
                )
    return quotes


def _event_by_id(events: list[dict[str, Any]], event_id: str) -> dict[str, Any] | None:
    for event in events:
        if str(event.get("id") or "") == event_id:
            return event
    return None


def _pairs_from_odds_api_events(
    posted_event: dict[str, Any],
    close_event: dict[str, Any],
    *,
    sport: str,
    commence: datetime,
    source_feed: str,
) -> list[HistoricalClose]:
    home = str(posted_event.get("home_team") or "")
    away = str(posted_event.get("away_team") or "")
    event_name = f"{away} @ {home}".strip(" @")
    season = str(commence.year)
    posted_books = _index_book_markets(posted_event)
    close_books = _index_book_markets(close_event)
    fd_posted = posted_books.get("fanduel") or {}
    pin_close = close_books.get("pinnacle") or {}
    fd_close = close_books.get("fanduel") or {}
    close_map = pin_close or fd_close
    close_book = "pinnacle" if pin_close else ("fanduel" if fd_close else "")
    if not fd_posted or not close_map or not close_book:
        return []
    rows: list[HistoricalClose] = []
    for key, posted_price in fd_posted.items():
        close_price = close_map.get(key)
        if close_price is None:
            continue
        market, selection, point = key
        posted_at = posted_price[1]
        close_at = close_price[1]
        rows.append(
            HistoricalClose(
                season=season,
                event_id=str(posted_event.get("id") or ""),
                sport=sport,
                event=event_name,
                market=market,
                selection=selection,
                posted_odds=posted_price[0],
                close_odds=close_price[0],
                posted_feed=source_feed,
                posted_book="fanduel",
                close_book=close_book,
                close_feed=source_feed,
                posted_at=posted_at,
                close_at=close_at,
                commence_at=_iso(commence),
                close_kind="true_close",
            )
        )
        _ = point
    return rows


def _index_book_markets(event: dict[str, Any]) -> dict[str, dict[tuple[str, str, float | None], tuple[int, str]]]:
    out: dict[str, dict[tuple[str, str, float | None], tuple[int, str]]] = {}
    for bookmaker in event.get("bookmakers") or []:
        book = normalize_book(str(bookmaker.get("key") or ""))
        if not book:
            continue
        updated = str(bookmaker.get("last_update") or "")
        bucket = out.setdefault(book, {})
        for market in bookmaker.get("markets") or []:
            try:
                market_name = normalize_market(str(market.get("key") or ""))
            except ValueError:
                continue
            for outcome in market.get("outcomes") or []:
                try:
                    price = parse_american(outcome.get("price"))
                except (TypeError, ValueError):
                    continue
                selection = str(outcome.get("name") or "")
                point = _optional_float(outcome.get("point"))
                bucket[(market_name, selection, point)] = (price, updated)
    return out


def _pairs_from_sgo_event(event: dict[str, Any]) -> list[HistoricalClose]:
    event_id = str(event.get("eventID") or event.get("id") or "")
    if not event_id:
        return []
    sport = _sgo_sport(event)
    teams = event.get("teams") or {}
    home = _team_name(teams.get("home") or {})
    away = _team_name(teams.get("away") or {})
    commence = _utc(event.get("status", {}).get("startsAt") or event.get("startTime"))
    if commence is None:
        return []
    season = str(commence.year)
    odds = event.get("odds") or {}
    items = odds.values() if isinstance(odds, dict) else odds if isinstance(odds, list) else []
    rows: list[HistoricalClose] = []
    for odd in items:
        market = _sgo_market(odd)
        if not market:
            continue
        selection = _sgo_selection(odd, home=home, away=away)
        by_book = odd.get("byBookmaker") or {}
        if not isinstance(by_book, dict):
            continue
        fd = by_book.get("fanduel") or by_book.get("FanDuel") or {}
        pin = by_book.get("pinnacle") or by_book.get("Pinnacle") or {}
        posted = _sgo_price(fd, ("openOdds", "odds", "currentOdds"))
        pin_close = _sgo_price(pin, ("closeOdds", "odds"))
        fd_close = _sgo_price(fd, ("closeOdds", "odds"))
        close_odds = pin_close if pin_close is not None else fd_close
        close_book = "pinnacle" if pin_close is not None else ("fanduel" if fd_close is not None else "")
        if posted is None or close_odds is None or not close_book:
            continue
        rows.append(
            HistoricalClose(
                season=season,
                event_id=event_id,
                sport=sport,
                event=f"{away} @ {home}".strip(" @"),
                market=market,
                selection=selection,
                posted_odds=posted,
                close_odds=close_odds,
                posted_feed="sportsgameodds",
                posted_book="fanduel",
                close_book=close_book,
                close_feed="sportsgameodds",
                posted_at=str(fd.get("lastUpdateAt") or ""),
                close_at=str((pin or fd).get("lastUpdateAt") or ""),
                commence_at=_iso(commence),
                close_kind="true_close",
            )
        )
    return rows


def _sgo_sport(event: dict[str, Any]) -> str:
    league = str(event.get("leagueID") or event.get("league") or "").upper()
    sport = str(event.get("sportID") or event.get("sport") or "").lower()
    mapping = {
        "MLB": "baseball_mlb",
        "NFL": "americanfootball_nfl",
        "NCAAF": "americanfootball_ncaaf",
        "NBA": "basketball_nba",
        "NHL": "icehockey_nhl",
    }
    if league in mapping:
        return mapping[league]
    if "baseball" in sport:
        return "baseball_mlb"
    if "football" in sport and "ncaa" in league.lower():
        return "americanfootball_ncaaf"
    if "football" in sport:
        return "americanfootball_nfl"
    return sport or "unknown"


def _sgo_market(odd: dict[str, Any]) -> str | None:
    bet = str(odd.get("betTypeID") or odd.get("betType") or "").lower()
    period = str(odd.get("periodID") or odd.get("period") or "game").lower()
    if period not in {"game", "ft", "full", ""}:
        return None
    if bet in {"ml", "moneyline", "h2h"}:
        return "ml"
    if bet in {"sp", "spread", "spreads"}:
        return "spread"
    if bet in {"ou", "total", "totals", "overunder"}:
        return "total"
    odd_id = str(odd.get("oddID") or "")
    if "-ml-" in odd_id:
        return "ml"
    if "-sp-" in odd_id:
        return "spread"
    if "-ou-" in odd_id:
        return "total"
    return None


def _sgo_selection(odd: dict[str, Any], *, home: str, away: str) -> str:
    side = str(odd.get("sideID") or odd.get("side") or "").lower()
    if side in {"home", "over"}:
        return home if side == "home" else "Over"
    if side in {"away", "under"}:
        return away if side == "away" else "Under"
    return str(odd.get("playerName") or odd.get("statEntityID") or side or "")


def _sgo_price(book_odd: Any, keys: tuple[str, ...]) -> int | None:
    if not isinstance(book_odd, dict):
        return None
    for key in keys:
        if book_odd.get(key) in (None, ""):
            continue
        try:
            return parse_american(book_odd.get(key))
        except (TypeError, ValueError):
            continue
    return None


def _team_name(team: Any) -> str:
    if isinstance(team, str):
        return team
    if not isinstance(team, dict):
        return ""
    names = team.get("names") or {}
    return str(names.get("long") or names.get("medium") or team.get("name") or team.get("teamID") or "")


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
