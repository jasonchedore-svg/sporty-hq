"""Ontario hybrid playbook: session stop, unit size, straights, one bet per event."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sporty_hq.config import Settings
from sporty_hq.models import Bet, normalize_market
from sporty_hq.storage import Store


@dataclass
class PlaybookViolation(Exception):
    message: str
    code: str

    def __str__(self) -> str:  # pragma: no cover
        return self.message


@dataclass
class SessionSnapshot:
    start: datetime
    bets_logged: int
    realized_pnl: float
    open_risk: float
    worst_case_pnl: float
    remaining_bets: int


def session_start(now: datetime, timezone_name: str) -> datetime:
    tz = ZoneInfo(timezone_name)
    local = now.astimezone(tz)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(now.tzinfo or tz)


def session_snapshot(store: Store, settings: Settings, now: datetime) -> SessionSnapshot:
    start = session_start(now, settings.timezone)
    today = store.bets_logged_since(start)
    realized = sum(b.pnl or 0.0 for b in today if b.pnl is not None)
    open_today = [b for b in today if b.is_open]
    open_risk = sum(b.stake for b in open_today)
    return SessionSnapshot(
        start=start,
        bets_logged=len(today),
        realized_pnl=round(realized, 2),
        open_risk=round(open_risk, 2),
        worst_case_pnl=round(realized - open_risk, 2),
        remaining_bets=max(0, settings.max_bets_per_session - len(today)),
    )


def validate_new_bet(
    store: Store,
    settings: Settings,
    *,
    event_id: str,
    market: str,
    stake: float,
    now: datetime,
    force: bool = False,
) -> SessionSnapshot:
    """Raise PlaybookViolation for hard rules. Session caps are hard unless force=True."""
    try:
        normalize_market(market)
    except ValueError as exc:
        raise PlaybookViolation(str(exc), "straights_only") from exc

    if store.open_bets_for_event(event_id):
        raise PlaybookViolation(
            f"Playbook: max 1 open bet per event (event_id={event_id} already has an open ticket).",
            "one_per_event",
        )

    snap = session_snapshot(store, settings, now)
    if not force and snap.bets_logged >= settings.max_bets_per_session:
        raise PlaybookViolation(
            f"Playbook: ~{settings.max_bets_per_session} bets/session already logged today "
            f"({snap.bets_logged}). Use --force to override.",
            "session_cap",
        )

    worst_if_logged = snap.worst_case_pnl - stake
    if not force and worst_if_logged <= settings.session_stop:
        raise PlaybookViolation(
            f"Playbook: session stop {settings.session_stop:.0f} would be reached "
            f"(worst-case P&L {worst_if_logged:.2f} including this ${stake:.0f} unit). "
            "Use --force to override.",
            "session_stop",
        )

    if abs(stake - settings.unit_stake) > 1e-9:
        # Flat $25 units: allow with a note from the CLI, not a hard fail.
        pass

    return snap
