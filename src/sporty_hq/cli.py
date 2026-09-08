"""Typer CLI: ingest/scan, log-bet, settle, clv-report, brief, alert-test, remind, demo."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from sporty_hq import DISCLAIMER, __version__
from sporty_hq.alerts.bus import AlertBus
from sporty_hq.brief import (
    EDUCATION_FOOTER,
    build_brief,
    read_pack,
    render_markdown as render_brief_markdown,
    write_pack,
)
from sporty_hq.archive import (
    OWNER_ARCHIVE_CLEARED_AT,
    OWNER_ARCHIVE_STATUS,
    audit_archive,
    load_documented_gaps,
    render_audit_markdown,
    save_audit,
)
from sporty_hq.backtest import load_historical_closes, render_backtest_markdown, run_backtest, save_result
from sporty_hq.bankroll import suggested_stake
from sporty_hq.config import Settings, load_settings
from sporty_hq.engine import ScanConfig, score_quotes
from sporty_hq.gates import evaluate_gates
from sporty_hq.killswitch import (
    ReviewArtifactError,
    maybe_trip_paper_clv,
    resume as resume_desk,
    trip_acted_before_gate,
)
from sporty_hq.models import (
    Alert,
    AlertType,
    Bet,
    Candidate,
    Lesson,
    display_market,
    normalize_market,
    normalize_postmortem,
)
from sporty_hq.odds_math import clv_pct, parse_american, settle_pnl
from sporty_hq.playbook import (
    PlaybookViolation,
    format_stop_block,
    session_snapshot,
    stop_status,
    validate_new_bet,
)
from sporty_hq.providers import load_provider
from sporty_hq.reports import (
    ModelHealth,
    paper_clv_summary,
    render_html,
    render_markdown,
)
from sporty_hq.session import persist_live_session, read_session, session_row_to_bet
from sporty_hq.storage import Store, utcnow
from sporty_hq.streaming import (
    StreamingUnavailable,
    collect_stream_quotes,
    load_stream_provider,
)

app = typer.Typer(
    name="sporty",
    help="Sporty HQ — FanDuel research + alerts for Ontario. Does not place bets.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = REPO_ROOT / "fixtures" / "demo_odds.json"
DEFAULT_SAMPLE_SESSION = REPO_ROOT / "fixtures" / "sample_session.json"


def _settings(
    data_dir: Optional[Path] = None,
    min_edge: Optional[float] = None,
) -> Settings:
    overrides = {}
    if data_dir is not None:
        overrides["data_dir"] = data_dir
    if min_edge is not None:
        overrides["min_edge"] = min_edge
    return load_settings(**overrides)


def _store(settings: Settings) -> Store:
    settings.ensure_data_dir()
    return Store(settings.db_path)


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


def _sync_kill_switch(settings: Settings, store: Store):
    return maybe_trip_paper_clv(
        settings.data_dir,
        store,
        unit=settings.unit_stake,
        judge_n=settings.clv_judge_n,
    )


def _require_ops(settings: Settings, store: Store) -> None:
    """Refuse ingest/scan/log/remind while paused. Settle + CLV report stay up."""
    desk = _sync_kill_switch(settings, store)
    if desk.paused:
        console.print(f"[red]{desk.pause_message()}[/red]")
        raise typer.Exit(1)


def _trip_acted(settings: Settings, store: Store, action: str) -> None:
    paper = paper_clv_summary(store.list_bets(), settings.unit_stake, settings.clv_judge_n)
    desk = trip_acted_before_gate(
        settings.data_dir,
        store,
        action=action,
        paper_health=paper.health,
        paper_n=paper.clv_n,
        judge_n=settings.clv_judge_n,
    )
    bus = AlertBus.from_settings(store, settings)
    bus.publish(
        Alert(
            type=AlertType.KILL_SWITCH,
            title="Kill switch PAUSED",
            body=desk.pause_message(),
            dedup_key=f"kill_switch:{desk.trip}:{desk.tripped_at}",
            payload={"trip": desk.trip, "detail": desk.detail},
        )
    )
    console.print(f"[red]{desk.pause_message()}[/red]")
    raise typer.Exit(1)


def _sync_session(settings: Settings, store: Store, now: datetime | None = None) -> None:
    persist_live_session(
        settings.session_path,
        store.list_bets(),
        now=now or utcnow(),
        stake_unit_usd=settings.unit_stake,
        stop_loss_usd=settings.session_stop,
        edge_floor_pct=round(settings.min_edge * 100.0, 4),
        max_bets=settings.max_bets_per_session,
    )


@app.callback()
def _root() -> None:
    """Sporty HQ CLI."""


@app.command()
def version() -> None:
    """Print version and the gambling disclaimer."""
    console.print(f"sporty-hq {__version__}")
    console.print(DISCLAIMER)


@app.command()
def ingest(
    source: str = typer.Option(
        "auto",
        "--source",
        help="auto | fixture | opticodds | stream | oddsapi",
    ),
    path: Optional[Path] = typer.Option(None, "--path", help="JSON or CSV fixture path"),
    replay: Optional[Path] = typer.Option(
        None, "--replay", help="Replay a fixture as a push batch (stream stub / offline)"
    ),
    max_events: int = typer.Option(40, "--max-events", help="Cap quotes from OpticOdds realtime (WS then SSE)"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", envvar="SPORTY_HQ_DATA_DIR"),
) -> None:
    """Load odds. Live prefers OpticOdds realtime (WS first, SSE fallback) + Pinnacle. Odds API is optional stub only. Never scrapes in a loop."""
    settings = _settings(data_dir)
    store = _store(settings)
    _require_ops(settings, store)
    quotes, provider_name, note = _ingest_quotes(
        settings, source=source, path=path, replay=replay, max_events=max_events
    )
    if not quotes:
        raise typer.BadParameter("Ingest produced 0 quotes")
    batch_id = _short_id()
    store.insert_quotes(batch_id, provider_name, quotes)
    books = sorted({q.book for q in quotes})
    events = sorted({q.event_id for q in quotes})
    console.print(note)
    console.print(
        f"Ingested [bold]{len(quotes)}[/bold] quotes "
        f"({len(events)} events, books: {', '.join(books)}) batch={batch_id} "
        f"via {provider_name}"
    )
    console.print(f"DB: {settings.db_path}")


@app.command()
def scan(
    min_edge: Optional[float] = typer.Option(None, "--min-edge", help="Minimum EV after juice (default 0.03)"),
    book: Optional[str] = typer.Option(None, "--book", help="Target book (default fanduel)"),
    alerts: bool = typer.Option(False, "--alerts", help="Publish new_candidate alerts (deduped)"),
    json_out: bool = typer.Option(False, "--json", help="Print candidates as JSON"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", envvar="SPORTY_HQ_DATA_DIR"),
) -> None:
    """Optional prediction candy. CLV dashboard is the scoreboard. Held behind the gate."""
    settings = _settings(data_dir, min_edge)
    store = _store(settings)
    _require_ops(settings, store)
    now = utcnow()
    gates = evaluate_gates(store, settings, now)
    console.print(
        f"CLV is the scoreboard. Gate 1 backtest={'cleared' if gates.backtest.cleared else 'open'} "
        f"(caveat: backtest ≠ will work again). "
        f"Gate 2 paper confirm={'cleared' if gates.paper_confirm.cleared else 'open'} "
        "(~2–3 weeks, avg CLV must stay > 0). "
        "Scan is optional prediction candy — not a ticket. Human-source HOLD."
    )
    if alerts and not gates.live_unlocked:
        _trip_acted(settings, store, action="scan --alerts")
    stops = stop_status(store, settings, now)
    console.print(format_stop_block(stops, settings))
    batch_id = store.latest_batch_id()
    if not batch_id:
        raise typer.Exit("No odds ingested yet. Run: sporty ingest --source fixture")
    quotes = store.quotes_for_batch(batch_id)
    config = ScanConfig(
        target_book=book or settings.target_book,
        sharp_book=settings.sharp_book,
        min_edge=settings.min_edge,
    )
    candidates = score_quotes(quotes, config)
    # Human-source lessons HOLD until feed + CLV dashboard are logging.
    if settings.kelly_fraction > 0:
        for cand in candidates:
            cand.suggested_stake = suggested_stake(
                settings, fair_prob=cand.fair_prob, decimal_odds=cand.decimal_odds
            )
    stored = store.replace_candidates(batch_id, candidates)
    _write_scan_markdown(settings, stored)
    if json_out:
        console.print_json(data=[c.to_row() for c in stored])
    elif not stored:
        console.print("Quiet: nothing cleared ≥3% edge.")
    else:
        _print_candidates(stored, settings.min_edge)
    if alerts:
        if not stored:
            console.print("Quiet: no new_candidate alerts.")
        else:
            bus = AlertBus.from_settings(store, settings)
            sent = bus.new_candidates(stored)
            console.print(f"Alerts sent: {sent} (deduped against prior keys)")
    console.print(
        "Hybrid research/alerts only — place any ticket on FanDuel mobile yourself. HQ never fills."
    )


@app.command("log-bet")
def log_bet(
    candidate_id: Optional[int] = typer.Option(None, "--candidate-id", help="Use a scanned candidate"),
    event_id: Optional[str] = typer.Option(None, "--event-id"),
    event: Optional[str] = typer.Option(None, "--event", help="Event name if not using a candidate"),
    market: Optional[str] = typer.Option(None, "--market", help="ml | spread | total"),
    selection: Optional[str] = typer.Option(None, "--pick", "--selection", help="Pick (straight)"),
    odds: Optional[str] = typer.Option(None, "--odds", help="American odds at bet, e.g. +165 or -110"),
    stake: Optional[float] = typer.Option(None, "--stake", help="Flat unit (default $25)"),
    edge_note: str = typer.Option("", "--edge-note", "--notes", help="Edge note column"),
    sport: str = typer.Option("", "--sport"),
    point: Optional[float] = typer.Option(None, "--point"),
    force: bool = typer.Option(False, "--force", help="Override session cap (not kill switch / live lock)"),
    kelly: bool = typer.Option(False, "--kelly", help="Held: not the primary path. Cap 1 unit if used."),
    live: bool = typer.Option(
        False,
        "--live",
        help="Live ticket. Locked until historical backtest + ~2–3 week paper confirm. Default is paper.",
    ),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", envvar="SPORTY_HQ_DATA_DIR"),
) -> None:
    """Log a PAPER ticket for the CLV dashboard (default). HQ never places FanDuel bets."""
    settings = _settings(data_dir)
    store = _store(settings)
    _require_ops(settings, store)
    now = utcnow()
    kind = "live" if live else "paper"
    if live:
        gates = evaluate_gates(store, settings, now)
        if not gates.live_unlocked:
            _trip_acted(settings, store, action="log-bet --live")
    cand: Candidate | None = None
    if candidate_id is not None:
        cand = store.get_candidate(candidate_id)
        if cand is None:
            raise typer.BadParameter(f"Unknown candidate id {candidate_id} — run scan first")
    event_id_v = (cand.event_id if cand else event_id) or ""
    event_name = (cand.event_name if cand else event) or event_id_v
    market_v = cand.market if cand else (market or "")
    selection_v = cand.selection if cand else (selection or "")
    if not event_id_v or not market_v or not selection_v:
        raise typer.BadParameter("Need --candidate-id or --event-id --market --selection --odds")
    try:
        market_v = normalize_market(market_v)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if odds is not None:
        american = parse_american(odds)
    elif cand is not None:
        american = cand.american_odds
    else:
        raise typer.BadParameter("--odds is required unless --candidate-id is set")
    if stake is not None:
        stake_v = stake
    elif kelly or settings.kelly_fraction > 0:
        if cand is None:
            raise typer.BadParameter("--kelly needs --candidate-id (fair_prob / decimal)")
        stake_v = suggested_stake(
            settings,
            fair_prob=cand.fair_prob,
            decimal_odds=cand.decimal_odds,
            kelly=kelly,
        )
        console.print(
            f"Kelly suggestion ${stake_v:.2f} (unit ${settings.unit_stake:.0f}, "
            f"cap {settings.kelly_cap_units:g}u, fraction "
            f"{settings.kelly_fraction or 0.25:g}). HQ does not place bets."
        )
    else:
        stake_v = settings.unit_stake
    try:
        validate_new_bet(
            store,
            settings,
            event_id=event_id_v,
            market=market_v,
            stake=stake_v,
            now=now,
            force=force,
            kind=kind,
        )
    except PlaybookViolation as exc:
        raise typer.Exit(str(exc)) from exc

    bet = Bet(
        id=_short_id(),
        logged_at=now,
        event_id=event_id_v,
        event_name=event_name,
        sport=(cand.sport if cand else sport) or "",
        commence_at=cand.commence_at if cand else None,
        market=market_v,
        selection=selection_v,
        point=cand.point if cand is not None else point,
        odds_at_bet=american,
        stake=stake_v,
        edge_note=edge_note or (cand.rationale if cand else ""),
        kind=kind,
    )
    store.insert_bet(bet)
    _sync_session(settings, store, now)
    if abs(stake_v - settings.unit_stake) > 1e-9:
        console.print(
            f"[yellow]Note:[/yellow] playbook unit is ${settings.unit_stake:.0f}; logged ${stake_v:.2f}."
        )
    snap = session_snapshot(store, settings, now)
    console.print(
        f"Logged {kind} ticket [bold]{bet.id}[/bold] {bet.event_name} {display_market(bet.market)} "
        f"{bet.selection} {_fmt_odds(bet.odds_at_bet)} stake ${bet.stake:.0f}"
    )
    console.print(
        f"Session {snap.start.date()}: {snap.bets_logged}/{settings.max_bets_per_session} bets, "
        f"realized ${snap.realized_pnl:+.2f}, open risk ${snap.open_risk:.0f}, "
        f"worst-case ${snap.worst_case_pnl:+.2f} (daily stop {settings.daily_stop:.0f}, "
        f"seasonal {settings.seasonal_stop:.0f})"
    )


@app.command()
def settle(
    bet_id: str = typer.Argument(..., help="Bet id from log-bet"),
    result: str = typer.Option(..., "--result", help="win | loss | push | void"),
    close_odds: str = typer.Option(..., "--close-odds", help="American close (required), e.g. +145"),
    notes: Optional[str] = typer.Option(None, "--edge-note", "--notes"),
    postmortem: Optional[str] = typer.Option(
        None,
        "--postmortem",
        help="HELD human-source field: injury_missed | weather_ignored | steam_missed | other",
    ),
    lesson: Optional[str] = typer.Option(
        None, "--lesson", help="HELD: optional free-text; not required to settle"
    ),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", envvar="SPORTY_HQ_DATA_DIR"),
) -> None:
    """Settle a ticket. Close line is required so CLV can be scored. Allowed while paused."""
    settings = _settings(data_dir)
    store = _store(settings)
    bet = store.get_bet(bet_id)
    if bet is None:
        raise typer.Exit(f"Unknown bet id {bet_id}")
    kind = result.strip().lower()
    try:
        bet.pnl = settle_pnl(kind, bet.stake, bet.odds_at_bet)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    bet.result = kind
    bet.close_odds = parse_american(close_odds)
    bet.clv_pct = round(clv_pct(bet.odds_at_bet, bet.close_odds), 2)
    bet.settled_at = utcnow()
    if notes:
        bet.edge_note = (bet.edge_note + " | " if bet.edge_note else "") + notes
    tag: str | None = None
    if postmortem:
        try:
            tag = normalize_postmortem(postmortem)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    bet.postmortem = tag
    bet.lesson = (lesson or "").strip()
    store.update_bet(bet)
    if tag:
        from sporty_hq.lessons import teams_from_event

        store.insert_lesson(
            Lesson(
                sport=bet.sport,
                event_id=bet.event_id,
                event_name=bet.event_name,
                market=bet.market,
                selection=bet.selection,
                postmortem=tag,
                lesson=bet.lesson,
                teams=teams_from_event(bet.event_name, bet.selection),
                bet_id=bet.id,
            )
        )
    _sync_session(settings, store)
    desk = _sync_kill_switch(settings, store)
    clv_txt = "0" if bet.clv_pct == 0 else f"{bet.clv_pct:+.2f}%"
    extra = f" postmortem={tag}" if tag else ""
    console.print(
        f"Settled {bet.id} {bet.result} pnl ${bet.pnl:+.2f} CLV {clv_txt} "
        f"(bet {_fmt_odds(bet.odds_at_bet)} close {_fmt_odds(bet.close_odds)}){extra}"
    )
    if desk.paused:
        console.print(f"[red]{desk.pause_message()}[/red]")


@app.command("clv-report")
def clv_report(
    fmt: str = typer.Option("md", "--format", help="md | html | json"),
    out: Optional[Path] = typer.Option(None, "--out", help="Write file instead of stdout"),
    gate: bool = typer.Option(
        False,
        "--gate",
        help="Exit 1 if model health is FAILING (n>=100 and avg CLV <= 0)",
    ),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", envvar="SPORTY_HQ_DATA_DIR"),
) -> None:
    """Cumulative CLV dashboard. Paper avg CLV is the primary health metric."""
    settings = _settings(data_dir)
    store = _store(settings)
    bets = store.list_bets()
    desk = _sync_kill_switch(settings, store)
    paper = paper_clv_summary(bets, settings.unit_stake, judge_n=settings.clv_judge_n)
    gates = evaluate_gates(store, settings, utcnow())
    kind = fmt.strip().lower()
    if kind in {"md", "markdown"}:
        text = render_markdown(
            bets,
            settings.unit_stake,
            judge_n=settings.clv_judge_n,
            desk=desk,
            gates=gates,
        )
    elif kind == "html":
        text = render_html(
            bets,
            settings.unit_stake,
            judge_n=settings.clv_judge_n,
            desk=desk,
            gates=gates,
        )
    elif kind == "json":
        text = json.dumps(
            {
                "summary": paper.__dict__,
                "kill_switch": desk.to_json_dict(),
                "gates": gates.to_json_dict(),
                "bets": [b.to_row() for b in bets],
            },
            indent=2,
            default=str,
        )
    else:
        raise typer.BadParameter("format must be md, html, or json")
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        console.print(f"Wrote {out}")
    else:
        console.print(text)
    console.print(
        f"Paper health: {paper.health} (avg CLV n={paper.clv_n}) · "
        f"kill switch: {'PAUSED' if desk.paused else 'RUNNING'}"
    )
    if gate and (paper.health == ModelHealth.FAILING.value or desk.paused):
        raise typer.Exit(1)


@app.command("desk-status")
def desk_status_cmd(
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", envvar="SPORTY_HQ_DATA_DIR"),
) -> None:
    """Print kill-switch status (running | paused) and paper CLV gate."""
    settings = _settings(data_dir)
    store = _store(settings)
    desk = _sync_kill_switch(settings, store)
    paper = paper_clv_summary(store.list_bets(), settings.unit_stake, settings.clv_judge_n)
    gates = evaluate_gates(store, settings, utcnow())
    payload = {
        "kill_switch": desk.to_json_dict(),
        "paper_health": paper.health,
        "paper_n": paper.clv_n,
        "paper_avg_clv": paper.avg_clv,
        "gates": gates.to_json_dict(),
        "live_unlocked": gates.live_unlocked,
    }
    console.print_json(data=payload)


@app.command()
def resume(
    review: Path = typer.Option(..., "--review", help="Written review artifact (required)"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", envvar="SPORTY_HQ_DATA_DIR"),
) -> None:
    """Resume after a kill-switch pause. Requires a written review. No --force."""
    settings = _settings(data_dir)
    store = _store(settings)
    try:
        desk = resume_desk(settings.data_dir, store, review)
    except ReviewArtifactError as exc:
        raise typer.Exit(str(exc)) from exc
    console.print(f"Desk RUNNING. Review archived: {desk.review_path}")
    console.print(desk.detail)


@app.command("archive-audit")
def archive_audit_cmd(
    path: Optional[Path] = typer.Option(
        None, "--path", help="OpticOdds archive export (CSV/JSON). Required unless --owner-cleared."
    ),
    seasons: int = typer.Option(1, "--seasons", help="Trailing seasons to audit (1–3)"),
    gaps: Optional[Path] = typer.Option(
        None, "--gaps", help="JSON of documented coverage exclusions"
    ),
    owner_cleared: bool = typer.Option(
        False,
        "--owner-cleared",
        help="Report the standing owner CLEARED flag (2026-09-08). File audit still required to score.",
    ),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", envvar="SPORTY_HQ_DATA_DIR"),
) -> None:
    """Audit OpticOdds historical archive before backtest. Does not invent closes."""
    settings = _settings(data_dir)
    if owner_cleared and path is None:
        console.print_json(
            data={
                "owner_status": OWNER_ARCHIVE_STATUS,
                "owner_cleared_at": OWNER_ARCHIVE_CLEARED_AT,
                "passed": None,
                "detail": (
                    f"Owner flagged OpticOdds archive audit {OWNER_ARCHIVE_STATUS} "
                    f"on {OWNER_ARCHIVE_CLEARED_AT}. Pass --path to run file checks "
                    "(coverage, timestamps, true close vs last-seen). "
                    "A CLEARED flag is not a CLV number."
                ),
            }
        )
        return
    if path is None:
        raise typer.BadParameter(
            "Pass --path to an OpticOdds archive export, or --owner-cleared to print the standing flag."
        )
    rows = load_historical_closes(path)
    documented = load_documented_gaps(gaps)
    audit = audit_archive(rows, seasons_requested=seasons, documented_gaps=documented)
    out = save_audit(settings.data_dir, audit)
    md = render_audit_markdown(audit)
    console.print(md)
    console.print(f"Wrote {out} and {settings.data_dir / 'archive_audit.md'}")
    if not audit.passed:
        raise typer.Exit(1)


@app.command()
def backtest(
    path: Optional[Path] = typer.Option(
        None,
        "--path",
        help="CSV/JSON OpticOdds archive export (posted vs close). Required. No silent fixture.",
    ),
    source: str = typer.Option(
        "archive",
        "--source",
        help="archive (local export) | opticodds (refuses: SSE has no historical closes)",
    ),
    seasons: int = typer.Option(1, "--seasons", help="How many trailing seasons in the file (1–3)"),
    gaps: Optional[Path] = typer.Option(
        None, "--gaps", help="JSON of documented archive gaps/exclusions"
    ),
    fmt: str = typer.Option(
        "md",
        "--format",
        help="md (owner report: every assumption + data source) | json",
    ),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", envvar="SPORTY_HQ_DATA_DIR"),
) -> None:
    """Gate 1: historical CLV vs closes. Archive audit is a hard stop first. No bets."""
    settings = _settings(data_dir)
    store = _store(settings)
    kind = source.strip().lower()
    if kind in {"opticodds", "sse", "live", "stream"}:
        keyed = settings.secret_configured("opticodds_api_key")
        if not keyed:
            console.print(
                "[red]OPTICODDS_API_KEY is not set.[/red] OpticOdds SSE is realtime only and "
                "does not contain historical closes. Export the cleared archive to CSV/JSON "
                "and pass --path. HQ will not invent numbers."
            )
            raise typer.Exit(2)
        console.print(
            "[red]OpticOdds SSE is realtime only — there is no historical-close fetch in this CLI.[/red] "
            "Export the owner-cleared archive to CSV/JSON and pass --path. "
            "HQ will not invent closes from the live stream."
        )
        raise typer.Exit(2)
    if kind not in {"archive", "file", "csv", "json"}:
        raise typer.BadParameter("source must be archive (local export) or opticodds")
    if path is None:
        console.print(
            "[red]--path is required.[/red] Pass the OpticOdds archive export (CSV/JSON) "
            "with posted_feed=opticodds, posted_book=fanduel, close_book=pinnacle. "
            "HQ will not silently load a fixture or invent closes."
        )
        raise typer.Exit(2)
    rows = load_historical_closes(path)
    documented = load_documented_gaps(gaps)
    audit = audit_archive(rows, seasons_requested=seasons, documented_gaps=documented)
    save_audit(settings.data_dir, audit)
    result = run_backtest(
        rows,
        seasons=seasons,
        min_n=settings.backtest_min_n,
        source=str(path),
        target_book=settings.target_book,
        sharp_book=settings.sharp_book,
        audit=audit,
        documented_gaps=documented,
    )
    out = save_result(settings.data_dir, result)
    md_path = settings.data_dir / "backtest.md"
    report = render_backtest_markdown(result)
    kind_fmt = (fmt or "md").strip().lower()
    if kind_fmt not in {"md", "json", "markdown"}:
        raise typer.BadParameter("format must be md or json")
    if not audit.passed:
        console.print(render_audit_markdown(audit))
        console.print(report)
        console.print(
            "[red]STOPPED for human review.[/red] Archive audit failed "
            "(coverage / timestamps / true-close). No vanity CLV will be scored. "
            f"See {settings.data_dir / 'archive_audit.md'} and {md_path}"
        )
        store.record_desk_event(
            kind="archive_audit",
            trip=None,
            detail=audit.detail,
            review_path=str(settings.data_dir / "archive_audit.md"),
        )
        raise typer.Exit(1)
    store.record_desk_event(
        kind="backtest",
        trip=None,
        detail=result.note,
        review_path=str(md_path),
    )
    if kind_fmt == "json":
        console.print_json(data=result.to_json_dict())
    else:
        console.print(report)
    console.print(
        f"avg CLV={result.avg_clv} n={result.n} health={result.health} "
        f"cleared={result.cleared} feed_parity={result.feed_parity} "
        f"archive_audit={result.archive_audit_passed} sample={result.sample}"
    )
    console.print(f"Wrote {out} and {md_path}")
    if not result.cleared:
        raise typer.Exit(1)


@app.command()
def brief(
    source: str = typer.Option(
        "auto",
        "--source",
        help="auto (latest ingest, else fixture) | fixture | oddsapi",
    ),
    path: Optional[Path] = typer.Option(None, "--path", help="JSON or CSV fixture path"),
    pack: bool = typer.Option(
        False, "--pack", help="Thursday close-challenge pack (2–3 games); saved for Friday"
    ),
    closes: bool = typer.Option(
        False,
        "--closes",
        "--results",
        help="Friday close-challenge results vs the saved Thursday pack",
    ),
    fmt: str = typer.Option("json", "--format", help="json | md"),
    out: Optional[Path] = typer.Option(None, "--out", help="Write file instead of stdout"),
    book: Optional[str] = typer.Option(None, "--book", help="Target book (default fanduel)"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", envvar="SPORTY_HQ_DATA_DIR"),
) -> None:
    """Odds Arcade education brief (line move + juice $100 compare). Does not place bets."""
    settings = _settings(data_dir)
    store = _store(settings)
    quotes, history = _brief_quotes(settings, store, source=source, path=path)
    if not quotes:
        raise typer.Exit("No quotes for a brief. Run: sporty ingest --source fixture")
    saved_pack = None
    pack_path = settings.close_challenge_pack_path
    if closes and pack_path.exists():
        saved_pack = read_pack(pack_path)
    payload = build_brief(
        quotes,
        history=history,
        include_pack=pack,
        include_closes=closes,
        saved_pack=saved_pack,
        target_book=book or settings.target_book,
    )
    if pack and payload.close_challenge_pack:
        write_pack(pack_path, payload.close_challenge_pack, payload.as_of)
    kind = fmt.strip().lower()
    if kind in {"md", "markdown"}:
        text = render_brief_markdown(payload)
    elif kind == "json":
        text = json.dumps(payload.to_json_dict(), indent=2, default=str)
    else:
        raise typer.BadParameter("format must be json or md")
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        console.print(f"Wrote {out}")
    elif kind == "json":
        console.print_json(data=payload.to_json_dict())
    else:
        console.print(text)
    console.print(EDUCATION_FOOTER)


@app.command("alert-test")
def alert_test(
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", envvar="SPORTY_HQ_DATA_DIR"),
) -> None:
    """Send a test alert through console, file, and any configured webhooks."""
    settings = _settings(data_dir)
    store = _store(settings)
    bus = AlertBus.from_settings(store, settings)
    alert = Alert(
        type=AlertType.TEST,
        title="Sporty HQ test alert",
        body=(
            "v1 notifiers: console + file"
            + (", generic webhook" if settings.secret_configured("webhook_url") else "")
            + ". Slack/SMS are later — not required. HQ does not place bets."
        ),
        dedup_key=f"test:{_short_id()}",
        payload={"version": __version__},
    )
    bus.publish(alert)
    channels = [n.name for n in bus.notifiers]
    console.print(f"Test alert published via: {', '.join(channels)}")
    if "slack" not in channels:
        console.print("Slack skipped (optional later; no OAuth).")
    console.print(f"File log: {settings.alerts_log_path}")


@app.command()
def remind(
    min_minutes: Optional[int] = typer.Option(None, "--min-minutes", help="Pre-game window start (default 30)"),
    max_minutes: Optional[int] = typer.Option(None, "--max-minutes", help="Pre-game window end (default 60)"),
    kind: str = typer.Option("all", "--type", help="pre_game | settle | all"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", envvar="SPORTY_HQ_DATA_DIR"),
) -> None:
    """Pre-game reminders 30–60m before tip on flagged (≥3%) plays only. Quiet otherwise."""
    settings = _settings(data_dir)
    if min_minutes is not None:
        settings.remind_min_minutes = min_minutes
    if max_minutes is not None:
        settings.remind_max_minutes = max_minutes
    store = _store(settings)
    _require_ops(settings, store)
    bus = AlertBus.from_settings(store, settings)
    now = utcnow()
    sent = 0
    wanted = kind.strip().lower()
    if wanted not in {"pre_game", "settle", "all"}:
        raise typer.BadParameter("--type must be pre_game, settle, or all")

    gates = evaluate_gates(store, settings, now)
    if wanted in {"pre_game", "all"}:
        if not gates.live_unlocked:
            console.print(
                "Pre-game alerts skipped — sending a reminder is acting before gates "
                "(backtest + ~2–3 week paper). Kill switch stays armed."
            )
        else:
            sent += _pre_game_reminders(store, bus, settings, now)
    if wanted in {"settle", "all"}:
        sent += _settle_reminders(store, bus, now)
    if sent == 0:
        console.print(
            f"Quiet: nothing flagged ≥{settings.min_edge:.0%} in the "
            f"{settings.remind_min_minutes}–{settings.remind_max_minutes}m window."
        )
    else:
        console.print(f"Reminders sent: {sent}")


@app.command()
def session(
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", envvar="SPORTY_HQ_DATA_DIR"),
) -> None:
    """Print data/session.json (locked v1 fields). Syncs from user-logged bets only."""
    settings = _settings(data_dir)
    store = _store(settings)
    state = persist_live_session(
        settings.session_path,
        store.list_bets(),
        now=utcnow(),
        stake_unit_usd=settings.unit_stake,
        stop_loss_usd=settings.session_stop,
        edge_floor_pct=round(settings.min_edge * 100.0, 4),
        max_bets=settings.max_bets_per_session,
    )
    console.print_json(data=state.to_json_dict())


@app.command()
def demo(
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
    fixture: Path = typer.Option(DEFAULT_FIXTURE, "--fixture"),
    sample_session: Path = typer.Option(DEFAULT_SAMPLE_SESSION, "--sample-session"),
) -> None:
    """Fixture scan (MLB/NFL first) + sample CLV report. Does not fake fills or place bets."""
    settings = _settings(data_dir)
    store = _store(settings)
    provider = load_provider("fixture", path=fixture)
    quotes = provider.fetch_quotes()
    batch_id = _short_id()
    store.insert_quotes(batch_id, provider.name, quotes)
    candidates = score_quotes(
        quotes,
        ScanConfig(
            target_book=settings.target_book,
            sharp_book=settings.sharp_book,
            min_edge=settings.min_edge,
        ),
    )
    stored = store.replace_candidates(batch_id, candidates)
    _write_scan_markdown(settings, stored)
    console.print(
        f"[bold]Demo ingest[/bold] {len(quotes)} quotes → {len(stored)} candidates "
        f"(≥ {settings.min_edge:.0%} edge; MLB+NFL first, no fake fills)"
    )
    if stored:
        _print_candidates(stored, settings.min_edge)
    else:
        console.print("Quiet: nothing cleared ≥3% edge.")

    sample = read_session(sample_session)
    dest = settings.data_dir / "sample_session.json"
    dest.write_text(json.dumps(sample.to_json_dict(), indent=2) + "\n", encoding="utf-8")
    sample_bets = [session_row_to_bet(row) for row in sample.bets]
    report_path = settings.data_dir / "clv-report.md"
    html_path = settings.data_dir / "clv-report.html"
    md = render_markdown(
        sample_bets, settings.unit_stake, sample=True, judge_n=settings.clv_judge_n
    )
    report_path.write_text(md, encoding="utf-8")
    html_path.write_text(
        render_html(sample_bets, settings.unit_stake, sample=True, judge_n=settings.clv_judge_n),
        encoding="utf-8",
    )
    console.print(md)
    console.print(f"Wrote {report_path}, {html_path}, and {dest}")
    console.print("Hybrid: research/alerts only — never fake fills or place FanDuel bets.")
    console.print(DISCLAIMER)


def _ingest_quotes(
    settings: Settings,
    *,
    source: str,
    path: Optional[Path],
    replay: Optional[Path],
    max_events: int,
) -> tuple[list, str, str]:
    """Return quotes, provider name, and a user-facing note (never includes keys)."""
    kind = source.strip().lower()
    odds_key = (
        settings.the_odds_api_key.get_secret_value()
        if settings.secret_configured("the_odds_api_key")
        else None
    )
    optic_key = (
        settings.opticodds_api_key.get_secret_value()
        if settings.secret_configured("opticodds_api_key")
        else None
    )
    fixture_path = path or DEFAULT_FIXTURE
    if kind == "auto":
        if replay is not None or optic_key:
            kind = "stream"
        elif odds_key:
            kind = "oddsapi"
        else:
            kind = "fixture"

    if kind in {"fixture", "json", "csv", "file"}:
        provider = load_provider("fixture", path=fixture_path)
        return provider.fetch_quotes(), provider.name, "fixture ingest (offline/dev)"

    if kind in {"oddsapi", "theoddsapi", "the-odds-api"}:
        if not odds_key:
            provider = load_provider("fixture", path=fixture_path)
            return (
                provider.fetch_quotes(),
                provider.name,
                "degrade: THE_ODDS_API_KEY unset — fixture ingest.",
            )
        provider = load_provider("oddsapi", api_key=odds_key)
        return (
            provider.fetch_quotes(),
            provider.name,
            "The Odds API REST snapshot (no public WebSocket; not a poll loop).",
        )

    if kind in {"stream", "opticodds", "sse", "websocket", "ws"}:
        explicit_optic = kind in {"opticodds", "sse", "websocket", "ws"}
        provider, note = load_stream_provider(
            opticodds_key=optic_key,
            odds_api_key=odds_key if (kind == "stream" and not optic_key) else None,
            replay_path=replay,
            fixture_fallback=fixture_path,
        )
        try:
            quotes = collect_stream_quotes(provider, max_events=max_events)
            transport = getattr(provider, "transport", "")
            if transport == "sse":
                note = note + " Active transport: SSE (WebSocket did not connect)."
            elif transport == "websocket":
                note = note + " Active transport: WebSocket."
            return quotes, provider.name, note
        except StreamingUnavailable:
            if optic_key or explicit_optic:
                fallback = load_provider("fixture", path=fixture_path)
                return (
                    fallback.fetch_quotes(),
                    fallback.name,
                    "degrade: OpticOdds realtime failed (WS then SSE). "
                    "Will not invent numbers or switch to Odds API. Fixture ingest.",
                )
            if odds_key:
                rest = load_provider("oddsapi", api_key=odds_key)
                return (
                    rest.fetch_quotes(),
                    rest.name,
                    "optional stub: The Odds API REST snapshot only (no WebSocket). "
                    "Set OPTICODDS_API_KEY for the live path.",
                )
            fallback = load_provider("fixture", path=fixture_path)
            return (
                fallback.fetch_quotes(),
                fallback.name,
                "degrade: no OPTICODDS_API_KEY — fixture ingest.",
            )

    raise typer.BadParameter("source must be auto, fixture, opticodds, stream, or oddsapi")


def _brief_quotes(
    settings: Settings,
    store: Store,
    *,
    source: str,
    path: Optional[Path],
) -> tuple[list, list]:
    """Current quotes plus chronological history from the store (open vs current)."""
    kind = source.strip().lower()
    history = store.quotes_chronological()
    if kind in {"fixture", "json", "csv", "file"} or (path is not None and kind == "auto"):
        fixture_path = path or DEFAULT_FIXTURE
        provider = load_provider("fixture", path=fixture_path)
        return provider.fetch_quotes(), history
    if kind in {"oddsapi", "theoddsapi", "the-odds-api"}:
        api_key = (
            settings.the_odds_api_key.get_secret_value()
            if settings.secret_configured("the_odds_api_key")
            else None
        )
        provider = load_provider("oddsapi", api_key=api_key)
        return provider.fetch_quotes(), history
    if kind not in {"auto", "db", "store"}:
        raise typer.BadParameter("source must be auto, fixture, or oddsapi")
    batch_id = store.latest_batch_id()
    if batch_id:
        return store.quotes_for_batch(batch_id), history
    fixture_path = path or DEFAULT_FIXTURE
    provider = load_provider("fixture", path=fixture_path)
    return provider.fetch_quotes(), history


def _print_candidates(candidates: list[Candidate], min_edge: float) -> None:
    table = Table(title=f"Candidates (min edge {min_edge:.0%} after juice)")
    table.add_column("id", justify="right", no_wrap=True)
    table.add_column("event", overflow="fold")
    table.add_column("market", no_wrap=True)
    table.add_column("pick", overflow="fold")
    table.add_column("odds", justify="right", no_wrap=True)
    table.add_column("edge %", justify="right", no_wrap=True)
    table.add_column("rationale", overflow="fold")
    if not candidates:
        console.print("No candidates met the edge filter.")
        return
    for cand in candidates:
        sel = cand.selection if cand.point is None else f"{cand.selection} {cand.point}"
        table.add_row(
            str(cand.id or ""),
            cand.event_name,
            display_market(cand.market),
            sel,
            f"{cand.american_odds:+d}",
            f"{cand.edge_pct:.2f}",
            cand.rationale,
        )
    console.print(table)


def _write_scan_markdown(settings: Settings, candidates: list[Candidate]) -> None:
    lines = [
        "# Sporty HQ scan",
        "",
        f"Min edge: {settings.min_edge:.0%} after juice · target {settings.target_book}",
        "",
        "| id | event | market | pick | odds | edge % | rationale |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for cand in candidates:
        sel = cand.selection if cand.point is None else f"{cand.selection} {cand.point}"
        lines.append(
            f"| {cand.id} | {cand.event_name} | {display_market(cand.market)} | {sel} | "
            f"{cand.american_odds:+d} | {cand.edge_pct:.2f} | {cand.rationale} |"
        )
    if not candidates:
        lines.append("|  | _none_ |  |  |  |  |  |")
    lines.extend(["", f"_{DISCLAIMER}_", ""])
    path = settings.data_dir / "last_scan.md"
    path.write_text("\n".join(lines), encoding="utf-8")


def _pre_game_reminders(store: Store, bus: AlertBus, settings: Settings, now: datetime) -> int:
    """Only flagged scan candidates (≥ edge floor) whose tip is 30–60 minutes out."""
    lo = timedelta(minutes=settings.remind_min_minutes)
    hi = timedelta(minutes=settings.remind_max_minutes)
    seen_events: set[str] = set()
    sent = 0
    for cand in store.list_candidates():
        if cand.edge_pct + 1e-9 < settings.min_edge * 100.0:
            continue
        if cand.event_id in seen_events:
            continue
        commence = cand.commence_at
        if commence is None:
            continue
        delta = commence - now
        if not (lo <= delta <= hi):
            continue
        seen_events.add(cand.event_id)
        mins = int(delta.total_seconds() // 60)
        line = "" if cand.point is None else f" {cand.point}"
        alert = Alert(
            type=AlertType.PRE_GAME_REMINDER,
            title=f"Pre-game ({mins}m): {cand.event_name}",
            body=(
                f"Flagged {cand.selection}{line} {display_market(cand.market)} @ {cand.american_odds:+d} "
                f"({cand.edge_pct:.1f}% edge). Tip {commence.isoformat()}. "
                "Confirm on FanDuel mobile yourself — HQ does not place bets."
            ),
            dedup_key=f"pre_game:{cand.event_id}:{settings.remind_min_minutes}-{settings.remind_max_minutes}",
            payload={"event_id": cand.event_id, "commence_at": commence.isoformat()},
        )
        if bus.publish(alert):
            sent += 1
    return sent


def _settle_reminders(store: Store, bus: AlertBus, now: datetime) -> int:
    sent = 0
    for bet in store.open_bets():
        if bet.commence_at is None or bet.commence_at > now:
            continue
        alert = Alert(
            type=AlertType.SETTLE_REMINDER,
            title=f"Settle reminder: {bet.event_name}",
            body=(
                f"Open ticket {bet.id} {bet.selection} {_fmt_odds(bet.odds_at_bet)} "
                f"has commenced. Run: sporty settle {bet.id} --result win|loss|push "
                "--close-odds … [--postmortem … --lesson …]"
            ),
            dedup_key=f"settle:{bet.id}",
            payload={"bet_id": bet.id, "event_id": bet.event_id},
        )
        if bus.publish(alert):
            sent += 1
    return sent


def _fmt_odds(value: int | None) -> str:
    if value is None:
        return "—"
    return f"{value:+d}"


if __name__ == "__main__":  # pragma: no cover
    app()
