"""Markdown + HTML dashboards: CLV, win rate, P&L."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean

from sporty_hq import DISCLAIMER
from sporty_hq.models import Bet


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


def summarize(bets: list[Bet], unit: float = 25.0) -> ClvSummary:
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
        avg_clv=round(mean(clvs), 2) if clvs else None,
        clv_n=len(clvs),
        units=round(total_pnl / unit, 2) if unit else 0.0,
    )


def _fmt_odds(value: int | None) -> str:
    if value is None:
        return "—"
    return f"{value:+d}"


def _fmt_pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}%"


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}"


def render_markdown(bets: list[Bet], unit: float = 25.0) -> str:
    summary = summarize(bets, unit)
    wr = "—" if summary.win_rate is None else f"{summary.win_rate * 100:.1f}%"
    roi = "—" if summary.roi is None else f"{summary.roi * 100:.1f}%"
    avg_clv = "—" if summary.avg_clv is None else f"{summary.avg_clv:+.2f}%"
    lines = [
        "# Sporty HQ — CLV / P&L",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "## Summary",
        "",
        f"- Bets: **{summary.bets}** ({summary.open_bets} open, {summary.settled} settled)",
        f"- Record: **{summary.wins}-{summary.losses}-{summary.pushes}** (win rate {wr})",
        f"- P&L: **${summary.total_pnl:+.2f}** ({summary.units:+.2f} u @ ${unit:.0f})",
        f"- ROI: **{roi}** on ${summary.total_stake:.2f} settled stake",
        f"- Avg CLV: **{avg_clv}** (n={summary.clv_n})",
        "",
        "## Bet log",
        "",
        "| date | event | market | selection | odds_at_bet | stake | result | close_odds | CLV | pnl | notes |",
        "|---|---|---|---|---:|---:|---|---:|---:|---:|---|",
    ]
    for bet in bets:
        logged = bet.logged_at.strftime("%Y-%m-%d")
        clv = "—" if bet.clv_pct is None else f"{bet.clv_pct:+.2f}%"
        sel = bet.selection if bet.point is None else f"{bet.selection} {bet.point}"
        lines.append(
            "| "
            + " | ".join(
                [
                    logged,
                    bet.event_name,
                    bet.market,
                    sel,
                    _fmt_odds(bet.odds_at_bet),
                    f"{bet.stake:.2f}",
                    bet.result or "open",
                    _fmt_odds(bet.close_odds),
                    clv,
                    _fmt_money(bet.pnl),
                    bet.notes.replace("|", "/"),
                ]
            )
            + " |"
        )
    if not bets:
        lines.append("| — | _no bets logged_ |  |  |  |  |  |  |  |  |  |")
    lines.extend(["", f"_{DISCLAIMER}_", ""])
    return "\n".join(lines)


def render_html(bets: list[Bet], unit: float = 25.0) -> str:
    summary = summarize(bets, unit)
    wr = "—" if summary.win_rate is None else f"{summary.win_rate * 100:.1f}%"
    roi = "—" if summary.roi is None else f"{summary.roi * 100:.1f}%"
    avg_clv = "—" if summary.avg_clv is None else f"{summary.avg_clv:+.2f}%"
    rows = []
    for bet in bets:
        pnl_class = ""
        if bet.pnl is not None:
            pnl_class = "pos" if bet.pnl > 0 else "neg" if bet.pnl < 0 else ""
        clv_class = ""
        if bet.clv_pct is not None:
            clv_class = "pos" if bet.clv_pct > 0 else "neg" if bet.clv_pct < 0 else ""
        sel = bet.selection if bet.point is None else f"{bet.selection} {bet.point}"
        rows.append(
            "<tr>"
            + "".join(
                f"<td>{html.escape(str(cell))}</td>"
                for cell in [
                    bet.logged_at.strftime("%Y-%m-%d"),
                    bet.event_name,
                    bet.market,
                    sel,
                    _fmt_odds(bet.odds_at_bet),
                    f"{bet.stake:.2f}",
                    bet.result or "open",
                    _fmt_odds(bet.close_odds),
                ]
            )
            + f'<td class="{clv_class}">{html.escape("—" if bet.clv_pct is None else f"{bet.clv_pct:+.2f}%")}</td>'
            + f'<td class="{pnl_class}">{html.escape(_fmt_money(bet.pnl))}</td>'
            + f"<td>{html.escape(bet.notes)}</td>"
            + "</tr>"
        )
    table_body = "\n".join(rows) or '<tr><td colspan="11">No bets logged yet.</td></tr>'
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
    main {{ max-width: 1100px; margin: 0 auto; padding: 2rem 1.25rem 4rem; }}
    h1 {{ font-size: 1.6rem; margin: 0 0 .25rem; }}
    .sub {{ color: #8aa0b5; margin-bottom: 1.5rem; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: .75rem; margin-bottom: 1.5rem; }}
    .card {{ background: #18222c; border: 1px solid #2a3a4a; border-radius: 10px; padding: .9rem 1rem; }}
    .card span {{ display: block; color: #8aa0b5; font-size: .75rem; text-transform: uppercase; letter-spacing: .04em; }}
    .card strong {{ font-size: 1.25rem; }}
    table {{ width: 100%; border-collapse: collapse; font-size: .9rem; background: #18222c; border-radius: 10px; overflow: hidden; }}
    th, td {{ text-align: left; padding: .55rem .7rem; border-bottom: 1px solid #2a3a4a; }}
    th {{ color: #8aa0b5; font-weight: 600; font-size: .75rem; text-transform: uppercase; }}
    .pos {{ color: #3dd68c; }}
    .neg {{ color: #ff6b6b; }}
    footer {{ margin-top: 2rem; color: #8aa0b5; font-size: .85rem; line-height: 1.45; }}
  </style>
</head>
<body>
<main>
  <h1>Sporty HQ</h1>
  <p class="sub">CLV, win rate, and P&amp;L · generated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</p>
  <div class="cards">
    <div class="card"><span>Record</span><strong>{summary.wins}-{summary.losses}-{summary.pushes}</strong></div>
    <div class="card"><span>Win rate</span><strong>{wr}</strong></div>
    <div class="card"><span>P&amp;L</span><strong>${summary.total_pnl:+.2f}</strong></div>
    <div class="card"><span>ROI</span><strong>{roi}</strong></div>
    <div class="card"><span>Avg CLV</span><strong>{avg_clv}</strong></div>
    <div class="card"><span>Open</span><strong>{summary.open_bets}</strong></div>
  </div>
  <table>
    <thead>
      <tr>
        <th>date</th><th>event</th><th>market</th><th>selection</th>
        <th>odds_at_bet</th><th>stake</th><th>result</th><th>close_odds</th>
        <th>CLV</th><th>pnl</th><th>notes</th>
      </tr>
    </thead>
    <tbody>
      {table_body}
    </tbody>
  </table>
  <footer>{disclaimer}</footer>
</main>
</body>
</html>
"""
