from datetime import datetime, timezone

from sporty_hq.models import Bet
from sporty_hq.reports import render_html, render_markdown, summarize


def _bet(**kwargs) -> Bet:
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)
    data = dict(
        id="b1",
        logged_at=now,
        event_id="e1",
        event_name="BOS @ TOR",
        sport="nhl",
        commence_at=now,
        market="ml",
        selection="Toronto Maple Leafs",
        odds_at_bet=165,
        stake=25.0,
        result="win",
        close_odds=148,
        clv_pct=8.16,
        pnl=41.25,
        edge_note="demo",
    )
    data.update(kwargs)
    return Bet(**data)


def test_summarize_and_markdown() -> None:
    bets = [
        _bet(),
        _bet(id="b2", result="loss", pnl=-25.0, clv_pct=4.0, event_name="NYK @ TOR"),
    ]
    summary = summarize(bets)
    assert summary.wins == 1
    assert summary.losses == 1
    assert summary.win_rate == 0.5
    assert summary.total_pnl == 16.25
    assert summary.avg_clv and summary.avg_clv > 0
    md = render_markdown(bets)
    assert "Time (ET)" in md
    assert "Pick" in md
    assert "Edge note" in md
    assert "not gambling advice" in md.lower() or "research" in md.lower()
    html = render_html(bets)
    assert "Sporty HQ" in html
    assert "41.25" in html or "+41.25" in html
    assert "Time (ET)" in html
