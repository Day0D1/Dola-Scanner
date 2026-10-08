"""SQLite persistence (stdlib sqlite3, one connection per call, WAL mode)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .config import settings

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS setups (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'live',      -- live | backtest
    run_id INTEGER,
    pair TEXT NOT NULL, timeframe TEXT NOT NULL, htf TEXT,
    direction TEXT NOT NULL,                  -- BUY | SELL
    decision TEXT NOT NULL,                   -- APPROVED | REJECTED | WATCH
    status TEXT NOT NULL,                     -- PENDING TRIGGERED WIN LOSS EXPIRED INVALIDATED MISSED REJECTED WATCH
    reject_reason TEXT,
    poi_type TEXT, liquidity_type TEXT, liquidity_shape TEXT, liquidity_depth REAL,
    entry REAL, stop_loss REAL, take_profit REAL, rr REAL, structure_rr REAL,
    protected_price REAL, protected_time INTEGER, bos_price REAL, bos_time INTEGER,
    range_extreme REAL, equilibrium REAL,
    htf_zone TEXT, sweep_type TEXT,
    lots REAL, stop_pips REAL, risk_amount REAL,
    result_r REAL, pnl REAL,
    detected_bar_time INTEGER NOT NULL,
    detected_at INTEGER NOT NULL,
    triggered_at INTEGER, closed_at INTEGER, last_bar_time INTEGER,
    alerted INTEGER DEFAULT 0, suppressed INTEGER DEFAULT 0,
    details TEXT,
    updated_at INTEGER NOT NULL,
    UNIQUE(uid, source, run_id)
);
CREATE INDEX IF NOT EXISTS ix_setups_status ON setups(source, status);
CREATE INDEX IF NOT EXISTS ix_setups_pair ON setups(pair, timeframe);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, setup_id INTEGER, ts INTEGER NOT NULL, kind TEXT NOT NULL, message TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_setup ON events(setup_id);
CREATE TABLE IF NOT EXISTS backtest_runs (
    id INTEGER PRIMARY KEY, started_at INTEGER, finished_at INTEGER, status TEXT,
    params TEXT, stats TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, duration REAL, pairs INTEGER,
    candidates INTEGER, approved INTEGER, errors TEXT
);
"""


def _path() -> str:
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    return settings.db_path


@contextmanager
def connect():
    con = sqlite3.connect(_path(), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        with _lock:
            yield con
            con.commit()
    finally:
        con.close()


MIGRATIONS = {   # columns added after the first release: name -> type
    "partial_price": "REAL",
    "partial_at": "INTEGER",
}


def init() -> None:
    with connect() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        have = {r["name"] for r in con.execute("PRAGMA table_info(setups)")}
        for col, typ in MIGRATIONS.items():
            if col not in have:
                con.execute(f"ALTER TABLE setups ADD COLUMN {col} {typ}")
    from . import accounts      # trading accounts + per-account trade ledger
    accounts.init()


def now() -> int:
    return int(time.time())


def rows(sql: str, params: tuple | dict = ()) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def one(sql: str, params: tuple | dict = ()) -> dict | None:
    r = rows(sql, params)
    return r[0] if r else None


def execute(sql: str, params: tuple | dict = ()) -> int:
    with connect() as con:
        cur = con.execute(sql, params)
        return cur.lastrowid


# ---------------------------------------------------------------- key/value settings

def kv_get(key: str, default=None):
    r = one("SELECT value FROM kv WHERE key=?", (key,))
    return json.loads(r["value"]) if r else default


def kv_set(key: str, value) -> None:
    execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)))


# ---------------------------------------------------------------- setups

SETUP_COLS = [
    "uid", "source", "run_id", "pair", "timeframe", "htf", "direction", "decision", "status",
    "reject_reason", "poi_type", "liquidity_type", "liquidity_shape", "liquidity_depth", "entry",
    "stop_loss", "take_profit", "rr", "structure_rr", "protected_price", "protected_time",
    "bos_price", "bos_time", "range_extreme", "equilibrium", "htf_zone", "sweep_type", "lots",
    "stop_pips", "risk_amount", "result_r", "pnl", "detected_bar_time", "detected_at",
    "triggered_at", "closed_at", "last_bar_time", "alerted", "suppressed", "details", "updated_at",
    "partial_price", "partial_at",
]


def get_setup(uid: str, source: str = "live", run_id: int | None = None) -> dict | None:
    return one("SELECT * FROM setups WHERE uid=? AND source=? AND run_id IS ?", (uid, source, run_id))


def insert_setup(rec: dict) -> int:
    rec = {k: rec.get(k) for k in SETUP_COLS}
    if isinstance(rec.get("details"), (dict, list)):
        rec["details"] = json.dumps(rec["details"])
    rec["updated_at"] = now()
    cols = ",".join(rec)
    return execute(f"INSERT INTO setups({cols}) VALUES({','.join('?' * len(rec))})", tuple(rec.values()))


def update_setup(setup_id: int, **fields) -> None:
    if "details" in fields and isinstance(fields["details"], (dict, list)):
        fields["details"] = json.dumps(fields["details"])
    fields["updated_at"] = now()
    sets = ",".join(f"{k}=?" for k in fields)
    execute(f"UPDATE setups SET {sets} WHERE id=?", (*fields.values(), setup_id))


def bulk_insert_setups(recs: list[dict]) -> None:
    if not recs:
        return
    ts = now()
    vals = []
    for rec in recs:
        r = {k: rec.get(k) for k in SETUP_COLS}
        if isinstance(r.get("details"), (dict, list)):
            r["details"] = json.dumps(r["details"])
        r["updated_at"] = ts
        vals.append(tuple(r.values()))
    with connect() as con:
        con.executemany(f"INSERT OR REPLACE INTO setups({','.join(SETUP_COLS)}) "
                        f"VALUES({','.join('?' * len(SETUP_COLS))})", vals)


def add_event(setup_id: int | None, kind: str, message: str) -> None:
    execute("INSERT INTO events(setup_id, ts, kind, message) VALUES(?,?,?,?)",
            (setup_id, now(), kind, message))
