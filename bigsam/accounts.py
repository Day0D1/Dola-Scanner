"""Trading accounts with their own risk profiles, and the per-account trade ledger.

Every approved setup becomes one ``account_trades`` row per active account, sized and managed
with that account's rules (risk %, fixed vs compounding, partial profit, exposure cap). Rows are
walked forward through the same candles as the setup, so each account gets its own realistic
history: fills, partials, exits, P&L and balance.
"""
from __future__ import annotations

import dataclasses
import time

from . import db
from .config import EngineParams, settings
from .pipeline import advance
from .risk import account_lots

OPEN = ("PENDING", "TRIGGERED")
RESOLVED = ("WIN", "PARTIAL_WIN", "LOSS")

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,       -- prop | live
    currency TEXT NOT NULL DEFAULT 'USD',                                  -- USD | USC (cents)
    start_balance REAL NOT NULL, risk_pct REAL NOT NULL, compounding INTEGER DEFAULT 0,
    partial_at_r REAL DEFAULT 0, partial_pct REAL DEFAULT 50, max_open INTEGER DEFAULT 2,
    prop_rules INTEGER DEFAULT 0, active INTEGER DEFAULT 1, created_at INTEGER, tracking_start INTEGER
);
CREATE TABLE IF NOT EXISTS account_trades (
    id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, setup_id INTEGER NOT NULL,
    pair TEXT, timeframe TEXT, direction TEXT, status TEXT, skip_reason TEXT,
    entry REAL, stop_loss REAL, take_profit REAL, partial_price REAL, partial_at INTEGER,
    rr REAL, range_extreme REAL, lots REAL, risk_amount REAL, stop_pips REAL, min_lot INTEGER DEFAULT 0,
    detected_bar_time INTEGER, last_bar_time INTEGER, triggered_at INTEGER, closed_at INTEGER,
    result_r REAL, pnl REAL, exit_price REAL, created_at INTEGER,
    UNIQUE(account_id, setup_id)
);
CREATE INDEX IF NOT EXISTS ix_at_account ON account_trades(account_id, status);
"""

DEFAULTS = [
    # Prop: survival first - fixed $10 (0.4%), partial at +1.5R, max 2 open, firm rules on.
    dict(name="Prop · GFT Goat Stride", kind="prop", currency="USD", start_balance=2500, risk_pct=0.4,
         compounding=0, partial_at_r=1.5, partial_pct=50, max_open=2, prop_rules=1),
    # Live cent: growth + live-execution test - 1.5% of current balance, hold to full 3R.
    dict(name="Live · Cent", kind="live", currency="USC", start_balance=1200, risk_pct=1.5,
         compounding=1, partial_at_r=0, partial_pct=50, max_open=3, prop_rules=0),
]
EDITABLE = ("name", "currency", "start_balance", "risk_pct", "compounding", "partial_at_r", "partial_pct",
            "max_open", "prop_rules", "active")


def init() -> None:
    with db.connect() as con:
        con.executescript(SCHEMA)
    if not db.one("SELECT id FROM accounts LIMIT 1"):
        now = db.now()
        for a in DEFAULTS:
            cols = list(a) + ["created_at", "tracking_start"]
            db.execute(f"INSERT INTO accounts({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                       (*a.values(), now, now))


def all_accounts(active_only: bool = True) -> list[dict]:
    return db.rows("SELECT * FROM accounts" + (" WHERE active=1" if active_only else "") + " ORDER BY id")


def get(account_id: int) -> dict | None:
    return db.one("SELECT * FROM accounts WHERE id=?", (account_id,))


def update(account_id: int, **fields) -> dict:
    fields = {k: v for k, v in fields.items() if k in EDITABLE and v is not None}
    if fields:
        db.execute(f"UPDATE accounts SET {','.join(f'{k}=?' for k in fields)} WHERE id=?",
                   (*fields.values(), account_id))
    return get(account_id)


def balance(acc: dict) -> float:
    r = db.one("SELECT COALESCE(SUM(pnl),0) s FROM account_trades WHERE account_id=? AND status IN "
               "('WIN','PARTIAL_WIN','LOSS')", (acc["id"],))
    return acc["start_balance"] + r["s"]


def risk_amount(acc: dict) -> float:
    base = balance(acc) if acc["compounding"] else acc["start_balance"]
    return base * acc["risk_pct"] / 100


def unit(acc: dict) -> str:
    return "¢" if acc["currency"].upper() == "USC" else "$"


def fmt_money(acc: dict, v: float | None, sign: bool = False) -> str:
    if v is None:
        return "—"
    if acc["currency"].upper() == "USC":
        return f"{v:+,.0f}¢" if sign else f"{v:,.0f}¢"
    return (f"{'+' if v >= 0 else '-'}${abs(v):,.2f}" if sign else f"${v:,.2f}")


def _open_trades(account_id: int, statuses=OPEN) -> list[dict]:
    q = ",".join("?" * len(statuses))
    return db.rows(f"SELECT * FROM account_trades WHERE account_id=? AND status IN ({q})", (account_id, *statuses))


def _hedge(rec: dict, active: list[dict]) -> str | None:
    for a in active:
        if a["pair"] == rec["pair"] and a["direction"] != rec["direction"]:
            return f"Skipped: opposite {a['direction']} active on {rec['pair']} (no hedging)"
    return None


# ------------------------------------------------------------------ new setup -> account trades

def plan(rec: dict, prices: dict) -> list[dict]:
    """Per-account sizing and management for an approved setup (used in the alert)."""
    out = []
    for acc in all_accounts():
        risk = risk_amount(acc)
        size = account_lots(rec["pair"], rec["entry"], rec["stop_loss"], risk, prices, acc["currency"])
        partial = None
        if acc["partial_at_r"] and acc["partial_at_r"] > 0:
            partial = rec["entry"] + acc["partial_at_r"] * (rec["entry"] - rec["stop_loss"])
        active = _open_trades(acc["id"])
        n_open = sum(1 for a in active if a["status"] == "TRIGGERED")
        out.append({
            "account": acc, "lots": size["lots"], "risk": size["risk"], "target_risk": risk,
            "stop_pips": size["stop_pips"], "min_lot": size["min_lot"], "partial_price": partial,
            "skip": _hedge(rec, active) if size["lots"] else "Skipped: could not size the trade",
            "cap_full": n_open >= acc["max_open"],
            "reward": None if size["risk"] is None else size["risk"] * (
                (acc["partial_pct"] / 100 * acc["partial_at_r"] + (1 - acc["partial_pct"] / 100) * (rec["rr"] or 3))
                if partial is not None else (rec["rr"] or 3)),
        })
    return out


def create(setup_id: int, rec: dict, plans: list[dict]) -> None:
    now = db.now()
    for p in plans:
        acc = p["account"]
        db.execute(
            "INSERT OR IGNORE INTO account_trades(account_id, setup_id, pair, timeframe, direction, status, "
            "skip_reason, entry, stop_loss, take_profit, partial_price, rr, range_extreme, lots, risk_amount, "
            "stop_pips, min_lot, detected_bar_time, last_bar_time, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (acc["id"], setup_id, rec["pair"], rec["timeframe"], rec["direction"],
             "SKIPPED" if p["skip"] else "PENDING", p["skip"], rec["entry"], rec["stop_loss"],
             rec["take_profit"], p["partial_price"], rec["rr"], rec.get("range_extreme"), p["lots"], p["risk"],
             p["stop_pips"], int(p["min_lot"]), rec["detected_bar_time"], rec["last_bar_time"], now))


# ------------------------------------------------------------------ walk trades forward

EXIT_PRICE = {"WIN": "take_profit", "LOSS": "stop_loss", "PARTIAL_WIN": "entry"}


def advance_trades(ctx, P: EngineParams) -> list[tuple[dict, dict, str, str]]:
    """Advance every open account trade on this pair/timeframe. Returns (account, trade, kind, msg)."""
    out = []
    accs = {a["id"]: a for a in all_accounts(active_only=False)}
    rows = db.rows("SELECT * FROM account_trades WHERE pair=? AND timeframe=? AND status IN ('PENDING','TRIGGERED')",
                   (ctx.pair, ctx.tf))
    for t in rows:
        acc = accs.get(t["account_id"])
        if acc is None:
            continue
        Pa = dataclasses.replace(P, partial_pct=acc["partial_pct"])
        was_pending = t["status"] == "PENDING"
        rec = dict(t)
        events = advance(rec, ctx, Pa)
        if was_pending and rec.get("triggered_at"):
            open_now = [o for o in _open_trades(acc["id"], ("TRIGGERED",)) if o["id"] != t["id"]]
            reason = _hedge(rec, open_now)
            if not reason and len(open_now) >= acc["max_open"]:
                reason = f"Cancelled: {len(open_now)}/{acc['max_open']} positions already open when it would have filled"
            if reason:
                db.execute("UPDATE account_trades SET status='SKIPPED', skip_reason=?, last_bar_time=? WHERE id=?",
                           (reason, rec["last_bar_time"], t["id"]))
                out.append((acc, {**t, "status": "SKIPPED"}, "SKIPPED", reason))
                continue
        exit_price = rec.get(EXIT_PRICE.get(rec["status"], ""), None) if rec["status"] in EXIT_PRICE else None
        db.execute("UPDATE account_trades SET status=?, triggered_at=?, partial_at=?, closed_at=?, result_r=?, "
                   "pnl=?, exit_price=?, last_bar_time=? WHERE id=?",
                   (rec["status"], rec.get("triggered_at"), rec.get("partial_at"), rec.get("closed_at"),
                    rec.get("result_r"), rec.get("pnl"), exit_price, rec["last_bar_time"], t["id"]))
        for kind, ts, msg in events:
            out.append((acc, rec, kind, msg))
    return out


# ------------------------------------------------------------------ history / stats

def unrealized(t: dict, price: float, partial_pct: float) -> float:
    """Open P&L in account units at ``price`` (includes a banked partial)."""
    risk_dist = abs(t["entry"] - t["stop_loss"]) or 1e-12
    sign = 1 if t["direction"] == "BUY" else -1
    r_now = sign * (price - t["entry"]) / risk_dist
    if t.get("partial_at") and t.get("partial_price") is not None:
        frac = partial_pct / 100
        r_now = frac * abs(t["partial_price"] - t["entry"]) / risk_dist + (1 - frac) * r_now
    return r_now * (t["risk_amount"] or 0)


def summary(acc: dict, last_prices: dict[tuple[str, str], float] | None = None) -> dict:
    trades = db.rows("SELECT * FROM account_trades WHERE account_id=? ORDER BY COALESCE(closed_at, triggered_at, "
                     "detected_bar_time)", (acc["id"],))
    closed = sorted((t for t in trades if t["status"] in RESOLVED), key=lambda t: t["closed_at"])
    bal, peak, max_dd = acc["start_balance"], acc["start_balance"], 0.0
    curve = [{"t": acc["tracking_start"] or acc["created_at"], "balance": round(bal, 2)}]
    for t in closed:
        bal += t["pnl"] or 0
        peak = max(peak, bal)
        max_dd = max(max_dd, peak - bal)
        t["balance_after"] = round(bal, 2)
        curve.append({"t": t["closed_at"], "balance": round(bal, 2), "pair": t["pair"], "pnl": t["pnl"]})
    floating = 0.0
    for t in trades:
        if t["status"] == "TRIGGERED" and last_prices:
            px = last_prices.get((t["pair"], t["timeframe"]))
            if px is not None:
                t["unrealized"] = round(unrealized(t, px, acc["partial_pct"]), 2)
                t["last_price"] = px
                floating += t["unrealized"]
    wins = [t for t in closed if (t["result_r"] or 0) > 0]
    gross_w = sum(t["pnl"] for t in wins)
    gross_l = -sum(t["pnl"] for t in closed if (t["pnl"] or 0) < 0)
    counts = {}
    for t in trades:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    return {
        "account": {**acc, "unit": unit(acc)},
        "balance": round(bal, 2), "equity": round(bal + floating, 2), "floating": round(floating, 2),
        "net_pnl": round(bal - acc["start_balance"], 2),
        "return_pct": round(100 * (bal - acc["start_balance"]) / acc["start_balance"], 2),
        "risk_now": round(risk_amount(acc), 2),
        "trades": len(closed), "wins": len(wins), "losses": len(closed) - len(wins),
        "full_wins": sum(1 for t in closed if t["status"] == "WIN"),
        "partial_wins": sum(1 for t in closed if t["status"] == "PARTIAL_WIN"),
        "win_rate": round(100 * len(wins) / len(closed), 1) if closed else 0.0,
        "profit_factor": round(gross_w / gross_l, 2) if gross_l else None,
        "net_r": round(sum(t["result_r"] or 0 for t in closed), 2),
        "max_dd": round(max_dd, 2), "max_dd_pct": round(100 * max_dd / acc["start_balance"], 2),
        "counts": counts, "curve": curve,
        "items": list(reversed(trades)),
    }


def setups_with_trades(setup_ids: list[int]) -> dict[int, list[dict]]:
    if not setup_ids:
        return {}
    q = ",".join("?" * len(setup_ids))
    out: dict[int, list[dict]] = {}
    for r in db.rows(f"SELECT * FROM account_trades WHERE setup_id IN ({q})", tuple(setup_ids)):
        out.setdefault(r["setup_id"], []).append(r)
    return out


def _now() -> int:
    return int(time.time())
