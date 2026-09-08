"""session.json — locked v1 fields. Never a FanDuel fill; user-logged tickets only."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sporty_hq.models import Bet, display_market, normalize_market

ET = ZoneInfo("America/New_York")

SESSION_FIELDS = (
    "session_id",
    "status",
    "stake_unit_usd",
    "stop_loss_usd",
    "edge_floor_pct",
    "max_bets",
    "pnl_usd",
    "clv_sum",
    "clv_n",
    "bets",
)

BET_LOG_COLUMNS = (
    "Time (ET)",
    "Event",
    "Market",
    "Pick",
    "Odds at bet",
    "Stake",
    "Close odds",
    "CLV",
    "Result",
    "P&L",
    "Edge note",
)


def format_time_et(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt.astimezone(ET).strftime("%Y-%m-%d %H:%M ET")


def session_id_for(now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    return now.astimezone(ET).strftime("%Y-%m-%d")


def bet_to_session_row(bet: Bet) -> dict[str, Any]:
    return {
        "id": bet.id,
        "time_et": format_time_et(bet.logged_at),
        "event": bet.event_name,
        "market": display_market(bet.market) if bet.market else "",
        "pick": bet.pick,
        "odds_at_bet": bet.odds_at_bet,
        "stake": bet.stake,
        "close_odds": bet.close_odds,
        "clv": bet.clv_pct,
        "result": bet.result,
        "pnl": bet.pnl,
        "edge_note": bet.edge_note,
        "event_id": bet.event_id,
        "sport": bet.sport,
        "kind": getattr(bet, "kind", None) or "paper",
    }


def log_row(bet: Bet) -> dict[str, Any]:
    """Exact bet-log columns (display keys)."""
    clv: Any
    if bet.clv_pct is None:
        clv = None
    elif abs(bet.clv_pct) < 1e-9:
        clv = 0
    else:
        clv = round(bet.clv_pct, 2)
    return {
        "Time (ET)": format_time_et(bet.logged_at),
        "Event": bet.event_name,
        "Market": display_market(bet.market) if bet.market else "",
        "Pick": bet.pick,
        "Odds at bet": bet.odds_at_bet,
        "Stake": bet.stake,
        "Close odds": bet.close_odds,
        "CLV": clv,
        "Result": bet.result,
        "P&L": bet.pnl,
        "Edge note": bet.edge_note,
    }


@dataclass
class SessionState:
    session_id: str
    status: str = "open"
    stake_unit_usd: float = 25
    stop_loss_usd: float = -100
    edge_floor_pct: float = 3
    max_bets: int = 4
    pnl_usd: float = 0
    clv_sum: float = 0
    clv_n: int = 0
    bets: list[dict[str, Any]] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "stake_unit_usd": self.stake_unit_usd,
            "stop_loss_usd": self.stop_loss_usd,
            "edge_floor_pct": self.edge_floor_pct,
            "max_bets": self.max_bets,
            "pnl_usd": self.pnl_usd,
            "clv_sum": self.clv_sum,
            "clv_n": self.clv_n,
            "bets": self.bets,
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> SessionState:
        missing = [k for k in SESSION_FIELDS if k not in data]
        if missing:
            raise ValueError(f"session.json missing fields: {missing}")
        return cls(
            session_id=str(data["session_id"]),
            status=str(data["status"]),
            stake_unit_usd=float(data["stake_unit_usd"]),
            stop_loss_usd=float(data["stop_loss_usd"]),
            edge_floor_pct=float(data["edge_floor_pct"]),
            max_bets=int(data["max_bets"]),
            pnl_usd=float(data["pnl_usd"]),
            clv_sum=float(data["clv_sum"]),
            clv_n=int(data["clv_n"]),
            bets=list(data["bets"] or []),
        )


def build_session(
    bets: list[Bet],
    *,
    now: datetime,
    stake_unit_usd: float = 25,
    stop_loss_usd: float = -100,
    edge_floor_pct: float = 3,
    max_bets: int = 4,
    status: str | None = None,
) -> SessionState:
    sid = session_id_for(now)
    rows = [bet_to_session_row(b) for b in bets]
    pnl = round(sum(b.pnl or 0.0 for b in bets if b.pnl is not None), 2)
    clvs = [b.clv_pct for b in bets if b.clv_pct is not None]
    clv_sum = round(sum(clvs), 2)
    resolved = status
    if resolved is None:
        if pnl <= stop_loss_usd:
            resolved = "stopped"
        else:
            resolved = "open"
    return SessionState(
        session_id=sid,
        status=resolved,
        stake_unit_usd=stake_unit_usd,
        stop_loss_usd=stop_loss_usd,
        edge_floor_pct=edge_floor_pct,
        max_bets=max_bets,
        pnl_usd=pnl,
        clv_sum=clv_sum,
        clv_n=len(clvs),
        bets=rows,
    )


def write_session(path: Path, session: SessionState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session.to_json_dict(), indent=2) + "\n", encoding="utf-8")


def read_session(path: Path) -> SessionState:
    data = json.loads(path.read_text(encoding="utf-8"))
    return SessionState.from_json_dict(data)


def session_row_to_bet(row: dict[str, Any]) -> Bet:
    """Rehydrate a session.json bet row for reports (sample or live)."""
    raw_time = str(row.get("time_et") or row.get("logged_at") or "")
    logged = _parse_time_et(raw_time)
    market = normalize_market(str(row.get("market") or "ml"))
    return Bet(
        id=str(row.get("id") or ""),
        logged_at=logged,
        event_id=str(row.get("event_id") or ""),
        event_name=str(row.get("event") or ""),
        sport=str(row.get("sport") or ""),
        commence_at=None,
        market=market,
        selection=str(row.get("pick") or row.get("selection") or ""),
        point=None,
        odds_at_bet=int(row["odds_at_bet"]),
        stake=float(row.get("stake") or 25),
        result=row.get("result"),
        close_odds=row.get("close_odds"),
        clv_pct=row.get("clv"),
        pnl=row.get("pnl"),
        edge_note=str(row.get("edge_note") or ""),
        kind=str(row.get("kind") or "paper"),
    )


def _parse_time_et(text: str) -> datetime:
    cleaned = text.replace(" ET", "").strip()
    try:
        naive = datetime.strptime(cleaned, "%Y-%m-%d %H:%M")
        return naive.replace(tzinfo=ET)
    except ValueError:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ET)
        return parsed


def persist_live_session(
    path: Path,
    bets: list[Bet],
    *,
    now: datetime,
    stake_unit_usd: float,
    stop_loss_usd: float,
    edge_floor_pct: float,
    max_bets: int,
) -> SessionState:
    """Rewrite session.json from user-logged bets. Never invents fills."""
    today = session_id_for(now)
    start = datetime.fromisoformat(f"{today}T00:00:00").replace(tzinfo=ET)
    todays = [b for b in bets if b.logged_at.astimezone(ET) >= start]
    session = build_session(
        todays,
        now=now,
        stake_unit_usd=stake_unit_usd,
        stop_loss_usd=stop_loss_usd,
        edge_floor_pct=edge_floor_pct,
        max_bets=max_bets,
    )
    write_session(path, session)
    return session
