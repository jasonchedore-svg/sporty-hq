"""Fan-out alerts with SQLite dedup. Never places bets."""

from __future__ import annotations

from sporty_hq.alerts.notifiers import (
    ConsoleNotifier,
    FileNotifier,
    Notifier,
    SlackNotifier,
    WebhookNotifier,
)
from sporty_hq.config import Settings
from sporty_hq.models import Alert, AlertType, Candidate
from sporty_hq.storage import Store


class AlertBus:
    def __init__(self, store: Store, notifiers: list[Notifier]) -> None:
        self.store = store
        self.notifiers = notifiers

    @classmethod
    def from_settings(cls, store: Store, settings: Settings, *, console: bool = True) -> AlertBus:
        """v1: console + file, plus generic webhook if configured.

        Slack incoming webhook is opt-in later (``enable_slack``). No OAuth.
        """
        notifiers: list[Notifier] = []
        if console:
            notifiers.append(ConsoleNotifier())
        notifiers.append(FileNotifier(settings.alerts_log_path))
        if settings.secret_configured("webhook_url"):
            assert settings.webhook_url is not None
            notifiers.append(WebhookNotifier(settings.webhook_url.get_secret_value()))
        if settings.enable_slack and settings.secret_configured("slack_webhook_url"):
            assert settings.slack_webhook_url is not None
            notifiers.append(SlackNotifier(settings.slack_webhook_url.get_secret_value()))
        return cls(store, notifiers)

    def publish(self, alert: Alert) -> bool:
        """Send if dedup key is new. Returns whether it was sent."""
        payload = {"title": alert.title, "type": alert.type.value, **alert.payload}
        if not self.store.claim_alert(alert.dedup_key, alert.type.value, payload):
            return False
        errors: list[str] = []
        for notifier in self.notifiers:
            try:
                notifier.send(alert)
            except Exception as exc:  # noqa: BLE001 - keep other channels alive
                errors.append(f"{notifier.name}: {exc}")
        if errors:
            raise RuntimeError("Some notifiers failed: " + "; ".join(errors))
        return True

    def new_candidates(self, candidates: list[Candidate]) -> int:
        sent = 0
        for cand in candidates:
            line = "" if cand.point is None else f" {cand.point}"
            alert = Alert(
                type=AlertType.NEW_CANDIDATE,
                title=f"Candidate {cand.edge_pct:.1f}%: {cand.event_name}",
                body=(
                    f"{cand.selection}{line} {cand.market.upper()} @ {cand.american_odds:+d} "
                    f"({cand.book}) edge {cand.edge_pct:.1f}%.\n{cand.rationale}\n"
                    "Place on FanDuel mobile yourself if you take it — HQ does not bet."
                ),
                dedup_key=(
                    f"new_candidate:{cand.event_id}:{cand.market}:{cand.selection.lower()}:"
                    f"{cand.point}:{cand.american_odds}"
                ),
                payload={
                    "event_id": cand.event_id,
                    "market": cand.market,
                    "selection": cand.selection,
                    "odds": cand.american_odds,
                    "edge_pct": cand.edge_pct,
                },
            )
            if self.publish(alert):
                sent += 1
        return sent
