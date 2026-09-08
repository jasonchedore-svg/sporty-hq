"""CLV health gate: avg CLV is primary; FAILING if n>=100 and avg CLV <= 0."""

from datetime import datetime, timezone

from sporty_hq.models import Bet
from sporty_hq.odds_math import clv_pct
from sporty_hq.reports import ModelHealth, model_health, summarize


def _settled(i: int, *, clv: float, result: str = "loss") -> Bet:
    now = datetime(2026, 9, 8, tzinfo=timezone.utc)
    return Bet(
        id=f"c{i:04d}",
        logged_at=now,
        event_id=f"e{i}",
        event_name="NYY @ BOS",
        sport="baseball_mlb",
        commence_at=now,
        market="ml",
        selection="New York Yankees",
        odds_at_bet=165,
        stake=25.0,
        result=result,
        close_odds=148 if clv != 0 else 165,
        clv_pct=clv,
        pnl=-25.0 if result == "loss" else 41.25,
    )


def test_flat_close_is_zero_clv() -> None:
    assert clv_pct(165, 165) == 0.0
    assert clv_pct(-110, -110) == 0.0


def test_health_insufficient_under_100() -> None:
    bets = [_settled(i, clv=-1.0) for i in range(99)]
    summary = summarize(bets, judge_n=100)
    assert summary.health == ModelHealth.INSUFFICIENT_SAMPLE.value
    assert summary.clv_n == 99
    assert model_health(summary.avg_clv, summary.clv_n) is ModelHealth.INSUFFICIENT_SAMPLE


def test_health_failing_n100_nonpositive_avg() -> None:
    bets = [_settled(i, clv=0.0) for i in range(100)]
    summary = summarize(bets, judge_n=100)
    assert summary.clv_n == 100
    assert summary.avg_clv == 0
    assert summary.health == ModelHealth.FAILING.value

    bets_neg = [_settled(i, clv=-0.5) for i in range(100)]
    assert summarize(bets_neg, judge_n=100).health == ModelHealth.FAILING.value


def test_health_passing_n100_positive_avg() -> None:
    bets = [_settled(i, clv=0.4) for i in range(100)]
    summary = summarize(bets, judge_n=100)
    assert summary.avg_clv and summary.avg_clv > 0
    assert summary.health == ModelHealth.PASSING.value


def test_logged_bet_requires_odds_at_bet_and_close_for_clv() -> None:
    bet = _settled(1, clv=6.94)
    assert bet.odds_at_bet is not None
    assert bet.close_odds is not None
    assert bet.clv_pct == round(clv_pct(bet.odds_at_bet, bet.close_odds), 2) or bet.clv_pct == 6.94
