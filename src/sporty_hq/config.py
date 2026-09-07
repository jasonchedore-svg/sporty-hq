"""Runtime settings. Secrets stay in env / .env — never log them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _secret_str(value: str | SecretStr | None) -> SecretStr | None:
    if value is None or value == "":
        return None
    if isinstance(value, SecretStr):
        return value
    return SecretStr(value)


class Settings(BaseSettings):
    """Playbook + I/O. Env prefix SPORTY_HQ_ except Slack / Odds API aliases."""

    model_config = SettingsConfigDict(
        env_prefix="SPORTY_HQ_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    data_dir: Path = Path("data")
    min_edge: float = 0.03
    unit_stake: float = 25.0
    session_stop: float = -100.0
    max_bets_per_session: int = 4
    target_book: str = "fanduel"
    remind_minutes: int = 45
    region: str = "on"
    timezone: str = "America/Toronto"
    webhook_url: SecretStr | None = None
    slack_webhook_url: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SLACK_WEBHOOK_URL",
            "SPORTY_HQ_SLACK_WEBHOOK_URL",
        ),
    )
    the_odds_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("THE_ODDS_API_KEY", "SPORTY_HQ_THE_ODDS_API_KEY"),
    )

    @field_validator("webhook_url", "slack_webhook_url", "the_odds_api_key", mode="before")
    @classmethod
    def _empty_secret_to_none(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        return value

    @field_validator("target_book", mode="before")
    @classmethod
    def _norm_book(cls, value: Any) -> str:
        return str(value).strip().lower() if value else "fanduel"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sporty.db"

    @property
    def alerts_log_path(self) -> Path:
        return self.data_dir / "alerts.jsonl"

    def ensure_data_dir(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir

    def secret_configured(self, name: str) -> bool:
        value = getattr(self, name)
        return value is not None and bool(value.get_secret_value())

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return (
            f"Settings(data_dir={self.data_dir!r}, min_edge={self.min_edge}, "
            f"target_book={self.target_book!r}, slack={self.secret_configured('slack_webhook_url')}, "
            f"webhook={self.secret_configured('webhook_url')}, "
            f"odds_api={self.secret_configured('the_odds_api_key')})"
        )


def load_settings(**overrides: Any) -> Settings:
    settings = Settings(**overrides)
    settings.ensure_data_dir()
    return settings
