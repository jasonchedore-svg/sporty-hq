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
from sporty_hq.sports import family
from sporty_hq.storage import utcnow

MIN_SEASONS = 1
MAX_SEASONS = 3
ALLOWED_MARKETS = ("ml", "spread", "total")

# Owner-facing report must include every one of these. If an edge prints, these
# are what produced it. Do not drop an id without updating the markdown renderer.
REQUIRED_ASSUMPTION_IDS = (
    "workstream",
    "scoreboard",
    "clv_formula",
    "vig",
    "feed_parity",
    "close_definition",
    "markets",
    "seasons",
    "filters",
    "sample_size",
    "archive_audit",
    "sample",
    "paper_only",
    "caveat",
    "no_live",
)
REQUIRED_DATA_SOURCE_IDS = (
    "archive_file",
    "posted_feed",
    "close_feed",
    "seasons",
    "markets",
    "sports",
    "filters",
    "sample_size",
    "close_kind",
    "pinnacle_close",
    "coverage_cost",
    "odds_api",
    "sportsgameodds",
    "opticodds",
    "live_stream",
    "scan_engine",
    "invented_closes",
)


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
    close_book: str = ""
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
    per_market: dict[str, dict[str, Any]] = field(default_factory=dict)
    per_sport: dict[str, dict[str, Any]] = field(default_factory=dict)
    feed_parity: bool = False
    feed_id: str = ""
    feed_detail: str = ""
    live_feed_id: str = LIVE_FEED_ID
    archive_audit_passed: bool = False
    archive_audit_detail: str = ""
    sample: bool = False
    pinnacle_close: bool = False
    liabilities: list[str] = field(default_factory=list)
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
            per_market=dict(data.get("per_market") or {}),
            per_sport=dict(data.get("per_sport") or {}),
            feed_parity=feed_parity,
            feed_id=str(data.get("feed_id") or ""),
            feed_detail=str(data.get("feed_detail") or ""),
            live_feed_id=str(data.get("live_feed_id") or LIVE_FEED_ID),
            archive_audit_passed=archive_ok,
            archive_audit_detail=str(data.get("archive_audit_detail") or ""),
            sample=sample,
            pinnacle_close=bool(data.get("pinnacle_close")),
            liabilities=list(data.get("liabilities") or []),
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
    md_path = Path(data_dir) / "backtest.md"
    md_path.write_text(render_backtest_markdown(result), encoding="utf-8")
    return path


def is_sample_source(source: str) -> bool:
    text = (source or "").replace("\\", "/").lower()
    return "fixtures/" in text or text.startswith("fixture") or "sample" in text


