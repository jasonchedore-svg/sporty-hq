from sporty_hq.odds_math import (
    american_to_decimal,
    clv_pct,
    decimal_to_american,
    edge,
    implied_prob,
    juice_compare_to_win,
    juice_pct,
    multiplicative_devig,
    parse_american,
    risk_to_win,
    settle_pnl,
)
import pytest


def test_american_decimal_roundtrip() -> None:
    for american in (100, 150, -110, -200, 250):
        dec = american_to_decimal(american)
        assert decimal_to_american(dec) == american


def test_implied_and_juice() -> None:
    # Classic -110 / -110 is ~4.76% juice
    probs = [implied_prob(-110), implied_prob(-110)]
    assert abs(sum(probs) - 1.0476) < 1e-3
    assert abs(juice_pct(probs) - 4.76) < 0.05
    fair = multiplicative_devig(probs)
    assert abs(sum(fair) - 1.0) < 1e-12
    assert abs(fair[0] - 0.5) < 1e-9


def test_edge_plus_money() -> None:
    # Fair 40.4% at +165 (2.65) ≈ 7.1% EV
    ev = edge(0.404, american_to_decimal(165))
    assert ev == pytest.approx(0.0706, abs=1e-3)


def test_clv_better_number() -> None:
    # Bet +165, close +148 → positive CLV
    assert clv_pct(165, 148) > 0
    assert clv_pct(-110, -120) > 0
    assert clv_pct(165, 165) == 0.0
    assert clv_pct(-110, -110) == 0


def test_settle_pnl() -> None:
    assert settle_pnl("win", 25, 165) == pytest.approx(41.25)
    assert settle_pnl("loss", 25, 165) == -25
    assert settle_pnl("push", 25, 165) == 0
    assert settle_pnl("void", 25, -110) == 0


def test_parse_american() -> None:
    assert parse_american("+165") == 165
    assert parse_american("-110") == -110
    assert parse_american(150) == 150


def test_rejects_zero_odds() -> None:
    with pytest.raises(ValueError):
        american_to_decimal(0)
    with pytest.raises(ValueError):
        implied_prob(0)
    with pytest.raises(ValueError):
        settle_pnl("draw", 25, 100)


def test_risk_to_win_100_juice_compare() -> None:
    assert risk_to_win(-110) == 110.0
    assert risk_to_win(-120) == 120.0
    assert risk_to_win(150) == pytest.approx(66.67, abs=0.005)
    assert risk_to_win(140) == pytest.approx(71.43, abs=0.005)
    assert risk_to_win(100) == 100.0

    better, worse, risk_b, risk_w, diff = juice_compare_to_win(-110, -120)
    assert better == -110
    assert worse == -120
    assert risk_b == 110.0
    assert risk_w == 120.0
    assert diff == 10.0

    better, worse, risk_b, risk_w, diff = juice_compare_to_win(140, 165)
    assert better == 165
    assert worse == 140
    assert risk_b == pytest.approx(60.61, abs=0.005)
    assert risk_w == pytest.approx(71.43, abs=0.005)
    assert diff == pytest.approx(risk_w - risk_b, abs=0.001)
    assert diff > 0
