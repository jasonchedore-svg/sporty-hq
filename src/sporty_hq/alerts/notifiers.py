"""Alert notifiers: console, JSONL file, generic webhook, Slack incoming webhook."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import httpx
from rich.console import Console

from sporty_hq.models import Alert


class Notifier(Protocol):
    name: str

    def send(self, alert: Alert) -> None:
        ...


class ConsoleNotifier:
    name = "console"

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def send(self, alert: Alert) -> None:
        self.console.print(f"[bold cyan][{alert.type.value}][/bold cyan] {alert.title}")
        self.console.print(alert.body)


class FileNotifier:
    name = "file"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def send(self, alert: Alert) -> None:
        record = {
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "type": alert.type.value,
            "title": alert.title,
            "body": alert.body,
            "dedup_key": alert.dedup_key,
            "payload": alert.payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")


class WebhookNotifier:
    """JSON POST. Point at IFTTT Webhooks, a Twilio Function, Zapier, or n8n for SMS."""

    name = "webhook"

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        self.url = url
        self.timeout = timeout

    def send(self, alert: Alert) -> None:
        payload = {
            "type": alert.type.value,
            "title": alert.title,
            "body": alert.body,
            "dedup_key": alert.dedup_key,
            "payload": alert.payload,
        }
        response = httpx.post(self.url, json=payload, timeout=self.timeout)
        response.raise_for_status()


class SlackNotifier:
    """Slack incoming webhook. Uses ``text`` so it works without bot tokens."""

    name = "slack"

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        self.url = url
        self.timeout = timeout

    def send(self, alert: Alert) -> None:
        text = f"*{alert.title}*\n{alert.body}"
        response = httpx.post(self.url, json={"text": text}, timeout=self.timeout)
        response.raise_for_status()
