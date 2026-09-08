"""American/decimal odds, de-vig, edge, and CLV helpers."""

from __future__ import annotations

import statistics


def american_to_decimal(american: int | float) -> float:
    american = int(american)
    if american == 0:
        raise ValueError("American odds cannot be 0")
    if american > 0:
        return 1.0 + american / 100.0
    return 1.0 + 100.0 / abs(american)


def decimal_to_american(decimal: float) -> int:
    if decimal <= 1.0:
        raise ValueError("Decimal odds must be > 1")
    if decimal >= 2.0:
        return int(round((decimal - 1.0) * 100.0))
    return int(round(-100.0 / (decimal - 1.0)))


def implied_prob(american: int | float) -> float:
    """Raw (vigged) implied probability from American odds."""
    american = int(american)
    if american == 0:
        raise ValueError("American odds cannot be 0")
    if american > 0:
        return 100.0 / (american + 100.0)
    return abs(american) / (abs(american) + 100.0)


def parse_american(value: str | int | float) -> int:
    if isinstance(value, bool):
        raise ValueError("Invalid American odds")
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace("−", "-")
    if not text:
        raise ValueError("Empty American odds")
    return int(text)


def multiplicative_devig(probs: list[float]) -> list[float]:
    """Proportional (multiplicative) de-vig. Sum of inputs is 1 + juice."""
    if not probs or any(p < 0 for p in probs):
        raise ValueError("Probabilities must be non-empty and non-negative")
    total = sum(probs)
    if total <= 0:
        raise ValueError("Implied probabilities must sum to > 0")
    return [p / total for p in probs]


def juice_pct(probs: list[float]) -> float:
    """Overround as a percent (e.g. 4.5 means 4.5% juice)."""
    return (sum(probs) - 1.0) * 100.0


def median(values: list[float]) -> float:
    if not values:
        raise ValueError("Cannot take median of empty list")
    return float(statistics.median(values))


def edge(fair_prob: float, decimal_odds: float) -> float:
    """Expected value as a fraction: fair_p * decimal - 1."""
    if not 0.0 <= fair_prob <= 1.0:
        raise ValueError("fair_prob must be in [0, 1]")
    if decimal_odds <= 1.0:
        raise ValueError("decimal_odds must be > 1")
    return fair_prob * decimal_odds - 1.0


def clv_pct(odds_at_bet: int, close_odds: int) -> float:
    """American vs close on the same market.

    Flat (same American number) is 0. Positive means you beat the close.
    Judge process on ~100+ bets — small-n CLV is directional only.
    """
    if int(odds_at_bet) == int(close_odds):
        return 0.0
    bet_dec = american_to_decimal(odds_at_bet)
    close_dec = american_to_decimal(close_odds)
    return (bet_dec / close_dec - 1.0) * 100.0


def settle_pnl(result: str, stake: float, american_odds: int) -> float:
    """Straight-bet P&L. Push/void returns 0."""
    if stake < 0:
        raise ValueError("stake must be >= 0")
    kind = result.strip().lower()
    if kind == "win":
        return round(stake * (american_to_decimal(american_odds) - 1.0), 2)
    if kind == "loss":
        return round(-stake, 2)
    if kind in {"push", "void"}:
        return 0.0
    raise ValueError(f"Unknown result '{result}' (use win, loss, push, void)")


def risk_to_win(american: int | float, win: float = 100.0) -> float:
    """Stake required to profit ``win`` dollars at American odds.

    Education helper (juice explainer): -110 → 110.0 to win 100; +150 → 66.67.
    """
    american = int(american)
    if american == 0:
        raise ValueError("American odds cannot be 0")
    if win < 0:
        raise ValueError("win must be >= 0")
    if american > 0:
        return round(win * 100.0 / american, 2)
    return round(win * abs(american) / 100.0, 2)


def juice_compare_to_win(
    price_a: int | float,
    price_b: int | float,
    win: float = 100.0,
) -> tuple[int, int, float, float, float]:
    """Compare two American prices by risk to win ``win``.

    Returns ``(better_price, worse_price, risk_better, risk_worse, diff_usd)``.
    Higher American number is the better price (-110 beats -120; +165 beats +140).
    ``diff_usd`` is extra stake at the worse number (always ≥ 0).
    """
    a, b = int(price_a), int(price_b)
    better, worse = (a, b) if a >= b else (b, a)
    risk_better = risk_to_win(better, win)
    risk_worse = risk_to_win(worse, win)
    return better, worse, risk_better, risk_worse, round(risk_worse - risk_better, 2)
