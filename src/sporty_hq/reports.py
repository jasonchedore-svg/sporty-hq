"""Markdown + HTML dashboards: locked v1 bet-log columns, CLV, win rate, P&L."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from statistics import mean

from sporty_hq import DISCLAIMER
from sporty_hq.models import Bet
from sporty_hq.session import BET_LOG_COLUMNS, log_row
from typing import Any

CLV_JUDGE_N = 100


class ModelHealth(str, Enum):
    PASSING = "PASSING"
    FAILING = "FAILING"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


@dataclass
class ClvSummary:
    bets: int
    open_bets: int
    settled: int
    wins: int
    losses: int
    pushes: int
    win_rate: float | None
    total_stake: float
    total_pnl: float
    roi: float | None
    avg_clv: float | None
    clv_n: int
    units: float
    clv_ready: bool
    health: str = ModelHealth.INSUFFICIENT_SAMPLE.value
    paper_n: int = 0
    live_unlocked: bool = False


def model_health(avg_clv: float | None, clv_n: int, judge_n: int = CLV_JUDGE_N) -> ModelHealth:
    """Primary process metric: average CLV.

    FAILING if n >= judge_n and avg CLV <= 0 (flat close is 0).
    Smaller samples are INSUFFICIENT_SAMPLE — directional only.
    """
    if clv_n < judge_n:
        return ModelHealth.INSUFFICIENT_SAMPLE
    if avg_clv is None or avg_clv <= 0:
        return ModelHealth.FAILING
    return ModelHealth.PASSING


def summarize(bets: list[Bet], unit: float = 25.0, judge_n: int = CLV_JUDGE_N) -> ClvSummary:
    open_bets = [b for b in bets if b.is_open]
    settled = [b for b in bets if not b.is_open]
    wins = [b for b in settled if b.result == "win"]
    losses = [b for b in settled if b.result == "loss"]
    pushes = [b for b in settled if b.result in {"push", "void"}]
    decided = len(wins) + len(losses)
    pnl_values = [b.pnl for b in settled if b.pnl is not None]
    total_pnl = round(sum(pnl_values), 2)
    total_stake = round(sum(b.stake for b in settled), 2)
    clvs = [b.clv_pct for b in settled if b.clv_pct is not None]
    avg_clv = round(mean(clvs), 2) if clvs else None
    health = model_health(avg_clv, len(clvs), judge_n)
    return ClvSummary(
        bets=len(bets),
        open_bets=len(open_bets),
        settled=len(settled),
        wins=len(wins),
        losses=len(losses),
        pushes=len(pushes),
        win_rate=(len(wins) / decided) if decided else None,
        total_stake=total_stake,
        total_pnl=total_pnl,
        roi=(total_pnl / total_stake) if total_stake else None,
        avg_clv=avg_clv,
        clv_n=len(clvs),
        units=round(total_pnl / unit, 2) if unit else 0.0,
        clv_ready=len(clvs) >= judge_n,
        health=health.value,
        paper_n=sum(1 for b in settled if is_paper(b) and b.clv_pct is not None),
        live_unlocked=False,
    )


def is_paper(bet: Bet) -> bool:
    return (getattr(bet, "kind", None) or "paper").strip().lower() != "live"


def paper_tickets(bets: list[Bet], *, since=None) -> list[Bet]:
    pool = [b for b in bets if is_paper(b)]
    if since is None:
        return pool
    return [b for b in pool if b.logged_at >= since]


def paper_clv_summary(
    bets: list[Bet],
    unit: float = 25.0,
    judge_n: int = CLV_JUDGE_N,
    *,
    since=None,
) -> ClvSummary:
    """Primary scoreboard: paper tickets only (posted vs close)."""
    summary = summarize(paper_tickets(bets, since=since), unit, judge_n)
    summary.paper_n = summary.clv_n
    summary.live_unlocked = False  # live unlock is gates (backtest + 2–3 week paper), not n≥100
    return summary


def live_logging_allowed(
    bets: list[Bet],
    *,
    unit: float = 25.0,
    judge_n: int = CLV_JUDGE_N,
    since=None,
) -> tuple[bool, ClvSummary]:
    """Paper CLV snapshot only. Live unlock lives in ``gates.evaluate_gates``."""
    summary = paper_clv_summary(bets, unit, judge_n, since=since)
    return False, summary


def _fmt_odds(value: int | None) -> str:
    if value is None:
        return "—"
    return f"{value:+d}"


def _fmt_clv(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value) < 1e-9:
        return "0"
    return f"{value:+.2f}%"


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}"


def _clv_footnote(summary: ClvSummary, judge_n: int) -> str:
    health = summary.health
    if health == ModelHealth.FAILING.value:
        return (
            f"HEALTH {health}: n={summary.clv_n} (≥{judge_n}) and avg CLV "
            f"{summary.avg_clv} ≤ 0. Model is FAILING — do not scale."
        )
    if health == ModelHealth.PASSING.value:
        return (
            f"HEALTH {health}: n={summary.clv_n} (≥{judge_n}) and avg CLV "
            f"{summary.avg_clv} > 0. Primary metric is average CLV, not win rate."
        )
    return (
        f"HEALTH {health}: CLV n={summary.clv_n} is below the ~{judge_n}+ sample "
        "to judge the process — treat average CLV as directional only."
    )


def _kill_switch_md(desk: Any, paper: ClvSummary, judge_n: int, gates: Any = None) -> list[str]:
    status = "PAUSED" if desk is not None and getattr(desk, "paused", False) else "RUNNING"
    trip = getattr(desk, "trip", None) if desk is not None else None
    detail = getattr(desk, "detail", "") if desk is not None else ""
    live = bool(gates.live_unlocked) if gates is not None else False
    bt = "—"
    pc = "—"
    if gates is not None:
        bt = "cleared" if gates.backtest.cleared else "open"
        pc = "cleared" if gates.paper_confirm.cleared else "open"
    lines = [
        "## Kill switch + validation gates",
        "",
        f"- Desk: **{status}**",
        f"- Trip: **{trip or '—'}**",
        "- Gate 1 backtest (prior 1–3 seasons vs close): **" + bt + "**",
        "- Gate 2 paper confirm (current season ~2–3 weeks, not a full 17): **" + pc + "**",
        f"- Feed path: **{getattr(gates, 'feed_id', None) or 'opticodds+pinnacle'}**",
        f"- Live unlocked: **{live}** (both gates; kill switch not paused)",
        f"- A) Pause if flagged research is acted on before those gates clear.",
        f"- B) Pause if paper avg CLV is non-positive after n≥{judge_n} (health FAILING).",
        f"- Paper health (kill-switch sample): **{paper.health}** n={paper.clv_n}",
        "- Resume: written review artifact only (`sporty resume --review`). No silent override.",
        "",
    ]
    if status == "PAUSED":
        lines.extend(
            [
                f"> PAUSED ({trip}): {detail}",
                "",
            ]
        )
    return lines


def render_markdown(
    bets: list[Bet],
    unit: float = 25.0,
    *,
    sample: bool = False,
    judge_n: int = CLV_JUDGE_N,
    desk: Any = None,
    gates: Any = None,
) -> str:
    summary = summarize(bets, unit, judge_n)
    paper = paper_clv_summary(bets, unit, judge_n)
    wr = "—" if summary.win_rate is None else f"{summary.win_rate * 100:.1f}%"
    roi = "—" if summary.roi is None else f"{summary.roi * 100:.1f}%"
    avg_clv = "—" if summary.avg_clv is None else (
        "0" if abs(summary.avg_clv) < 1e-9 else f"{summary.avg_clv:+.2f}%"
    )
    paper_avg = "—" if paper.avg_clv is None else (
        "0" if abs(paper.avg_clv) < 1e-9 else f"{paper.avg_clv:+.2f}%"
    )
    header = " | ".join(BET_LOG_COLUMNS)
    align = "|---|---|---|---|---:|---:|---:|---:|---|---:|---|"
    lines = [
        "# Sporty HQ — CLV / P&L",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
    ]
    if sample:
        lines.extend(
            [
                "> SAMPLE illustration only — not FanDuel fills. Hybrid HQ never places bets.",
                "",
            ]
        )
    lines.extend(_kill_switch_md(desk, paper, judge_n, gates))
    lines.extend(
        [
            "## Summary",
            "",
            f"- Bets: **{summary.bets}** ({summary.open_bets} open, {summary.settled} settled)",
            f"- Record: **{summary.wins}-{summary.losses}-{summary.pushes}** (win rate {wr})",
            f"- P&L: **${summary.total_pnl:+.2f}** ({summary.units:+.2f} u @ ${unit:.0f})",
            f"- ROI: **{roi}** on ${summary.total_stake:.2f} settled stake",
            f"- Paper avg CLV: **{paper_avg}** (n={paper.clv_n}) — **primary health metric**",
            f"- Paper health: **{paper.health}** · live unlocked: **{bool(gates.live_unlocked) if gates is not None else False}**",
            f"- All-ticket avg CLV: **{avg_clv}** (n={summary.clv_n})",
            f"- {_clv_footnote(paper, judge_n)}",
            "",
            "## Bet log",
            "",
            f"| {header} |",
            align,
        ]
    )
    for bet in bets:
        row = log_row(bet)
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["Time (ET)"]),
                    str(row["Event"]),
                    str(row["Market"]),
                    str(row["Pick"]),
                    _fmt_odds(row["Odds at bet"]),
                    f"{row['Stake']:.2f}",
                    _fmt_odds(row["Close odds"]),
                    _fmt_clv(row["CLV"] if row["CLV"] is None else float(row["CLV"])),
                    str(row["Result"] or "open"),
                    _fmt_money(row["P&L"]),
                    str(row["Edge note"]).replace("|", "/"),
                ]
            )
            + " |"
        )
    if not bets:
        lines.append("| — | _no bets logged_ |  |  |  |  |  |  |  |  |  |")
    lines.extend(["", f"_{DISCLAIMER}_", ""])
    return "\n".join(lines)


def render_html(
    bets: list[Bet],
    unit: float = 25.0,
    *,
    sample: bool = False,
    judge_n: int = CLV_JUDGE_N,
    desk: Any = None,
    gates: Any = None,
) -> str:
    summary = summarize(bets, unit, judge_n)
    paper = paper_clv_summary(bets, unit, judge_n)
    wr = "—" if summary.win_rate is None else f"{summary.win_rate * 100:.1f}%"
    roi = "—" if summary.roi is None else f"{summary.roi * 100:.1f}%"
    avg_clv = "—" if paper.avg_clv is None else (
        "0" if abs(paper.avg_clv) < 1e-9 else f"{paper.avg_clv:+.2f}%"
    )
    paused = bool(desk is not None and getattr(desk, "paused", False))
    trip = getattr(desk, "trip", None) if desk is not None else None
    pause_banner = ""
    if paused:
        pause_banner = (
            f'<p class="sub" style="color:#ff6b6b">KILL SWITCH PAUSED '
            f"({html.escape(str(trip or ''))}). Resume requires a written review. "
            "No silent override.</p>"
        )
    heads = "".join(f"<th>{html.escape(col)}</th>" for col in BET_LOG_COLUMNS)
    rows = []
    for bet in bets:
        row = log_row(bet)
        pnl = row["P&L"]
        clv = row["CLV"]
        pnl_class = ""
        if pnl is not None:
            pnl_class = "pos" if pnl > 0 else "neg" if pnl < 0 else ""
        clv_class = ""
        if clv is not None:
            clv_class = "pos" if clv > 0 else "neg" if clv < 0 else ""
        cells = [
            row["Time (ET)"],
            row["Event"],
            row["Market"],
            row["Pick"],
            _fmt_odds(row["Odds at bet"]),
            f"{row['Stake']:.2f}",
            _fmt_odds(row["Close odds"]),
            _fmt_clv(None if clv is None else float(clv)),
            row["Result"] or "open",
            _fmt_money(pnl),
            row["Edge note"],
        ]
        tds = []
        for i, cell in enumerate(cells):
            cls = ""
            if i == 7:
                cls = clv_class
            if i == 9:
                cls = pnl_class
            attr = f' class="{cls}"' if cls else ""
            tds.append(f"<td{attr}>{html.escape(str(cell))}</td>")
        rows.append("<tr>" + "".join(tds) + "</tr>")
    table_body = "\n".join(rows) or f'<tr><td colspan="{len(BET_LOG_COLUMNS)}">No bets logged yet.</td></tr>'
    banner = (
        '<p class="sub">SAMPLE illustration only — not FanDuel fills. HQ never places bets.</p>'
        if sample
        else ""
    )
    footnote = html.escape(_clv_footnote(summary, judge_n))
    disclaimer = html.escape(DISCLAIMER)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Sporty HQ — CLV / P&amp;L</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 0; background: #0e141b; color: #e8eef4; }}
    main {{ max-width: 1200px; margin: 0 auto; padding: 2rem 1.25rem 4rem; }}
    h1 {{ font-size: 1.6rem; margin: 0 0 .25rem; }}
    .sub {{ color: #8aa0b5; margin-bottom: 1.5rem; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: .75rem; margin-bottom: 1.5rem; }}
    .card {{ background: #18222c; border: 1px solid #2a3a4a; border-radius: 10px; padding: .9rem 1rem; }}
    .card span {{ display: block; color: #8aa0b5; font-size: .75rem; text-transform: uppercase; letter-spacing: .04em; }}
    .card strong {{ font-size: 1.25rem; }}
    table {{ width: 100%; border-collapse: collapse; font-size: .85rem; background: #18222c; border-radius: 10px; overflow: hidden; }}
    th, td {{ text-align: left; padding: .55rem .7rem; border-bottom: 1px solid #2a3a4a; }}
    th {{ color: #8aa0b5; font-weight: 600; font-size: .7rem; text-transform: none; }}
    .pos {{ color: #3dd68c; }}
    .neg {{ color: #ff6b6b; }}
    footer {{ margin-top: 2rem; color: #8aa0b5; font-size: .85rem; line-height: 1.45; }}
  </style>
</head>
<body>
<main>
  <h1>Sporty HQ</h1>
  <p class="sub">CLV, win rate, and P&amp;L · generated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</p>
  {banner}
  {pause_banner}
  <div class="cards">
    <div class="card"><span>Record</span><strong>{summary.wins}-{summary.losses}-{summary.pushes}</strong></div>
    <div class="card"><span>Win rate</span><strong>{wr}</strong></div>
    <div class="card"><span>P&amp;L</span><strong>${summary.total_pnl:+.2f}</strong></div>
    <div class="card"><span>ROI</span><strong>{roi}</strong></div>
    <div class="card"><span>Paper avg CLV</span><strong>{avg_clv}</strong></div>
    <div class="card"><span>Health</span><strong>{paper.health}</strong></div>
    <div class="card"><span>Kill switch</span><strong>{"PAUSED" if paused else "RUNNING"}</strong></div>
  </div>
  <table>
    <thead>
      <tr>{heads}</tr>
    </thead>
    <tbody>
      {table_body}
    </tbody>
  </table>
  <footer>{footnote}<br/><br/>{disclaimer}</footer>
</main>
</body>
</html>
"""
