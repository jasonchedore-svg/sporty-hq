from datetime import datetime, timedelta, timezone
from pathlib import Path

from typer.testing import CliRunner

from sporty_hq.cli import app
from sporty_hq.models import Bet, Candidate
from sporty_hq.storage import Store
from tests.conftest import DEMO_CSV, DEMO_JSON

runner = CliRunner()


def test_ingest_scan_log_settle_report(data_dir: Path) -> None:
    r = runner.invoke(app, ["ingest", "--source", "fixture", "--path", str(DEMO_JSON), "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    assert "quotes" in r.output.lower()

    r = runner.invoke(app, ["scan", "--data-dir", str(data_dir), "--min-edge", "0.03"])
    assert r.exit_code == 0, r.output
    scan_md = data_dir / "last_scan.md"
    assert scan_md.exists()
    text = scan_md.read_text(encoding="utf-8")
    assert "Yankees" in text or "Eagles" in text

    r = runner.invoke(
        app,
        ["log-bet", "--data-dir", str(data_dir), "--candidate-id", "1"],
    )
    assert r.exit_code == 0, r.output
    assert "Logged bet" in r.output
    assert (data_dir / "session.json").exists()
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
            "--pick",
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
    assert "Time (ET)" in r.output
    assert "Pick" in r.output
    assert "Edge note" in r.output

    r = runner.invoke(app, ["session", "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    assert "stake_unit_usd" in r.output
    assert "clv_n" in r.output


def test_csv_ingest(data_dir: Path) -> None:
    r = runner.invoke(app, ["ingest", "--path", str(DEMO_CSV), "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["scan", "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    scan_md = (data_dir / "last_scan.md").read_text(encoding="utf-8")
    assert "Toronto Blue Jays" in scan_md
    assert "edge" in scan_md.lower()


def test_alert_test_and_remind(data_dir: Path) -> None:
    r = runner.invoke(app, ["alert-test", "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    assert (data_dir / "alerts.jsonl").exists()
    assert "published via: console, file" in r.output
    assert "Slack skipped" in r.output

    store = Store(data_dir / "sporty.db")
    flagged_tip = datetime.now(timezone.utc) + timedelta(minutes=40)
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    store.replace_candidates(
        None,
        [
            Candidate(
                event_id="flag-1",
                sport="baseball_mlb",
                event_name="NYY @ BOS",
                commence_at=flagged_tip,
                market="ml",
                selection="Yankees",
                book="fanduel",
                american_odds=150,
                decimal_odds=2.5,
                fair_prob=0.44,
                implied_prob=0.4,
                edge_pct=7.0,
                juice_pct=4.0,
                rationale="flagged",
            )
        ],
    )
    store.insert_bet(
        Bet(
            id="openpast",
            logged_at=datetime.now(timezone.utc),
            event_id="past-1",
            event_name="Started Game",
            sport="baseball_mlb",
            commence_at=past,
            market="ml",
            selection="Home",
            odds_at_bet=-110,
            stake=25,
        )
    )
    r = runner.invoke(app, ["remind", "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    assert "Reminders sent: 2" in r.output
    r = runner.invoke(app, ["remind", "--data-dir", str(data_dir)])
    assert "Quiet" in r.output or "Reminders sent: 0" in r.output


def test_remind_quiet_without_flags(data_dir: Path) -> None:
    r = runner.invoke(app, ["remind", "--type", "pre_game", "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    assert "Quiet" in r.output


def test_demo(data_dir: Path) -> None:
    r = runner.invoke(app, ["demo", "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    assert "candidates" in r.output.lower()
    assert "Logged demo bet" not in r.output
    assert "SAMPLE" in r.output
    assert "Time (ET)" in r.output
    assert (data_dir / "clv-report.md").exists()
    assert (data_dir / "sample_session.json").exists()
    report = (data_dir / "clv-report.md").read_text(encoding="utf-8")
    assert "not FanDuel fills" in report


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
            "--pick",
            "yes",
            "--odds",
            "-110",
        ],
    )
    assert r.exit_code != 0
