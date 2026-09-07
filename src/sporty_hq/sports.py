"""v1 sport calendar and scan priority. Props/parlays are out of scope."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sporty_hq.models import Quote

# Lower rank = scan first. MLB + NFL are the v1 desk; NCAAF next; NBA/NHL in-season; soccer last.
SPORT_RANK = {
    "baseball_mlb": 0,
    "mlb": 0,
    "americanfootball_nfl": 0,
    "nfl": 0,
    "americanfootball_ncaaf": 1,
    "ncaaf": 1,
    "basketball_nba": 2,
    "nba": 2,
    "icehockey_nhl": 2,
    "nhl": 2,
}

SOCCER_PREFIX = "soccer"
SOCCER_RANK = 3
SKIP_RANK = 99
SOCCER_MIN_BOOKS = 3  # liquid mains only

ET = ZoneInfo("America/New_York")


def family(sport: str) -> str:
    key = (sport or "").strip().lower()
    if key in {"mlb", "baseball_mlb"} or key.endswith("_mlb"):
        return "mlb"
    if key in {"nfl", "americanfootball_nfl"} or key.endswith("_nfl"):
        return "nfl"
    if key in {"ncaaf", "americanfootball_ncaaf"} or "ncaaf" in key or "ncaab" in key:
        if "ncaab" in key:
            return "skip"
        return "ncaaf"
    if key in {"nba", "basketball_nba"} or key.endswith("_nba"):
        return "nba"
    if key in {"nhl", "icehockey_nhl"} or key.endswith("_nhl"):
        return "nhl"
    if key.startswith(SOCCER_PREFIX) or "soccer" in key:
        return "soccer"
    return "skip"


def sport_rank(sport: str) -> int:
    fam = family(sport)
    if fam in {"mlb", "nfl"}:
        return 0
    if fam == "ncaaf":
        return 1
    if fam in {"nba", "nhl"}:
        return 2
    if fam == "soccer":
        return SOCCER_RANK
    return SKIP_RANK


def labor_day(year: int) -> date:
    first = date(year, 9, 1)
    return first + timedelta(days=(0 - first.weekday()) % 7)


def nfl_week1_wednesday(year: int) -> date:
    """NFL v1 window starts Wednesday of Week 1 (Labor Day Monday + 2)."""
    return labor_day(year) + timedelta(days=2)


def _as_et_date(when: datetime) -> date:
    if when.tzinfo is None:
        when = when.replace(tzinfo=ET)
    return when.astimezone(ET).date()


def in_season(sport: str, when: datetime) -> bool:
    """Whether this event’s start is inside the v1 season window for that sport."""
    fam = family(sport)
    day = _as_et_date(when)
    if fam == "mlb":
        return date(day.year, 3, 20) <= day <= date(day.year, 11, 5)
    if fam == "nfl":
        if day.month <= 2:
            start = nfl_week1_wednesday(day.year - 1)
            return start <= day <= date(day.year, 2, 15)
        start = nfl_week1_wednesday(day.year)
        return start <= day <= date(day.year + 1, 2, 15)
    if fam == "ncaaf":
        if day.month == 1:
            return day <= date(day.year, 1, 20)
        return date(day.year, 8, 24) <= day <= date(day.year, 12, 31)
    if fam == "nba":
        if day.month <= 6:
            return date(day.year - 1, 10, 14) <= day <= date(day.year, 6, 20)
        return date(day.year, 10, 14) <= day <= date(day.year + 1, 6, 20)
    if fam == "nhl":
        if day.month <= 6:
            return date(day.year - 1, 10, 7) <= day <= date(day.year, 6, 20)
        return date(day.year, 10, 7) <= day <= date(day.year + 1, 6, 20)
    if fam == "soccer":
        return True  # gated on liquidity, not calendar
    return False


def soccer_is_liquid(quotes: list[Quote], min_books: int = SOCCER_MIN_BOOKS) -> bool:
    books = {q.book for q in quotes}
    return len(books) >= min_books


def keep_quote(quote: Quote) -> bool:
    fam = family(quote.sport)
    if fam == "skip":
        return False
    if fam == "soccer":
        return True  # liquidity checked per market group
    return in_season(quote.sport, quote.commence_at)


def filter_v1_quotes(quotes: list[Quote]) -> list[Quote]:
    """Drop off-calendar sports, CFL/misc, and (later) anything that is not a main."""
    return [q for q in quotes if keep_quote(q)]
