"""Validation gates: backtest first, then ~2–3 week paper, live only if CLV stays > 0."""

from datetime import datetime, timedelta, timezone

from sporty_hq.backtest import save_result
from sporty_hq.gates import evaluate_gates
from sporty_hq.models import Bet
from tests.test_backtest import _covered_season
from sporty_hq.backtest import run_backtest


def _paper(i: int, *, clv: float, logged_at: datetime) -> Bet:
    return Bet(
        id=f"p{i:04d}",
        logged_at=logged_at,
        event_id=f"e{i}",
        event_name="NYY @ BOS",
        sport="baseball_mlb",
        commence_at=logged_at,
        market="ml",
        selection="Yankees",
        odds_at_bet=165,
        stake=25.0,
        result="win" if clv > 0 else "loss",
        close_odds=148 if clv > 0 else 165,
        clv_pct=clv,
        pnl=10.0 if clv > 0 else -25.0,
        kind="paper",
    )


def test_live_stays_locked_without_backtest(store, settings) -> None:
    now = datetime.now(timezone.utc)
    gates = evaluate_gates(store, settings, now)
    assert gates.backtest.cleared is False
    assert gates.paper_confirm.cleared is False
    assert gates.live_unlocked is False


def test_paper_confirm_is_not_a_full_season(settings) -> None:
    assert 14 <= settings.paper_confirm_days <= 21


def test_backtest_then_paper_then_live_requires_positive_clv(store, settings, data_dir) -> None:
    now = datetime.now(timezone.utc)
    result = run_backtest(
        _covered_season(),
        seasons=1,
        min_n=30,
        source="/tmp/oddsapi-archive.csv",
        sample=False,
    )
    assert result.cleared
    assert any(a["id"] == "caveat" for a in result.assumptions)
    save_result(data_dir, result)

    first = now - timedelta(days=settings.paper_confirm_days + 1)
    for i in range(settings.paper_confirm_min_n):
        store.insert_bet(_paper(i, clv=1.5, logged_at=first + timedelta(hours=i)))
    gates = evaluate_gates(store, settings, now)
    assert gates.backtest.cleared
    assert gates.paper_confirm.cleared
    assert gates.live_unlocked is False  # paper-only protocol

    # Paper avg CLV must stay positive — a later losing sample un-clears paper confirm.
    for i in range(40, 80):
        store.insert_bet(_paper(i, clv=-2.0, logged_at=now))
    gates = evaluate_gates(store, settings, now)
    assert gates.paper_confirm.cleared is False
    assert gates.live_unlocked is False
    assert load_status_not_weakened(settings)


def load_status_not_weakened(settings) -> bool:
    from sporty_hq.killswitch import load_status

    desk = load_status(settings.data_dir)
    assert desk.paused is False  # CLV drop before n=100 does not skip kill switch B; it just locks live
    return True
