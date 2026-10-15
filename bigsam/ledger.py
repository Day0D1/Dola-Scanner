"""The *actual* ledger: trades you really took, plus deposits / withdrawals.

``account_trades`` is the model (every alert, as if all were taken). This module records reality:

* ``real_trades`` - trades you took. Linked ones (``account_trade_id``) follow their alert: entry,
  exit and P&L fill in from the model unless you typed your own P&L (``pnl_manual``). Unlinked ones
  are entered by hand (e.g. from the broker's history).
* ``cash_moves`` - deposits (+), withdrawals (-) and balance adjustments.

Actual balance = ``actual_start`` + closed P&L + cash moves.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from . import db
from .config import _env

log = logging.getLogger("bigsam.ledger")

SCHEMA = """
CREATE TABLE IF NOT EXISTS real_trades (
    id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, setup_id INTEGER, account_trade_id INTEGER UNIQUE,
    ticket TEXT, pair TEXT, direction TEXT, lots REAL, status TEXT NOT NULL DEFAULT 'OPEN',  -- OPEN | CLOSED | CANCELLED
    open_time INTEGER, open_price REAL, stop_loss REAL, take_profit REAL,
    close_time INTEGER, close_price REAL, pnl REAL, pnl_manual INTEGER DEFAULT 0,
    source TEXT, note TEXT, created_at INTEGER
);
CREATE INDEX IF NOT EXISTS ix_rt_account ON real_trades(account_id, status);
CREATE TABLE IF NOT EXISTS cash_moves (
    id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, ts INTEGER NOT NULL, amount REAL NOT NULL,
    note TEXT, created_at INTEGER
);
"""
ACCOUNT_MIGRATIONS = {"actual_start": "REAL", "actual_since": "INTEGER"}
FINAL = ("WIN", "PARTIAL_WIN", "LOSS")
NO_FILL = ("MISSED", "EXPIRED", "SKIPPED", "INVALIDATED")
LINK_WINDOW = 3 * 86400        # a broker trade opened up to 3 days after an alert belongs to that alert


def init() -> None:
    with db.connect() as con:
        con.executescript(SCHEMA)
        cols = {r["name"] for r in con.execute("PRAGMA table_info(accounts)")}
        for col, typ in ACCOUNT_MIGRATIONS.items():
            if col not in cols:
                con.execute(f"ALTER TABLE accounts ADD COLUMN {col} {typ}")
    _seed()


def start(acc: dict) -> float:
    return acc["start_balance"] if acc.get("actual_start") is None else acc["actual_start"]


# ------------------------------------------------------------------ taking trades

def take(account_trade_id: int, source: str = "manual", ticket: str | None = None) -> dict | None:
    """Mark a model trade as really taken (idempotent). Returns the real trade."""
    t = db.one("SELECT * FROM account_trades WHERE id=?", (account_trade_id,))
    if not t:
        return None
    existing = db.one("SELECT * FROM real_trades WHERE account_trade_id=?", (account_trade_id,))
    if existing:
        if ticket and not existing["ticket"]:
            db.execute("UPDATE real_trades SET ticket=? WHERE id=?", (ticket, existing["id"]))
        return sync(existing["id"])
    rid = db.execute(
        "INSERT INTO real_trades(account_id, setup_id, account_trade_id, ticket, pair, direction, lots, status, "
        "open_time, open_price, stop_loss, take_profit, source, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (t["account_id"], t["setup_id"], t["id"], ticket, t["pair"], t["direction"], t["lots"], "OPEN",
         t["triggered_at"], t["entry"], t["stop_loss"], t["take_profit"], source, db.now()))
    return sync(rid)


def untake(account_trade_id: int) -> None:
    db.execute("DELETE FROM real_trades WHERE account_trade_id=?", (account_trade_id,))


def sync(real_id: int) -> dict | None:
    """Copy the linked model trade's fill / exit into the real trade (keeps a P&L you typed)."""
    r = db.one("SELECT * FROM real_trades WHERE id=?", (real_id,))
    if not r or not r["account_trade_id"]:
        return r
    t = db.one("SELECT * FROM account_trades WHERE id=?", (r["account_trade_id"],))
    if not t:
        return r
    upd: dict = {}
    if t["triggered_at"] and not r["open_time"]:
        upd["open_time"] = t["triggered_at"]
    if r["status"] == "OPEN":
        if t["status"] in FINAL:
            upd.update(status="CLOSED", close_time=t["closed_at"], close_price=t["exit_price"])
            if not r["pnl_manual"]:
                upd["pnl"] = t["pnl"]
        elif t["status"] in NO_FILL and not t["triggered_at"]:
            upd.update(status="CANCELLED", close_time=t["closed_at"] or db.now(), pnl=0)
    if upd:
        db.execute(f"UPDATE real_trades SET {','.join(f'{k}=?' for k in upd)} WHERE id=?", (*upd.values(), real_id))
        r.update(upd)
    return r


def sync_all() -> None:
    for r in db.rows("SELECT id FROM real_trades WHERE status='OPEN' AND account_trade_id IS NOT NULL"):
        sync(r["id"])


