"""Canonical live + backtest feed identity (payable cheap path).

Payable path: **The Odds API** and/or **SportsGameOdds**, FanDuel as the retail
take, plus **Pinnacle only if that provider exposes it**. OpticOdds is not a
hard requirement and cannot clear gate 1 (enterprise archive we will not invent).

If the cheaper feed has no Pinnacle close: log a KNOWN LIABILITY and score CLV
against the provider's own close. Never invent Pinnacle prices.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from sporty_hq.models import normalize_book

LIVE_FEED_ID = "oddsapi|sportsgameodds+optional-pinnacle"
LIVE_POSTED_BOOK = "fanduel"
LIVE_SHARP_BOOK = "pinnacle"

FEED_ODDSAPI = "oddsapi"
FEED_SGO = "sportsgameodds"
FEED_OPTICODDS = "opticodds"

ALLOWED_POSTED_FEEDS = frozenset({FEED_ODDSAPI, FEED_SGO})

POSTED_FEED_ALIASES = {
    "oddsapi": FEED_ODDSAPI,
    "theoddsapi": FEED_ODDSAPI,
    "the-odds-api": FEED_ODDSAPI,
    "the_odds_api": FEED_ODDSAPI,
    "odds-api": FEED_ODDSAPI,
    "sportsgameodds": FEED_SGO,
    "sports-game-odds": FEED_SGO,
    "sgo": FEED_SGO,
    "sportsgameodds.com": FEED_SGO,
}

ENTERPRISE_FEEDS = frozenset(
    {
        FEED_OPTICODDS,
        "optic",
        "sse",
        "opticodds-sse",
        "opticodds_sse",
        "websocket",
        "ws",
    }
)

SHARP_ONLY_FEEDS = frozenset(
    {"pinnacle", "pinny", "sharp", "pinnacle-history", "pinnacle_history"}
)

FORBIDDEN_POSTED_BOOKS = frozenset({"pinnacle", "pinnaclesports", "pinny", "sharp"})

LIABILITY_NO_PINNACLE = (
    "KNOWN LIABILITY: cheaper feed did not expose Pinnacle closes. CLV uses the "
    "provider's own close. HQ did not invent Pinnacle prices. Expect a thinner "
    "or noisier edge than a true sharp close."
)
LIABILITY_THIN_HISTORY = (
    "KNOWN COST: Odds API / SportsGameOdds history is thinner and gappier than "
    "an enterprise OpticOdds archive. Missing events/markets/snapshots are "
    "logged, not filled. A passing CLV on this feed may not survive a denser book."
)
LIABILITY_OPTICODDS_DROPPED = (
    "OpticOdds is not the payable path and is not required for gate 1. HQ will "
    "not invent OpticOdds archives or silently substitute them."
)


def describe_live_feed() -> str:
    return (
        "Payable path: The Odds API or SportsGameOdds (FanDuel retail take) plus "
        "Pinnacle only if that provider exposes it. Paper trade only. OpticOdds "
        "is not required and is not invented."
    )


def normalize_feed(value: str | None) -> str:
    return (value or "").strip().lower().replace(" ", "").replace("_", "-")


def canonical_posted_feed(value: str | None) -> str:
    raw = (value or "").strip().lower().replace(" ", "")
    hyphen = raw.replace("_", "-")
    if raw in POSTED_FEED_ALIASES:
        return POSTED_FEED_ALIASES[raw]
    if hyphen in POSTED_FEED_ALIASES:
        return POSTED_FEED_ALIASES[hyphen]
    if raw in ENTERPRISE_FEEDS or hyphen in ENTERPRISE_FEEDS:
        return FEED_OPTICODDS
    return hyphen or raw


@dataclass(frozen=True)
class FeedParity:
    matched: bool
    feed_id: str
    posted_feeds: tuple[str, ...]
    posted_books: tuple[str, ...]
    close_books: tuple[str, ...]
    close_feeds: tuple[str, ...]
    detail: str
    pinnacle_close: bool = False
    liabilities: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "feed_id": self.feed_id,
            "posted_feeds": list(self.posted_feeds),
            "posted_books": list(self.posted_books),
            "close_books": list(self.close_books),
            "close_feeds": list(self.close_feeds),
            "detail": self.detail,
            "pinnacle_close": self.pinnacle_close,
            "liabilities": list(self.liabilities),
            "live_feed_id": LIVE_FEED_ID,
            "live_path": describe_live_feed(),
        }


def evaluate_feed_parity(
    rows: Iterable[Any],
    *,
    target_book: str = LIVE_POSTED_BOOK,
    sharp_book: str = LIVE_SHARP_BOOK,
) -> FeedParity:
    """Require the payable cheap feed. Pinnacle close is optional, never invented."""
    target = normalize_book(target_book) or LIVE_POSTED_BOOK
    sharp = normalize_book(sharp_book) or LIVE_SHARP_BOOK
    items = list(rows)
    empty = FeedParity(
        matched=False,
        feed_id="missing",
        posted_feeds=(),
        posted_books=(),
        close_books=(),
        close_feeds=(),
        detail="No historical rows — cannot prove feed parity with the payable Odds API / SportsGameOdds path.",
        liabilities=(LIABILITY_THIN_HISTORY,),
    )
    if not items:
        return empty

    posted_feeds = tuple(canonical_posted_feed(getattr(r, "posted_feed", None)) for r in items)
    posted_books = tuple(
        normalize_book(getattr(r, "posted_book", None) or "") or "" for r in items
    )
    close_books = tuple(
        normalize_book(getattr(r, "close_book", None) or "") or "" for r in items
    )
    close_feeds = tuple(canonical_posted_feed(getattr(r, "close_feed", None)) for r in items)

    def fail(feed_id: str, detail: str, extra: tuple[str, ...] = ()) -> FeedParity:
        return FeedParity(
            matched=False,
            feed_id=feed_id,
            posted_feeds=tuple(sorted({f for f in posted_feeds if f})),
            posted_books=tuple(sorted({b for b in posted_books if b})),
            close_books=tuple(sorted({b for b in close_books if b})),
            close_feeds=tuple(sorted({f for f in close_feeds if f})),
            detail=detail,
            pinnacle_close=False,
            liabilities=(LIABILITY_THIN_HISTORY,) + extra,
        )

    if any(not f for f in posted_feeds) or any(not b for b in posted_books):
        return fail(
            "unlabeled",
            "Feed identity required on every row (posted_feed=oddsapi|sportsgameodds, "
            "posted_book=fanduel). Unlabeled history cannot clear gate 1.",
        )

    if any(f == FEED_OPTICODDS or f in ENTERPRISE_FEEDS for f in posted_feeds):
        return fail(
            "opticodds-enterprise",
            "FORBIDDEN for gate 1: OpticOdds is not the payable path. "
            "Backtest on The Odds API or SportsGameOdds. HQ will not invent an "
            f"OpticOdds archive. Found posted_feed={sorted(set(posted_feeds))}.",
            extra=(LIABILITY_OPTICODDS_DROPPED,),
        )

    sharp_feeds = sorted({f for f in posted_feeds if f in SHARP_ONLY_FEEDS})
    if sharp_feeds:
        return fail(
            "pinnacle-only",
            "FORBIDDEN: posted/take is Pinnacle/sharp-only. Live take is FanDuel "
            f"on Odds API or SportsGameOdds. Found posted_feed={sharp_feeds}.",
        )

    if any(b in FORBIDDEN_POSTED_BOOKS for b in posted_books):
        return fail(
            "pinnacle-only",
            "FORBIDDEN: posted/take book is Pinnacle. Live take is FanDuel. "
            f"Found posted_book={sorted(set(posted_books))}.",
        )

    unknown = sorted({f for f in posted_feeds if f not in ALLOWED_POSTED_FEEDS})
    if unknown:
        return fail(
            "mismatch",
            "posted_feed must be oddsapi or sportsgameodds (payable cheap path). "
            f"Found {unknown}.",
        )

    if any(b != target for b in posted_books):
        return fail(
            "mismatch",
            f"posted_book must be {target} (live target book). "
            f"Found {sorted(set(posted_books))}.",
        )

    unique_posted = tuple(sorted({f for f in posted_feeds if f}))
    unique_close_books = tuple(sorted({b for b in close_books if b}))
    unique_close_feeds = tuple(sorted({f for f in close_feeds if f}))
    pinnacle_close = bool(unique_close_books) and all(b == sharp for b in close_books)
    liabilities: list[str] = [LIABILITY_THIN_HISTORY]
    if not pinnacle_close:
        liabilities.append(LIABILITY_NO_PINNACLE)
        if any(not b for b in close_books):
            liabilities.append(
                "KNOWN LIABILITY: close_book unlabeled on one or more rows — "
                "not treated as Pinnacle. HQ did not invent a sharp close."
            )
        elif unique_close_books:
            liabilities.append(
                f"Close books present: {list(unique_close_books)} (not all {sharp})."
            )

    detail = (
        f"Feed parity matched payable path {LIVE_FEED_ID}: posted {list(unique_posted)}/"
        f"{target}. Pinnacle close={'yes' if pinnacle_close else 'NO — liability logged'}."
    )
    return FeedParity(
        matched=True,
        feed_id=LIVE_FEED_ID,
        posted_feeds=unique_posted,
        posted_books=(target,),
        close_books=unique_close_books,
        close_feeds=unique_close_feeds,
        detail=detail,
        pinnacle_close=pinnacle_close,
        liabilities=tuple(liabilities),
    )
