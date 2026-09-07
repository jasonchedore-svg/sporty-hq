from datetime import datetime, timezone

from sporty_hq.models import Bet
from sporty_hq.session import (
    BET_LOG_COLUMNS,
    SESSION_FIELDS,
    build_session,
    log_row,
    read_session,
)
from tests.conftest import REPO


def test_sample_session_has_locked_fields() -> None:
    path = REPO / "fixtures" / "sample_session.json"
    session = read_session(path)
    data = session.to_json_dict()
    assert list(data.keys())[:10] == list(SESSION_FIELDS)
    assert session.stake_unit_usd == 25
    assert session.stop_loss_usd == -100
    assert session.edge_floor_pct == 3
    assert session.max_bets == 4
    assert session.status == "sample"
    for row in session.bets:
        for key in (
            "time_et",
            "event",
            "market",
            "pick",
            "odds_at_bet",
            "stake",
            "close_odds",
            "clv",
            "result",
            "pnl",
            "edge_note",
        ):
            assert key in row
    flat = next(b for b in session.bets if b["clv"] == 0)
    assert flat["odds_at_bet"] == flat["close_odds"]


def test_log_row_columns() -> None:
    bet = Bet(
        id="x",
        logged_at=datetime(2026, 9, 7, 17, 5, tzinfo=timezone.utc),
        event_id="e",
        event_name="NYY @ BOS",
        sport="baseball_mlb",
        commence_at=None,
        market="ml",
        selection="New York Yankees",
        odds_at_bet=165,
        stake=25,
        result="win",
        close_odds=148,
        clv_pct=6.94,
        pnl=41.25,
        edge_note="vs consensus",
    )
    row = log_row(bet)
    assert tuple(row.keys()) == BET_LOG_COLUMNS
    assert row["Time (ET)"].endswith("ET")
    assert row["Pick"] == "New York Yankees"
    assert row["Edge note"] == "vs consensus"
    session = build_session([bet], now=bet.logged_at)
    assert session.clv_n == 1
    assert session.pnl_usd == 41.25