def taken_ids(account_id: int) -> set[int]:
    return {r["account_trade_id"] for r in db.rows(
        "SELECT account_trade_id FROM real_trades WHERE account_id=? AND account_trade_id IS NOT NULL", (account_id,))}


# ------------------------------------------------------------------ manual entries

EDITABLE = ("ticket", "pair", "direction", "lots", "status", "open_time", "open_price", "stop_loss", "take_profit",
            "close_time", "close_price", "pnl", "note")


def find_alert(account_id: int, pair: str, direction: str, open_time: int | None) -> dict | None:
    """The alert a broker trade most likely came from: same account/pair/side, detected before it opened."""
    if not open_time:
        return None
    return db.one("SELECT * FROM account_trades WHERE account_id=? AND pair=? AND direction=? "
                  "AND detected_bar_time <= ? AND detected_bar_time >= ? AND id NOT IN "
                  "(SELECT account_trade_id FROM real_trades WHERE account_trade_id IS NOT NULL) "
                  "ORDER BY detected_bar_time DESC LIMIT 1",
                  (account_id, pair, direction, open_time, open_time - LINK_WINDOW))


def add_trade(account_id: int, source: str = "manual", **f) -> dict:
    f = {k: v for k, v in f.items() if k in EDITABLE}
    f["pair"] = (f.get("pair") or "").upper()
    f["direction"] = (f.get("direction") or "").upper()
    if not f.get("status"):
        f["status"] = "CLOSED" if f.get("pnl") is not None else "OPEN"
    alert = find_alert(account_id, f["pair"], f["direction"], f.get("open_time"))
    cols = {**f, "account_id": account_id, "setup_id": alert["setup_id"] if alert else None,
            "account_trade_id": alert["id"] if alert else None, "pnl_manual": int(f.get("pnl") is not None),
            "source": source, "created_at": db.now()}
    rid = db.execute(f"INSERT INTO real_trades({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                     tuple(cols.values()))
    return sync(rid)


def edit_trade(real_id: int, **f) -> dict | None:
    f = {k: v for k, v in f.items() if k in EDITABLE}
    if "pnl" in f:
        f["pnl_manual"] = int(f["pnl"] is not None)
        if f["pnl"] is not None and "status" not in f:
            f["status"] = "CLOSED"
    if f:
        db.execute(f"UPDATE real_trades SET {','.join(f'{k}=?' for k in f)} WHERE id=?", (*f.values(), real_id))
    return db.one("SELECT * FROM real_trades WHERE id=?", (real_id,))


def delete_trade(real_id: int) -> None:
    db.execute("DELETE FROM real_trades WHERE id=?", (real_id,))


def add_cash(account_id: int, amount: float, ts: int | None = None, note: str = "") -> int:
    return db.execute("INSERT INTO cash_moves(account_id, ts, amount, note, created_at) VALUES(?,?,?,?,?)",
                      (account_id, ts or db.now(), amount, note, db.now()))


def delete_cash(cash_id: int) -> None:
    db.execute("DELETE FROM cash_moves WHERE id=?", (cash_id,))


def match_balance(acc: dict, broker_balance: float) -> float:
    """Book the difference to the broker's balance as an adjustment; returns the adjustment."""
    diff = round(broker_balance - balance(acc), 2)
    if diff:
        add_cash(acc["id"], diff, note="Adjustment to match MT5 balance")
    return diff


# ------------------------------------------------------------------ balances / stats

def closed_trades(account_id: int, since: int | None = None) -> list[dict]:
    q = "SELECT * FROM real_trades WHERE account_id=? AND status='CLOSED'"
    params: tuple = (account_id,)
    if since:
        q, params = q + " AND close_time >= ?", (account_id, since)
    return db.rows(q + " ORDER BY close_time", params)


def balance(acc: dict) -> float:
    r = db.one("SELECT COALESCE(SUM(pnl),0) s FROM real_trades WHERE account_id=? AND status='CLOSED'", (acc["id"],))
    c = db.one("SELECT COALESCE(SUM(amount),0) s FROM cash_moves WHERE account_id=?", (acc["id"],))
    return start(acc) + r["s"] + c["s"]


def open_float(acc: dict, prices: dict, unrealized) -> tuple[float, list[dict]]:
    """Floating P&L of open real trades that follow a live model position."""
    total, out = 0.0, []
    for r in db.rows("SELECT r.*, t.status m_status, t.entry m_entry, t.stop_loss m_sl, t.partial_price, t.partial_at, "
                     "t.risk_amount, t.timeframe FROM real_trades r LEFT JOIN account_trades t ON t.id=r.account_trade_id "
                     "WHERE r.account_id=? AND r.status='OPEN'", (acc["id"],)):
        px = prices.get((r["pair"], r["timeframe"])) if r["m_status"] == "TRIGGERED" else None
        if px is not None:
            m = {"entry": r["m_entry"], "stop_loss": r["m_sl"], "direction": r["direction"],
                 "partial_price": r["partial_price"], "partial_at": r["partial_at"], "risk_amount": r["risk_amount"]}
            r["unrealized"] = round(unrealized(m, px, acc["partial_pct"]), 2)
            r["last_price"] = px
            total += r["unrealized"]
        out.append(r)
    return total, out


def summary(acc: dict, prices: dict, unrealized) -> dict:
    sync_all()
    s0 = start(acc)
    trades = db.rows("SELECT * FROM real_trades WHERE account_id=? ORDER BY COALESCE(close_time, open_time, created_at)",
                     (acc["id"],))
    cash = db.rows("SELECT * FROM cash_moves WHERE account_id=? ORDER BY ts", (acc["id"],))
    events = [(t["close_time"] or 0, "trade", t) for t in trades if t["status"] == "CLOSED"]
    events += [(c["ts"], "cash", c) for c in cash]
    events.sort(key=lambda e: e[0])
    bal = peak = s0
    max_dd = 0.0
    curve = [{"t": acc.get("actual_since") or acc["created_at"], "balance": round(s0, 2)}]
    for ts, kind, x in events:
        bal += (x["pnl"] or 0) if kind == "trade" else x["amount"]
        x["balance_after"] = round(bal, 2)
        if kind == "trade":                    # drawdown from trading only, not withdrawals
            peak = max(peak, bal)
            max_dd = max(max_dd, peak - bal)
        else:
            peak += x["amount"]
        curve.append({"t": ts, "balance": round(bal, 2), "pair": x.get("pair") or (x.get("note") or "cash"),
                      "pnl": x["pnl"] if kind == "trade" else x["amount"]})
    floating, open_rows = open_float(acc, prices, unrealized)
    by_id = {r["id"]: r for r in open_rows}
    for t in trades:
        if t["status"] == "OPEN" and t["account_trade_id"]:
            m = db.one("SELECT status FROM account_trades WHERE id=?", (t["account_trade_id"],))
            t["model_status"] = m["status"] if m else None
        if t["id"] in by_id:
            t.update(unrealized=by_id[t["id"]].get("unrealized"), last_price=by_id[t["id"]].get("last_price"))
    closed = [t for t in trades if t["status"] == "CLOSED"]
    wins = [t for t in closed if (t["pnl"] or 0) > 0]
    gross_w = sum(t["pnl"] for t in wins)
    gross_l = -sum(t["pnl"] for t in closed if (t["pnl"] or 0) < 0)
    net_trading = sum(t["pnl"] or 0 for t in closed)
    deposits = sum(c["amount"] for c in cash if c["amount"] > 0)
    withdrawals = -sum(c["amount"] for c in cash if c["amount"] < 0)
    return {
        "start": round(s0, 2), "since": acc.get("actual_since"),
        "balance": round(bal, 2), "equity": round(bal + floating, 2), "floating": round(floating, 2),
        "net_pnl": round(net_trading, 2), "return_pct": round(100 * net_trading / s0, 2) if s0 else 0.0,
        "deposits": round(deposits, 2), "withdrawals": round(withdrawals, 2),
        "trades": len(closed), "wins": len(wins), "losses": len(closed) - len(wins),
        "open": sum(1 for t in trades if t["status"] == "OPEN"),
        "win_rate": round(100 * len(wins) / len(closed), 1) if closed else 0.0,
        "profit_factor": round(gross_w / gross_l, 2) if gross_l else None,
        "max_dd": round(max_dd, 2), "max_dd_pct": round(100 * max_dd / s0, 2) if s0 else 0.0,
        "curve": curve, "items": list(reversed(trades)), "cash": list(reversed(cash)),
    }


# ------------------------------------------------------------------ one-time backfill

def _utc(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())


def _seed() -> None:
    """Backfill real trades / deposits once, from the ``LEDGER_SEED`` env var (JSON). The figures are
    private, so they live in the host's environment, never in this (public) code:
    {"prop"|"cent": {"start": 2500, "since": "2026-10-08T00:00:00",
                     "trades": [{"ticket", "pair", "direction", "lots", "open_time", "open_price",
                                 "close_time", "close_price", "pnl"}], "cash": [{"ts", "amount", "note"}]}}"""
    raw = _env("LEDGER_SEED", "")
    if not raw or db.kv_get("ledger_seed_v1"):
        return
    try:
        seed = json.loads(raw)
    except ValueError:
        log.warning("LEDGER_SEED is not valid JSON - skipped")
        return
    accs = db.rows("SELECT * FROM accounts ORDER BY id")
    targets = {"prop": next((a for a in accs if a["prop_rules"]), None),
               "cent": next((a for a in accs if (a["currency"] or "").upper() == "USC"), None)}
    for key, data in seed.items():
        acc = targets.get(key)
        if acc is None:
            continue
        db.execute("UPDATE accounts SET actual_start=?, actual_since=? WHERE id=?",
                   (data["start"], _utc(data["since"]), acc["id"]))
        for t in data.get("trades", []):
            add_trade(acc["id"], source="import", status="CLOSED", note="Imported from broker history",
                      **{**t, "open_time": _utc(t["open_time"]), "close_time": _utc(t["close_time"])})
        for c in data.get("cash", []):
            add_cash(acc["id"], c["amount"], _utc(c["ts"]), c.get("note", ""))
    db.kv_set("ledger_seed_v1", db.now())
