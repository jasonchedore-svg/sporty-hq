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
        store.insert_bet(_bet(f"evt-{i}", id=f"bet{i:04d}", logged_at=now))
    with pytest.raises(PlaybookViolation) as exc:
        validate_new_bet(
            store, settings, event_id="evt-new", market="spread", stake=25, now=now
        )
    assert exc.value.code == "session_cap"
    # force bypasses cap
    snap = validate_new_bet(
        store, settings, event_id="evt-new", market="spread", stake=25, now=now, force=True
    )
    assert snap.bets_logged == 4


def test_session_stop(store, settings) -> None:
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
    with pytest.raises(PlaybookViolation) as exc:
        validate_new_bet(
            store, settings, event_id="evt-2", market="ml", stake=25, now=now
        )
    assert exc.value.code == "session_stop"
