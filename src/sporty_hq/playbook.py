"""Ontario hybrid playbook: daily/seasonal stops, unit size, straights, one bet per event."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sporty_hq.bankroll import season_start_et
from sporty_hq.config import Settings
from sporty_hq.models import normalize_market
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


@dataclass(frozen=True)
class SeasonalSnapshot:
    start: datetime
    bets_logged: int
    realized_pnl: float
    open_risk: float
    worst_case_pnl: float


@dataclass(frozen=True)
class StopStatus:
    daily_hit: bool
    seasonal_hit: bool
    daily: SessionSnapshot
    seasonal: SeasonalSnapshot
    reasons: tuple[str, ...]

    @property
    def hit(self) -> bool:
        return self.daily_hit or self.seasonal_hit


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


def seasonal_snapshot(store: Store, settings: Settings, now: datetime) -> SeasonalSnapshot:
    start = season_start_et(now, settings.timezone, settings.season_start)
    pool = store.bets_logged_since(start)
    realized = sum(b.pnl or 0.0 for b in pool if b.pnl is not None)
    open_bets = [b for b in pool if b.is_open]
    open_risk = sum(b.stake for b in open_bets)
    return SeasonalSnapshot(
        start=start,
        bets_logged=len(pool),
        realized_pnl=round(realized, 2),
        open_risk=round(open_risk, 2),
        worst_case_pnl=round(realized - open_risk, 2),
    )


def stop_status(store: Store, settings: Settings, now: datetime) -> StopStatus:
    """Stops are hit on *realized* P&L. Open risk is shown but does not stop the scan."""
    daily = session_snapshot(store, settings, now)
    seasonal = seasonal_snapshot(store, settings, now)
    daily_hit = daily.realized_pnl <= settings.daily_stop
    seasonal_hit = seasonal.realized_pnl <= settings.seasonal_stop
    reasons: list[str] = []
    if daily_hit:
        reasons.append(
            f"daily stop {settings.daily_stop:.0f} hit "
            f"(realized ${daily.realized_pnl:+.2f})"
        )
    if seasonal_hit:
        reasons.append(
            f"seasonal stop {settings.seasonal_stop:.0f} hit "
            f"(realized ${seasonal.realized_pnl:+.2f})"
        )
    return StopStatus(
        daily_hit=daily_hit,
        seasonal_hit=seasonal_hit,
        daily=daily,
        seasonal=seasonal,
        reasons=tuple(reasons),
    )


def format_stop_block(status: StopStatus, settings: Settings) -> str:
    lines = [
        f"Daily: realized ${status.daily.realized_pnl:+.2f} / stop {settings.daily_stop:.0f} "
        f"({status.daily.bets_logged} bets, worst-case ${status.daily.worst_case_pnl:+.2f})",
        f"Season: realized ${status.seasonal.realized_pnl:+.2f} / stop {settings.seasonal_stop:.0f} "
        f"({status.seasonal.bets_logged} bets since {status.seasonal.start.date()})",
    ]
    if status.hit:
        lines.append("STOPPED — playbook refuses new bets. HQ does not place bets.")
        lines.extend(f"- {r}" for r in status.reasons)
    return "\n".join(lines)


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
    """Raise PlaybookViolation for hard rules.

    Daily/seasonal stops are never bypassable (including ``--force``).
    Session bet-count cap is hard unless ``force=True``.
    """
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
    seasonal = seasonal_snapshot(store, settings, now)

    if snap.realized_pnl <= settings.daily_stop:
        raise PlaybookViolation(
            f"Playbook: daily stop {settings.daily_stop:.0f} hit "
            f"(realized ${snap.realized_pnl:+.2f}). New bets refused.",
            "daily_stop",
        )
    if seasonal.realized_pnl <= settings.seasonal_stop:
        raise PlaybookViolation(
            f"Playbook: seasonal stop {settings.seasonal_stop:.0f} hit "
            f"(realized ${seasonal.realized_pnl:+.2f}). New bets refused.",
            "seasonal_stop",
        )

    if not force and snap.bets_logged >= settings.max_bets_per_session:
        raise PlaybookViolation(
            f"Playbook: ~{settings.max_bets_per_session} bets/session already logged today "
            f"({snap.bets_logged}). Use --force to override.",
            "session_cap",
        )

    worst_if_logged = snap.worst_case_pnl - stake
    if worst_if_logged < settings.daily_stop:
        raise PlaybookViolation(
            f"Playbook: daily stop {settings.daily_stop:.0f} would be breached "
            f"(worst-case P&L {worst_if_logged:.2f} including this ${stake:.0f} unit). "
            "New bets refused.",
            "daily_stop",
        )
    seasonal_worst_if = seasonal.worst_case_pnl - stake
    if seasonal_worst_if < settings.seasonal_stop:
        raise PlaybookViolation(
            f"Playbook: seasonal stop {settings.seasonal_stop:.0f} would be breached "
            f"(worst-case P&L {seasonal_worst_if:.2f} including this ${stake:.0f} unit). "
            "New bets refused.",
            "seasonal_stop",
        )

    if abs(stake - settings.unit_stake) > 1e-9:
        # Flat $25 units: allow with a note from the CLI, not a hard fail.
        pass

    return snap
