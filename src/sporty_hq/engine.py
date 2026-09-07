"""Score FanDuel (or other target book) quotes vs consensus / model stub."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sporty_hq.models import Candidate, Quote, display_market, normalize_book
from sporty_hq.odds_math import (
    american_to_decimal,
    edge,
    implied_prob,
    juice_pct,
    median,
    multiplicative_devig,
)


@dataclass(frozen=True)
class ScanConfig:
    target_book: str = "fanduel"
    min_edge: float = 0.03
    home_prob_bump: float = 0.0  # model stub: add to home-team fair prob, then renormalize


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
    Optional ``home_prob_bump`` is a placeholder model until a real one exists.
    """
    config = config or ScanConfig()
    target = normalize_book(config.target_book)
    grouped: dict[tuple, list[Quote]] = defaultdict(list)
    for quote in quotes:
        # Totals share a point (5.5/5.5). Spreads use opposite signs (+3.5/-3.5) — group on abs.
        grouped[_market_group_key(quote)].append(quote)

    candidates: list[Candidate] = []
    for (_event_id, market, point), group in grouped.items():
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
                f"{target_juice:.1f}% juice; juice-removed EV {ev:.1%}. "
                f"Stub model home bump {config.home_prob_bump:+.0%}."
                if config.home_prob_bump
                else (
                    f"{tquote.book} {tquote.selection}{line} {tquote.american_odds:+d} vs consensus "
                    f"fair {fair_p:.1%} ({books_label}). Implied {implied:.1%} with "
                    f"{target_juice:.1f}% juice; juice-removed EV {ev:.1%}."
                )
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
                )
            )

    candidates.sort(key=lambda c: (-c.edge_pct, c.event_name, c.selection))
    return candidates


def _market_group_key(quote: Quote) -> tuple:
    point = quote.point
    if point is None:
        return (quote.event_id, quote.market, None)
    rounded = round(float(point), 2)
    if quote.market == "spread":
        return (quote.event_id, quote.market, round(abs(rounded), 2))
    return (quote.event_id, quote.market, rounded)
