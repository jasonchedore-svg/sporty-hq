from datetime import datetime, timedelta, timezone
from pathlib import Path

from typer.testing import CliRunner

from sporty_hq.cli import app
from sporty_hq.models import Bet
from sporty_hq.storage import Store
from tests.conftest import DEMO_CSV, DEMO_JSON

runner = CliRunner()


def test_ingest_scan_log_settle_report(data_dir: Path) -> None:
    r = runner.invoke(app, ["ingest", "--source", "fixture", "--path", str(DEMO_JSON), "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    assert "quotes" in r.output.lower()

    r = runner.invoke(app, ["scan", "--data-dir", str(data_dir), "--min-edge", "0.03"])
    assert r.exit_code == 0, r.output
    assert "Candidates" in r.output or "edge" in r.output.lower()
    scan_md = data_dir / "last_scan.md"
    assert scan_md.exists()

    r = runner.invoke(
        app,
        [
            "log-bet",
            "--data-dir",
            str(data_dir),
            "--candidate-id",
            "1",
        ],
    )
    assert r.exit_code == 0, r.output
    assert "Logged bet" in r.output
    store = Store(data_dir / "sporty.db")
    bets = store.list_bets()
    assert len(bets) == 1
    first = bets[0]
    bet_id = first.id
    r = runner.invoke(
        app,
        [
            "log-bet",
            "--data-dir",
            str(data_dir),
            "--event-id",
            first.event_id,
            "--event",
            first.event_name,
            "--market",
            "ml",
            "--selection",
            "Other",
            "--odds",
            "-110",
        ],
    )
    assert r.exit_code != 0
    assert "1 open bet" in r.output

    r = runner.invoke(
        app,
        ["settle", bet_id, "--result", "win", "--close-odds", "+148", "--data-dir", str(data_dir)],
    )
    assert r.exit_code == 0, r.output
    assert "CLV" in r.output

    r = runner.invoke(app, ["clv-report", "--format", "md", "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    assert "P&L" in r.output or "P&amp;L" in r.output or "pnl" in r.output.lower()


def test_csv_ingest(data_dir: Path) -> None:
    r = runner.invoke(app, ["ingest", "--path", str(DEMO_CSV), "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["scan", "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    scan_md = (data_dir / "last_scan.md").read_text(encoding="utf-8")
    assert "Toronto Maple Leafs" in scan_md
    assert "edge" in scan_md.lower()


def test_alert_test_and_remind(data_dir: Path) -> None:
    r = runner.invoke(app, ["alert-test", "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    assert (data_dir / "alerts.jsonl").exists()

    store = Store(data_dir / "sporty.db")
    soon = datetime.now(timezone.utc) + timedelta(minutes=20)
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    store.insert_bet(
        Bet(
            id="openpast",
            logged_at=datetime.now(timezone.utc),
            event_id="past-1",
            event_name="Started Game",
            sport="nhl",
            commence_at=past,
            market="ml",
            selection="Home",
            odds_at_bet=-110,
            stake=25,
        )
    )
    store.insert_bet(
        Bet(
            id="opensoon",
            logged_at=datetime.now(timezone.utc),
            event_id="soon-1",
            event_name="Soon Game",
            sport="nhl",
            commence_at=soon,
            market="ml",
            selection="Away",
            odds_at_bet=150,
            stake=25,
        )
    )
    r = runner.invoke(app, ["remind", "--minutes", "45", "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    assert "Reminders sent: 2" in r.output
    r = runner.invoke(app, ["remind", "--minutes", "45", "--data-dir", str(data_dir)])
    assert "Reminders sent: 0" in r.output


def test_demo(data_dir: Path) -> None:
    r = runner.invoke(app, ["demo", "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    assert "candidates" in r.output.lower()
    assert "CLV" in r.output
    assert (data_dir / "clv-report.md").exists()
    assert (data_dir / "clv-report.html").exists()


def test_parlay_rejected(data_dir: Path) -> None:
    r = runner.invoke(
        app,
        [
            "log-bet",
            "--data-dir",
            str(data_dir),
            "--event-id",
            "x",
            "--event",
            "x",
            "--market",
            "parlay",
            "--selection",
            "yes",
            "--odds",
            "-110",
        ],
    )
    assert r.exit_code != 0
