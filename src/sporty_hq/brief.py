"""Odds Arcade education briefs. Research copy only — never places bets."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sporty_hq import DISCLAIMER
from sporty_hq.models import Quote, display_market, normalize_book
from sporty_hq.odds_math import clv_pct, juice_compare_to_win
from sporty_hq.sports import family, filter_v1_quotes, soccer_is_liquid, sport_rank

BRIEF_KIND = "odds_arcade_brief"
PACK_KIND = "close_challenge_pack"
PACK_MIN = 2
PACK_MAX = 3
CADENCE = {
    "daily": "09:00 America/New_York",
    "pack": "Thursday",
    "closes": "Friday",
}
OPEN_LINE_MISSING_HINT = (
    "Example open — no earlier HQ snapshot for this market. "
    "Illustrated for education only; not a live steam read. HQ does not place bets."
)
NO_CLOSE_HINT = (
    "Example close — no Friday snapshot matched this pack game. "
    "Illustrated for education only. HQ does not place bets."
)
EDUCATION_FOOTER = (
    "Odds Arcade is education-only. Sporty HQ does not place bets, "
    "does not fake fills, and does not log into FanDuel."
)

# Documented padding if fewer than 2 live events are on the board.
_EXAMPLE_PACK_GAMES: tuple[dict[str, Any], ...] = (
    {
        "sport": "baseball_mlb",
        "event": "Example Away @ Example Home",
        "market": "ml",
        "selection": "Example Away",
        "current_line": None,
        "current_price": 150,
    },
    {
        "sport": "americanfootball_nfl",
        "event": "Example Road @ Example Home",
        "market": "spread",
        "selection": "Example Home",
        "current_line": 3.0,
        "current_price": -110,
    },
)


@dataclass
class LineMove:
    sport: str
    event: str
    market: str
    open_line: float | None
    open_price: int
    current_line: float | None
    current_price: int
    delta: float
    why_hint: str
    as_of: str
    example: bool
    selection: str = ""

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        return row


@dataclass
class JuiceExplainer:
    event: str
    market: str
    selection: str
    better_price: int
    worse_price: int
    risk_to_win_100_better: float
    risk_to_win_100_worse: float
    diff_usd: float
    as_of: str
    example: bool

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChallengeGame:
    sport: str
    event: str
    market: str
    current_line: float | None
    current_price: int
    as_of: str
    example: bool
    selection: str = ""
    event_id: str = ""

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChallengeResult(ChallengeGame):
    close_line: float | None = None
    close_price: int | None = None
    beat_close: bool | None = None
    note: str = ""

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OddsArcadeBrief:
    line_move_of_the_day: LineMove
    juice_explainer: JuiceExplainer
    as_of: str
    close_challenge_pack: list[ChallengeGame] | None = None
    close_challenge_results: list[ChallengeResult] | None = None
    cadence: dict[str, str] = field(default_factory=lambda: dict(CADENCE))
    kind: str = BRIEF_KIND
    disclaimer: str = EDUCATION_FOOTER

    def to_json_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "as_of": self.as_of,
            "cadence": dict(self.cadence),
            "disclaimer": self.disclaimer,
            "line_move_of_the_day": self.line_move_of_the_day.to_row(),
            "juice_explainer": self.juice_explainer.to_row(),
        }
        if self.close_challenge_pack is not None:
            payload["close_challenge_pack"] = [g.to_row() for g in self.close_challenge_pack]
        if self.close_challenge_results is not None:
            payload["close_challenge_results"] = [g.to_row() for g in self.close_challenge_results]
        return payload


def build_brief(
    quotes: list[Quote],
    *,
    history: Iterable[tuple[datetime, Quote]] | None = None,
    include_pack: bool = False,
    include_closes: bool = False,
    saved_pack: list[dict[str, Any]] | None = None,
    target_book: str = "fanduel",
    as_of: datetime | None = None,
) -> OddsArcadeBrief:
    """Assemble the daily education brief from research quotes.

    Open lines come from earlier ingest snapshots in ``history``. If none,
    ``line_move_of_the_day.example`` is true and the hint documents that.
    """
    now = as_of or datetime.now(timezone.utc)
    stamp = _iso(now)
    book = normalize_book(target_book)
    pool = _research_pool(quotes)
    hist = list(history or [])
    line_move = pick_line_move(pool, hist, target_book=book, as_of=stamp)
    juice = pick_juice_explainer(pool, target_book=book, as_of=stamp)
    pack: list[ChallengeGame] | None = None
    results: list[ChallengeResult] | None = None
    if include_pack:
        pack = build_close_challenge_pack(pool, target_book=book, as_of=stamp)
    if include_closes:
        results = build_close_challenge_results(
            pool,
            saved_pack=saved_pack,
            target_book=book,
            as_of=stamp,
        )
    return OddsArcadeBrief(
        as_of=stamp,
        line_move_of_the_day=line_move,
        juice_explainer=juice,
        close_challenge_pack=pack,
        close_challenge_results=results,
    )


def pick_line_move(
    quotes: list[Quote],
    history: Iterable[tuple[datetime, Quote]],
    *,
    target_book: str = "fanduel",
    as_of: str,
) -> LineMove:
    book = normalize_book(target_book)
    quotes = _research_pool(quotes)
    timed = [(when, q) for when, q in history if q.event_id in {x.event_id for x in quotes}]
    by_id: dict[tuple, list[tuple[datetime, Quote]]] = {}
    for when, quote in timed:
        if normalize_book(quote.book) != book:
            continue
        by_id.setdefault(_side_key(quote), []).append((when, quote))

    best: LineMove | None = None
    best_score: tuple[float, int, str] | None = None
    for _key, snaps in by_id.items():
        snaps.sort(key=lambda item: item[0])
        if len(snaps) < 2:
            continue
        _first_when, first = snaps[0]
        _last_when, last = snaps[-1]
        if first.point == last.point and first.american_odds == last.american_odds:
            continue
        move = _line_move_from_pair(first, last, as_of=as_of, example=False)
        score = (abs(move.delta), -sport_rank(last.sport), last.event_name)
        if best is None or best_score is None or score > best_score:
            best = move
            best_score = score
    if best is not None:
        return best
    return _example_line_move(quotes, target_book=book, as_of=as_of)


def pick_juice_explainer(
    quotes: list[Quote],
    *,
    target_book: str = "fanduel",
    as_of: str,
) -> JuiceExplainer:
    book = normalize_book(target_book)
    quotes = _research_pool(quotes)
    grouped: dict[tuple, list[Quote]] = {}
    for quote in quotes:
        grouped.setdefault(quote.line_key, []).append(quote)

    best: JuiceExplainer | None = None
    best_score: tuple[float, int, int] | None = None
    for _key, group in grouped.items():
        prices = {q.american_odds for q in group}
        if len(prices) < 2:
            continue
        books = {normalize_book(q.book) for q in group}
        has_target = book in books
        target_prices = [q.american_odds for q in group if normalize_book(q.book) == book]
        other_prices = [q.american_odds for q in group if normalize_book(q.book) != book]
        if target_prices and other_prices:
            a, b = target_prices[0], _farthest_price(target_prices[0], other_prices)
        else:
            ordered = sorted(prices)
            a, b = ordered[0], ordered[-1]
        better, worse, risk_b, risk_w, diff = juice_compare_to_win(a, b)
        sample = next((q for q in group if q.american_odds in {better, worse}), group[0])
        explainer = JuiceExplainer(
            event=sample.event_name,
            market=display_market(sample.market),
            selection=_selection_label(sample),
            better_price=better,
            worse_price=worse,
            risk_to_win_100_better=risk_b,
            risk_to_win_100_worse=risk_w,
            diff_usd=diff,
            as_of=as_of,
            example=False,
        )
        score = (diff, 1 if has_target else 0, -sport_rank(sample.sport))
        if best is None or best_score is None or score > best_score:
            best = explainer
            best_score = score
    if best is not None:
        return best
    return _example_juice_explainer(quotes, target_book=book, as_of=as_of)


def build_close_challenge_pack(
    quotes: list[Quote],
    *,
    target_book: str = "fanduel",
    as_of: str,
) -> list[ChallengeGame]:
    book = normalize_book(target_book)
    quotes = _research_pool(quotes)
    featured = _featured_quotes(quotes, target_book=book)
    games: list[ChallengeGame] = []
    seen: set[str] = set()
    for quote in featured:
        if quote.event_id in seen:
            continue
        seen.add(quote.event_id)
        games.append(
            ChallengeGame(
                sport=quote.sport,
                event=quote.event_name,
                market=display_market(quote.market),
                current_line=quote.point,
                current_price=quote.american_odds,
                as_of=as_of,
                example=False,
                selection=_selection_label(quote),
                event_id=quote.event_id,
            )
        )
        if len(games) >= PACK_MAX:
            break
    pad_i = 0
    while len(games) < PACK_MIN and pad_i < len(_EXAMPLE_PACK_GAMES):
        raw = _EXAMPLE_PACK_GAMES[pad_i]
        pad_i += 1
        games.append(
            ChallengeGame(
                sport=str(raw["sport"]),
                event=str(raw["event"]),
                market=display_market(str(raw["market"])),
                current_line=raw["current_line"],
                current_price=int(raw["current_price"]),
                as_of=as_of,
                example=True,
                selection=str(raw["selection"]),
            )
        )
    return games


def build_close_challenge_results(
    quotes: list[Quote],
    *,
    saved_pack: list[dict[str, Any]] | None,
    target_book: str = "fanduel",
    as_of: str,
) -> list[ChallengeResult]:
    book = normalize_book(target_book)
    pack_rows = list(saved_pack or [])
    example_pack = False
    if not pack_rows:
        pack_rows = [g.to_row() for g in build_close_challenge_pack(quotes, target_book=book, as_of=as_of)]
        example_pack = True
    index = _quote_index(quotes, target_book=book)
    results: list[ChallengeResult] = []
    for row in pack_rows:
        event = str(row.get("event") or "")
        market = str(row.get("market") or "")
        selection = str(row.get("selection") or "")
        event_id = str(row.get("event_id") or "")
        current_line = _as_optional_float(row.get("current_line"))
        current_price = int(row.get("current_price") or 0)
        close = _match_close(index, event_id=event_id, event=event, market=market, selection=selection)
        if close is None or example_pack:
            close_line, close_price = _illustrate_close(current_line, current_price, market)
            beat, note = _beat_close_note(current_line, current_price, close_line, close_price)
            note = NO_CLOSE_HINT if close is None or example_pack else note
            results.append(
                ChallengeResult(
                    sport=str(row.get("sport") or (close.sport if close else "")),
                    event=event,
                    market=market,
                    current_line=current_line,
                    current_price=current_price,
                    as_of=as_of,
                    example=True,
                    selection=selection,
                    event_id=event_id,
                    close_line=close_line,
                    close_price=close_price,
                    beat_close=beat,
                    note=note,
                )
            )
            continue
        beat, note = _beat_close_note(
            current_line, current_price, close.point, close.american_odds
        )
        results.append(
            ChallengeResult(
                sport=close.sport,
                event=close.event_name,
                market=display_market(close.market),
                current_line=current_line,
                current_price=current_price,
                as_of=as_of,
                example=False,
                selection=_selection_label(close),
                event_id=close.event_id,
                close_line=close.point,
                close_price=close.american_odds,
                beat_close=beat,
                note=note,
            )
        )
    return results


def pack_to_json(games: list[ChallengeGame], as_of: str) -> dict[str, Any]:
    return {
        "kind": PACK_KIND,
        "as_of": as_of,
        "disclaimer": EDUCATION_FOOTER,
        "games": [g.to_row() for g in games],
    }


def write_pack(path: Path, games: list[ChallengeGame], as_of: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pack_to_json(games, as_of), indent=2) + "\n", encoding="utf-8")
    return path


def read_pack(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    games = payload.get("games")
    if isinstance(games, list):
        return games
    return []


def render_markdown(brief: OddsArcadeBrief) -> str:
    move = brief.line_move_of_the_day
    juice = brief.juice_explainer
    example_tag = " *(example)*" if move.example else ""
    juice_tag = " *(example)*" if juice.example else ""
    lines = [
        "# Odds Arcade brief",
        "",
        f"_Generated {brief.as_of}_",
        "",
        f"Cadence: daily **{CADENCE['daily']}** · pack **{CADENCE['pack']}** · closes **{CADENCE['closes']}**.",
        "",
        f"> {EDUCATION_FOOTER}",
        "",
        "## Line move of the day" + example_tag,
        "",
        f"- **{move.event}** ({move.sport}) · {move.market}"
        + (f" · {move.selection}" if move.selection else ""),
        f"- Open: {_fmt_line(move.open_line)} @ {_fmt_odds(move.open_price)}",
        f"- Current: {_fmt_line(move.current_line)} @ {_fmt_odds(move.current_price)}",
        f"- Delta: {move.delta:g}",
        f"- {move.why_hint}",
        "",
        "## Juice explainer ($100 to win)" + juice_tag,
        "",
        f"- **{juice.event}** · {juice.market} · {juice.selection}",
        f"- Better price {_fmt_odds(juice.better_price)}: risk **${juice.risk_to_win_100_better:.2f}** to win $100",
        f"- Worse price {_fmt_odds(juice.worse_price)}: risk **${juice.risk_to_win_100_worse:.2f}** to win $100",
        f"- Extra juice at the worse number: **${juice.diff_usd:.2f}**",
        "",
    ]
    if brief.close_challenge_pack is not None:
        lines.extend(["## Close-challenge pack (Thursday)", ""])
        for i, game in enumerate(brief.close_challenge_pack, start=1):
            tag = " · example" if game.example else ""
            lines.append(
                f"{i}. **{game.event}** ({game.sport}) {game.market} "
                f"{game.selection} {_fmt_line(game.current_line)} @ {_fmt_odds(game.current_price)}"
                f"{tag}"
            )
        lines.append("")
    if brief.close_challenge_results is not None:
        lines.extend(["## Close-challenge results (Friday)", ""])
        for i, game in enumerate(brief.close_challenge_results, start=1):
            tag = " · example" if game.example else ""
            beat = "—" if game.beat_close is None else ("yes" if game.beat_close else "no")
            lines.append(
                f"{i}. **{game.event}** {game.market} Thursday {_fmt_line(game.current_line)} "
                f"@ {_fmt_odds(game.current_price)} → close {_fmt_line(game.close_line)} "
                f"@ {_fmt_odds(game.close_price)} · beat close: **{beat}**{tag}"
            )
            if game.note:
                lines.append(f"   - {game.note}")
        lines.append("")
    lines.extend([f"_{DISCLAIMER}_", ""])
    return "\n".join(lines)


def _research_pool(quotes: list[Quote]) -> list[Quote]:
    """Same v1 calendar as scan; drop illiquid soccer so briefs stay on desk sports."""
    filtered = filter_v1_quotes(quotes)
    by_event: dict[str, list[Quote]] = {}
    for quote in filtered:
        by_event.setdefault(quote.event_id, []).append(quote)
    kept: list[Quote] = []
    for group in by_event.values():
        if family(group[0].sport) == "soccer" and not soccer_is_liquid(group):
            continue
        kept.extend(group)
    return kept or filtered or list(quotes)


def _side_key(quote: Quote) -> tuple:
    return (quote.event_id, quote.market, quote.selection.strip().lower())


def _line_delta(open_line: float | None, current_line: float | None, open_price: int, current_price: int) -> float:
    if open_line is not None and current_line is not None and open_line != current_line:
        return round(current_line - open_line, 4)
    return float(current_price - open_price)


def _line_move_from_pair(open_q: Quote, current_q: Quote, *, as_of: str, example: bool) -> LineMove:
    delta = _line_delta(open_q.point, current_q.point, open_q.american_odds, current_q.american_odds)
    if example:
        hint = OPEN_LINE_MISSING_HINT
    elif current_q.market in {"spread", "total"} and open_q.point is not None and current_q.point is not None:
        move = current_q.point - open_q.point
        direction = "down" if move < 0 else "up" if move > 0 else "unchanged"
        hint = (
            f"Line {direction} {abs(move):g} from {open_q.point:g} to {current_q.point:g} "
            f"({open_q.american_odds:+d} → {current_q.american_odds:+d}). {EDUCATION_FOOTER}"
        )
    else:
        hint = (
            f"Price {open_q.american_odds:+d} → {current_q.american_odds:+d}. {EDUCATION_FOOTER}"
        )
    return LineMove(
        sport=current_q.sport,
        event=current_q.event_name,
        market=display_market(current_q.market),
        open_line=open_q.point,
        open_price=open_q.american_odds,
        current_line=current_q.point,
        current_price=current_q.american_odds,
        delta=delta,
        why_hint=hint,
        as_of=as_of,
        example=example,
        selection=_selection_label(current_q),
    )


def _example_line_move(quotes: list[Quote], *, target_book: str, as_of: str) -> LineMove:
    featured = _featured_quotes(quotes, target_book=target_book)
    current = featured[0] if featured else _fallback_quote()
    open_q = Quote(
        event_id=current.event_id,
        sport=current.sport,
        commence_at=current.commence_at,
        home_team=current.home_team,
        away_team=current.away_team,
        book=current.book,
        market=current.market,
        selection=current.selection,
        american_odds=_illustrate_open_price(current.american_odds),
        point=_illustrate_open_line(current.point, current.market),
        source="example-open",
    )
    return _line_move_from_pair(open_q, current, as_of=as_of, example=True)


def _example_juice_explainer(quotes: list[Quote], *, target_book: str, as_of: str) -> JuiceExplainer:
    featured = _featured_quotes(quotes, target_book=target_book)
    sample = featured[0] if featured else _fallback_quote()
    worse = sample.american_odds - 10 if sample.american_odds > 0 else sample.american_odds - 10
    if worse == 0:
        worse = -110
    better, worse_p, risk_b, risk_w, diff = juice_compare_to_win(sample.american_odds, worse)
    return JuiceExplainer(
        event=sample.event_name,
        market=display_market(sample.market),
        selection=_selection_label(sample),
        better_price=better,
        worse_price=worse_p,
        risk_to_win_100_better=risk_b,
        risk_to_win_100_worse=risk_w,
        diff_usd=diff,
        as_of=as_of,
        example=True,
    )


def _featured_quotes(quotes: list[Quote], *, target_book: str) -> list[Quote]:
    book = normalize_book(target_book)
    pool = [q for q in quotes if normalize_book(q.book) == book]
    if not pool:
        pool = list(quotes)
    pool.sort(key=lambda q: (sport_rank(q.sport), q.commence_at, q.event_name, q.market, q.selection))
    return pool


def _quote_index(quotes: list[Quote], *, target_book: str) -> dict[tuple, Quote]:
    index: dict[tuple, Quote] = {}
    for quote in _featured_quotes(quotes, target_book=target_book):
        index[_match_key(quote.event_id, quote.event_name, display_market(quote.market), _selection_label(quote))] = quote
        index[_match_key(quote.event_id, quote.event_name, quote.market, quote.selection.strip().lower())] = quote
    return index


def _match_key(event_id: str, event: str, market: str, selection: str) -> tuple:
    return (
        (event_id or "").strip().lower(),
        (event or "").strip().lower(),
        (market or "").strip().lower(),
        (selection or "").strip().lower(),
    )


def _match_close(
    index: dict[tuple, Quote],
    *,
    event_id: str,
    event: str,
    market: str,
    selection: str,
) -> Quote | None:
    keys = [
        _match_key(event_id, event, market, selection),
        _match_key(event_id, "", market, selection),
        _match_key("", event, market, selection),
    ]
    for key in keys:
        if key in index:
            return index[key]
        # Relaxed: any market/selection on this event
        for stored_key, quote in index.items():
            if event_id and stored_key[0] == event_id.strip().lower():
                return quote
            if event and stored_key[1] == event.strip().lower():
                return quote
    return None


def _beat_close_note(
    thursday_line: float | None,
    thursday_price: int,
    close_line: float | None,
    close_price: int,
) -> tuple[bool, str]:
    clv = clv_pct(thursday_price, close_price)
    beat = clv > 0
    if abs(clv) < 1e-9:
        note = "Flat vs close (same American number)."
        beat = False
    elif beat:
        note = f"Thursday number beat the close (CLV {clv:+.2f}%)."
    else:
        note = f"Close was a better number (CLV {clv:+.2f}%)."
    if thursday_line is not None and close_line is not None and thursday_line != close_line:
        note += f" Line {thursday_line:g} → {close_line:g}."
    note += f" {EDUCATION_FOOTER}"
    return beat, note


def _illustrate_open_line(current_line: float | None, market: str) -> float | None:
    if current_line is None or market == "ml":
        return current_line
    return round(current_line + 1.0, 4)


def _illustrate_open_price(current_price: int) -> int:
    if current_price >= 100:
        return max(100, current_price - 15)
    return current_price + 10


def _illustrate_close(current_line: float | None, current_price: int, market: str) -> tuple[float | None, int]:
    close_line = current_line
    label = market.lower()
    if current_line is not None and ("spread" in label or "total" in label):
        close_line = round(current_line - 0.5, 4)
    if current_price >= 100:
        close_price = max(100, current_price - 10)
    else:
        close_price = current_price - 5
        if close_price == 0:
            close_price = -105
    return close_line, close_price


def _farthest_price(anchor: int, others: list[int]) -> int:
    return max(others, key=lambda p: abs(p - anchor))


def _selection_label(quote: Quote) -> str:
    if quote.point is None:
        return quote.selection
    if quote.market == "spread":
        return f"{quote.selection} {quote.point:+g}"
    return f"{quote.selection} {quote.point:g}"


def _fmt_odds(value: int | None) -> str:
    if value is None:
        return "—"
    return f"{value:+d}"


def _fmt_line(value: float | None) -> str:
    if value is None:
        return "ML"
    return f"{value:g}"


def _iso(when: datetime) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).isoformat()


def _as_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _fallback_quote() -> Quote:
    return Quote(
        event_id="example-ml",
        sport="baseball_mlb",
        commence_at=datetime.now(timezone.utc),
        home_team="Example Home",
        away_team="Example Away",
        book="fanduel",
        market="ml",
        selection="Example Away",
        american_odds=150,
        source="example",
    )
