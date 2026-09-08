"""Fixed-unit bankroll and optional fractional Kelly (capped at 1 unit by default).

Sporty HQ never places bets. Sizing here is a suggestion for the human on FanDuel mobile.
Daily/seasonal stop *enforcement* lives in ``playbook`` so HQ can refuse tickets.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sporty_hq.config import Settings
from sporty_hq.odds_math import kelly_stake as _kelly_stake


def season_start_et(now: datetime, timezone_name: str, configured: str | None) -> datetime:
    """Season window start.

    Hypothesis: default is March 20 of the current ET year (MLB open-ish), rolling
    back a year if we are before that date. Override with ``SPORTY_HQ_SEASON_START``.
    """
    tz = ZoneInfo(timezone_name)
    local = now.astimezone(tz)
    if configured:
        parsed = datetime.strptime(configured.strip(), "%Y-%m-%d").replace(tzinfo=tz)
        return parsed.astimezone(now.tzinfo or tz)
    start_this = local.replace(month=3, day=20, hour=0, minute=0, second=0, microsecond=0)
    if local < start_this:
        start_this = start_this.replace(year=start_this.year - 1)
    return start_this.astimezone(now.tzinfo or tz)


def suggested_stake(
    settings: Settings,
    *,
    fair_prob: float,
    decimal_odds: float,
    kelly: bool = False,
) -> float:
    """Flat unit unless Kelly is enabled (CLI ``--kelly`` or ``kelly_fraction`` > 0).

    When ``--kelly`` is set and ``kelly_fraction`` is 0, default to quarter-Kelly
    (hypothesis: 1/4 is a common fractional default) still capped at ``kelly_cap_units``.
    """
    if kelly:
        fraction = settings.kelly_fraction if settings.kelly_fraction > 0 else 0.25
    else:
        fraction = settings.kelly_fraction
    if fraction <= 0:
        return round(settings.unit_stake, 2)
    return _kelly_stake(
        fair_prob,
        decimal_odds,
        bankroll=settings.bankroll_usd,
        fraction=fraction,
        unit=settings.unit_stake,
        cap_units=settings.kelly_cap_units,
    )
