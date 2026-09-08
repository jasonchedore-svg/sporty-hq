"""OpticOdds archive audit: coverage, timestamps, true close vs last-seen."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sporty_hq.archive import (
    audit_archive,
    render_audit_markdown,
)
from tests.test_backtest import _covered_season, _row


def test_audit_passes_on_true_close_coverage() -> None:
    rows = _covered_season()
    audit = audit_archive(rows, seasons_requested=1)
    assert audit.passed is True
    md = render_audit_markdown(audit)
    assert "PASSED" in md
    assert "STOP FOR HUMAN REVIEW" not in md
    assert "## Coverage gaps" in md
    assert "## Timestamps" in md
    assert "## Close quality" in md


def test_last_seen_fails_markdown_and_stops() -> None:
    rows = _covered_season()
    for r in rows:
        r.close_kind = "last_seen"
    audit = audit_archive(rows, seasons_requested=1)
    assert audit.passed is False
    md = render_audit_markdown(audit)
    assert "STOP FOR HUMAN REVIEW" in md
    assert "last_seen" in md
    assert "Do **not** run a vanity backtest" in md


def test_missing_timestamps_fail_audit() -> None:
    rows = _covered_season()
    for r in rows:
        r.posted_at = ""
        r.close_at = ""
        r.commence_at = ""
    audit = audit_archive(rows, seasons_requested=1)
    assert audit.passed is False
    assert audit.timestamp_issues
    md = render_audit_markdown(audit)
    assert "STOP FOR HUMAN REVIEW" in md
    assert "missing timestamp" in md.lower() or "Timestamps" in md


def test_stale_close_fails_audit() -> None:
    rows = _covered_season()
    commence = datetime(2025, 9, 1, 23, 0, tzinfo=timezone.utc)
    for i, r in enumerate(rows):
        tip = commence + timedelta(days=i)
        r.commence_at = tip.isoformat().replace("+00:00", "Z")
        r.close_at = (tip - timedelta(hours=20)).isoformat().replace("+00:00", "Z")
        r.posted_at = (tip - timedelta(hours=21)).isoformat().replace("+00:00", "Z")
    audit = audit_archive(rows, seasons_requested=1)
    assert audit.passed is False
    assert audit.timestamp_issues


def test_thin_market_coverage_fails_audit() -> None:
    rows = [_row(i=i, market="ml") for i in range(5)]
    audit = audit_archive(rows, seasons_requested=1)
    assert audit.passed is False
    assert any("missing markets" in g for g in audit.undocumented_gaps)
    md = render_audit_markdown(audit)
    assert "STOP FOR HUMAN REVIEW" in md
    assert "Coverage" in md
