"""OpticOdds historical archive audit — hard prerequisite before any backtest.

Owner (2026-09-08): the OpticOdds archive audit is CLEARED. That standing flag
does not skip per-file checks. Every `sporty backtest` must pass this audit
first. Thin coverage, junk timestamps, or last-seen quotes: emit a report and
**stop** — do not score a vanity CLV that looks cleaner than the archive.

Checks:
- coverage gaps (seasons / sport families / straight markets)
- missing or stale timestamps (posted_at, close_at, commence_at)
- true close vs last-seen quote

Gaps may be excluded only when documented (row exclude_reason or gaps JSON).
Feed parity (OpticOdds posted + Pinnacle close) is a separate required gate.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from sporty_hq.sports import family
from sporty_hq.storage import utcnow

OWNER_ARCHIVE_STATUS = "CLEARED"
OWNER_ARCHIVE_CLEARED_AT = "2026-09-08"

TRUE_CLOSE = frozenset({"true_close", "true-close", "official_close", "official-close", "close"})
LAST_SEEN = frozenset({"last_seen", "last-seen", "last_quote", "last-quote", "snapshot"})
EXPECTED_MARKETS = ("ml", "spread", "total")
EXCLUDE_REASON_MIN = 20
# Hypothesis: a pregame true close lands near scheduled start, not a quote from
# the previous afternoon. Flag closes older than 12h before tip or >30m after.
STALE_BEFORE_HOURS = 12.0
STALE_AFTER_MINUTES = 30.0


@dataclass
class ArchiveGap:
    season: str = ""
    sport: str = ""
    market: str = ""
    event_id: str = ""
    kind: str = "coverage"
    reason: str = ""

    def documented(self) -> bool:
        return len(self.reason.strip()) >= EXCLUDE_REASON_MIN


@dataclass
class ArchiveAudit:
    passed: bool
    owner_status: str
    n_rows: int
    n_included: int
    n_excluded: int
    seasons: list[str]
    coverage_gaps: list[str] = field(default_factory=list)
    timestamp_issues: list[str] = field(default_factory=list)
    close_quality_issues: list[str] = field(default_factory=list)
    undocumented_gaps: list[str] = field(default_factory=list)
    excluded: list[dict[str, Any]] = field(default_factory=list)
    detail: str = ""
    as_of: str = ""

    def to_json_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["owner_cleared_at"] = OWNER_ARCHIVE_CLEARED_AT
        return payload

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> ArchiveAudit:
        return cls(
            passed=bool(data.get("passed")),
            owner_status=str(data.get("owner_status") or ""),
            n_rows=int(data.get("n_rows") or 0),
            n_included=int(data.get("n_included") or 0),
            n_excluded=int(data.get("n_excluded") or 0),
            seasons=list(data.get("seasons") or []),
            coverage_gaps=list(data.get("coverage_gaps") or []),
            timestamp_issues=list(data.get("timestamp_issues") or []),
            close_quality_issues=list(data.get("close_quality_issues") or []),
            undocumented_gaps=list(data.get("undocumented_gaps") or []),
            excluded=list(data.get("excluded") or []),
            detail=str(data.get("detail") or ""),
            as_of=str(data.get("as_of") or ""),
        )


def audit_path(data_dir: Path) -> Path:
    return Path(data_dir) / "archive_audit.json"


def save_audit(data_dir: Path, audit: ArchiveAudit) -> Path:
    path = audit_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(audit.to_json_dict(), indent=2) + "\n", encoding="utf-8")
    md_path = Path(data_dir) / "archive_audit.md"
    md_path.write_text(render_audit_markdown(audit), encoding="utf-8")
    return path


def render_audit_markdown(audit: ArchiveAudit) -> str:
    """Human-review report. Failed audits must not be followed by a vanity CLV print."""
    status = "PASSED" if audit.passed else "FAILED — STOP FOR HUMAN REVIEW"
    lines = [
        "# OpticOdds historical archive audit",
        "",
        f"**{status}**",
        "",
        f"- Owner standing flag: **{audit.owner_status}** ({OWNER_ARCHIVE_CLEARED_AT})",
        f"- Rows in lookback: **{audit.n_rows}** (included {audit.n_included}, excluded {audit.n_excluded})",
        f"- Seasons: **{', '.join(audit.seasons) or '—'}**",
        f"- As of: {audit.as_of or '—'}",
        "",
        audit.detail,
        "",
        "## Coverage gaps (events / markets / seasons)",
        "",
    ]
    coverage = audit.coverage_gaps + audit.undocumented_gaps
    if coverage:
        lines.extend(f"- {item}" for item in coverage)
    else:
        lines.append("- None flagged.")
    lines.extend(["", "## Timestamps (stale or missing)", ""])
    if audit.timestamp_issues:
        lines.extend(f"- {item}" for item in audit.timestamp_issues)
    else:
        lines.append("- None flagged.")
    lines.extend(["", "## Close quality (true close vs last-seen)", ""])
    if audit.close_quality_issues:
        lines.extend(f"- {item}" for item in audit.close_quality_issues)
    else:
        lines.append("- None flagged.")
    if not audit.passed:
        lines.extend(
            [
                "",
                "## Stop",
                "",
                "Do **not** run a vanity backtest on this file. Coverage is thin, timestamps "
                "are junk, and/or closes are last-seen quotes. Fix the archive or document "
                "gaps in a `--gaps` JSON (reason ≥20 chars), then re-run `sporty archive-audit`.",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def load_audit(data_dir: Path) -> ArchiveAudit | None:
    path = audit_path(data_dir)
    if not path.exists():
        return None
    return ArchiveAudit.from_json_dict(json.loads(path.read_text(encoding="utf-8")))


def load_documented_gaps(path: Path | None) -> list[ArchiveGap]:
    if path is None:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    items = payload.get("gaps", payload) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError("Archive gaps file must be a list or {gaps: [...]}")
    gaps: list[ArchiveGap] = []
    for raw in items:
        gap = ArchiveGap(
            season=str(raw.get("season") or "").strip(),
            sport=str(raw.get("sport") or "").strip(),
            market=str(raw.get("market") or "").strip(),
            event_id=str(raw.get("event_id") or "").strip(),
            kind=str(raw.get("kind") or "coverage").strip() or "coverage",
            reason=str(raw.get("reason") or "").strip(),
        )
        gaps.append(gap)
    return gaps


def audit_archive(
    rows: Iterable[Any],
    *,
    seasons_requested: int = 1,
    documented_gaps: list[ArchiveGap] | None = None,
    stale_before_hours: float = STALE_BEFORE_HOURS,
    stale_after_minutes: float = STALE_AFTER_MINUTES,
) -> ArchiveAudit:
    items = list(rows)
    docs = [g for g in (documented_gaps or []) if g.documented()]
    seasons = sorted({str(getattr(r, "season", "") or "") for r in items if getattr(r, "season", "")})
    chosen = seasons[-seasons_requested:] if seasons else []
    selected = [r for r in items if str(getattr(r, "season", "") or "") in chosen]

    coverage: list[str] = []
    timestamps: list[str] = []
    quality: list[str] = []
    undocumented: list[str] = []
    excluded_rows: list[dict[str, Any]] = []

    if len(chosen) < seasons_requested:
        msg = (
            f"Coverage: requested {seasons_requested} season(s), archive has "
            f"{len(chosen)} ({chosen or 'none'})."
        )
        if any(g.kind == "coverage" for g in docs):
            coverage.append(msg + " Documented.")
        else:
            undocumented.append(msg)

    included: list[Any] = []
    for row in selected:
        event_id = str(getattr(row, "event_id", "") or "")
        if _row_excluded(row):
            reason = str(getattr(row, "exclude_reason", "") or "").strip()
            if len(reason) < EXCLUDE_REASON_MIN:
                undocumented.append(
                    f"{event_id or '?'}: excluded without a documented reason "
                    f"(≥{EXCLUDE_REASON_MIN} chars)."
                )
            excluded_rows.append(
                {
                    "event_id": event_id,
                    "season": getattr(row, "season", ""),
                    "reason": reason,
                }
            )
            continue
        included.append(row)

    by_season_sport: dict[tuple[str, str], set[str]] = {}
    for row in included:
        season = str(getattr(row, "season", "") or "")
        fam = family(str(getattr(row, "sport", "") or ""))
        market = str(getattr(row, "market", "") or "")
        if fam and fam != "skip":
            by_season_sport.setdefault((season, fam), set()).add(market)
        if not str(getattr(row, "event_id", "") or "").strip():
            coverage.append("Row missing event_id.")
        kind = _close_kind(row)
        if kind == "last_seen":
            quality.append(
                f"{event_id_of(row)}: close_kind=last_seen — not a true close. "
                "Exclude with a documented reason or replace with official close."
            )
        elif kind != "true_close":
            quality.append(
                f"{event_id_of(row)}: close_kind={kind or 'unknown'} — "
                "archive must label true_close vs last_seen."
            )
        timestamps.extend(_timestamp_issues(row, stale_before_hours, stale_after_minutes))

    for (season, fam), markets in sorted(by_season_sport.items()):
        missing = [m for m in EXPECTED_MARKETS if m not in markets]
        if not missing:
            continue
        msg = f"Coverage: season {season} {fam} missing markets {missing}."
        if all(
            _gap_documented(docs, kind="coverage", season=season, sport=fam, market=m)
            for m in missing
        ):
            coverage.append(msg + " Documented and excluded from scoring.")
        else:
            undocumented.append(msg)

    if not included:
        undocumented.append("No included archive rows after exclusions — nothing to backtest.")

    passed = not undocumented and not quality and not timestamps and bool(included)
    if passed:
        detail = (
            f"Archive audit PASSED ({OWNER_ARCHIVE_STATUS} {OWNER_ARCHIVE_CLEARED_AT}). "
            f"Included n={len(included)} across seasons {chosen}; closes are true_close."
        )
    else:
        detail = (
            f"Archive audit FAILED — backtest results are not valid. "
            f"{len(undocumented)} undocumented gap(s), {len(quality)} close-quality, "
            f"{len(timestamps)} timestamp issue(s)."
        )
    return ArchiveAudit(
        passed=passed,
        owner_status=OWNER_ARCHIVE_STATUS,
        n_rows=len(selected),
        n_included=len(included),
        n_excluded=len(excluded_rows),
        seasons=chosen,
        coverage_gaps=coverage,
        timestamp_issues=timestamps[:50],
        close_quality_issues=quality[:50],
        undocumented_gaps=undocumented[:50],
        excluded=excluded_rows[:50],
        detail=detail,
        as_of=utcnow().isoformat(),
    )


def included_rows(rows: Iterable[Any], audit: ArchiveAudit | None = None) -> list[Any]:
    """Rows that may be scored. Excluded / last-seen tickets stay out."""
    out = []
    for row in rows:
        if _row_excluded(row):
            continue
        if _close_kind(row) != "true_close":
            continue
        out.append(row)
    if audit is not None and not audit.passed:
        return []
    return out


def event_id_of(row: Any) -> str:
    return str(getattr(row, "event_id", "") or "") or "?"


def _row_excluded(row: Any) -> bool:
    value = getattr(row, "excluded", False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _close_kind(row: Any) -> str:
    raw = str(getattr(row, "close_kind", "") or "").strip().lower().replace(" ", "_")
    if raw in TRUE_CLOSE:
        return "true_close"
    if raw in LAST_SEEN:
        return "last_seen"
    return raw or "unknown"


def _parse_ts(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _timestamp_issues(row: Any, stale_before_hours: float, stale_after_minutes: float) -> list[str]:
    eid = event_id_of(row)
    posted = _parse_ts(getattr(row, "posted_at", None))
    close = _parse_ts(getattr(row, "close_at", None))
    commence = _parse_ts(getattr(row, "commence_at", None))
    issues: list[str] = []
    if posted is None or close is None or commence is None:
        issues.append(
            f"{eid}: missing timestamp(s) posted_at/close_at/commence_at "
            "(cannot tell true close from last-seen)."
        )
        return issues
    if posted > commence:
        issues.append(f"{eid}: posted_at is after commence — not a takeable pregame number.")
    if close < posted:
        issues.append(f"{eid}: close_at is before posted_at.")
    if close + timedelta(hours=stale_before_hours) < commence:
        issues.append(
            f"{eid}: close_at is >{stale_before_hours:g}h before commence "
            "(looks like last-seen, not a true close)."
        )
    if close > commence + timedelta(minutes=stale_after_minutes):
        issues.append(
            f"{eid}: close_at is >{stale_after_minutes:g}m after commence "
            "(not a pregame close)."
        )
    return issues


def _gap_documented(
    docs: list[ArchiveGap],
    *,
    kind: str,
    season: str,
    sport: str,
    market: str,
) -> bool:
    fam = family(sport) if sport else ""
    for gap in docs:
        if gap.kind and gap.kind != kind:
            continue
        if gap.season and season and gap.season != season:
            continue
        if gap.sport and sport:
            if family(gap.sport) not in {fam, gap.sport} and gap.sport != sport:
                continue
        if gap.market and market and gap.market != market:
            continue
        return True
    return False
