"""Shared fixtures for Sporty HQ tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sporty_hq.config import Settings
from sporty_hq.models import Quote
from sporty_hq.storage import Store

REPO = Path(__file__).resolve().parents[1]
DEMO_JSON = REPO / "fixtures" / "demo_odds.json"
DEMO_CSV = REPO / "fixtures" / "demo_odds.csv"
DEMO_CLOSES = REPO / "fixtures" / "demo_closes.json"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def settings(data_dir: Path) -> Settings:
    return Settings(data_dir=data_dir)


@pytest.fixture
def store(settings: Settings) -> Store:
    return Store(settings.db_path)


def make_quote(**overrides) -> Quote:
    base = dict(
        event_id="e1",
        sport="icehockey_nhl",
        commence_at=datetime.now(timezone.utc) + timedelta(hours=3),
        home_team="Toronto Maple Leafs",
        away_team="Boston Bruins",
        book="fanduel",
        market="ml",
        selection="Toronto Maple Leafs",
        american_odds=150,
        point=None,
        source="test",
    )
    base.update(overrides)
    return Quote(**base)
