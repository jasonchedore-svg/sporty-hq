"""Validation gates for live unlock. Does not weaken the owner kill switch.

1) Historical backtest vs prior-season closes (1–3 seasons) — beat the close.
2) Current-season paper for ~2–3 weeks (not a full 17-week slate).
3) Live only after both. Kill switch still trips on (A) flagged pre-gate action
   or (B) n≥100 paper with non-positive avg CLV.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sporty_hq.backtest import load_result
from sporty_hq.bankroll import season_start_et
from sporty_hq.config import Settings
from sporty_hq.feed import LIVE_FEED_ID, describe_live_feed
from sporty_hq.killswitch import load_status
from sporty_hq.models import Bet
from sporty_hq.reports import paper_tickets
from sporty_hq.storage import Store


@dataclass(frozen=True)
class GateResult:
    name: str
    cleared: bool
    n: int
    avg_clv: float | None
    detail: str


@dataclass(frozen=True)
class ValidationGates:
    backtest: GateResult
    paper_confirm: GateResult
    live_unlocked: bool
    kill_switch_paused: bool
    feed_id: str = LIVE_FEED_ID
    feed_path: str = ""

    def to_json_dict(self) -> dict:
        return {
            "backtest": self.backtest.__dict__,
            "paper_confirm": self.paper_confirm.__dict__,
            "live_unlocked": self.live_unlocked,
            "kill_switch_paused": self.kill_switch_paused,
            "feed_id": self.feed_id,
            "feed_path": self.feed_path or describe_live_feed(),
        }


def evaluate_gates(
    store: Store,
    settings: Settings,
    now: datetime,
    *,
    season_start: datetime | None = None,
) -> ValidationGates:
    desk = load_status(settings.data_dir)
    if season_start is None:
        season_start = season_start_et(now, settings.timezone, settings.season_start)
    bt = _backtest_gate(settings)
    paper = _paper_confirm_gate(store.list_bets(), settings, now, since=season_start)
    live = bt.cleared and paper.cleared and not desk.paused
    return ValidationGates(
        backtest=bt,
        paper_confirm=paper,
        live_unlocked=live,
        kill_switch_paused=desk.paused,
        feed_id=LIVE_FEED_ID,
        feed_path=describe_live_feed(),
    )


def _backtest_gate(settings: Settings) -> GateResult:
    stored = load_result(settings.data_dir)
    if stored is None:
        return GateResult(
            name="backtest",
            cleared=False,
            n=0,
            avg_clv=None,
            detail=(
                "No backtest.json — run sporty backtest with 1–3 seasons of "
                f"{LIVE_FEED_ID} historical closes (OpticOdds posted + Pinnacle close)."
            ),
        )
    cleared = bool(
        stored.cleared
        and stored.feed_parity
        and stored.archive_audit_passed
        and not stored.sample
    )
    detail = stored.note
    if stored.sample:
        detail = "SAMPLE backtest cannot clear gate 1. " + (stored.note or "")
    elif not stored.archive_audit_passed:
        detail = (
            stored.archive_audit_detail
            or "Archive audit not passed — backtest results are not valid."
        ) + (f" {stored.note}" if stored.note else "")
    elif not stored.feed_parity:
        detail = (
            stored.feed_detail
            or "Feed parity failed — backtest feed is not the live OpticOdds+Pinnacle path."
        ) + (f" {stored.note}" if stored.note else "")
    return GateResult(
        name="backtest",
        cleared=cleared,
        n=stored.n,
        avg_clv=stored.avg_clv,
        detail=detail,
    )


def _paper_confirm_gate(
    bets: list[Bet],
    settings: Settings,
    now: datetime,
    *,
    since: datetime | None,
) -> GateResult:
    """Current-season paper for paper_confirm_days (default 21 ≈ 3 weeks).

    Hypothesis: min n = paper_confirm_min_n (default 12) so a handful of lucky
    tickets cannot clear the gate. Avg CLV must be > 0. This is NOT the 100+
    kill-switch sample.
    """
    papers = [b for b in paper_tickets(bets, since=since) if b.clv_pct is not None]
    n = len(papers)
    avg = None
    if papers:
        avg = round(sum(b.clv_pct or 0.0 for b in papers) / n, 2)
    if not papers:
        return GateResult(
            name="paper_confirm",
            cleared=False,
            n=0,
            avg_clv=None,
            detail="No settled current-season paper with close lines yet.",
        )
    first = min(b.logged_at for b in papers)
    span_days = max(0, int((now - first).total_seconds() // 86400))
    need_days = settings.paper_confirm_days
    need_n = settings.paper_confirm_min_n
    ok_span = span_days >= need_days
    ok_n = n >= need_n
    ok_clv = avg is not None and avg > 0
    cleared = ok_span and ok_n and ok_clv
    detail = (
        f"Paper confirm n={n} (min {need_n}), span {span_days}d (need {need_days}d ≈ 2–3 weeks), "
        f"avg CLV {avg}. "
        + ("Cleared." if cleared else "Not cleared — keep paper logging, do not go live.")
    )
    return GateResult(
        name="paper_confirm",
        cleared=cleared,
        n=n,
        avg_clv=avg,
        detail=detail,
    )
