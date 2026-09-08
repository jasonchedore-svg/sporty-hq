import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from typer.testing import CliRunner

from sporty_hq.cli import app
from sporty_hq.models import Bet, Candidate
from sporty_hq.storage import Store
from tests.conftest import DEMO_CSV, DEMO_JSON

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


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
    assert "Logged paper ticket" in r.output or "Logged" in r.output
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
    assert "Pre-game alerts skipped" in r.output or "Reminders sent: 1" in r.output
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


def test_brief_help_and_fixture_json(data_dir: Path) -> None:
    r = runner.invoke(app, ["brief", "--help"])
    assert r.exit_code == 0, r.output
    help_text = " ".join(_plain(r.output).lower().split())
    assert "does not place" in help_text and "bets" in help_text
    assert "--pack" in help_text
    assert "--closes" in help_text

    out = data_dir / "brief.json"
    r = runner.invoke(
        app,
        [
            "brief",
            "--source",
            "fixture",
            "--path",
            str(DEMO_JSON),
            "--pack",
            "--format",
            "json",
            "--out",
            str(out),
            "--data-dir",
            str(data_dir),
        ],
    )
    assert r.exit_code == 0, r.output
    assert "does not place bets" in r.output.lower()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["kind"] == "odds_arcade_brief"
    assert payload["line_move_of_the_day"]["example"] is True
    assert payload["juice_explainer"]["example"] is False
    assert 2 <= len(payload["close_challenge_pack"]) <= 3
    assert (data_dir / "close_challenge_pack.json").exists()

    r = runner.invoke(
        app,
        [
            "brief",
            "--source",
            "fixture",
            "--path",
            str(DEMO_JSON),
            "--closes",
            "--format",
            "md",
            "--data-dir",
            str(data_dir),
        ],
    )
    assert r.exit_code == 0, r.output
    assert "Close-challenge results" in r.output
    assert "beat close" in r.output.lower()


def test_log_bet_live_trips_kill_switch_before_gates(data_dir: Path) -> None:
    r = runner.invoke(
        app,
        [
            "log-bet",
            "--live",
            "--event-id",
            "x",
            "--event",
            "x",
            "--market",
            "ml",
            "--pick",
            "Home",
            "--odds",
            "-110",
            "--data-dir",
            str(data_dir),
        ],
    )
    assert r.exit_code == 1, r.output
    assert "KILL SWITCH" in r.output or "acted" in r.output.lower()


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


def test_ingest_stream_replay_and_degrade(data_dir: Path) -> None:
    r = runner.invoke(
        app,
        [
            "ingest",
            "--source",
            "stream",
            "--replay",
            str(DEMO_JSON),
            "--data-dir",
            str(data_dir),
        ],
    )
    assert r.exit_code == 0, r.output
    assert "replay" in r.output.lower()

    r = runner.invoke(app, ["ingest", "--source", "stream", "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    assert "degrade" in r.output.lower()


def test_settle_requires_close_odds_and_loss_postmortem(data_dir: Path) -> None:
    r = runner.invoke(
        app, ["ingest", "--source", "fixture", "--path", str(DEMO_JSON), "--data-dir", str(data_dir)]
    )
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["scan", "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["log-bet", "--data-dir", str(data_dir), "--candidate-id", "1"])
    assert r.exit_code == 0, r.output
    store = Store(data_dir / "sporty.db")
    bet_id = store.list_bets()[0].id

    r = runner.invoke(app, ["settle", bet_id, "--result", "win", "--data-dir", str(data_dir)])
    assert r.exit_code != 0

    r = runner.invoke(
        app,
        ["settle", bet_id, "--result", "loss", "--close-odds", "+148", "--data-dir", str(data_dir)],
    )
    assert r.exit_code == 0, r.output
    again = store.get_bet(bet_id)
    assert again is not None
    assert again.close_odds == 148
    assert again.clv_pct is not None


def test_settle_optional_postmortem_still_stores_lesson(data_dir: Path) -> None:
    r = runner.invoke(
        app, ["ingest", "--source", "fixture", "--path", str(DEMO_JSON), "--data-dir", str(data_dir)]
    )
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["scan", "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["log-bet", "--data-dir", str(data_dir), "--candidate-id", "1"])
    assert r.exit_code == 0, r.output
    store = Store(data_dir / "sporty.db")
    bet_id = store.list_bets()[0].id
    r = runner.invoke(
        app,
        [
            "settle",
            bet_id,
            "--result",
            "loss",
            "--close-odds",
            "+148",
            "--postmortem",
            "injury_missed",
            "--lesson",
            "Cole IL not in the model",
            "--data-dir",
            str(data_dir),
        ],
    )
    assert r.exit_code == 0, r.output
    again = store.get_bet(bet_id)
    assert again is not None
    assert again.postmortem == "injury_missed"
    lessons = store.list_lessons()
    assert lessons and "Cole IL" in lessons[0].lesson


def test_scan_respects_daily_stop(data_dir: Path) -> None:
    from datetime import datetime, timezone

    from sporty_hq.models import Bet

    store = Store(data_dir / "sporty.db")
    now = datetime.now(timezone.utc)
    store.insert_bet(
        Bet(
            id="stoploss",
            logged_at=now,
            event_id="dead",
            event_name="Stopped",
            sport="baseball_mlb",
            commence_at=now,
            market="ml",
            selection="X",
            odds_at_bet=-110,
            stake=25,
            result="loss",
            pnl=-100.0,
            close_odds=-110,
            clv_pct=0.0,
        )
    )
    r = runner.invoke(
        app, ["ingest", "--source", "fixture", "--path", str(DEMO_JSON), "--data-dir", str(data_dir)]
    )
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["scan", "--data-dir", str(data_dir)])
    assert r.exit_code == 0, r.output
    assert "CLV is the scoreboard" in r.output


def test_clv_report_gate_failing(data_dir: Path) -> None:
    from datetime import datetime, timezone

    from sporty_hq.models import Bet

    store = Store(data_dir / "sporty.db")
    now = datetime.now(timezone.utc)
    for i in range(100):
        store.insert_bet(
            Bet(
                id=f"g{i:04d}",
                logged_at=now,
                event_id=f"e{i}",
                event_name="NYY @ BOS",
                sport="baseball_mlb",
                commence_at=now,
                market="ml",
                selection="Yankees",
                odds_at_bet=165,
                stake=25,
                result="loss",
                pnl=-25,
                close_odds=165,
                clv_pct=0.0,
            )
        )
    r = runner.invoke(app, ["clv-report", "--format", "json", "--gate", "--data-dir", str(data_dir)])
    assert r.exit_code == 1
    assert "FAILING" in r.output
