from datetime import datetime, timezone

import pytest

from sporty_hq.models import Bet, normalize_market
from sporty_hq.playbook import PlaybookViolation, validate_new_bet
from sporty_hq.storage import utcnow


def _bet(event_id: str, **kwargs) -> Bet:
    now = utcnow()
    data = dict(
        id="abc12345",
        logged_at=now,
        event_id=event_id,
        event_name="away @ home",
        sport="nhl",
        commence_at=now,
        market="ml",
        selection="Home",
        odds_at_bet=-110,
        stake=25.0,
    )
    data.update(kwargs)
    return Bet(**data)


def test_rejects_non_straights() -> None:
    with pytest.raises(ValueError):
        normalize_market("parlay")
    with pytest.raises(ValueError):
        normalize_market("player_props")


def test_one_open_per_event(store, settings) -> None:
    store.insert_bet(_bet("evt-1"))
    with pytest.raises(PlaybookViolation) as exc:
        validate_new_bet(
            store,
            settings,
            event_id="evt-1",
            market="ml",
            stake=25,
            now=datetime.now(timezone.utc),
        )
    assert exc.value.code == "one_per_event"


def test_session_cap(store, settings) -> None:
    now = datetime.now(timezone.utc)
    for i in range(4):
        store.insert_bet(
            _bet(
                f"evt-{i}",
                id=f"bet{i:04d}",
                logged_at=now,
                result="win",
                pnl=25.0,
            )
        )
    with pytest.raises(PlaybookViolation) as exc:
        validate_new_bet(
            store, settings, event_id="evt-new", market="spread", stake=25, now=now
        )
    assert exc.value.code == "session_cap"
    # force bypasses cap (stops are a different rule)
    snap = validate_new_bet(
        store, settings, event_id="evt-new", market="spread", stake=25, now=now, force=True
    )
    assert snap.bets_logged == 4


def test_session_stop_applies_to_live_only(store, settings) -> None:
    now = datetime.now(timezone.utc)
    store.insert_bet(
        _bet(
            "evt-loss",
            id="loss0001",
            result="loss",
            pnl=-100.0,
            stake=25,
            logged_at=now,
        )
    )
    snap = validate_new_bet(
        store, settings, event_id="evt-2", market="ml", stake=25, now=now, kind="paper"
    )
    assert snap.bets_logged >= 1
    with pytest.raises(PlaybookViolation) as exc:
        validate_new_bet(
            store, settings, event_id="evt-2", market="ml", stake=25, now=now, kind="live"
        )
    assert exc.value.code == "live_locked"


def test_seasonal_stop_does_not_block_paper(store, settings) -> None:
    now = datetime.now(timezone.utc)
    settings.seasonal_stop = -50.0
    settings.season_start = "2020-01-01"
    store.insert_bet(
        _bet(
            "evt-old",
            id="oldloss1",
            result="loss",
            pnl=-50.0,
            stake=25,
            logged_at=now,
        )
    )
    snap = validate_new_bet(
        store, settings, event_id="evt-new", market="ml", stake=25, now=now, force=True, kind="paper"
    )
    assert snap.bets_logged >= 1
    with pytest.raises(PlaybookViolation) as exc:
        validate_new_bet(
            store,
            settings,
            event_id="evt-new",
            market="ml",
            stake=25,
            now=now,
            force=True,
            kind="live",
        )
    assert exc.value.code == "live_locked"
