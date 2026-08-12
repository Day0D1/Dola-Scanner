"""SQLite state store: scan history + entry-alert de-duplication."""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from scanner.breadth import BreadthReading
from scanner.signals import StockSignal


DATA_DIR = Path(os.getenv("DATA_DIR") or (Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "state.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS scan_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_at TEXT NOT NULL,
            breadth_verdict TEXT,
            breadth_spx TEXT,
            breadth_vix TEXT,
            breadth_bpnya TEXT,
            signals_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_scan_history_at ON scan_history(scan_at);

        CREATE TABLE IF NOT EXISTS entry_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,
            fired_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_entry_alerts_ticker_at
            ON entry_alerts(ticker, direction, fired_at);

        CREATE TABLE IF NOT EXISTS candidate_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            candidate_type TEXT NOT NULL,
            fired_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_candidate_alerts_ticker_at
            ON candidate_alerts(ticker, candidate_type, fired_at);

        CREATE TABLE IF NOT EXISTS bpnya_history (
            date TEXT PRIMARY KEY,
            pct REAL NOT NULL,
            universe_size INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
        """)


def record_scan(scan_at: dt.datetime, breadth: BreadthReading, signals: Iterable[StockSignal]) -> int:
    payload = [
        {
            "ticker": s.ticker,
            "last_close": s.last_close,
            "rsi": None if s.rsi != s.rsi else s.rsi,  # NaN-safe
            "bb_upper": s.bb_upper,
            "bb_middle": s.bb_middle,
            "bb_lower": s.bb_lower,
            "pnf_column": s.pnf_column,
            "candidate": s.candidate,
            "entry_trigger": s.entry_trigger,
            "band_pierce_today": s.band_pierce_today,
        }
        for s in signals
    ]
    with _connect() as c:
        cur = c.execute(
            """
            INSERT INTO scan_history
                (scan_at, breadth_verdict, breadth_spx, breadth_vix, breadth_bpnya, signals_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                scan_at.isoformat(),
                breadth.verdict,
                breadth.spx_trend,
                breadth.vix_trend,
                breadth.bpnya_trend,
                json.dumps(payload),
            ),
        )
        return cur.lastrowid or 0


def was_entry_alerted_recently(ticker: str, direction: str, within_hours: int = 24, now: Optional[dt.datetime] = None) -> bool:
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = (now - dt.timedelta(hours=within_hours)).isoformat()
    with _connect() as c:
        row = c.execute(
            "SELECT 1 FROM entry_alerts WHERE ticker=? AND direction=? AND fired_at>=? LIMIT 1",
            (ticker, direction, cutoff),
        ).fetchone()
        return row is not None


def mark_entry_alerted(ticker: str, direction: str, when: Optional[dt.datetime] = None) -> None:
    when = when or dt.datetime.now(dt.timezone.utc)
    with _connect() as c:
        c.execute(
            "INSERT INTO entry_alerts (ticker, direction, fired_at) VALUES (?, ?, ?)",
            (ticker, direction, when.isoformat()),
        )


def was_candidate_alerted_recently(ticker: str, candidate_type: str, within_hours: int = 24, now: Optional[dt.datetime] = None) -> bool:
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = (now - dt.timedelta(hours=within_hours)).isoformat()
    with _connect() as c:
        row = c.execute(
            "SELECT 1 FROM candidate_alerts WHERE ticker=? AND candidate_type=? AND fired_at>=? LIMIT 1",
            (ticker, candidate_type, cutoff),
        ).fetchone()
        return row is not None


def mark_candidate_alerted(ticker: str, candidate_type: str, when: Optional[dt.datetime] = None) -> None:
    when = when or dt.datetime.now(dt.timezone.utc)
    with _connect() as c:
        c.execute(
            "INSERT INTO candidate_alerts (ticker, candidate_type, fired_at) VALUES (?, ?, ?)",
            (ticker, candidate_type, when.isoformat()),
        )


def upsert_bpnya(date_str: str, pct: float, universe_size: int) -> None:
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    with _connect() as c:
        c.execute(
            """
            INSERT INTO bpnya_history (date, pct, universe_size, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                pct = excluded.pct,
                universe_size = excluded.universe_size,
                updated_at = excluded.updated_at
            """,
            (date_str, pct, universe_size, now_iso),
        )


def get_bpnya_history() -> list:
    """Returns [(date_str, pct), ...] ordered oldest-first."""
    with _connect() as c:
        return [(r[0], r[1]) for r in c.execute(
            "SELECT date, pct FROM bpnya_history ORDER BY date ASC"
        )]


def get_bpnya_latest() -> Optional[dict]:
    with _connect() as c:
        row = c.execute(
            "SELECT date, pct, universe_size FROM bpnya_history ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return {"date": row[0], "pct": row[1], "universe_size": row[2]}
