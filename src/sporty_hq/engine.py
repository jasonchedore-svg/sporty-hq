"""Score FanDuel (or other target book) quotes vs consensus / model stub."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sporty_hq.models import Candidate, Quote, SHARP_BOOK_DEFAULT, display_market, normalize_book
from sporty_hq.odds_math import (
    american_to_decimal,
    edge,
    implied_prob,
    juice_pct,
    median,
    multiplicative_devig,
)
from sporty_hq.sports import (
    family,
    filter_v1_quotes,
    soccer_is_liquid,
    sport_rank,
)


@dataclass(frozen=True)
class ScanConfig:
    target_book: str = "fanduel"
    sharp_book: str = SHARP_BOOK_DEFAULT
    min_edge: float = 0.03
    home_prob_bump: float = 0.0  # model stub: add to home-team fair prob, then renormalize
    apply_calendar: bool = True


def _book_sides(quotes: list[Quote]) -> dict[str, dict[str, Quote]]:
    """book -> selection_lower -> quote, only if the book posted a full two-way (or more)."""
    by_book: dict[str, dict[str, Quote]] = defaultdict(dict)
    for quote in quotes:
        by_book[quote.book][quote.selection.strip().lower()] = quote
    return {book: sides for book, sides in by_book.items() if len(sides) >= 2}


def _fair_map(sides: dict[str, Quote]) -> tuple[dict[str, float], float]:
    names = list(sides.keys())
    raw = [implied_prob(sides[name].american_odds) for name in names]
    fair = multiplicative_devig(raw)
    return dict(zip(names, fair, strict=True)), juice_pct(raw)


def _apply_home_stub(
    fair: dict[str, float],
    home_team: str,
    bump: float,
) -> dict[str, float]:
    if bump == 0.0:
        return fair
    home_key = home_team.strip().lower()
    if home_key not in fair:
        return fair
    adjusted = dict(fair)
    adjusted[home_key] = min(0.99, max(0.01, adjusted[home_key] + bump))
    others = [k for k in adjusted if k != home_key]
    remainder = max(0.01, 1.0 - adjusted[home_key])
    other_total = sum(adjusted[k] for k in others) or 1.0
    for key in others:
        adjusted[key] = remainder * (adjusted[key] / other_total)
    return adjusted


def score_quotes(quotes: list[Quote], config: ScanConfig | None = None) -> list[Candidate]:
    """Rank target-book selections with juice-removed edge vs consensus median.

    Consensus = median multiplicative de-vig probability across non-target books.
    Pinnacle (or ``sharp_book``) is overlaid as the sharp benchmark vs FanDuel retail
    when it posts a two-way. Hypothesis: Pinnacle de-vig is closer to true market
    than a multi-book median; edge keepers still use consensus until that is A/B'd.
    Optional ``home_prob_bump`` is a placeholder model until a real one exists.
    """
    config = config or ScanConfig()
    pool = filter_v1_quotes(quotes) if config.apply_calendar else list(quotes)
    target = normalize_book(config.target_book)
    grouped: dict[tuple, list[Quote]] = defaultdict(list)
    for quote in pool:
        # Totals share a point (5.5/5.5). Spreads use opposite signs (+3.5/-3.5) — group on abs.
        grouped[_market_group_key(quote)].append(quote)

    candidates: list[Candidate] = []
    for (_event_id, market, point), group in grouped.items():
        if family(group[0].sport) == "soccer" and not soccer_is_liquid(group):
            continue
        sides_by_book = _book_sides(group)
        if target not in sides_by_book:
            continue
        consensus_fairs: dict[str, list[float]] = defaultdict(list)
        consensus_books: list[str] = []
        for book, sides in sides_by_book.items():
            if book == target:
                continue
            fair, _juice = _fair_map(sides)
            fair = _apply_home_stub(fair, group[0].home_team, config.home_prob_bump)
            consensus_books.append(book)
            for selection, prob in fair.items():
                consensus_fairs[selection].append(prob)
        if not consensus_books:
            continue

        target_sides = sides_by_book[target]
        target_fair, target_juice = _fair_map(target_sides)
        sample = next(iter(target_sides.values()))
        sharp = normalize_book(config.sharp_book)
        sharp_sides = sides_by_book.get(sharp)
        sharp_fair: dict[str, float] = {}
        if sharp_sides:
            sharp_fair, _sharp_juice = _fair_map(sharp_sides)

        for sel_key, tquote in target_sides.items():
            if sel_key not in consensus_fairs:
                continue
            fair_p = median(consensus_fairs[sel_key])
            decimal = american_to_decimal(tquote.american_odds)
            ev = edge(fair_p, decimal)
            if ev + 1e-12 < config.min_edge:
                continue
            implied = implied_prob(tquote.american_odds)
            line = "" if point is None else f" {point:+g}" if market == "spread" else f" {point:g}"
            books_label = "/".join(sorted(consensus_books))
            market_label = display_market(market)
            rationale = (
                f"{tquote.book} {tquote.selection}{line} {tquote.american_odds:+d} vs consensus "
                f"fair {fair_p:.1%} ({books_label}). Implied {implied:.1%} with "
                f"{target_juice:.1f}% juice; juice-removed EV {ev:.1%}."
            )
            if config.home_prob_bump:
                rationale += f" Stub model home bump {config.home_prob_bump:+.0%}."
            pin_odds = None
            pin_fair = None
            if sharp_sides and sel_key in sharp_sides:
                pin_odds = sharp_sides[sel_key].american_odds
                pin_fair = sharp_fair.get(sel_key)
                pin_fair_txt = f"{pin_fair:.1%}" if pin_fair is not None else "—"
                rationale += (
                    f" Sharp benchmark {sharp} {pin_odds:+d} (fair {pin_fair_txt}) vs "
                    f"{tquote.book} retail {tquote.american_odds:+d}."
                )
            candidates.append(
                Candidate(
                    event_id=sample.event_id,
                    sport=sample.sport,
                    event_name=sample.event_name,
                    commence_at=sample.commence_at,
                    market=market,
                    selection=tquote.selection,
                    point=point,
                    book=tquote.book,
                    american_odds=tquote.american_odds,
                    decimal_odds=round(decimal, 4),
                    fair_prob=round(fair_p, 6),
                    implied_prob=round(implied, 6),
                    edge_pct=round(ev * 100.0, 2),
                    juice_pct=round(target_juice, 2),
                    rationale=rationale,
                    consensus_books=sorted(consensus_books),
                    pinnacle_odds=pin_odds,
                    pinnacle_fair=round(pin_fair, 6) if pin_fair is not None else None,
                )
            )

    candidates.sort(
        key=lambda c: (sport_rank(c.sport), -c.edge_pct, c.event_name, c.selection)
    )
    return candidates


def _market_group_key(quote: Quote) -> tuple:
    point = quote.point
    if point is None:
        return (quote.event_id, quote.market, None)
    rounded = round(float(point), 2)
    if quote.market == "spread":
        return (quote.event_id, quote.market, round(abs(rounded), 2))
    return (quote.event_id, quote.market, rounded)
