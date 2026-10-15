"""FastAPI web app: auth, JSON API, and the single-page dashboard."""
from __future__ import annotations

import hmac
import html
import json
from urllib.parse import quote
import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from . import accounts, analytics, auth, backtest, db, execution, ledger, notifier, prop
from .config import HTF_MAP, settings
from .data import price_decimals
from .scanner import Scanner, account, audit_active, loss_streak

log = logging.getLogger("bigsam.web")
STATIC = Path(__file__).parent / "static"
LIST_COLS = ("id, uid, source, run_id, pair, timeframe, htf, direction, decision, status, reject_reason, "
             "poi_type, liquidity_type, liquidity_shape, liquidity_depth, entry, stop_loss, take_profit, rr, "
             "structure_rr, protected_price, bos_price, range_extreme, equilibrium, htf_zone, sweep_type, lots, "
             "stop_pips, risk_amount, result_r, pnl, detected_bar_time, detected_at, triggered_at, closed_at, "
             "alerted, suppressed, partial_price, partial_at")

scanner: Scanner | None = None
bot: notifier.CommandBot | None = None


# ------------------------------------------------------------------ telegram commands

def bot_handler(cmd: str) -> str | None:
    if cmd in ("/start", "/help"):
        return ("<b>BigSam Alerts</b>\n/status – scanner & risk state\n/open – pending & active setups\n"
                "/stats – live performance")
    if cmd == "/status":
        st = scanner.status if scanner else {}
        last = datetime.fromtimestamp(st["last_run"], timezone.utc).strftime("%H:%M UTC") if st.get("last_run") else "—"
        a = account()
        return (f"<b>Status</b>: {'🚨 AUDIT' if audit_active() else '✅ ACTIVE'}\n"
                f"Last scan: {last} · pairs {len(settings.pairs)} · TFs {', '.join(settings.ltf_timeframes)}\n"
                f"Risk/trade: ${a['risk_per_trade']:,.0f} · loss streak {loss_streak()}\n"
                + _actual_lines() + _prop_line())
    if cmd == "/open":
        rows = db.rows(f"SELECT {LIST_COLS} FROM setups WHERE source='live' AND status IN ('PENDING','TRIGGERED') "
                       "ORDER BY detected_at DESC LIMIT 15")
        if not rows:
            return "No pending or active setups."
        return "\n".join(f"{'🟢' if r['direction'] == 'BUY' else '🔴'} {r['pair']} {r['timeframe']} {r['status']} "
                         f"@ {r['entry']:.{price_decimals(r['pair'])}f}" for r in rows)
    if cmd == "/stats":
        a = account()
        s = analytics.compute(db.rows(f"SELECT {LIST_COLS} FROM setups WHERE source='live'"),
                              a["account_size"], a["risk_per_trade"])["summary"]
        return (f"<b>Live stats</b>\nTrades {s['trades']} · WR {s['win_rate']}% · Net {s['net_r']:+g}R\n"
                f"Expectancy {s['expectancy_r']:+g}R · PF {s['profit_factor'] or '—'} · Max DD {s['max_drawdown_r']}R")
    return None


def button_handler(data: str) -> tuple[str, str | None]:
    """Telegram inline buttons on alerts: ``took:<account_trade_id>`` logs the trade as really taken."""
    kind, _, arg = data.partition(":")
    if kind != "took" or not arg.isdigit():
        return "Unknown button", None
    t = db.one("SELECT * FROM account_trades WHERE id=?", (int(arg),))
    if not t:
        return "That alert no longer exists", None
    acc = accounts.get(t["account_id"])
    if db.one("SELECT id FROM real_trades WHERE account_trade_id=?", (t["id"],)):
        return f"Already logged on {acc['name']}", None
    ledger.take(t["id"], source="telegram")
    return (f"Logged on {acc['name']}",
            f"✅ <b>Logged as taken</b> on {acc['name']}: {t['pair']} {t['direction']} {t['lots']} lots "
            f"@ {t['entry']:.{price_decimals(t['pair'])}f}\n<i>It follows this alert to its exit. "
            "If your real fill or P&amp;L differs, edit it on the History page.</i>")


