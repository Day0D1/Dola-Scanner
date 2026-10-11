"""One-tap order execution on MT5 through MetaApi (https://metaapi.cloud).

Flow: the Telegram alert carries a link per execution-enabled account -> the link opens the
iPhone Shortcut (or a confirm page) -> the trader confirms -> ``place`` sends the pending limit
order with SL/TP to MT5. Orders the system placed are cancelled automatically if the setup
misses, expires or is skipped before filling.

Secrets (environment, never stored in the DB): METAAPI_TOKEN, EXEC_KEY (the Shortcut's key).
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

import httpx

from . import db
from .config import TF_SECONDS, _env, settings

log = logging.getLogger("bigsam.exec")
OK_CODES = {"TRADE_RETCODE_DONE", "TRADE_RETCODE_PLACED", "TRADE_RETCODE_DONE_PARTIAL"}
MAGIC = 25801


def metaapi_token() -> str:
    return _env("METAAPI_TOKEN", "")


def exec_key() -> str:
    return _env("EXEC_KEY", "")


def enabled(acc: dict) -> bool:
    return (acc.get("exec_mode") or "off") == "metaapi" and bool(acc.get("exec_account_id"))


def new_token() -> str:
    return secrets.token_urlsafe(16)


def symbol(acc: dict, pair: str) -> str:
    return f"{pair}{acc.get('exec_symbol_suffix') or ''}"


def _trade(acc: dict, body: dict) -> tuple[bool, dict, str]:
    token = metaapi_token()
    if not token:
        return False, {}, "METAAPI_TOKEN is not set on the server"
    region = acc.get("exec_region") or "new-york"
    url = (f"https://mt-client-api-v1.{region}.agiliumtrade.ai/users/current/accounts/"
           f"{acc['exec_account_id']}/trade")
    try:
        r = httpx.post(url, json=body, timeout=30,
                       headers={"auth-token": token, "Accept": "application/json"})
    except httpx.HTTPError as e:
        return False, {}, f"MetaApi unreachable: {e}"
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code != 200:
        return False, data, f"MetaApi HTTP {r.status_code}: {data.get('message') or r.text[:200]}"
    ok = data.get("stringCode") in OK_CODES
    return ok, data, f"{data.get('stringCode')}: {data.get('message', '')}".strip()


def order_body(t: dict, acc: dict) -> dict:
    expires = t["detected_bar_time"] + settings.engine.expiry_bars * TF_SECONDS[t["timeframe"]]
    return {
        "actionType": "ORDER_TYPE_BUY_LIMIT" if t["direction"] == "BUY" else "ORDER_TYPE_SELL_LIMIT",
        "symbol": symbol(acc, t["pair"]), "volume": t["lots"], "openPrice": t["entry"],
        "stopLoss": t["stop_loss"], "takeProfit": t["take_profit"],
        "comment": f"BigSam {t['setup_id']}", "clientId": f"BS{t['id']}", "magic": MAGIC,
        "expiration": {"type": "ORDER_TIME_SPECIFIED",
                       "time": datetime.fromtimestamp(expires, timezone.utc).isoformat().replace("+00:00", "Z")},
    }


def summary(t: dict, acc: dict) -> str:
    from .accounts import fmt_money
    side = "BUY LIMIT" if t["direction"] == "BUY" else "SELL LIMIT"
    lot_word = "cent-lots" if (acc["currency"] or "").upper() == "USC" else "lots"
    return (f"{acc['name']}: {side} {symbol(acc, t['pair'])} {t['lots']:.2f} {lot_word} @ {t['entry']} | "
            f"SL {t['stop_loss']} | TP {t['take_profit']} | risk {fmt_money(acc, t['risk_amount'])}")


def _claim(token: str) -> bool:
    """Atomically mark a trade as being placed (stops double taps / double placement)."""
    with db.connect() as con:
        cur = con.execute("UPDATE account_trades SET exec_status='placing' WHERE exec_token=? AND status='PENDING' "
                          "AND (exec_status IS NULL OR exec_status='failed')", (token,))
        return cur.rowcount == 1


def place(token: str) -> dict:
    t = db.one("SELECT * FROM account_trades WHERE exec_token=?", (token,))
    if not t:
        return {"ok": False, "message": "Unknown or expired link"}
    acc = db.one("SELECT * FROM accounts WHERE id=?", (t["account_id"],))
    if not enabled(acc):
        return {"ok": False, "message": f"Execution is not enabled for {acc['name']}"}
    if t["exec_status"] == "placed":
        return {"ok": True, "message": f"Already placed (order {t['exec_order_id']})", "order_id": t["exec_order_id"]}
    if t["status"] != "PENDING":
        return {"ok": False, "message": f"Not placed: the setup is now {t['status'].lower()}"
                                        + (f" ({t['skip_reason']})" if t.get("skip_reason") else "")}
    if not _claim(token):
        return {"ok": False, "message": "Already being placed - check MT5"}
    ok, data, msg = _trade(acc, order_body(t, acc))
    now = db.now()
    db.execute("UPDATE account_trades SET exec_status=?, exec_order_id=?, exec_message=?, exec_at=? WHERE id=?",
               ("placed" if ok else "failed", data.get("orderId"), msg, now, t["id"]))
    db.add_event(t["setup_id"], "EXEC_PLACED" if ok else "EXEC_FAILED", f"[{acc['name']}] {msg}")
    return {"ok": ok, "message": ("Order placed on MT5. " if ok else "Order NOT placed. ") + msg,
            "order_id": data.get("orderId")}


def cancel_if_placed(t: dict, acc: dict, reason: str) -> str | None:
    """Cancel the broker order of a trade that missed/expired/was skipped before filling."""
    if t.get("exec_status") != "placed" or not t.get("exec_order_id") or not enabled(acc):
        return None
    ok, data, msg = _trade(acc, {"actionType": "ORDER_CANCEL", "orderId": str(t["exec_order_id"])})
    db.execute("UPDATE account_trades SET exec_status=?, exec_message=? WHERE id=?",
               ("cancelled" if ok else "cancel_failed", msg, t["id"]))
    note = (f"MT5 order {t['exec_order_id']} cancelled ({reason})." if ok else
            f"Could not cancel MT5 order {t['exec_order_id']}: {msg}. Check MT5 - it may already be filled.")
    db.add_event(t["setup_id"], "EXEC_CANCEL", f"[{acc['name']}] {note}")
    return note