def assumption_log(
    *,
    seasons: int,
    min_n: int,
    sample: bool,
    season_labels: list[str] | None = None,
    n_scored: int = 0,
) -> list[dict[str, str]]:
    """Every scoring assumption. Do not hide filters, vig, close definition, or sample size."""
    labels = ", ".join(season_labels) if season_labels else "(none in file)"
    return [
        {
            "id": "workstream",
            "statement": (
                "This phase's only active workstream is the historical backtest. "
                "Scan, Odds Arcade brief, human-source lessons, and live ingest are not "
                "inputs to this score and cannot produce the number below."
            ),
        },
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
                f"Payable path is {LIVE_FEED_ID}: posted_feed=oddsapi|sportsgameodds, "
                "posted_book=fanduel. Pinnacle close is used only if the provider exposes it. "
                "OpticOdds is not required and cannot clear. "
                f"{describe_live_feed()}"
            ),
        },
        {
            "id": "close_definition",
            "statement": (
                "Close must be close_kind=true_close (pregame close on this feed), not last_seen "
                "junk. Preferred close_book=pinnacle when the cheaper feed actually has Pinnacle. "
                "If it does not, CLV uses the provider close and a KNOWN LIABILITY is logged. "
                "HQ never invents Pinnacle prices."
            ),
        },
        {
            "id": "markets",
            "statement": (
                "Straights only: ml, spread, total. No props, no parlays. Markets are "
                "normalized then scored as-is. Missing expected markets fail the archive "
                "audit; they are not silently filled."
            ),
        },
        {
            "id": "seasons",
            "statement": (
                f"Lookback is the latest {seasons} season label(s) present in the file "
                f"(allowed {MIN_SEASONS}–{MAX_SEASONS}). Labels this run: {labels}."
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
            "id": "sample_size",
            "statement": (
                f"Gate 1 requires n ≥ {min_n} scored true_close rows (this run n={n_scored}). "
                "Kill-switch n≥100 is a different sample and is not used here. "
                "n < min_n cannot clear."
            ),
        },
        {
            "id": "archive_audit",
            "statement": (
                "Hard prerequisite before scoring. Each backtest re-runs file checks "
                "(coverage gaps, stale/missing timestamps, true_close vs last_seen). "
                "Failed audit: no CLV is scored (no vanity backtest). Owner standing "
                "flag CLEARED 2026-09-08 does not skip per-file checks."
            ),
        },
        {
            "id": "sample",
            "statement": (
                "Repo fixtures are SAMPLE schema only and cannot clear gate 1. "
                "HQ will not invent Odds API, SportsGameOdds, Pinnacle, or OpticOdds closes "
                "if a key or archive export is missing."
                if sample
                else "Source is an operator-supplied cheap-feed archive or live historical pull, not a repo fixture."
            ),
        },
        {
            "id": "paper_only",
            "statement": (
                "Paper trade only. Gate 1 measures cheap-feed CLV. It does not unlock live "
                "tickets. log-bet --live stays refused and still trips kill switch A."
            ),
        },
        {
            "id": "caveat",
            "statement": (
                "CAVEAT: a passing historical backtest only shows would-have-beaten-the-close "
                "on that cheaper-feed sample. Backtest ≠ will work again. Expect thinner edge "
                "and more missing history than an enterprise OpticOdds archive. Gate 2 "
                "(current-season paper ~2–3 weeks, avg CLV > 0) is still the paper confirm. "
                "Kill switch is unchanged. No live tickets."
            ),
        },
        {
            "id": "no_live",
            "statement": (
                "This command does not place bets, does not unlock live logging, and does not "
                "bypass the kill switch. This protocol is paper-only."
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
    min_n: int = 30,
    season_labels: list[str] | None = None,
    markets: list[str] | None = None,
    sports: list[str] | None = None,
    close_kinds: list[str] | None = None,
    n_excluded_rows: int = 0,
) -> list[dict[str, str]]:
    seasons = ", ".join(season_labels or []) or "(none)"
    market_list = ", ".join(markets or []) or "(none)"
    sport_list = ", ".join(sports or []) or "(none)"
    kinds = ", ".join(close_kinds or []) or "(none)"
    return [
        {
            "id": "archive_file",
            "statement": f"{'SAMPLE fixture' if sample else 'Archive export'}: {source or '(none)'}",
        },
        {
            "id": "posted_feed",
            "statement": (
                f"Posted/take feed+book: {list(parity.posted_feeds)} / {list(parity.posted_books)}. "
                "Required: oddsapi|sportsgameodds / fanduel."
            ),
        },
        {
            "id": "close_feed",
            "statement": (
                f"Close/benchmark: {list(parity.close_feeds)} / {list(parity.close_books)}. "
                "Pinnacle if the provider exposed it; otherwise provider close + liability."
            ),
        },
        {
            "id": "seasons",
            "statement": f"Season labels in lookback: {seasons}.",
        },
        {
            "id": "markets",
            "statement": (
                f"Markets present in lookback: {market_list}. "
                f"Allowed straights: {', '.join(ALLOWED_MARKETS)}."
            ),
        },
        {
            "id": "sports",
            "statement": f"Sport families present in lookback: {sport_list}.",
        },
        {
            "id": "filters",
            "statement": (
                f"Lookback rows={n_loaded}; scored={n_scored}; "
                f"excluded via documented reason={n_excluded_rows} "
                f"(audit included={audit.n_included}, excluded={audit.n_excluded}). "
                "No undocumented drop."
            ),
        },
        {
            "id": "sample_size",
            "statement": (
                f"Scored n={n_scored} (gate 1 min_n={min_n}). "
                "This is not the kill-switch n≥100 sample."
            ),
        },
        {
            "id": "close_kind",
            "statement": f"close_kind values in lookback: {kinds}. Only true_close may score.",
        },
        {
            "id": "pinnacle_close",
            "statement": (
                "Pinnacle close present."
                if getattr(parity, "pinnacle_close", False)
                else "Pinnacle close NOT present — KNOWN LIABILITY; not invented."
            ),
        },
        {
            "id": "coverage_cost",
            "statement": (
                "Cheap-feed history is thinner (Odds API scores daysFrom≤3 unless you pass "
                "an export; SGO historical needs a higher plan). Missing rows stay missing."
            ),
        },
        {
            "id": "odds_api",
            "statement": (
                "The Odds API is a payable source for this backtest when posted_feed=oddsapi "
                "or --source oddsapi with THE_ODDS_API_KEY."
            ),
        },
        {
            "id": "sportsgameodds",
            "statement": (
                "SportsGameOdds is a payable source when posted_feed=sportsgameodds or "
                "--source sportsgameodds with SPORTSGAMEODDS_API_KEY."
            ),
        },
        {
            "id": "opticodds",
            "statement": (
                "OpticOdds was not used and was not invented. It is not the payable path."
            ),
        },
        {
            "id": "live_stream",
            "statement": (
                "No OpticOdds WS/SSE historical pull. This number comes from the Odds API / "
                "SportsGameOdds archive or historical REST above."
            ),
        },
        {
            "id": "scan_engine",
            "statement": "engine.score_quotes / consensus EV is not a data source for CLV.",
        },
        {
            "id": "invented_closes",
            "statement": "HQ did not invent closes, fills, or missing timestamps.",
        },
    ]


def render_backtest_markdown(result: BacktestResult) -> str:
    """Owner-facing report: every assumption and every data source, then the number."""
    stopped = (not result.archive_audit_passed) or "STOPPED" in (result.note or "")
    if stopped and not result.archive_audit_passed:
        headline = "STOPPED — archive audit failed. No vanity CLV. STOP FOR HUMAN REVIEW."
    elif result.sample:
        headline = "SAMPLE fixture — cannot clear gate 1. Schema only."
    elif result.cleared:
        headline = f"Gate 1 CLEAR on this file: avg CLV {result.avg_clv:+.2f} on n={result.n}."
    else:
        headline = "Gate 1 NOT cleared."
    avg = "—" if result.avg_clv is None else f"{result.avg_clv:+.2f}"
    lines = [
        "# Historical CLV backtest report",
        "",
        f"**{headline}**",
        "",
        "This phase: **backtest is the only active workstream.** Scan, brief, and lessons "
        "are HOLD and are not inputs to this number. If an edge prints, the slices and "
        "logs below are exactly what produced it.",
        "",
        "## Scoreboard",
        "",
        f"- avg CLV: **{avg}**",
        f"- n (scored): **{result.n}** (min_n={result.min_n})",
        f"- health: **{result.health}**",
        f"- cleared: **{result.cleared}**",
        f"- feed_parity: **{result.feed_parity}** (`{result.feed_id or result.live_feed_id}`)",
        f"- archive_audit: **{result.archive_audit_passed}**",
        f"- sample: **{result.sample}**",
        f"- pinnacle_close: **{result.pinnacle_close}**",
        f"- source: `{result.source or '—'}`",
        f"- as of: {result.as_of or '—'}",
        f"- note: {result.note or '—'}",
        "",
        "## Every assumption",
        "",
        "If this number looks good, these are the rules that produced it. Nothing else.",
        "",
    ]
    for item in result.assumptions:
        lines.append(f"- **{item.get('id', '?')}:** {item.get('statement', '')}")
    lines.extend(
        [
            "",
            "## Every data source",
            "",
            "Feeds, seasons, markets, filters, vig handling, close definition, and sample "
            "size are logged here (vig + close definition also appear under assumptions).",
            "",
        ]
    )
    for item in result.data_sources:
        lines.append(f"- **{item.get('id', '?')}:** {item.get('statement', '')}")
    lines.extend(["", "## What produced this number (slices)", ""])
    if not result.n:
        lines.append("No scored rows — nothing to slice. Do not treat a blank as a clean pass.")
    else:
        lines.extend(_slice_table("Season", result.per_season))
        lines.append("")
        lines.extend(_slice_table("Market", result.per_market))
        lines.append("")
        lines.extend(_slice_table("Sport family", result.per_sport))
        lines.extend(
            [
                "",
                "If avg CLV is positive, check whether one season/market/sport is carrying "
                "the whole number before treating it as desk-wide edge.",
            ]
        )
    lines.extend(
        [
            "",
            "## Feed parity",
            "",
            f"- Payable path: **{result.live_feed_id}** (Odds API or SportsGameOdds, FanDuel take, Pinnacle optional)",
            f"- Pinnacle close: **{result.pinnacle_close}**",
            f"- This file: **{result.feed_detail or '—'}**",
            "",
            "## Known liabilities",
            "",
        ]
    )
    liabilities = list(result.liabilities or [])
    if liabilities:
        lines.extend(f"- {item}" for item in liabilities)
    else:
        lines.append("- None logged.")
    lines.extend(
        [
            "",
            "## Archive audit (gate 0)",
            "",
            f"- passed: **{result.archive_audit_passed}**",
            f"- {result.archive_audit_detail or '—'}",
            "",
            "## Live / kill switch",
            "",
            "This report does not place bets and does not unlock `log-bet --live`. "
            "Paper trade only. Kill switch A (acted before gates / live attempt) and B "
            "(paper avg CLV ≤ 0 at n≥100) are unchanged. Resume still requires a written review.",
            "",
            "**CAVEAT: backtest ≠ will work again.**",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def _slice_table(title: str, slices: dict[str, dict[str, Any]]) -> list[str]:
    lines = [f"### {title}", "", "| slice | n | avg CLV |", "|---|---:|---:|"]
    if not slices:
        lines.append("| — | 0 | — |")
        return lines
    for key, payload in slices.items():
        n = payload.get("n", 0)
        avg = payload.get("avg_clv")
        avg_s = "—" if avg is None else f"{avg:+.2f}"
        lines.append(f"| {key} | {n} | {avg_s} |")
    return lines


def _group_clv(rows: list[HistoricalClose], key_fn) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[HistoricalClose]] = {}
    for row in rows:
        groups.setdefault(str(key_fn(row) or "—"), []).append(row)
    out: dict[str, dict[str, Any]] = {}
    for key in sorted(groups):
        clvs = [clv_pct(r.posted_odds, r.close_odds) for r in groups[key]]
        out[key] = {
            "n": len(clvs),
            "avg_clv": round(sum(clvs) / len(clvs), 2) if clvs else None,
        }
    return out


def _inventory(rows: list[HistoricalClose]) -> tuple[list[str], list[str], list[str], list[str]]:
    seasons = sorted({r.season for r in rows if r.season})
    markets = sorted({r.market for r in rows if r.market})
    sports = sorted({family(r.sport) if family(r.sport) != "skip" else (r.sport or "—") for r in rows})
    kinds = sorted({(r.close_kind or "unknown") for r in rows})
    return seasons, markets, sports, kinds


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
    inv_seasons, inv_markets, inv_sports, inv_kinds = _inventory(selected)
    n_excluded_rows = sum(1 for r in selected if r.excluded)

    def _logs(*, n_scored: int, parity: FeedParity) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        return (
            assumption_log(
                seasons=seasons,
                min_n=min_n,
                sample=sample,
                season_labels=chosen,
                n_scored=n_scored,
            ),
            data_source_log(
                source=source,
                sample=sample,
                parity=parity,
                audit=audit,
                n_loaded=len(selected),
                n_scored=n_scored,
                min_n=min_n,
                season_labels=inv_seasons or chosen,
                markets=inv_markets,
                sports=inv_sports,
                close_kinds=inv_kinds,
                n_excluded_rows=n_excluded_rows,
            ),
        )

    if not audit.passed:
        assumptions, sources = _logs(n_scored=0, parity=evaluate_feed_parity([]))
        # Do not score CLV on a junk/thin archive — that would look cleaner than reality.
        return BacktestResult(
            cleared=False,
            health=ModelHealth.INSUFFICIENT_SAMPLE.value,
            avg_clv=None,
            n=0,
            seasons=chosen,
            min_n=min_n,
            source=source,
            as_of=utcnow().isoformat(),
            note=(
                "STOPPED: archive audit failed — no vanity CLV. Human review required. "
                + audit.detail
            ),
            per_season={},
            per_market={},
            per_sport={},
            feed_parity=False,
            feed_id="",
            feed_detail="Not evaluated — archive audit must pass first.",
            live_feed_id=LIVE_FEED_ID,
            archive_audit_passed=False,
            archive_audit_detail=audit.detail,
            sample=sample,
            pinnacle_close=False,
            liabilities=list(getattr(evaluate_feed_parity([]), "liabilities", ()) or []),
            assumptions=assumptions,
            data_sources=sources,
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
    if not parity.pinnacle_close:
        note = (note + " " if note else "") + "KNOWN LIABILITY: no Pinnacle close on this cheaper feed."
    per_season = _group_clv(scored, lambda r: r.season)
    per_market = _group_clv(scored, lambda r: r.market)
    per_sport = _group_clv(scored, lambda r: family(r.sport) if family(r.sport) != "skip" else r.sport)
    assumptions, sources = _logs(n_scored=n, parity=parity)
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
        per_market=per_market,
        per_sport=per_sport,
        feed_parity=parity.matched,
        feed_id=parity.feed_id,
        feed_detail=parity.detail,
        live_feed_id=LIVE_FEED_ID,
        archive_audit_passed=audit.passed,
        archive_audit_detail=audit.detail,
        sample=sample,
        pinnacle_close=parity.pinnacle_close,
        liabilities=list(parity.liabilities),
        assumptions=assumptions,
        data_sources=sources,
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
    close_book = str(raw.get("close_book") or "").strip()
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
        close_book=close_book.lower(),
        close_feed=close_feed.lower(),
        posted_at=str(raw.get("posted_at") or "").strip(),
        close_at=str(raw.get("close_at") or "").strip(),
        commence_at=str(raw.get("commence_at") or "").strip(),
        close_kind=str(raw.get("close_kind") or "").strip().lower(),
        excluded=_truthy(raw.get("excluded")),
        exclude_reason=str(raw.get("exclude_reason") or "").strip(),
    )
