"""Historical backtest: feed parity, archive audit, CLV vs close, assumption log."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from typer.testing import CliRunner

from sporty_hq.archive import audit_archive
from sporty_hq.backtest import HistoricalClose, load_historical_closes, run_backtest, save_result
from sporty_hq.cli import app
from sporty_hq.feed import evaluate_feed_parity
from sporty_hq.gates import evaluate_gates
from sporty_hq.killswitch import load_status

runner = CliRunner()
REPO = Path(__file__).resolve().parents[1]
SAMPLE_CSV = REPO / "fixtures" / "historical_closes.csv"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _row(*, season="2025", i=0, market="ml", sport="baseball_mlb", **over) -> HistoricalClose:
    commence = datetime(2025, 9, 1, 23, 0, tzinfo=timezone.utc) + timedelta(days=i + 1)
    posted = commence - timedelta(hours=3)
    close = commence - timedelta(minutes=5)
    data = dict(
        season=season,
        event_id=f"e-{season}-{sport}-{market}-{i}",
        sport=sport,
        event="NYY @ BOS",
        market=market,
        selection="Yankees",
        posted_odds=165,
        close_odds=148,
        posted_feed="opticodds",
        posted_book="fanduel",
        close_book="pinnacle",
        close_feed="opticodds",
        posted_at=_iso(posted),
        close_at=_iso(close),
        commence_at=_iso(commence),
        close_kind="true_close",
    )
    data.update(over)
    return HistoricalClose(**data)


def _covered_season(season="2025", n_each=4) -> list[HistoricalClose]:
    rows: list[HistoricalClose] = []
    i = 0
    for sport in ("baseball_mlb", "americanfootball_nfl", "americanfootball_ncaaf"):
        for market in ("ml", "spread", "total"):
            for _ in range(n_each):
                rows.append(_row(season=season, i=i, market=market, sport=sport))
                i += 1
    return rows


def test_feed_parity_rejects_pinnacle_only_history() -> None:
    rows = _covered_season()
    for r in rows:
        r.posted_feed = "pinnacle"
        r.posted_book = "pinnacle"
    parity = evaluate_feed_parity(rows)
    assert not parity.matched
    result = run_backtest(rows, seasons=1, min_n=30, source="/tmp/archive.csv", sample=False)
    assert result.cleared is False
    assert result.feed_parity is False


def test_backtest_clears_on_opticodds_fanduel_plus_pinnacle_true_close() -> None:
    rows = _covered_season()
    result = run_backtest(rows, seasons=1, min_n=30, source="/tmp/opticodds-archive.csv", sample=False)
    assert result.archive_audit_passed
    assert result.feed_parity
    assert result.n >= 30
    assert result.avg_clv and result.avg_clv > 0
    assert result.cleared is True
    assert result.sample is False
    ids = {a["id"] for a in result.assumptions}
    assert {"clv_formula", "vig", "feed_parity", "close_definition", "lookback", "filters", "archive_audit", "caveat"} <= ids
    assert "backtest ≠ will work again" in result.note or any(
        "will work again" in a["statement"] for a in result.assumptions
    )
    sources = {d["id"] for d in result.data_sources}
    assert "odds_api" in sources
    assert "archive_file" in sources


def test_last_seen_close_fails_audit_and_cannot_clear() -> None:
    rows = _covered_season()
    for r in rows:
        r.close_kind = "last_seen"
    audit = audit_archive(rows, seasons_requested=1)
    assert audit.passed is False
    result = run_backtest(rows, seasons=1, min_n=30, source="/tmp/archive.csv", sample=False, audit=audit)
    assert result.cleared is False
    assert result.archive_audit_passed is False
    assert result.avg_clv is None
    assert result.n == 0
    assert "STOPPED" in result.note
    assert "vanity" in result.note.lower()


def test_sample_fixture_cannot_clear_gate(data_dir: Path) -> None:
    r = runner.invoke(
        app,
        ["backtest", "--path", str(SAMPLE_CSV), "--seasons", "1", "--data-dir", str(data_dir)],
    )
    assert r.exit_code == 1, r.output
    assert "sample=True" in r.output or '"sample": true' in r.output.lower() or '"sample": true' in r.output
    assert "assumptions" in r.output
    stored = (data_dir / "backtest.json").read_text(encoding="utf-8")
    assert "SAMPLE" in stored or '"sample": true' in stored
    from sporty_hq.config import Settings
    from sporty_hq.storage import Store, utcnow

    gates = evaluate_gates(Store(data_dir / "sporty.db"), Settings(data_dir=data_dir), utcnow())
    assert gates.backtest.cleared is False
    assert gates.live_unlocked is False
    assert load_status(data_dir).paused is False


def test_backtest_refuses_opticodds_source_without_inventing(data_dir: Path) -> None:
    r = runner.invoke(app, ["backtest", "--source", "opticodds", "--data-dir", str(data_dir)])
    assert r.exit_code == 2
    assert "OPTICODDS_API_KEY" in r.output
    assert "will not invent" in r.output.lower()


def test_backtest_requires_path(data_dir: Path) -> None:
    r = runner.invoke(app, ["backtest", "--data-dir", str(data_dir)])
    assert r.exit_code == 2
    assert "--path is required" in r.output


def test_archive_audit_owner_cleared_flag(data_dir: Path) -> None:
    r = runner.invoke(app, ["archive-audit", "--owner-cleared", "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    assert "CLEARED" in r.output
    assert "2026-09-08" in r.output


def test_archive_audit_on_sample_file(data_dir: Path) -> None:
    r = runner.invoke(
        app,
        ["archive-audit", "--path", str(SAMPLE_CSV), "--seasons", "1", "--data-dir", str(data_dir)],
    )
    assert r.exit_code == 0, r.output
    assert "PASSED" in r.output
    assert "CLEARED" in r.output
    assert (data_dir / "archive_audit.md").exists()
    assert "STOP FOR HUMAN REVIEW" not in r.output


def test_load_csv_feed_identity() -> None:
    rows = load_historical_closes(SAMPLE_CSV)
    assert rows
    assert rows[0].posted_feed == "opticodds"
    assert rows[0].posted_book == "fanduel"
    assert rows[0].close_book == "pinnacle"


def test_backtest_cli_stops_on_last_seen_without_vanity_clv(data_dir: Path) -> None:
    junk = data_dir / "last_seen.csv"
    junk.write_text(SAMPLE_CSV.read_text(encoding="utf-8").replace("true_close", "last_seen"), encoding="utf-8")
    r = runner.invoke(
        app,
        ["backtest", "--path", str(junk), "--seasons", "1", "--data-dir", str(data_dir)],
    )
    assert r.exit_code == 1, r.output
    assert "STOP FOR HUMAN REVIEW" in r.output
    assert "STOPPED for human review" in r.output
    assert "vanity" in r.output.lower()
    assert "avg CLV=" not in r.output
    stored = json.loads((data_dir / "backtest.json").read_text(encoding="utf-8"))
    assert stored["avg_clv"] is None
    assert stored["n"] == 0
    assert stored["cleared"] is False
    assert stored["archive_audit_passed"] is False
    md = (data_dir / "archive_audit.md").read_text(encoding="utf-8")
    assert "STOP FOR HUMAN REVIEW" in md
    assert "last_seen" in md


def test_failed_audit_overwrites_prior_cleared_backtest(data_dir: Path, settings, store) -> None:
    good = run_backtest(
        _covered_season(),
        seasons=1,
        min_n=30,
        source="/tmp/opticodds-archive.csv",
        sample=False,
    )
    assert good.cleared is True
    save_result(data_dir, good)
    from sporty_hq.storage import utcnow

    assert evaluate_gates(store, settings, utcnow()).backtest.cleared is True

    junk = data_dir / "thin.csv"
    junk.write_text(SAMPLE_CSV.read_text(encoding="utf-8").replace("true_close", "last_seen"), encoding="utf-8")
    r = runner.invoke(
        app,
        ["backtest", "--path", str(junk), "--seasons", "1", "--data-dir", str(data_dir)],
    )
    assert r.exit_code == 1, r.output
    gates = evaluate_gates(store, settings, utcnow())
    assert gates.backtest.cleared is False
    assert gates.live_unlocked is False
    stored = json.loads((data_dir / "backtest.json").read_text(encoding="utf-8"))
    assert stored["avg_clv"] is None


def test_archive_audit_cli_stops_on_thin_coverage(data_dir: Path) -> None:
    thin = data_dir / "thin.csv"
    header = SAMPLE_CSV.read_text(encoding="utf-8").splitlines()[0]
    body = SAMPLE_CSV.read_text(encoding="utf-8").splitlines()[1]
    thin.write_text(header + "\n" + body + "\n", encoding="utf-8")
    r = runner.invoke(
        app,
        ["archive-audit", "--path", str(thin), "--seasons", "1", "--data-dir", str(data_dir)],
    )
    assert r.exit_code == 1, r.output
    assert "STOP FOR HUMAN REVIEW" in r.output
    assert "Coverage" in r.output


def test_saved_sample_result_does_not_unlock_live(data_dir: Path, settings, store) -> None:
    rows = _covered_season()
    result = run_backtest(rows, seasons=1, min_n=30, source=str(SAMPLE_CSV), sample=None)
    assert result.sample is True
    assert result.cleared is False
    save_result(data_dir, result)
    from sporty_hq.storage import utcnow

    gates = evaluate_gates(store, settings, utcnow())
    assert gates.backtest.cleared is False
    assert gates.live_unlocked is False
