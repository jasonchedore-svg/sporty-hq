from pathlib import Path

import httpx
import pytest

from sporty_hq.alerts.bus import AlertBus
from sporty_hq.alerts.notifiers import FileNotifier, SlackNotifier, WebhookNotifier
from sporty_hq.models import Alert, AlertType, Candidate
from sporty_hq.storage import utcnow


def _alert(key: str = "k1") -> Alert:
    return Alert(
        type=AlertType.NEW_CANDIDATE,
        title="edge",
        body="body",
        dedup_key=key,
        payload={"event_id": "e1"},
    )


def test_file_notifier_and_dedup(store, data_dir: Path) -> None:
    log = data_dir / "alerts.jsonl"
    bus = AlertBus(store, [FileNotifier(log)])
    assert bus.publish(_alert("dup")) is True
    assert bus.publish(_alert("dup")) is False
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert store.alert_was_sent("dup")


def test_webhook_and_slack(monkeypatch, store) -> None:
    posts: list[tuple[str, dict]] = []

    def fake_post(url, json, timeout):  # noqa: A002
        posts.append((url, json))

        class Resp:
            def raise_for_status(self) -> None:
                return None

        return Resp()

    monkeypatch.setattr(httpx, "post", fake_post)
    bus = AlertBus(
        store,
        [
            WebhookNotifier("https://example.test/hook"),
            SlackNotifier("https://hooks.slack.com/services/T/B/X"),
        ],
    )
    assert bus.publish(_alert("wh"))
    assert posts[0][1]["type"] == "new_candidate"
    assert "text" in posts[1][1]


def test_new_candidate_alerts(store, data_dir: Path) -> None:
    bus = AlertBus(store, [FileNotifier(data_dir / "a.jsonl")])
    cand = Candidate(
        event_id="e1",
        sport="nhl",
        event_name="BOS @ TOR",
        commence_at=utcnow(),
        market="ml",
        selection="Toronto Maple Leafs",
        book="fanduel",
        american_odds=165,
        decimal_odds=2.65,
        fair_prob=0.4,
        implied_prob=0.38,
        edge_pct=7.0,
        juice_pct=5.0,
        rationale="test",
    )
    assert bus.new_candidates([cand]) == 1
    assert bus.new_candidates([cand]) == 0


def test_webhook_error_surfaces(monkeypatch, store) -> None:
    def boom(url, json, timeout):  # noqa: A002
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(httpx, "post", boom)
    bus = AlertBus(store, [WebhookNotifier("https://example.test/hook")])
    with pytest.raises(RuntimeError, match="webhook"):
        bus.publish(_alert("err"))


def test_from_settings_v1_skips_slack_even_if_url_set(settings, store) -> None:
    from pydantic import SecretStr

    settings.slack_webhook_url = SecretStr("https://hooks.slack.com/services/T/B/X")
    settings.enable_slack = False
    settings.webhook_url = SecretStr("https://example.test/hook")
    names = [n.name for n in AlertBus.from_settings(store, settings).notifiers]
    assert names == ["console", "file", "webhook"]


def test_from_settings_v1_console_file_only(settings, store) -> None:
    names = [n.name for n in AlertBus.from_settings(store, settings).notifiers]
    assert names == ["console", "file"]
    assert "slack" not in names
