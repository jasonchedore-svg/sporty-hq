"""Historical CLV backtest vs prior-season closing lines (gate 1).

Same scoreboard as the dashboard: posted American vs close American, flat = 0.
It does **not** run the scan/edge model. Every assumption and data source is
logged on the result so a passing edge is explainable.

Feed parity (owner, non-negotiable): rows must be the live path —
OpticOdds FanDuel posted + Pinnacle sharp close. Sharp-only / Odds-API-only
history cannot clear this gate.

HQ never invents closes. A repo fixture is labeled SAMPLE and cannot clear
gate 1. Pass an OpticOdds archive export with --path.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sporty_hq.archive import ArchiveAudit, audit_archive, included_rows
from sporty_hq.feed import (
    LIVE_FEED_ID,
    LIVE_POSTED_BOOK,
    LIVE_SHARP_BOOK,
    FeedParity,
    describe_live_feed,
    evaluate_feed_parity,
)
from sporty_hq.models import normalize_market
from sporty_hq.odds_math import clv_pct, parse_american
from sporty_hq.reports import ModelHealth
from sporty_hq.storage import utcnow

MIN_SEASONS = 1
MAX_SEASONS = 3


@dataclass
class HistoricalClose:
    season: str
    event_id: str
    sport: str
    event: str
    market: str
    selection: str
    posted_odds: int
    close_odds: int
    posted_feed: str = ""
    posted_book: str = ""
    close_book: str = LIVE_SHARP_BOOK
    close_feed: str = ""
    posted_at: str = ""
    close_at: str = ""
    commence_at: str = ""
    close_kind: str = ""
    excluded: bool = False
    exclude_reason: str = ""


@dataclass
class BacktestResult:
    cleared: bool
    health: str
    avg_clv: float | None
    n: int
    seasons: list[str]
    min_n: int
    source: str
    as_of: str
    note: str = ""
    per_season: dict[str, dict[str, Any]] = field(default_factory=dict)
    feed_parity: bool = False
    feed_id: str = ""
    feed_detail: str = ""
    live_feed_id: str = LIVE_FEED_ID
    archive_audit_passed: bool = False
    archive_audit_detail: str = ""
    sample: bool = False
    assumptions: list[dict[str, str]] = field(default_factory=list)
    data_sources: list[dict[str, str]] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> BacktestResult:
        feed_parity = bool(data.get("feed_parity"))
        archive_ok = bool(data.get("archive_audit_passed"))
        sample = bool(data.get("sample"))
        cleared = bool(data.get("cleared")) and feed_parity and archive_ok and not sample
        return cls(
            cleared=cleared,
            health=str(data.get("health") or ModelHealth.INSUFFICIENT_SAMPLE.value),
            avg_clv=data.get("avg_clv"),
            n=int(data.get("n") or 0),
            seasons=list(data.get("seasons") or []),
            min_n=int(data.get("min_n") or 0),
            source=str(data.get("source") or ""),
            as_of=str(data.get("as_of") or ""),
            note=str(data.get("note") or ""),
            per_season=dict(data.get("per_season") or {}),
            feed_parity=feed_parity,
            feed_id=str(data.get("feed_id") or ""),
            feed_detail=str(data.get("feed_detail") or ""),
            live_feed_id=str(data.get("live_feed_id") or LIVE_FEED_ID),
            archive_audit_passed=archive_ok,
            archive_audit_detail=str(data.get("archive_audit_detail") or ""),
            sample=sample,
            assumptions=list(data.get("assumptions") or []),
            data_sources=list(data.get("data_sources") or []),
        )


def result_path(data_dir: Path) -> Path:
    return Path(data_dir) / "backtest.json"


def load_result(data_dir: Path) -> BacktestResult | None:
    path = result_path(data_dir)
    if not path.exists():
        return None
    return BacktestResult.from_json_dict(json.loads(path.read_text(encoding="utf-8")))


def save_result(data_dir: Path, result: BacktestResult) -> Path:
    path = result_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_json_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def is_sample_source(source: str) -> bool:
    text = (source or "").replace("\\", "/").lower()
    return "fixtures/" in text or text.startswith("fixture") or "sample" in text


def assumption_log(*, seasons: int, min_n: int, sample: bool) -> list[dict[str, str]]:
    """Every scoring assumption. Do not hide filters or vig handling."""
    return [
        {
            "id": "scoreboard",
            "statement": (
                "Scoreboard is average CLV, same math as the paper dashboard. "
                "The scan/edge model is not used and cannot clear this gate."
            ),
        },
        {
            "id": "clv_formula",
            "statement": (
                "CLV% = 0 if posted American equals close American; else "
                "(decimal(posted) / decimal(close) - 1) * 100. Flat close = 0. "
                "Positive = beat the close."
            ),
        },
        {
            "id": "vig",
            "statement": (
                "No de-vig on CLV. Posted and close are converted American→decimal "
                "as-is. Scan juice-removal / Pinnacle multiplicative de-vig is not applied here."
            ),
        },
        {
            "id": "feed_parity",
            "statement": (
                f"Live path is {LIVE_FEED_ID}: posted_feed=opticodds, posted_book=fanduel, "
                "close_book=pinnacle. Pinnacle-only or Odds API history cannot clear. "
                f"{describe_live_feed()}"
            ),
        },
        {
            "id": "close_definition",
            "statement": (
                "Close must be close_kind=true_close (official/pregame close), not last_seen "
                "or a stale snapshot. Pinnacle on the OpticOdds archive is the close/benchmark; "
                "FanDuel on OpticOdds is the posted/take."
            ),
        },
        {
            "id": "lookback",
            "statement": (
                f"Lookback is the latest {seasons} season label(s) present in the file "
                f"(allowed 1–3). Gate 1 min_n={min_n} (not the kill-switch n≥100)."
            ),
        },
        {
            "id": "filters",
            "statement": (
                "Rows with excluded=true are dropped only when exclude_reason is documented "
                "(≥20 chars). last_seen / unknown close_kind never score. Missing feed identity "
                "fails the whole run (no silent drop)."
            ),
        },
        {
            "id": "archive_audit",
            "statement": (
                "Owner flagged the OpticOdds archive audit CLEARED on 2026-09-08. "
                "Each backtest still re-runs file checks (coverage, timestamps, true close). "
                "A failed file audit means this result is not valid."
            ),
        },
        {
            "id": "sample",
            "statement": (
                "Repo fixtures are SAMPLE schema only and cannot clear gate 1. "
                "HQ will not invent OpticOdds closes if a key or archive export is missing."
                if sample
                else "Source is an operator-supplied archive export, not a repo fixture."
            ),
        },
        {
            "id": "caveat",
            "statement": (
                "CAVEAT: a passing historical backtest only shows would-have-beaten-the-close "
                "on that prior-season sample. Backtest ≠ will work again. Gate 2 "
                "(current-season paper ~2–3 weeks, avg CLV > 0) is required before any "
                "live consideration. Kill switch is unchanged."
            ),
        },
        {
            "id": "no_live",
            "statement": (
                "This command does not place bets, does not unlock live logging, and does not "
                "bypass the kill switch. Live stays locked until gate 1 (this file, non-sample) "
                "and gate 2 (~2–3 week paper) both clear."
            ),
        },
    ]


def data_source_log(
    *,
    source: str,
    sample: bool,
    parity: FeedParity,
    audit: ArchiveAudit,
    n_loaded: int,
    n_scored: int,
) -> list[dict[str, str]]:
    return [
        {
            "id": "archive_file",
            "statement": f"{'SAMPLE fixture' if sample else 'Archive export'}: {source or '(none)'}",
        },
        {
            "id": "posted_feed",
            "statement": (
                f"Posted/take feed+book: {list(parity.posted_feeds)} / {list(parity.posted_books)}. "
                "Required: opticodds / fanduel."
            ),
        },
        {
            "id": "close_feed",
            "statement": (
                f"Close/benchmark: {list(parity.close_feeds)} / {list(parity.close_books)}. "
                "Required: opticodds stream + pinnacle close."
            ),
        },
        {
            "id": "odds_api",
            "statement": "The Odds API REST stub is not a data source for this backtest.",
        },
        {
            "id": "row_counts",
            "statement": (
                f"Loaded {n_loaded} lookback rows; scored {n_scored} after exclusions "
                f"(audit included={audit.n_included}, excluded={audit.n_excluded})."
            ),
        },
        {
            "id": "scan_engine",
            "statement": "engine.score_quotes / consensus EV is not a data source for CLV.",
        },
    ]


def load_historical_closes(path: Path) -> list[HistoricalClose]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _from_csv(path)
    if suffix in {".json", ".jsonl"}:
        return _from_json(path)
    raise ValueError(f"Unsupported historical close file {suffix} (use .csv or .json)")


def run_backtest(
    rows: list[HistoricalClose],
    *,
    seasons: int = 1,
    min_n: int = 30,
    source: str = "",
    target_book: str = LIVE_POSTED_BOOK,
    sharp_book: str = LIVE_SHARP_BOOK,
    audit: ArchiveAudit | None = None,
    documented_gaps: list | None = None,
    sample: bool | None = None,
) -> BacktestResult:
    """Avg CLV > 0 on the latest ``seasons`` (1–3) clears gate 1 only with parity+audit."""
    if seasons < MIN_SEASONS or seasons > MAX_SEASONS:
        raise ValueError(f"seasons must be {MIN_SEASONS}–{MAX_SEASONS}")
    sample = is_sample_source(source) if sample is None else sample
    by_season: dict[str, list[HistoricalClose]] = {}
    for row in rows:
        by_season.setdefault(row.season, []).append(row)
    chosen = sorted(by_season.keys())[-seasons:]
    selected = [r for s in chosen for r in by_season[s]]
    if audit is None:
        audit = audit_archive(
            selected, seasons_requested=seasons, documented_gaps=documented_gaps
        )
    scored = included_rows(selected, audit)
    parity: FeedParity = evaluate_feed_parity(
        scored if scored else selected,
        target_book=target_book,
        sharp_book=sharp_book,
    )
    clvs = [clv_pct(r.posted_odds, r.close_odds) for r in scored]
    n = len(clvs)
    avg = round(sum(clvs) / n, 2) if n else None
    if n < min_n:
        health = ModelHealth.INSUFFICIENT_SAMPLE.value
        clv_ok = False
        clv_note = f"Backtest n={n} below min_n={min_n}."
    elif avg is None or avg <= 0:
        health = ModelHealth.FAILING.value
        clv_ok = False
        clv_note = f"Backtest avg CLV {avg} ≤ 0 on n={n} — would not have beaten the close."
    else:
        health = ModelHealth.PASSING.value
        clv_ok = True
        clv_note = (
            f"Backtest avg CLV {avg:+.2f} on n={n} across seasons {chosen}. "
            "CAVEAT: backtest ≠ will work again — paper confirm still required."
        )
    blockers: list[str] = []
    if not audit.passed:
        blockers.append(audit.detail)
    if not parity.matched:
        blockers.append(parity.detail)
    if sample:
        blockers.append("SAMPLE source — cannot clear gate 1 (no vanity fixture pass).")
        health = ModelHealth.INSUFFICIENT_SAMPLE.value
    if not clv_ok:
        blockers.append(clv_note)
    cleared = bool(clv_ok and parity.matched and audit.passed and not sample)
    note = clv_note if cleared else " ".join(blockers) or clv_note
    per_season: dict[str, dict[str, Any]] = {}
    for season in chosen:
        season_rows = [r for r in scored if r.season == season]
        season_clvs = [clv_pct(r.posted_odds, r.close_odds) for r in season_rows]
        s_avg = round(sum(season_clvs) / len(season_clvs), 2) if season_clvs else None
        per_season[season] = {"n": len(season_clvs), "avg_clv": s_avg}
    return BacktestResult(
        cleared=cleared,
        health=health,
        avg_clv=avg,
        n=n,
        seasons=chosen,
        min_n=min_n,
        source=source,
        as_of=utcnow().isoformat(),
        note=note,
        per_season=per_season,
        feed_parity=parity.matched,
        feed_id=parity.feed_id,
        feed_detail=parity.detail,
        live_feed_id=LIVE_FEED_ID,
        archive_audit_passed=audit.passed,
        archive_audit_detail=audit.detail,
        sample=sample,
        assumptions=assumption_log(seasons=seasons, min_n=min_n, sample=sample),
        data_sources=data_source_log(
            source=source,
            sample=sample,
            parity=parity,
            audit=audit,
            n_loaded=len(selected),
            n_scored=n,
        ),
    )


def _from_csv(path: Path) -> list[HistoricalClose]:
    rows: list[HistoricalClose] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV {path} has no header")
        required = {
            "season",
            "event_id",
            "sport",
            "event",
            "market",
            "selection",
            "posted_odds",
            "close_odds",
        }
        missing = required - {h.strip() for h in reader.fieldnames}
        if missing:
            raise ValueError(f"Historical close CSV missing columns: {sorted(missing)}")
        for raw in reader:
            if not any((v or "").strip() for v in raw.values()):
                continue
            rows.append(_row(raw))
    return rows


def _from_json(path: Path) -> list[HistoricalClose]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("closes", payload) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError("JSON historical closes must be a list or {closes: [...]}")
    return [_row(item) for item in items]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _row(raw: dict[str, Any]) -> HistoricalClose:
    posted_feed = str(raw.get("posted_feed") or raw.get("feed") or "").strip()
    posted_book = str(raw.get("posted_book") or "").strip()
    close_book = str(raw.get("close_book") or LIVE_SHARP_BOOK).strip()
    close_feed = str(raw.get("close_feed") or "").strip()
    return HistoricalClose(
        season=str(raw.get("season") or "").strip(),
        event_id=str(raw.get("event_id") or "").strip(),
        sport=str(raw.get("sport") or "").strip(),
        event=str(raw.get("event") or raw.get("event_name") or "").strip(),
        market=normalize_market(str(raw.get("market") or "ml")),
        selection=str(raw.get("selection") or raw.get("pick") or "").strip(),
        posted_odds=parse_american(raw.get("posted_odds", raw.get("odds_at_bet"))),
        close_odds=parse_american(raw.get("close_odds")),
        posted_feed=posted_feed.lower(),
        posted_book=posted_book.lower(),
        close_book=close_book.lower() or LIVE_SHARP_BOOK,
        close_feed=close_feed.lower(),
        posted_at=str(raw.get("posted_at") or "").strip(),
        close_at=str(raw.get("close_at") or "").strip(),
        commence_at=str(raw.get("commence_at") or "").strip(),
        close_kind=str(raw.get("close_kind") or "").strip().lower(),
        excluded=_truthy(raw.get("excluded")),
        exclude_reason=str(raw.get("exclude_reason") or "").strip(),
    )
