"""Canonical live feed identity. Backtest must match this path.

Live Sporty ingest: **OpticOdds SSE realtime** (FanDuel retail posted numbers)
plus **Pinnacle** as the sharp overlay and preferred closing line. The Odds API
is an optional REST stub only — it is not the live path and cannot clear gate 1.

FORBIDDEN: backtest on Pinnacle closes / sharp-only history, or any other retail
feed, then go live on OpticOdds. Production edge may not exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from sporty_hq.models import normalize_book

LIVE_FEED_ID = "opticodds+pinnacle"
LIVE_POSTED_FEED = "opticodds"
LIVE_POSTED_BOOK = "fanduel"
LIVE_SHARP_BOOK = "pinnacle"
OPTIONAL_STUB_FEED = "oddsapi"

POSTED_FEED_ALIASES = {
    "opticodds": LIVE_POSTED_FEED,
    "optic": LIVE_POSTED_FEED,
    "sse": LIVE_POSTED_FEED,
    "opticodds_sse": LIVE_POSTED_FEED,
    "opticodds-sse": LIVE_POSTED_FEED,
}

FORBIDDEN_POSTED_FEEDS = frozenset(
    {
        "pinnacle",
        "pinny",
        "sharp",
        "pinnacle-history",
        "pinnacle_history",
        "oddsapi",
        "theoddsapi",
        "the-odds-api",
        "the_odds_api",
        OPTIONAL_STUB_FEED,
    }
)

FORBIDDEN_POSTED_BOOKS = frozenset({"pinnacle", "pinnaclesports", "pinny", "sharp"})

STUB_FEEDS = frozenset(
    {"oddsapi", "theoddsapi", "the-odds-api", "the_odds_api", OPTIONAL_STUB_FEED}
)


def describe_live_feed() -> str:
    return (
        "Live path: OpticOdds SSE realtime (FanDuel retail posted) + "
        "Pinnacle sharp benchmark/close. Odds API is an optional REST stub only."
    )


def normalize_feed(value: str | None) -> str:
    return (value or "").strip().lower().replace(" ", "").replace("_", "-")


def canonical_posted_feed(value: str | None) -> str:
    key = normalize_feed(value)
    return POSTED_FEED_ALIASES.get(key, key)


@dataclass(frozen=True)
class FeedParity:
    matched: bool
    feed_id: str
    posted_feeds: tuple[str, ...]
    posted_books: tuple[str, ...]
    close_books: tuple[str, ...]
    close_feeds: tuple[str, ...]
    detail: str

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "feed_id": self.feed_id,
            "posted_feeds": list(self.posted_feeds),
            "posted_books": list(self.posted_books),
            "close_books": list(self.close_books),
            "close_feeds": list(self.close_feeds),
            "detail": self.detail,
            "live_feed_id": LIVE_FEED_ID,
            "live_path": describe_live_feed(),
        }


def evaluate_feed_parity(
    rows: Iterable[Any],
    *,
    target_book: str = LIVE_POSTED_BOOK,
    sharp_book: str = LIVE_SHARP_BOOK,
) -> FeedParity:
    """Require the same posted feed + sharp close Sporty uses live.

    Posted/take = OpticOdds FanDuel. Close / CLV benchmark = Pinnacle.
    Sharp-only or Odds-API-only history never matches.
    """
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
        detail="No historical rows — cannot prove feed parity with live OpticOdds+Pinnacle.",
    )
    if not items:
        return empty

    posted_feeds = tuple(canonical_posted_feed(getattr(r, "posted_feed", None)) for r in items)
    posted_books = tuple(
        normalize_book(getattr(r, "posted_book", None) or "") or "" for r in items
    )
    close_books = tuple(
        normalize_book(getattr(r, "close_book", None) or sharp) or sharp for r in items
    )
    close_feeds = tuple(canonical_posted_feed(getattr(r, "close_feed", None)) for r in items)

    def fail(feed_id: str, detail: str) -> FeedParity:
        return FeedParity(
            matched=False,
            feed_id=feed_id,
            posted_feeds=tuple(sorted({f for f in posted_feeds if f})),
            posted_books=tuple(sorted({b for b in posted_books if b})),
            close_books=tuple(sorted({b for b in close_books if b})),
            close_feeds=tuple(sorted({f for f in close_feeds if f})),
            detail=detail,
        )

    if any(not f for f in posted_feeds) or any(not b for b in posted_books):
        return fail(
            "unlabeled",
            "Feed identity required on every row (posted_feed=opticodds, "
            "posted_book=fanduel, close_book=pinnacle). Unlabeled or sharp-only "
            "history cannot clear gate 1 — production edge may not exist.",
        )

    forbidden_feeds = sorted({f for f in posted_feeds if f in FORBIDDEN_POSTED_FEEDS})
    if forbidden_feeds:
        if any(f in STUB_FEEDS for f in forbidden_feeds):
            return fail(
                "oddsapi-stub",
                "FORBIDDEN: Odds API REST is an optional stub, not the live path. "
                "Backtest must use OpticOdds realtime posted numbers + Pinnacle close. "
                f"Found posted_feed={forbidden_feeds}.",
            )
        return fail(
            "pinnacle-only",
            "FORBIDDEN: backtest on Pinnacle/sharp-only history then go live on a "
            "different retail feed. Live posted feed is OpticOdds (FanDuel). "
            f"Found posted_feed={forbidden_feeds}.",
        )

    if any(b in FORBIDDEN_POSTED_BOOKS for b in posted_books):
        return fail(
            "pinnacle-only",
            "FORBIDDEN: posted/take book is Pinnacle (sharp-only). Live take is "
            f"FanDuel via OpticOdds; Pinnacle is the close/benchmark only. "
            f"Found posted_book={sorted(set(posted_books))}.",
        )

    if any(f != LIVE_POSTED_FEED for f in posted_feeds):
        return fail(
            "mismatch",
            f"posted_feed must be {LIVE_POSTED_FEED} (live SSE). "
            f"Found {sorted(set(posted_feeds))}. Do not mix feeds.",
        )

    if any(b != target for b in posted_books):
        return fail(
            "mismatch",
            f"posted_book must be {target} (live target book). "
            f"Found {sorted(set(posted_books))}. Retail mismatch vs live is forbidden.",
        )

    if any(b != sharp for b in close_books):
        return fail(
            "mismatch",
            f"close_book must be {sharp} (Pinnacle sharp close / CLV benchmark). "
            f"Found {sorted(set(close_books))}.",
        )

    extra_close_feeds = {f for f in close_feeds if f and f != LIVE_POSTED_FEED}
    if extra_close_feeds:
        return fail(
            "mismatch",
            "close_feed, when set, must be opticodds (same live stream that also "
            f"carries Pinnacle). Found {sorted(extra_close_feeds)}.",
        )

    return FeedParity(
        matched=True,
        feed_id=LIVE_FEED_ID,
        posted_feeds=(LIVE_POSTED_FEED,),
        posted_books=(target,),
        close_books=(sharp,),
        close_feeds=tuple(sorted({f for f in close_feeds if f})) or (LIVE_POSTED_FEED,),
        detail=(
            f"Feed parity matched live path {LIVE_FEED_ID}: OpticOdds posted/"
            f"{target} + Pinnacle close. Odds API stub not used."
        ),
    )