def _actual_lines() -> str:
    out = []
    for a in accounts.all_accounts():
        out.append(f"{a['name']}: actual balance {accounts.fmt_money(a, ledger.balance(a))}")
    return "\n".join(out) + "\n" if out else ""


def _prop_line() -> str:
    try:
        p = _prop_state()
    except Exception:
        return ""
    return (f"<b>{p['firm']}</b>\n"
            f"Equity ≈ ${p['equity']:,.2f} · floor ${p['max_loss_floor']:,.2f} "
            f"(room ${p['room_to_floor']:,.2f})\n"
            f"Today ${p['today_closed']:+,.2f} of −${p['daily_limit']:,.0f} · "
            f"floating ${p['floating_now']:,.2f} of −${p['floating_limit']:,.0f}\n"
            f"Valid days {p['valid_days']}/{p['valid_days_needed']} · consistency "
            f"{p['consistency_pct'] if p['consistency_pct'] is not None else '—'}% (max {p['consistency_max']:g}%) · "
            f"payout {'✅ eligible' if p['payout_eligible'] else 'not yet'}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_background()
    yield
    stop_background()


def start_background() -> None:
    """Start the scanner + Telegram bot. Called by our own lifespan, or by a host app that mounts
    BigSam under a path (Starlette does not run lifespans of mounted sub-apps)."""
    global scanner, bot
    if scanner is not None:
        return
    db.init()
    auth.ensure_admin()
    scanner = Scanner()
    if settings.run_scanner:
        scanner.start()
    if settings.telegram_token and settings.telegram_chat_id:
        bot = notifier.CommandBot(bot_handler, on_button=button_handler)
        bot.start()


def stop_background() -> None:
    global scanner, bot
    if scanner:
        scanner.stop()
    if bot:
        bot.stop()
    scanner, bot = None, None


db.init()
app = FastAPI(title="BigSam Alerts", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=auth.secret_key(), session_cookie="bigsam_session",
                   max_age=14 * 86400, same_site="lax", https_only=False)


def user(request: Request) -> str:
    u = request.session.get("user")
    if not u:
        raise HTTPException(401, "not authenticated")
    return u


# ------------------------------------------------------------------ auth

class LoginIn(BaseModel):
    username: str
    password: str


_fails: dict[str, list[float]] = {}


@app.post("/api/login")
def login(body: LoginIn, request: Request):
    ip = request.client.host if request.client else "?"
    recent = [t for t in _fails.get(ip, []) if time.time() - t < 600]
    if len(recent) >= 8:
        raise HTTPException(429, "too many attempts, try again in a few minutes")
    u = auth.authenticate(body.username.strip(), body.password)
    if not u:
        _fails[ip] = recent + [time.time()]
        time.sleep(0.6)
        raise HTTPException(401, "invalid username or password")
    _fails.pop(ip, None)
    request.session["user"] = u["username"]
    return {"username": u["username"]}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(u: str = Depends(user)):
    return {"username": u}


class PasswordIn(BaseModel):
    current: str
    new: str = Field(min_length=8)


@app.post("/api/password")
def change_password(body: PasswordIn, u: str = Depends(user)):
    if not auth.authenticate(u, body.current):
        raise HTTPException(400, "current password is wrong")
    auth.set_password(u, body.new)
    return {"ok": True}


# ------------------------------------------------------------------ data helpers

def _source_filter(source: str, run_id: int | None) -> tuple[str, tuple]:
    if source == "backtest":
        if run_id is None:
            raise HTTPException(400, "run_id required for backtest data")
        return "source='backtest' AND run_id=?", (run_id,)
    live_since = db.kv_get("live_since")       # set by the first real alert: earlier setups are test era
    if live_since:
        return "source='live' AND detected_at >= ?", (live_since,)
    return "source='live'", ()


def _analytics(source: str, run_id: int | None) -> dict:
    where, params = _source_filter(source, run_id)
    rows = db.rows(f"SELECT {LIST_COLS} FROM setups WHERE {where} AND decision!='WATCH'", params)
    a = account()
    return analytics.compute(rows, a["account_size"], a["risk_per_trade"])


# ------------------------------------------------------------------ prop firm

def _last_prices() -> dict[tuple[str, str], float]:
    if not scanner:
        return {}
    out = {k: float(ctx.B.c[-1]) for k, ctx in scanner.contexts.items() if len(ctx.B)}
    for pair, ctx in scanner.track_contexts.items():      # fresher 5m closes for pairs with live trades
        if len(ctx.B):
            for tf in ("5m", "15m", "1h"):
                out[(pair, tf)] = float(ctx.B.c[-1])
    return out


def _prop_account() -> dict | None:
    return next((a for a in accounts.all_accounts() if a["prop_rules"]), None)


def _prop_state() -> dict:
    acc = _prop_account()
    if acc is None:
        return {"enabled": False}
    # the firm's limits apply to the trades really taken (actual ledger), not to every alert
    ledger.sync_all()
    since = acc.get("actual_since") or acc["tracking_start"] or db.now()
    trades = [{"pnl": t["pnl"], "closed_at": t["close_time"]} for t in ledger.closed_trades(acc["id"], since)]
    floating, _ = ledger.open_float(acc, _last_prices(), accounts.unrealized)
    return prop.tracker(trades, floating, ledger.start(acc), accounts.risk_amount(acc), since,
                        db.kv_get("prop_last_payout"), db.now())


# ------------------------------------------------------------------ one-tap MT5 execution

SHORTCUT_NAME = "BigSam Trade"


def _exec_auth(request: Request) -> str:
    """The iPhone Shortcut sends X-Exec-Key; the confirm page uses the dashboard login."""
    key = execution.exec_key()
    sent = request.headers.get("x-exec-key", "")
    if key and sent and hmac.compare_digest(sent, key):
        return "shortcut"
    if request.session.get("user"):
        return request.session["user"]
    raise HTTPException(401, "not authorised: missing or wrong X-Exec-Key")


def _exec_trade(token: str) -> tuple[dict, dict]:
    t = db.one("SELECT * FROM account_trades WHERE exec_token=?", (token,))
    if not t:
        raise HTTPException(404, "unknown or expired link")
    return t, accounts.get(t["account_id"])


@app.get("/api/exec/{token}")
def exec_preview(token: str, request: Request):
    _exec_auth(request)
    t, acc = _exec_trade(token)
    can = t["status"] == "PENDING" and t["exec_status"] in (None, "failed") and execution.enabled(acc)
    return {"summary": execution.summary(t, acc), "status": t["status"], "exec_status": t["exec_status"],
            "can_place": can, "message": "Ready to place" if can else
            (f"Already {t['exec_status']}" if t["exec_status"] else f"Setup is {t['status'].lower()}")}


@app.post("/api/exec/{token}/place")
def exec_place(token: str, request: Request):
    who = _exec_auth(request)
    t, acc = _exec_trade(token)
    result = execution.place(token)
    db.add_event(t["setup_id"], "EXEC_REQUEST", f"[{acc['name']}] place requested via {who}: {result['message']}")
    notifier.send_about(t["setup_id"], notifier.exec_message(result, execution.summary(t, acc)))
    return result


def _ea_auth(request: Request) -> None:
    key = execution.exec_key()
    sent = request.headers.get("x-exec-key", "")
    if not (key and sent and hmac.compare_digest(sent, key)):
        raise HTTPException(401, "missing or wrong X-Exec-Key")


@app.get("/api/ea/orders")
def ea_orders(account: int, request: Request):
    """Polled every few seconds by BigSamBridge.mq5 on the trader's PC (plain-text protocol)."""
    _ea_auth(request)
    execution.ea_seen(account)
    return PlainTextResponse("\n".join(["OK"] + execution.ea_queue(account)))


@app.post("/api/ea/report")
async def ea_report(account: int, request: Request):
    _ea_auth(request)
    execution.ea_seen(account)
    body = (await request.body()).decode("utf-8", "replace")
    for line in body.splitlines():
        t, text = execution.ea_report(account, line)
        if t and text:
            notifier.send_about(t["setup_id"], text)
    return PlainTextResponse("OK")


@app.get("/x/{token}")
def exec_page(token: str):
    t = db.one("SELECT * FROM account_trades WHERE exec_token=?", (token,))
    if not t:
        return HTMLResponse("<h3>Unknown or expired link</h3>", status_code=404)
    acc = accounts.get(t["account_id"])
    summ = html.escape(execution.summary(t, acc))
    state = "ready" if t["status"] == "PENDING" and t["exec_status"] in (None, "failed") else \
        (t["exec_status"] or t["status"].lower())
    sc = f"shortcuts://run-shortcut?name={quote(SHORTCUT_NAME)}&input=text&text={quote(token)}"
    page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Place trade</title><style>
body{{font:16px -apple-system,system-ui,sans-serif;background:#0b0e13;color:#e6eaf0;margin:0;padding:24px 16px}}
.card{{background:#131821;border:1px solid #222a37;border-radius:12px;padding:18px;max-width:520px;margin:auto}}
code{{display:block;white-space:normal;background:#10141b;padding:12px;border-radius:8px;margin:12px 0;font-size:14px}}
a.btn,button{{display:block;width:100%;box-sizing:border-box;text-align:center;padding:14px;border-radius:10px;border:0;
font-size:17px;font-weight:600;margin-top:10px;text-decoration:none}} .p{{background:#f5b942;color:#1a1405}}
.s{{background:#222a37;color:#e6eaf0}} .m{{color:#8b95a7;font-size:13px;margin-top:12px}}
input{{display:block;width:100%;box-sizing:border-box;padding:12px;margin-top:8px;border-radius:8px;border:1px solid #2c3646;
background:#10141b;color:#e6eaf0;font-size:16px}} #login{{display:none;margin-top:14px}} .ok{{color:#2ec27e}} .bad{{color:#f0545c}}
</style></head><body><div class="card">
<b>BigSam · place order on MT5</b><code>{summ}</code><div>Status: <b id="st">{html.escape(state)}</b></div>
{"" if state != "ready" else f'''<button class="p" id="here">Place order on MT5</button>
<form id="login"><div class="m" style="margin-top:0">Sign in once on this phone (stays signed in for 14 days):</div>
<input name="username" autocomplete="username" placeholder="Username" value="admin" required>
<input name="password" type="password" autocomplete="current-password" placeholder="Password" required>
<button class="p" type="submit">Sign in and place order</button></form>
<a class="btn s" href="{sc}">Use iPhone Shortcut instead</a>'''}
<div class="m" id="msg">A pending limit order with stop loss and take profit. Nothing is sent until you confirm.</div>
</div><script>
const b=document.getElementById("here"), lf=document.getElementById("login"), msg=document.getElementById("msg");
const show=(t,c)=>{{ msg.textContent=t; msg.className="m "+(c||""); }};
async function place(){{
  const r=await fetch("../api/exec/{token}/place",{{method:"POST",credentials:"same-origin"}});
  if(r.status===401){{ lf.style.display="block"; b.style.display="none"; show("Sign in to confirm this order."); return; }}
  const d=await r.json().catch(()=>({{}}));
  show(d.message||d.detail||"Done", d.ok?"ok":"bad");
  if(d.ok){{ b.style.display="none"; lf.style.display="none"; document.getElementById("st").textContent="approved"; }}
}}
if(b) b.onclick=async()=>{{ if(!confirm("Place this order on MT5?")) return; b.disabled=true; await place(); b.disabled=false; }};
if(lf) lf.onsubmit=async(e)=>{{ e.preventDefault(); const f=new FormData(lf);
  const r=await fetch("../api/login",{{method:"POST",credentials:"same-origin",headers:{{"Content-Type":"application/json"}},
    body:JSON.stringify({{username:f.get("username"),password:f.get("password")}})}});
  if(!r.ok){{ const d=await r.json().catch(()=>({{}})); show(d.detail||"Sign-in failed","bad"); return; }}
  await place(); }};
</script></body></html>"""
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ accounts + trade history

@app.get("/api/accounts")
def list_accounts(u: str = Depends(user)):
    prices = _last_prices()
    out = []
    for a in accounts.all_accounts(active_only=False):
        s = accounts.summary(a, prices)
        s.pop("items"), s.pop("curve")
        s["ea_last_seen"] = execution.ea_last_seen(a["id"])
        s["actual_balance"] = round(ledger.balance(a), 2)
        out.append(s)
    return out


@app.get("/api/accounts/{aid}/history")
def account_history(aid: int, u: str = Depends(user)):
    acc = accounts.get(aid)
    if not acc:
        raise HTTPException(404, "account not found")
    s = accounts.summary(acc, _last_prices())
    taken = ledger.taken_ids(aid)
    for t in s["items"]:
        t["taken"] = t["id"] in taken
    return s


class AccountEdit(BaseModel):
    name: str | None = None
    currency: str | None = Field(None, pattern="^(USD|USC)$")
    start_balance: float | None = Field(None, gt=0)
    risk_pct: float | None = Field(None, gt=0, le=10)
    compounding: int | None = Field(None, ge=0, le=1)
    partial_at_r: float | None = Field(None, ge=0, le=10)
    partial_pct: float | None = Field(None, ge=0, le=100)
    max_open: int | None = Field(None, ge=1, le=20)
    prop_rules: int | None = Field(None, ge=0, le=1)
    active: int | None = Field(None, ge=0, le=1)
    actual_start: float | None = Field(None, gt=0)
    actual_since: int | None = Field(None, gt=0)
    exec_mode: str | None = Field(None, pattern="^(off|metaapi|ea)$")
    exec_account_id: str | None = Field(None, max_length=64)
    exec_region: str | None = Field(None, pattern="^[a-z-]{2,30}$")
    exec_symbol_suffix: str | None = Field(None, max_length=10)


@app.post("/api/accounts/{aid}")
def edit_account(aid: int, body: AccountEdit, u: str = Depends(user)):
    if not accounts.get(aid):
        raise HTTPException(404, "account not found")
    acc = accounts.update(aid, **body.model_dump())
    if acc["prop_rules"]:                       # keep the system-level risk model in step with the prop account
        db.kv_set("account_size", acc["start_balance"])
    return acc


# ------------------------------------------------------------------ actual ledger (trades really taken)

def _acc_or_404(aid: int) -> dict:
    acc = accounts.get(aid)
    if not acc:
        raise HTTPException(404, "account not found")
    return acc


@app.get("/api/accounts/{aid}/actual")
def account_actual(aid: int, u: str = Depends(user)):
    acc = _acc_or_404(aid)
    return {"account": {**acc, "unit": accounts.unit(acc)},
            **ledger.summary(acc, _last_prices(), accounts.unrealized)}


class RealTradeIn(BaseModel):
    ticket: str | None = Field(None, max_length=40)
    pair: str | None = Field(None, pattern="^[A-Za-z]{6}$")
    direction: str | None = Field(None, pattern="^(BUY|SELL|buy|sell)$")
    lots: float | None = Field(None, gt=0, le=1000)
    status: str | None = Field(None, pattern="^(OPEN|CLOSED|CANCELLED)$")
    open_time: int | None = None
    open_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    close_time: int | None = None
    close_price: float | None = None
    pnl: float | None = None
    note: str | None = Field(None, max_length=200)


@app.post("/api/accounts/{aid}/actual/trades")
def add_real_trade(aid: int, body: RealTradeIn, u: str = Depends(user)):
    _acc_or_404(aid)
    if not body.pair or not body.direction:
        raise HTTPException(400, "pair and direction are required")
    return ledger.add_trade(aid, **body.model_dump(exclude_none=True))


@app.post("/api/actual/trades/{rid}")
def edit_real_trade(rid: int, body: RealTradeIn, u: str = Depends(user)):
    if not db.one("SELECT id FROM real_trades WHERE id=?", (rid,)):
        raise HTTPException(404, "trade not found")
    return ledger.edit_trade(rid, **body.model_dump(exclude_unset=True))


@app.delete("/api/actual/trades/{rid}")
def delete_real_trade(rid: int, u: str = Depends(user)):
    ledger.delete_trade(rid)
    return {"ok": True}


@app.post("/api/account_trades/{tid}/take")
def take_trade(tid: int, u: str = Depends(user)):
    r = ledger.take(tid, source="web")
    if not r:
        raise HTTPException(404, "alert trade not found")
    return r


@app.delete("/api/account_trades/{tid}/take")
def untake_trade(tid: int, u: str = Depends(user)):
    ledger.untake(tid)
    return {"ok": True}


class CashIn(BaseModel):
    amount: float = Field(description="positive = deposit, negative = withdrawal")
    ts: int | None = None
    note: str = Field("", max_length=200)


@app.post("/api/accounts/{aid}/cash")
def add_cash(aid: int, body: CashIn, u: str = Depends(user)):
    _acc_or_404(aid)
    if not body.amount:
        raise HTTPException(400, "amount must not be zero")
    return {"id": ledger.add_cash(aid, body.amount, body.ts, body.note)}


@app.delete("/api/cash/{cid}")
def delete_cash(cid: int, u: str = Depends(user)):
    ledger.delete_cash(cid)
    return {"ok": True}


class MatchIn(BaseModel):
    balance: float = Field(ge=0)


@app.post("/api/accounts/{aid}/actual/match")
def match_balance(aid: int, body: MatchIn, u: str = Depends(user)):
    return {"adjustment": ledger.match_balance(_acc_or_404(aid), body.balance)}


@app.get("/api/prop")
def get_prop(u: str = Depends(user)):
    return _prop_state()


@app.post("/api/prop/payout")
def record_payout(u: str = Depends(user)):
    """You received a payout: start a new reward cycle (the firm resets the max-loss limit)."""
    db.kv_set("prop_last_payout", db.now())
    db.add_event(None, "PAYOUT", f"Payout recorded by {u}; new reward cycle started")
    return _prop_state()


@app.post("/api/prop/reset")
def reset_prop(u: str = Depends(user)):
    """Start tracking from now (e.g. the day you begin taking the alerts on the funded account)."""
    db.kv_set("prop_start", db.now())
    db.kv_set("prop_last_payout", None)
    return _prop_state()


# ------------------------------------------------------------------ API

@app.get("/api/overview")
def overview(u: str = Depends(user)):
    a = account()
    stats = _analytics("live", None)
    open_rows = db.rows(f"SELECT {LIST_COLS} FROM setups WHERE source='live' AND status IN ('PENDING','TRIGGERED') "
                        "ORDER BY detected_at DESC")
    watches = db.rows(f"SELECT {LIST_COLS} FROM setups WHERE source='live' AND decision='WATCH' "
                      "ORDER BY detected_at DESC LIMIT 8")
    events = db.rows("SELECT e.*, s.pair, s.timeframe, s.direction FROM events e LEFT JOIN setups s ON s.id=e.setup_id "
                     "WHERE s.source='live' OR e.setup_id IS NULL ORDER BY e.id DESC LIMIT 25")
    since = int(time.time()) - 86400
    today = db.one("SELECT COUNT(*) n, SUM(decision='APPROVED') approved FROM setups "
                   "WHERE source='live' AND detected_at>? AND decision!='WATCH'", (since,))
    return {
        "account": a, "audit": {"state": "AUDIT" if audit_active() else "ACTIVE", "loss_streak": loss_streak(),
                                "since": db.kv_get("audit_since")},
        "scanner": {**(scanner.status if scanner else {}), "enabled": settings.run_scanner,
                    "pairs": settings.pairs, "timeframes": settings.ltf_timeframes,
                    "provider": settings.data_provider},
        "telegram": notifier.enabled(),
        "summary": stats["summary"], "funnel": stats["funnel"], "current_batch": stats["current_batch"],
        "equity": stats["equity"][-200:], "open": open_rows, "watches": watches, "events": events,
        "last24h": {"candidates": today["n"] or 0, "approved": today["approved"] or 0},
        "prop": _prop_state(),
    }


@app.get("/api/setups")
def list_setups(source: str = "live", run_id: int | None = None, decision: str = "", status: str = "",
                pair: str = "", timeframe: str = "", direction: str = "", limit: int = 100, offset: int = 0,
                u: str = Depends(user)):
    where, params = _source_filter(source, run_id)
    clauses, p = [where], list(params)
    for col, val in (("decision", decision), ("status", status), ("pair", pair.upper()),
                     ("timeframe", timeframe), ("direction", direction.upper())):
        if val:
            vals = [v for v in val.split(",") if v]
            clauses.append(f"{col} IN ({','.join('?' * len(vals))})")
            p += vals
    w = " AND ".join(clauses)
    total = db.one(f"SELECT COUNT(*) n FROM setups WHERE {w}", tuple(p))["n"]
    rows = db.rows(f"SELECT {LIST_COLS} FROM setups WHERE {w} ORDER BY detected_bar_time DESC, id DESC "
                   f"LIMIT ? OFFSET ?", (*p, min(limit, 500), offset))
    return {"total": total, "items": rows}


@app.get("/api/setups/{sid}")
def get_setup(sid: int, u: str = Depends(user)):
    s = db.one("SELECT * FROM setups WHERE id=?", (sid,))
    if not s:
        raise HTTPException(404, "not found")
    details = json.loads(s.pop("details") or "{}")
    candles = list(details.pop("candles", []) or []) + list(details.pop("forward", []) or [])
    ctx = scanner.contexts.get((s["pair"], s["timeframe"])) if scanner and s["source"] == "live" else None
    if ctx is not None:
        last = candles[-1][0] if candles else 0
        if not candles:
            candles = ctx.candles(len(ctx.B) - 250, len(ctx.B) - 1)
        elif s["status"] in ("PENDING", "TRIGGERED"):
            candles += [c for c in ctx.candles(ctx.index_of(last + 1), len(ctx.B) - 1) if c[0] > last]
    events = db.rows("SELECT * FROM events WHERE setup_id=? ORDER BY id", (sid,))
    return {"setup": s, "details": details, "candles": candles, "events": events,
            "decimals": price_decimals(s["pair"])}


@app.get("/api/analytics")
def get_analytics(source: str = "live", run_id: int | None = None, u: str = Depends(user)):
    return _analytics(source, run_id)


@app.post("/api/scan")
def scan_now(u: str = Depends(user)):
    if scanner is None:
        raise HTTPException(503, "scanner unavailable")
    if scanner.status.get("running"):
        return {"started": False, "message": "a scan is already running"}
    threading.Thread(target=scanner.run_once_safe, daemon=True).start()
    return {"started": True}


@app.get("/api/scans")
def scans(u: str = Depends(user)):
    return db.rows("SELECT * FROM scans ORDER BY id DESC LIMIT 50")


class BacktestIn(BaseModel):
    pairs: list[str] = []
    timeframe: str = "15m"
    days: int = Field(59, ge=5, le=730)


@app.post("/api/backtests")
def start_backtest(body: BacktestIn, u: str = Depends(user)):
    if body.timeframe not in HTF_MAP:
        raise HTTPException(400, "unsupported timeframe")
    if db.one("SELECT id FROM backtest_runs WHERE status IN ('QUEUED','RUNNING') AND started_at > ?",
              (db.now() - 3600,)):
        raise HTTPException(409, "a backtest is already running")
    pairs = [p.upper() for p in body.pairs if p.strip()] or settings.pairs
    days = min(body.days, 59 if body.timeframe in ("5m", "15m") and settings.data_provider == "yfinance" else 730)
    rid = backtest.create_run(pairs, body.timeframe, days)
    threading.Thread(target=lambda: _safe_bt(rid, pairs, body.timeframe, days), daemon=True).start()
    return {"run_id": rid, "days": days}


def _safe_bt(rid, pairs, tf, days):
    try:
        backtest.run(rid, pairs, tf, days)
    except Exception:
        pass


@app.get("/api/backtests")
def list_backtests(u: str = Depends(user)):
    out = []
    for r in db.rows("SELECT * FROM backtest_runs ORDER BY id DESC LIMIT 50"):
        r["params"] = json.loads(r["params"] or "{}")
        r["params"].pop("engine", None)
        r["stats"] = json.loads(r["stats"]) if r["stats"] else None
        out.append(r)
    return out


@app.delete("/api/backtests/{rid}")
def delete_backtest(rid: int, u: str = Depends(user)):
    db.execute("DELETE FROM setups WHERE source='backtest' AND run_id=?", (rid,))
    db.execute("DELETE FROM backtest_runs WHERE id=?", (rid,))
    return {"ok": True}


@app.get("/api/settings")
def get_settings(u: str = Depends(user)):
    return {
        "account": account(), "pairs": settings.pairs, "timeframes": settings.ltf_timeframes,
        "htf_map": HTF_MAP, "provider": settings.data_provider,
        "scan_interval_minutes": settings.scan_interval_minutes,
        "telegram": {"configured": notifier.enabled(), "token_set": bool(settings.telegram_token),
                     "chat_set": bool(settings.telegram_chat_id), "watch_alerts": settings.alert_watch,
                     "lifecycle_alerts": settings.alert_lifecycle},
        "engine": settings.engine.__dict__,
        "audit": {"state": "AUDIT" if audit_active() else "ACTIVE", "loss_streak": loss_streak()},
    }


class AccountIn(BaseModel):
    account_size: float = Field(gt=0)
    max_drawdown_pct: float = Field(gt=0, le=100)


@app.post("/api/settings/account")
def set_account(body: AccountIn, u: str = Depends(user)):
    db.kv_set("account_size", body.account_size)
    db.kv_set("max_drawdown_pct", body.max_drawdown_pct)
    return account()


@app.post("/api/telegram/test")
def telegram_test(u: str = Depends(user)):
    if not notifier.enabled():
        raise HTTPException(400, "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env and restart")
    ok = notifier.send("✅ <b>BigSam Alerts</b> test message — Telegram is connected.")
    if not ok:
        raise HTTPException(502, "Telegram rejected the message - check the token and chat id")
    return {"ok": True}


@app.post("/api/audit/reset")
def audit_reset(u: str = Depends(user)):
    db.kv_set("audit_state", "ACTIVE")
    db.kv_set("audit_reset_at", db.now())
    db.add_event(None, "AUDIT_RESET", f"Audit reset by {u}")
    return {"ok": True}


@app.exception_handler(HTTPException)
async def http_exc(request: Request, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index(request: Request):
    # asset/API URLs are relative, so the page must be served with a trailing slash
    # (e.g. /bigsamalerts -> /bigsamalerts/ when mounted under a path)
    full = request.scope.get("root_path", "") + request.scope.get("path", "/")
    if not request.url.path.endswith("/") and not full.endswith("/"):
        return RedirectResponse(request.url.path + "/", status_code=307)
    # stamp asset URLs with their mtime so browsers pick up new versions after an update
    v = int(max((STATIC / f).stat().st_mtime for f in ("app.js", "styles.css")))
    html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__V__", str(v))
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
