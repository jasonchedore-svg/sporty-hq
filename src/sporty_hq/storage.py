"""SQLite persistence for quotes, candidates, bets, and alert dedup."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from sporty_hq.models import Bet, Candidate, Quote


SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_batches (
    id TEXT PRIMARY KEY,
    ingested_at TEXT NOT NULL,
    source TEXT NOT NULL,
    quote_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY,
    batch_id TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    source TEXT NOT NULL,
    event_id TEXT NOT NULL,
    sport TEXT NOT NULL,
    commence_at TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    book TEXT NOT NULL,
    market TEXT NOT NULL,
    selection TEXT NOT NULL,
    american_odds INTEGER NOT NULL,
    point REAL,
    FOREIGN KEY (batch_id) REFERENCES ingest_batches(id)
);

CREATE INDEX IF NOT EXISTS idx_quotes_batch ON quotes(batch_id);
CREATE INDEX IF NOT EXISTS idx_quotes_event ON quotes(event_id);

CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY,
    scanned_at TEXT NOT NULL,
    batch_id TEXT,
    event_id TEXT NOT NULL,
    sport TEXT NOT NULL,
    event_name TEXT NOT NULL,
    commence_at TEXT NOT NULL,
    market TEXT NOT NULL,
    selection TEXT NOT NULL,
    point REAL,
    book TEXT NOT NULL,
    american_odds INTEGER NOT NULL,
    decimal_odds REAL NOT NULL,
    fair_prob REAL NOT NULL,
    implied_prob REAL NOT NULL,
    edge_pct REAL NOT NULL,
    juice_pct REAL NOT NULL,
    rationale TEXT NOT NULL,
    consensus_books TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS bets (
    id TEXT PRIMARY KEY,
    logged_at TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_name TEXT NOT NULL,
    sport TEXT NOT NULL,
    commence_at TEXT,
    market TEXT NOT NULL,
    selection TEXT NOT NULL,
    point REAL,
    odds_at_bet INTEGER NOT NULL,
    stake REAL NOT NULL,
    result TEXT,
    close_odds INTEGER,
    clv_pct REAL,
    pnl REAL,
    edge_note TEXT NOT NULL DEFAULT '',
    settled_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_bets_event ON bets(event_id);
CREATE INDEX IF NOT EXISTS idx_bets_logged ON bets(logged_at);

CREATE TABLE IF NOT EXISTS alerts_sent (
    id INTEGER PRIMARY KEY,
    dedup_key TEXT NOT NULL UNIQUE,
    alert_type TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}'
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            _migrate_bets(conn)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def insert_quotes(self, batch_id: str, source: str, quotes: list[Quote]) -> None:
        ingested_at = utcnow().isoformat()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO ingest_batches (id, ingested_at, source, quote_count) VALUES (?, ?, ?, ?)",
                (batch_id, ingested_at, source, len(quotes)),
            )
            conn.executemany(
                """
                INSERT INTO quotes (
                    batch_id, ingested_at, source, event_id, sport, commence_at,
                    home_team, away_team, book, market, selection, american_odds, point
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        batch_id,
                        ingested_at,
                        q.source,
                        q.event_id,
                        q.sport,
                        q.commence_at.isoformat(),
                        q.home_team,
                        q.away_team,
                        q.book,
                        q.market,
                        q.selection,
                        q.american_odds,
                        q.point,
                    )
                    for q in quotes
                ],
            )

    def latest_batch_id(self) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM ingest_batches ORDER BY ingested_at DESC LIMIT 1"
            ).fetchone()
        return None if row is None else str(row["id"])

    def quotes_for_batch(self, batch_id: str) -> list[Quote]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM quotes WHERE batch_id = ? ORDER BY event_id, book, market",
                (batch_id,),
            ).fetchall()
        return [_quote_from_row(row) for row in rows]

    def replace_candidates(self, batch_id: str | None, candidates: list[Candidate]) -> list[Candidate]:
        scanned_at = utcnow()
        with self.connect() as conn:
            conn.execute("DELETE FROM candidates")
            stored: list[Candidate] = []
            for cand in candidates:
                cand.scanned_at = scanned_at
                cur = conn.execute(
                    """
                    INSERT INTO candidates (
                        scanned_at, batch_id, event_id, sport, event_name, commence_at,
                        market, selection, point, book, american_odds, decimal_odds,
                        fair_prob, implied_prob, edge_pct, juice_pct, rationale, consensus_books
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scanned_at.isoformat(),
                        batch_id,
                        cand.event_id,
                        cand.sport,
                        cand.event_name,
                        cand.commence_at.isoformat(),
                        cand.market,
                        cand.selection,
                        cand.point,
                        cand.book,
                        cand.american_odds,
                        cand.decimal_odds,
                        cand.fair_prob,
                        cand.implied_prob,
                        cand.edge_pct,
                        cand.juice_pct,
                        cand.rationale,
                        json.dumps(cand.consensus_books),
                    ),
                )
                cand.id = int(cur.lastrowid)
                stored.append(cand)
        return stored

    def list_candidates(self) -> list[Candidate]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM candidates ORDER BY edge_pct DESC, id ASC"
            ).fetchall()
        return [_candidate_from_row(row) for row in rows]

    def get_candidate(self, candidate_id: int) -> Candidate | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
        return None if row is None else _candidate_from_row(row)

    def insert_bet(self, bet: Bet) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO bets (
                    id, logged_at, event_id, event_name, sport, commence_at, market,
                    selection, point, odds_at_bet, stake, result, close_odds, clv_pct,
                    pnl, edge_note, settled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bet.id,
                    bet.logged_at.isoformat(),
                    bet.event_id,
                    bet.event_name,
                    bet.sport,
                    bet.commence_at.isoformat() if bet.commence_at else None,
                    bet.market,
                    bet.selection,
                    bet.point,
                    bet.odds_at_bet,
                    bet.stake,
                    bet.result,
                    bet.close_odds,
                    bet.clv_pct,
                    bet.pnl,
                    bet.edge_note,
                    bet.settled_at.isoformat() if bet.settled_at else None,
                ),
            )

    def update_bet(self, bet: Bet) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE bets SET result=?, close_odds=?, clv_pct=?, pnl=?, edge_note=?, settled_at=?
                WHERE id=?
                """,
                (
                    bet.result,
                    bet.close_odds,
                    bet.clv_pct,
                    bet.pnl,
                    bet.edge_note,
                    bet.settled_at.isoformat() if bet.settled_at else None,
                    bet.id,
                ),
            )

    def get_bet(self, bet_id: str) -> Bet | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM bets WHERE id = ?", (bet_id,)).fetchone()
        return None if row is None else _bet_from_row(row)

    def list_bets(self) -> list[Bet]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM bets ORDER BY logged_at ASC").fetchall()
        return [_bet_from_row(row) for row in rows]

    def open_bets(self) -> list[Bet]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM bets WHERE result IS NULL ORDER BY logged_at ASC"
            ).fetchall()
        return [_bet_from_row(row) for row in rows]

    def open_bets_for_event(self, event_id: str) -> list[Bet]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM bets WHERE event_id = ? AND result IS NULL",
                (event_id,),
            ).fetchall()
        return [_bet_from_row(row) for row in rows]

    def bets_logged_since(self, start: datetime) -> list[Bet]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM bets WHERE logged_at >= ? ORDER BY logged_at ASC",
                (start.isoformat(),),
            ).fetchall()
        return [_bet_from_row(row) for row in rows]

    def claim_alert(self, dedup_key: str, alert_type: str, payload: dict[str, Any]) -> bool:
        """Return True if this key is new (alert should send)."""
        try:
            with self.connect() as conn:
                conn.execute(
                    "INSERT INTO alerts_sent (dedup_key, alert_type, sent_at, payload) VALUES (?, ?, ?, ?)",
                    (dedup_key, alert_type, utcnow().isoformat(), json.dumps(payload)),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def alert_was_sent(self, dedup_key: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM alerts_sent WHERE dedup_key = ?", (dedup_key,)
            ).fetchone()
        return row is not None


def _row_edge_note(row: sqlite3.Row) -> str:
    keys = row.keys()
    if "edge_note" in keys:
        return row["edge_note"] or ""
    if "notes" in keys:
        return row["notes"] or ""
    return ""


def _migrate_bets(conn: sqlite3.Connection) -> None:
    names = {r[1] for r in conn.execute("PRAGMA table_info(bets)").fetchall()}
    if "notes" in names and "edge_note" not in names:
        conn.execute("ALTER TABLE bets RENAME COLUMN notes TO edge_note")


def _quote_from_row(row: sqlite3.Row) -> Quote:
    commence = parse_dt(row["commence_at"])
    assert commence is not None
    return Quote(
        event_id=row["event_id"],
        sport=row["sport"],
        commence_at=commence,
        home_team=row["home_team"],
        away_team=row["away_team"],
        book=row["book"],
        market=row["market"],
        selection=row["selection"],
        american_odds=int(row["american_odds"]),
        point=row["point"],
        source=row["source"],
    )


def _candidate_from_row(row: sqlite3.Row) -> Candidate:
    commence = parse_dt(row["commence_at"])
    assert commence is not None
    books_raw = row["consensus_books"]
    books = json.loads(books_raw) if books_raw else []
    return Candidate(
        id=int(row["id"]),
        scanned_at=parse_dt(row["scanned_at"]),
        event_id=row["event_id"],
        sport=row["sport"],
        event_name=row["event_name"],
        commence_at=commence,
        market=row["market"],
        selection=row["selection"],
        point=row["point"],
        book=row["book"],
        american_odds=int(row["american_odds"]),
        decimal_odds=float(row["decimal_odds"]),
        fair_prob=float(row["fair_prob"]),
        implied_prob=float(row["implied_prob"]),
        edge_pct=float(row["edge_pct"]),
        juice_pct=float(row["juice_pct"]),
        rationale=row["rationale"],
        consensus_books=list(books),
    )


def _bet_from_row(row: sqlite3.Row) -> Bet:
    return Bet(
        id=row["id"],
        logged_at=parse_dt(row["logged_at"]) or utcnow(),
        event_id=row["event_id"],
        event_name=row["event_name"],
        sport=row["sport"],
        commence_at=parse_dt(row["commence_at"]),
        market=row["market"],
        selection=row["selection"],
        point=row["point"],
        odds_at_bet=int(row["odds_at_bet"]),
        stake=float(row["stake"]),
        result=row["result"],
        close_odds=int(row["close_odds"]) if row["close_odds"] is not None else None,
        clv_pct=float(row["clv_pct"]) if row["clv_pct"] is not None else None,
        pnl=float(row["pnl"]) if row["pnl"] is not None else None,
        edge_note=_row_edge_note(row),
        settled_at=parse_dt(row["settled_at"]),
    )
