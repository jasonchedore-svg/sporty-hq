"""Kelly cap + hard daily/seasonal stop-loss."""

from datetime import datetime, timezone

import pytest

from sporty_hq.bankroll import suggested_stake
from sporty_hq.models import Bet
from sporty_hq.odds_math import american_to_decimal, kelly_stake
from sporty_hq.playbook import PlaybookViolation, stop_status, validate_new_bet
from sporty_hq.storage import utcnow


def test_kelly_capped_at_one_unit_by_default() -> None:
    # Huge bankroll + fat edge would be many units uncapped.
    stake = kelly_stake(
        0.55,
        american_to_decimal(-110),
        bankroll=10_000,
        fraction=1.0,
        unit=25.0,
        cap_units=1.0,
    )
    assert stake == 25.0


def test_kelly_respects_configured_cap_units() -> None:
    stake = kelly_stake(
        0.55,
        american_to_decimal(-110),
        bankroll=10_000,
        fraction=1.0,
        unit=25.0,
        cap_units=2.0,
    )
    assert stake == 50.0


def test_kelly_disabled_returns_flat_unit(settings) -> None:
    settings.kelly_fraction = 0.0
    assert suggested_stake(settings, fair_prob=0.55, decimal_odds=1.91) == 25.0


def test_quarter_kelly_still_capped(settings) -> None:
    settings.kelly_fraction = 0.25
    settings.bankroll_usd = 10_000
    settings.kelly_cap_units = 1.0
    stake = suggested_stake(settings, fair_prob=0.55, decimal_odds=american_to_decimal(-110))
    assert 0 < stake <= 25.0


def _loss(event_id: str, pnl: float, **kwargs) -> Bet:
    now = kwargs.pop("logged_at", utcnow())
    return Bet(
        id=kwargs.pop("id", "loss1"),
        logged_at=now,
        event_id=event_id,
        event_name="away @ home",
        sport="mlb",
        commence_at=now,
        market="ml",
        selection="Home",
        odds_at_bet=-110,
        stake=25.0,
        result="loss",
        pnl=pnl,
        close_odds=-110,
        clv_pct=0.0,
    )


def test_daily_stop_blocks_scan_status(store, settings) -> None:
    now = datetime.now(timezone.utc)
    store.insert_bet(_loss("e1", -100.0, id="d1", logged_at=now))
    status = stop_status(store, settings, now)
    assert status.daily_hit
    assert status.hit


def test_force_cannot_bypass_daily_stop(store, settings) -> None:
    now = datetime.now(timezone.utc)
    store.insert_bet(_loss("e1", -100.0, id="d1", logged_at=now))
    with pytest.raises(PlaybookViolation) as exc:
        validate_new_bet(
            store, settings, event_id="e2", market="ml", stake=25, now=now, force=True
        )
    assert exc.value.code == "daily_stop"
