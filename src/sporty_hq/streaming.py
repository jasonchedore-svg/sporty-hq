"""Leftover OpticOdds push ingest (explicit --source opticodds / stream replay).

Payable live + backtest path is The Odds API / SportsGameOdds. This module is
not required for gate 1 and must not invent historical OpticOdds closes.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx

from sporty_hq.models import Quote, normalize_book, normalize_market
from sporty_hq.odds_math import parse_american
from sporty_hq.providers import FixtureProvider

OPTICODDS_BASE = "https://api.opticodds.com/api/v3"
OPTICODDS_WS_BASE = "wss://api.opticodds.com/api/v3/stream"
OPTICODDS_SPORTS = ("baseball", "football", "basketball", "hockey")
# Hypothesis: OpticOdds sportsbook labels; max 5 per SSE connection.
DEFAULT_SPORTSBOOKS = ("FanDuel", "Pinnacle", "DraftKings", "BetMGM", "Caesars")
# Hypothesis: league filters for v1 sports on OpticOdds football/baseball paths.
DEFAULT_LEAGUES = ("MLB", "NFL", "NCAAF", "NBA", "NHL")


class StreamingUnavailable(RuntimeError):
    """Raised when a requested push transport does not exist."""


class StreamProvider(Protocol):
    name: str
    transport: str  # sse | websocket | replay | rest-snapshot

    def fetch_quotes(self) -> list[Quote]:
        ...

    def iter_quote_batches(self) -> Iterator[list[Quote]]:
        ...


def parse_sse_block(block: str) -> dict[str, str]:
    """Parse one SSE event (event/id/data/retry fields)."""
    event: dict[str, str] = {"event": "message", "data": "", "id": "", "retry": ""}
    data_lines: list[str] = []
    for raw in block.splitlines():
        line = raw.rstrip("\r")
        if not line or line.startswith(":"):
            continue
        if ":" in line:
            field, value = line.split(":", 1)
            value = value[1:] if value.startswith(" ") else value
        else:
            field, value = line, ""
        if field == "data":
            data_lines.append(value)
        elif field in event:
            event[field] = value
    event["data"] = "\n".join(data_lines)
    return event


def iter_sse_events(text: str) -> Iterator[dict[str, str]]:
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        yield parse_sse_block(block)


def _opticodds_sport_key(league: str, sport: str) -> str:
    league_u = (league or "").strip().upper()
    mapping = {
        "MLB": "baseball_mlb",
        "NFL": "americanfootball_nfl",
        "NCAAF": "americanfootball_ncaaf",
        "NBA": "basketball_nba",
        "NHL": "icehockey_nhl",
    }
    if league_u in mapping:
        return mapping[league_u]
    sport_l = (sport or "").strip().lower()
    if sport_l in {"baseball", "mlb"}:
        return "baseball_mlb"
    if sport_l in {"football", "nfl"}:
        return "americanfootball_nfl"
    if sport_l in {"basketball", "nba"}:
        return "basketball_nba"
    if sport_l in {"hockey", "nhl"}:
        return "icehockey_nhl"
    return sport_l or "unknown"


def quotes_from_opticodds_odds(payload: Any, source: str = "opticodds") -> list[Quote]:
    """Map an OpticOdds ``event: odds`` JSON blob to HQ quotes (mains only)."""
    if isinstance(payload, str):
        payload = json.loads(payload) if payload.strip() else {}
    items: Iterable[Any]
    if isinstance(payload, dict):
        raw = payload.get("data", payload)
        items = raw if isinstance(raw, list) else [raw]
    elif isinstance(payload, list):
        items = payload
    else:
        return []

    quotes: list[Quote] = []
    for odd in items:
        if not isinstance(odd, dict):
            continue
        if odd.get("is_main") is False:
            continue
        market_raw = str(odd.get("market") or "")
        lowered = market_raw.lower()
        if any(tok in lowered for tok in ("prop", "player", "period", "1st half", "first half", "alternate")):
            continue
        try:
            market = normalize_market(market_raw or str(odd.get("name") or ""))
        except ValueError:
            continue
        fixture_id = str(odd.get("fixture_id") or odd.get("game_id") or odd.get("id") or "")
        if not fixture_id:
            continue
        home = str(odd.get("home_team") or odd.get("home_competitor") or "")
        away = str(odd.get("away_team") or odd.get("away_competitor") or "")
        ts = odd.get("timestamp")
        if isinstance(ts, (int, float)):
            commence = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        elif odd.get("start_date") or odd.get("commence_time"):
            raw_t = str(odd.get("start_date") or odd.get("commence_time"))
            commence = datetime.fromisoformat(raw_t.replace("Z", "+00:00"))
            if commence.tzinfo is None:
                commence = commence.replace(tzinfo=timezone.utc)
        else:
            commence = datetime.now(timezone.utc)
        selection = str(odd.get("selection") or odd.get("name") or "")
        if odd.get("selection_line") in {"over", "under"} and selection.lower() not in {"over", "under"}:
            selection = str(odd["selection_line"]).title()
        try:
            price = parse_american(odd.get("price", odd.get("odds")))
        except (TypeError, ValueError):
            continue
        quotes.append(
            Quote(
                event_id=fixture_id,
                sport=_opticodds_sport_key(str(odd.get("league") or ""), str(odd.get("sport") or "")),
                commence_at=commence,
                home_team=home,
                away_team=away,
                book=normalize_book(str(odd.get("sportsbook") or odd.get("book") or "")),
                market=market,
                selection=selection,
                american_odds=price,
                point=_optional_float(odd.get("points", odd.get("point"))),
                source=source,
            )
        )
    return quotes


class FixtureReplayProvider:
    """Offline stream: emit fixture quotes as a single push batch."""

    name = "stream-replay"
    transport = "replay"

    def __init__(self, path: Path) -> None:
        self._inner = FixtureProvider(path)

    def fetch_quotes(self) -> list[Quote]:
        return self._inner.fetch_quotes()

    def iter_quote_batches(self) -> Iterator[list[Quote]]:
        quotes = self.fetch_quotes()
        if quotes:
            yield quotes


class OpticOddsSseProvider:
    """OpticOdds documented realtime transport: SSE (FAQ: no WebSockets)."""

    name = "opticodds"
    transport = "sse"
    BASE = OPTICODDS_BASE

    def __init__(
        self,
        api_key: str,
        sports: list[str] | None = None,
        sportsbooks: list[str] | None = None,
        leagues: list[str] | None = None,
        timeout: float = 15.0,
        max_events: int = 40,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OPTICODDS_API_KEY is not set")
        self._api_key = api_key
        self.sports = sports or list(OPTICODDS_SPORTS)
        self.sportsbooks = sportsbooks or list(DEFAULT_SPORTSBOOKS)
        self.leagues = leagues or list(DEFAULT_LEAGUES)
        self.timeout = timeout
        self.max_events = max_events
        self._client = http_client

    def fetch_quotes(self) -> list[Quote]:
        merged: list[Quote] = []
        for batch in self.iter_quote_batches():
            merged.extend(batch)
            if self.max_events and len(merged) >= self.max_events:
                break
        return merged[: self.max_events] if self.max_events else merged

    def iter_quote_batches(self) -> Iterator[list[Quote]]:
        client = self._client
        own_client = client is None
        if own_client:
            client = httpx.Client(timeout=httpx.Timeout(self.timeout, read=self.timeout))
        assert client is not None
        try:
            for sport in self.sports:
                params: list[tuple[str, str]] = [("odds_format", "AMERICAN"), ("is_main", "true")]
                for book in self.sportsbooks:
                    params.append(("sportsbook", book))
                for league in self.leagues:
                    params.append(("league", league))
                with client.stream(
                    "GET",
                    f"{self.BASE}/stream/odds/{sport}",
                    params=params,
                    headers={"X-Api-Key": self._api_key, "Accept": "text/event-stream"},
                ) as response:
                    response.raise_for_status()
                    buf = ""
                    n_events = 0
                    for chunk in response.iter_text():
                        buf += chunk
                        while "\n\n" in buf:
                            block, buf = buf.split("\n\n", 1)
                            event = parse_sse_block(block)
                            if event.get("event") != "odds":
                                continue
                            quotes = quotes_from_opticodds_odds(event.get("data") or "{}", source=self.name)
                            if quotes:
                                n_events += 1
                                yield quotes
                            if self.max_events and n_events >= self.max_events:
                                return
        finally:
            if own_client:
                client.close()


def connect_opticodds_ws(url: str, api_key: str, timeout: float):
    """Open a WebSocket. Never log ``url`` if it could contain a key (we use headers)."""
    try:
        from websockets.sync.client import connect as ws_connect
    except ImportError as exc:  # pragma: no cover - optional until installed
        raise StreamingUnavailable(
            "OpticOdds WebSocket client missing (pip install websockets). "
            "OpticOdds FAQ: they do not offer WebSockets; HQ falls back to SSE."
        ) from exc
    try:
        return ws_connect(
            url,
            additional_headers={"X-Api-Key": api_key, "User-Agent": "sporty-hq"},
            open_timeout=timeout,
            close_timeout=2.0,
        )
    except StreamingUnavailable:
        raise
    except Exception as exc:
        raise StreamingUnavailable(
            "OpticOdds WebSocket did not connect. FAQ: OpticOdds does not offer "
            "WebSockets; realtime is SSE GET /api/v3/stream/odds/{sport}."
        ) from exc


def opticodds_ws_url(sport: str, sportsbooks: list[str], leagues: list[str]) -> str:
    """Query string without the API key (key is an HTTP header)."""
    from urllib.parse import urlencode

    params: list[tuple[str, str]] = [("odds_format", "AMERICAN"), ("is_main", "true")]
    for book in sportsbooks:
        params.append(("sportsbook", book))
    for league in leagues:
        params.append(("league", league))
    return f"{OPTICODDS_WS_BASE}/odds/{sport}?{urlencode(params)}"


class OpticOddsWebsocketProvider:
    """Owner lock: try OpticOdds WebSocket before SSE. Pinnacle is a sportsbook on the stream."""

    name = "opticodds"
    transport = "websocket"

    def __init__(
        self,
        api_key: str,
        sports: list[str] | None = None,
        sportsbooks: list[str] | None = None,
        leagues: list[str] | None = None,
        timeout: float = 8.0,
        max_events: int = 40,
        ws_connect=None,
    ) -> None:
        if not api_key:
            raise ValueError("OPTICODDS_API_KEY is not set")
        self._api_key = api_key
        self.sports = sports or list(OPTICODDS_SPORTS)
        self.sportsbooks = sportsbooks or list(DEFAULT_SPORTSBOOKS)
        self.leagues = leagues or list(DEFAULT_LEAGUES)
        self.timeout = timeout
        self.max_events = max_events
        self._ws_connect = ws_connect or connect_opticodds_ws

    def fetch_quotes(self) -> list[Quote]:
        merged: list[Quote] = []
        for batch in self.iter_quote_batches():
            merged.extend(batch)
            if self.max_events and len(merged) >= self.max_events:
                break
        return merged[: self.max_events] if self.max_events else merged

    def iter_quote_batches(self) -> Iterator[list[Quote]]:
        n_events = 0
        for sport in self.sports:
            url = opticodds_ws_url(sport, self.sportsbooks, self.leagues)
            with self._ws_connect(url, self._api_key, self.timeout) as socket:
                for raw in socket:
                    quotes = quotes_from_opticodds_odds(raw, source=self.name)
                    if not quotes:
                        continue
                    n_events += 1
                    yield quotes
                    if self.max_events and n_events >= self.max_events:
                        return


class OpticOddsRealtimeProvider:
    """WebSocket first, SSE fallback. Never Odds API. Pinnacle rides the same OpticOdds stream."""

    name = "opticodds"

    def __init__(
        self,
        api_key: str,
        *,
        ws: StreamProvider | None = None,
        sse: StreamProvider | None = None,
        **kwargs: Any,
    ) -> None:
        if not api_key:
            raise ValueError("OPTICODDS_API_KEY is not set")
        self.ws = ws or OpticOddsWebsocketProvider(api_key, **kwargs)
        sse_kwargs = {k: v for k, v in kwargs.items() if k != "ws_connect"}
        self.sse = sse or OpticOddsSseProvider(api_key, **sse_kwargs)
        self.transport = "websocket"

    def fetch_quotes(self) -> list[Quote]:
        merged: list[Quote] = []
        for batch in self.iter_quote_batches():
            merged.extend(batch)
            cap = getattr(self.ws, "max_events", 40)
            if cap and len(merged) >= cap:
                break
        return merged

    def iter_quote_batches(self) -> Iterator[list[Quote]]:
        try:
            yield from self.ws.iter_quote_batches()
            self.transport = getattr(self.ws, "transport", "websocket")
        except StreamingUnavailable:
            self.transport = "sse"
            yield from self.sse.iter_quote_batches()


class TheOddsApiWebsocketStub:
    """Scaffold only — The Odds API has no public WebSocket (as of 2026).

    Hypothesis: a paid Quant-tier webhook/push may appear later. Until then this
    provider raises so callers degrade to fixture or a single REST snapshot.
    """

    name = "theoddsapi-ws"
    transport = "websocket"
    AVAILABLE = False

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or ""

    def fetch_quotes(self) -> list[Quote]:
        raise StreamingUnavailable(
            "The Odds API has no public WebSocket (REST polling only as of 2026). "
            "Use --source oddsapi for a REST snapshot. OpticOdds is not the payable path. "
            "HQ never scrapes in a loop."
        )

    def iter_quote_batches(self) -> Iterator[list[Quote]]:
        self.fetch_quotes()
        yield from ()


def collect_stream_quotes(
    provider: StreamProvider,
    *,
    max_events: int = 40,
) -> list[Quote]:
    out: list[Quote] = []
    for batch in provider.iter_quote_batches():
        out.extend(batch)
        if max_events and len(out) >= max_events:
            break
    return out[:max_events] if max_events else out


def load_stream_provider(
    *,
    opticodds_key: str | None,
    odds_api_key: str | None,
    replay_path: Path | None,
    fixture_fallback: Path | None,
) -> tuple[StreamProvider, str]:
    """Explicit OpticOdds leftover. Payable path is Odds API / SportsGameOdds REST."""
    if replay_path is not None:
        return FixtureReplayProvider(replay_path), "replay: fixture as push batch (offline/dev)"
    if opticodds_key:
        return (
            OpticOddsRealtimeProvider(opticodds_key),
            "leftover: OpticOdds realtime (not the payable path; cannot clear gate 1). "
            "WebSocket first, SSE if WS is unavailable. Odds API not used here.",
        )
    if odds_api_key:
        return (
            TheOddsApiWebsocketStub(odds_api_key),
            "Odds API has no public WebSocket. Use sporty ingest --source oddsapi for REST.",
        )
    if fixture_fallback is None:
        raise ValueError("No stream key and no fixture fallback path")
    return (
        FixtureReplayProvider(fixture_fallback),
        "degrade: no stream key — fixture ingest.",
    )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
