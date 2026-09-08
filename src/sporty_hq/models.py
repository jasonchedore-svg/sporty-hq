"""Domain models for quotes, candidates, bets, and alerts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


STRAIGHT_MARKETS = frozenset({"ml", "h2h", "spread", "spreads", "total", "totals"})

POSTMORTEM_TAGS = frozenset(
    {"injury_missed", "weather_ignored", "steam_missed", "other"}
)
SHARP_BOOK_DEFAULT = "pinnacle"

MARKET_ALIASES = {
    "ml": "ml",
    "h2h": "ml",
    "moneyline": "ml",
    "money_line": "ml",
    "spread": "spread",
    "spreads": "spread",
    "handicap": "spread",
    "total": "total",
    "totals": "total",
    "ou": "total",
    "over_under": "total",
}


class Result(str, Enum):
    WIN = "win"
    LOSS = "loss"
    PUSH = "push"
    VOID = "void"


class AlertType(str, Enum):
    NEW_CANDIDATE = "new_candidate"
    PRE_GAME_REMINDER = "pre_game_reminder"
    SETTLE_REMINDER = "settle_reminder"
    TEST = "test"


def normalize_market(market: str) -> str:
    key = market.strip().lower().replace("-", "_").replace(" ", "_")
    if key not in MARKET_ALIASES:
        raise ValueError(
            f"Market '{market}' is not a straight (use ml/spread/total). Playbook: straights only."
        )
    return MARKET_ALIASES[key]


def display_market(market: str) -> str:
    normalized = normalize_market(market)
    return {"ml": "ML", "spread": "Spread", "total": "Total"}[normalized]


def normalize_book(book: str) -> str:
    return book.strip().lower().replace(" ", "").replace("-", "").replace("_", "")


def normalize_postmortem(tag: str) -> str:
    key = tag.strip().lower().replace("-", "_").replace(" ", "_")
    if key not in POSTMORTEM_TAGS:
        raise ValueError(
            f"Postmortem '{tag}' is not one of: {', '.join(sorted(POSTMORTEM_TAGS))}"
        )
    return key


@dataclass(frozen=True)
class Quote:
    event_id: str
    sport: str
    commence_at: datetime
    home_team: str
    away_team: str
    book: str
    market: str
    selection: str
    american_odds: int
    point: float | None = None
    source: str = "unknown"

    @property
    def event_name(self) -> str:
        return f"{self.away_team} @ {self.home_team}"

    @property
    def line_key(self) -> tuple:
        point = None if self.point is None else round(float(self.point), 2)
        return (self.event_id, self.market, self.selection.strip().lower(), point)


@dataclass
class Candidate:
    event_id: str
    sport: str
    event_name: str
    commence_at: datetime
    market: str
    selection: str
    book: str
    american_odds: int
    decimal_odds: float
    fair_prob: float
    implied_prob: float
    edge_pct: float
    juice_pct: float
    rationale: str
    point: float | None = None
    consensus_books: list[str] = field(default_factory=list)
    pinnacle_odds: int | None = None
    pinnacle_fair: float | None = None
    suggested_stake: float | None = None
    lesson_hits: list[str] = field(default_factory=list)
    id: int | None = None
    scanned_at: datetime | None = None

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["commence_at"] = self.commence_at.isoformat()
        row["scanned_at"] = self.scanned_at.isoformat() if self.scanned_at else None
        return row


@dataclass
class Bet:
    id: str
    logged_at: datetime
    event_id: str
    event_name: str
    sport: str
    commence_at: datetime | None
    market: str
    selection: str
    odds_at_bet: int
    stake: float
    result: str | None = None
    close_odds: int | None = None
    clv_pct: float | None = None
    pnl: float | None = None
    edge_note: str = ""
    settled_at: datetime | None = None
    point: float | None = None
    postmortem: str | None = None
    lesson: str = ""

    @property
    def is_open(self) -> bool:
        return self.result is None

    @property
    def pick(self) -> str:
        if self.point is None:
            return self.selection
        if self.market == "spread":
            return f"{self.selection} {self.point:+g}"
        return f"{self.selection} {self.point:g}"

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        for key in ("logged_at", "commence_at", "settled_at"):
            value = getattr(self, key)
            row[key] = value.isoformat() if value else None
        row["pick"] = self.pick
        return row


@dataclass
class Lesson:
    """Persisted postmortem from a settled ticket. Surfaced on similar scans."""

    sport: str
    event_id: str
    event_name: str
    market: str
    selection: str
    postmortem: str
    lesson: str
    teams: list[str] = field(default_factory=list)
    bet_id: str | None = None
    created_at: datetime | None = None
    id: int | None = None

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["created_at"] = self.created_at.isoformat() if self.created_at else None
        return row


@dataclass
class Alert:
    type: AlertType
    title: str
    body: str
    dedup_key: str
    payload: dict[str, Any] = field(default_factory=dict)
