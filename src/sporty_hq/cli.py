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
from sporty_hq.config import Settings, load_settings
from sporty_hq.engine import ScanConfig, score_quotes
from sporty_hq.models import Alert, AlertType, Bet, Candidate, display_market, normalize_market
from sporty_hq.odds_math import clv_pct, parse_american, settle_pnl
from sporty_hq.playbook import PlaybookViolation, session_snapshot, validate_new_bet
from sporty_hq.providers import load_provider
from sporty_hq.reports import render_html, render_markdown, summarize
from sporty_hq.session import persist_live_session, read_session, session_row_to_bet
from sporty_hq.storage import Store, utcnow

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
    source: str = typer.Option("fixture", "--source", help="fixture | oddsapi"),
    path: Optional[Path] = typer.Option(None, "--path", help="JSON or CSV fixture path"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", envvar="SPORTY_HQ_DATA_DIR"),
) -> None:
    """Load odds from a fixture file or The Odds API (key via env)."""
    settings = _settings(data_dir)
    store = _store(settings)
    api_key = (
        settings.the_odds_api_key.get_secret_value()
        if settings.secret_configured("the_odds_api_key")
        else None
    )
    fixture_path = path or (DEFAULT_FIXTURE if source == "fixture" else None)
    provider = load_provider(source, path=fixture_path, api_key=api_key)
    quotes = provider.fetch_quotes()
    if not quotes:
        raise typer.BadParameter("Ingest produced 0 quotes")
    batch_id = _short_id()
    store.insert_quotes(batch_id, provider.name, quotes)
    books = sorted({q.book for q in quotes})
    events = sorted({q.event_id for q in quotes})
    console.print(
        f"Ingested [bold]{len(quotes)}[/bold] quotes "
        f"({len(events)} events, books: {', '.join(books)}) batch={batch_id}"
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
    """Score latest ingest vs consensus; print ranked candidates ≥ min edge."""
    settings = _settings(data_dir, min_edge)
    store = _store(settings)
    batch_id = store.latest_batch_id()
    if not batch_id:
        raise typer.Exit("No odds ingested yet. Run: sporty ingest --source fixture")
    quotes = store.quotes_for_batch(batch_id)
    config = ScanConfig(
        target_book=book or settings.target_book,
        min_edge=settings.min_edge,
    )
    candidates = score_quotes(quotes, config)
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
    force: bool = typer.Option(False, "--force", help="Override session cap / stop"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", envvar="SPORTY_HQ_DATA_DIR"),
) -> None:
    """Append a bet you placed on mobile. HQ never places it for you."""
    settings = _settings(data_dir)
    store = _store(settings)
    now = utcnow()
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
    stake_v = settings.unit_stake if stake is None else stake
    try:
        validate_new_bet(
            store,
            settings,
            event_id=event_id_v,
            market=market_v,
            stake=stake_v,
            now=now,
            force=force,
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
    )
    store.insert_bet(bet)
    _sync_session(settings, store, now)
    if abs(stake_v - settings.unit_stake) > 1e-9:
        console.print(
            f"[yellow]Note:[/yellow] playbook unit is ${settings.unit_stake:.0f}; logged ${stake_v:.2f}."
        )
    snap = session_snapshot(store, settings, now)
    console.print(
        f"Logged bet [bold]{bet.id}[/bold] {bet.event_name} {display_market(bet.market)} "
        f"{bet.selection} {_fmt_odds(bet.odds_at_bet)} stake ${bet.stake:.0f}"
    )
    console.print(
        f"Session {snap.start.date()}: {snap.bets_logged}/{settings.max_bets_per_session} bets, "
        f"realized ${snap.realized_pnl:+.2f}, open risk ${snap.open_risk:.0f}, "
        f"worst-case ${snap.worst_case_pnl:+.2f} (stop {settings.session_stop:.0f})"
    )


@app.command()
def settle(
    bet_id: str = typer.Argument(..., help="Bet id from log-bet"),
    result: str = typer.Option(..., "--result", help="win | loss | push | void"),
    close_odds: Optional[str] = typer.Option(None, "--close-odds", help="American close, e.g. +145"),
    notes: Optional[str] = typer.Option(None, "--edge-note", "--notes"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", envvar="SPORTY_HQ_DATA_DIR"),
) -> None:
    """Settle a logged bet and compute P&L + CLV vs close."""
    settings = _settings(data_dir)
    store = _store(settings)
    bet = store.get_bet(bet_id)
    if bet is None:
        raise typer.Exit(f"Unknown bet id {bet_id}")
    try:
        bet.pnl = settle_pnl(result, bet.stake, bet.odds_at_bet)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    bet.result = result.strip().lower()
    if close_odds is not None:
        bet.close_odds = parse_american(close_odds)
        bet.clv_pct = round(clv_pct(bet.odds_at_bet, bet.close_odds), 2)
    bet.settled_at = utcnow()
    if notes:
        bet.edge_note = (bet.edge_note + " | " if bet.edge_note else "") + notes
    store.update_bet(bet)
    _sync_session(settings, store)
    clv_txt = "0" if bet.clv_pct == 0 else ("—" if bet.clv_pct is None else f"{bet.clv_pct:+.2f}%")
    console.print(
        f"Settled {bet.id} {bet.result} pnl ${bet.pnl:+.2f} CLV {clv_txt} "
        f"(bet {_fmt_odds(bet.odds_at_bet)} close {_fmt_odds(bet.close_odds)})"
    )


@app.command("clv-report")
def clv_report(
    fmt: str = typer.Option("md", "--format", help="md | html | json"),
    out: Optional[Path] = typer.Option(None, "--out", help="Write file instead of stdout"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", envvar="SPORTY_HQ_DATA_DIR"),
) -> None:
    """Cumulative CLV, win rate, and P&L dashboard."""
    settings = _settings(data_dir)
    store = _store(settings)
    bets = store.list_bets()
    kind = fmt.strip().lower()
    if kind in {"md", "markdown"}:
        text = render_markdown(bets, settings.unit_stake, judge_n=settings.clv_judge_n)
    elif kind == "html":
        text = render_html(bets, settings.unit_stake, judge_n=settings.clv_judge_n)
    elif kind == "json":
        summary = summarize(bets, settings.unit_stake)
        text = json.dumps(
            {"summary": summary.__dict__, "bets": [b.to_row() for b in bets]},
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
    bus = AlertBus.from_settings(store, settings)
    now = utcnow()
    sent = 0
    wanted = kind.strip().lower()
    if wanted not in {"pre_game", "settle", "all"}:
        raise typer.BadParameter("--type must be pre_game, settle, or all")

    if wanted in {"pre_game", "all"}:
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
        ScanConfig(target_book=settings.target_book, min_edge=settings.min_edge),
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
                f"has commenced. Run: sporty settle {bet.id} --result win|loss|push --close-odds …"
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
