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
            fired_at TEXT NOT NULL,
            trigger_price REAL
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

        CREATE TABLE IF NOT EXISTS custom_watchlists (
            name TEXT PRIMARY KEY,
            tickers_json TEXT NOT NULL,
            source_url TEXT,
            as_of_date TEXT,
            fetched_at TEXT NOT NULL,
            meta_json TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_snapshot (
            date TEXT PRIMARY KEY,
            spx_signal TEXT,
            spx_column TEXT,
            spx_level REAL,
            spx_change REAL,
            bpnya_column TEXT,
            bpnya_level REAL,
            bpnya_change REAL,
            vix_column TEXT,
            vix_level REAL,
            vix_change REAL,
            regime TEXT,
            risk TEXT,
            updated_at TEXT NOT NULL
        );
        """)
        # Migrations for existing DBs.
        cols = [r[1] for r in c.execute("PRAGMA table_info(entry_alerts)")]
        if "trigger_price" not in cols:
            c.execute("ALTER TABLE entry_alerts ADD COLUMN trigger_price REAL")
        # BPNYA gained real OHLC (previously we only stored a single pct = close).
        # Add the three new columns idempotently; historical rows leave them NULL
        # and read paths fall back to (open=high=low=close=pct) for those rows.
        # `source` distinguishes app-generated estimates ('scan') from user-provided
        # authoritative data ('import' via CSV, 'manual' via daily-entry form). The
        # scan will not overwrite a row whose source is anything other than 'scan'.
        bp_cols = [r[1] for r in c.execute("PRAGMA table_info(bpnya_history)")]
        for col in ("open", "high", "low"):
            if col not in bp_cols:
                c.execute(f"ALTER TABLE bpnya_history ADD COLUMN {col} REAL")
        if "source" not in bp_cols:
            c.execute("ALTER TABLE bpnya_history ADD COLUMN source TEXT DEFAULT 'scan'")


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


def mark_entry_alerted(
    ticker: str, direction: str,
    when: Optional[dt.datetime] = None,
    trigger_price: Optional[float] = None,
) -> None:
    when = when or dt.datetime.now(dt.timezone.utc)
    with _connect() as c:
        c.execute(
            "INSERT INTO entry_alerts (ticker, direction, fired_at, trigger_price) VALUES (?, ?, ?, ?)",
            (ticker, direction, when.isoformat(), trigger_price),
        )


def get_entry_alerts_all() -> list:
    """All entry alerts, newest first."""
    with _connect() as c:
        rows = c.execute(
            "SELECT id, ticker, direction, fired_at, trigger_price FROM entry_alerts ORDER BY fired_at DESC"
        ).fetchall()
    return [
        {"id": r[0], "ticker": r[1], "direction": r[2], "fired_at": r[3], "trigger_price": r[4]}
        for r in rows
    ]


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


def upsert_bpnya(
    date_str: str,
    pct: float,
    universe_size: int,
    open_: Optional[float] = None,
    high: Optional[float] = None,
    low: Optional[float] = None,
    source: str = "scan",
) -> None:
    """Upsert one BPNYA daily row. `pct` is the close. OHLC columns are
    optional — if omitted, the read path treats open=high=low=close=pct.

    `source` = 'scan' (auto-generated from the scan universe) | 'import' (bulk
    CSV) | 'manual' (single-day form). Auto-scan writes with source='scan' and
    will NEVER overwrite a row whose current source is 'import' or 'manual' —
    user-provided StockCharts data always wins.
    """
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    # Two update policies:
    #   source='scan'  → update only if existing row's source is 'scan' (or NULL)
    #   source='import' or 'manual' → always update (user override)
    with _connect() as c:
        if source == "scan":
            c.execute(
                """
                INSERT INTO bpnya_history
                    (date, pct, universe_size, updated_at, open, high, low, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    pct           = CASE WHEN bpnya_history.source IN ('import','manual')
                                         THEN bpnya_history.pct ELSE excluded.pct END,
                    universe_size = CASE WHEN bpnya_history.source IN ('import','manual')
                                         THEN bpnya_history.universe_size ELSE excluded.universe_size END,
                    updated_at    = CASE WHEN bpnya_history.source IN ('import','manual')
                                         THEN bpnya_history.updated_at ELSE excluded.updated_at END,
                    open          = COALESCE(bpnya_history.open, excluded.open),
                    high          = COALESCE(bpnya_history.high, excluded.high),
                    low           = COALESCE(bpnya_history.low,  excluded.low)
                """,
                (date_str, pct, universe_size, now_iso, open_, high, low, source),
            )
        else:
            # import/manual — always wins; also promote source label.
            c.execute(
                """
                INSERT INTO bpnya_history
                    (date, pct, universe_size, updated_at, open, high, low, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    pct           = excluded.pct,
                    universe_size = excluded.universe_size,
                    updated_at    = excluded.updated_at,
                    open          = COALESCE(excluded.open, bpnya_history.open),
                    high          = COALESCE(excluded.high, bpnya_history.high),
                    low           = COALESCE(excluded.low,  bpnya_history.low),
                    source        = excluded.source
                """,
                (date_str, pct, universe_size, now_iso, open_, high, low, source),
            )


def get_bpnya_history() -> list:
    """Returns [(date_str, pct), ...] ordered oldest-first.
    Kept for backwards-compat; new callers should use get_bpnya_history_ohlc().
    """
    with _connect() as c:
        return [(r[0], r[1]) for r in c.execute(
            "SELECT date, pct FROM bpnya_history ORDER BY date ASC"
        )]


def get_bpnya_history_ohlc() -> list:
    """Full OHLC history. Returns [(date_str, open, high, low, close), ...]
    ordered oldest-first. Missing OHLC columns fall back to close."""
    with _connect() as c:
        rows = c.execute(
            "SELECT date, open, high, low, pct FROM bpnya_history ORDER BY date ASC"
        ).fetchall()
    return [
        (r[0],
         (r[1] if r[1] is not None else r[4]),
         (r[2] if r[2] is not None else r[4]),
         (r[3] if r[3] is not None else r[4]),
         r[4])
        for r in rows
    ]


def get_bpnya_latest() -> Optional[dict]:
    with _connect() as c:
        row = c.execute(
            "SELECT date, pct, universe_size FROM bpnya_history ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return {"date": row[0], "pct": row[1], "universe_size": row[2]}


# --- Daily snapshot -------------------------------------------------------

_SNAPSHOT_COLS = [
    "date",
    "spx_signal", "spx_column", "spx_level", "spx_change",
    "bpnya_column", "bpnya_level", "bpnya_change",
    "vix_column", "vix_level", "vix_change",
    "regime", "risk",
]


def upsert_daily_snapshot(payload: dict) -> None:
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    values = tuple(payload.get(c) for c in _SNAPSHOT_COLS) + (now_iso,)
    with _connect() as c:
        c.execute(
            f"""
            INSERT INTO daily_snapshot ({', '.join(_SNAPSHOT_COLS)}, updated_at)
            VALUES ({', '.join(['?'] * (len(_SNAPSHOT_COLS) + 1))})
            ON CONFLICT(date) DO UPDATE SET
                spx_signal    = excluded.spx_signal,
                spx_column    = excluded.spx_column,
                spx_level     = excluded.spx_level,
                spx_change    = excluded.spx_change,
                bpnya_column  = excluded.bpnya_column,
                bpnya_level   = excluded.bpnya_level,
                bpnya_change  = excluded.bpnya_change,
                vix_column    = excluded.vix_column,
                vix_level     = excluded.vix_level,
                vix_change    = excluded.vix_change,
                regime        = excluded.regime,
                risk          = excluded.risk,
                updated_at    = excluded.updated_at
            """,
            values,
        )


# --- Custom watchlists (IBD 50, etc.) ------------------------------------

def upsert_watchlist(
    name: str,
    tickers: list,
    source_url: Optional[str] = None,
    as_of_date: Optional[str] = None,
    meta: Optional[dict] = None,
) -> None:
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    with _connect() as c:
        c.execute(
            """
            INSERT INTO custom_watchlists
                (name, tickers_json, source_url, as_of_date, fetched_at, meta_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                tickers_json = excluded.tickers_json,
                source_url   = excluded.source_url,
                as_of_date   = excluded.as_of_date,
                fetched_at   = excluded.fetched_at,
                meta_json    = excluded.meta_json
            """,
            (
                name,
                json.dumps(sorted(set(tickers))),
                source_url,
                as_of_date,
                now_iso,
                json.dumps(meta) if meta is not None else None,
            ),
        )


def get_watchlist(name: str) -> Optional[dict]:
    with _connect() as c:
        row = c.execute(
            "SELECT name, tickers_json, source_url, as_of_date, fetched_at, meta_json "
            "FROM custom_watchlists WHERE name=?",
            (name,),
        ).fetchone()
    if not row:
        return None
    return {
        "name": row[0],
        "tickers": json.loads(row[1] or "[]"),
        "source_url": row[2],
        "as_of_date": row[3],
        "fetched_at": row[4],
        "meta": json.loads(row[5]) if row[5] else None,
    }


def get_daily_history(limit: int = 200) -> list:
    with _connect() as c:
        rows = c.execute(
            f"""
            SELECT {', '.join(_SNAPSHOT_COLS)}, updated_at
            FROM daily_snapshot
            ORDER BY date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    keys = _SNAPSHOT_COLS + ["updated_at"]
    return [dict(zip(keys, r)) for r in rows]
