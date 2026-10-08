"""Live market scanner: fetch -> detect -> validate -> persist -> alert -> track."""
from __future__ import annotations

import json
import logging
import threading
import time
import traceback

from . import accounts, db, news, notifier, prop
from .config import HTF_MAP, TF_SECONDS, settings
from .data import get_provider, pip_size
from .pipeline import OPEN_STATUSES, SeriesContext, TrackContext, advance, to_record
from .risk import AUDIT_LOSS_STREAK, risk_per_trade

log = logging.getLogger("bigsam.scanner")

LTF_DAYS = {"5m": 8, "15m": 25, "1h": 90}
HTF_DAYS = {"1h": 40, "4h": 240, "1d": 700}
HTF_REFRESH = {"1h": 600, "4h": 1200, "1d": 3600}
CATCHUP_BARS = 4
TRACK_STEP = 300          # seconds between trade-tracking ticks (5m candles)
TRACK_DAYS = 8


def account() -> dict:
    size = float(db.kv_get("account_size", settings.account_size))
    dd = float(db.kv_get("max_drawdown_pct", settings.max_drawdown_pct))
    return {"account_size": size, "max_drawdown_pct": dd, "risk_per_trade": risk_per_trade(size, dd),
            "currency": settings.account_currency}


def audit_active() -> bool:
    return db.kv_get("audit_state", "ACTIVE") == "AUDIT"


def loss_streak() -> int:
    since = db.kv_get("audit_reset_at", 0)
    rows = db.rows("SELECT status FROM setups WHERE source='live' AND status IN ('WIN','PARTIAL_WIN','LOSS') "
                   "AND closed_at > ? ORDER BY closed_at DESC, id DESC", (since,))
    n = 0
    for r in rows:
        if r["status"] != "LOSS":
            break
        n += 1
    return n


def _parse(row: dict) -> dict:
    rec = dict(row)
    if isinstance(rec.get("details"), str):
        try:
            rec["details"] = json.loads(rec["details"])
        except ValueError:
            rec["details"] = {}
    return rec


class Scanner:
    def __init__(self):
        self.provider = get_provider()
        self.lock = threading.Lock()
        self.contexts: dict[tuple[str, str], SeriesContext] = {}
        self.track_contexts: dict[str, TrackContext] = {}       # 5m candles of pairs with live trades
        self._htf: dict[str, tuple[float, dict]] = {}
        self.status = {"running": False, "last_run": None, "last_duration": None, "next_run": None,
                       "last_error": None, "scans": 0}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------- scheduling
    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scanner")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _next_tick(self) -> float:
        """Every 5 minutes (after each 5m candle closes). Ticks that land on the scan interval run
        the full setup scan; the others only track open/pending trades on 5m candles."""
        now = time.time()
        return (now // TRACK_STEP + 1) * TRACK_STEP + settings.scan_delay_seconds

    def _loop(self):
        self.run_once_safe()
        while not self._stop.is_set():
            nxt = self._next_tick()
            full = int((nxt - settings.scan_delay_seconds) // 60) % settings.scan_interval_minutes == 0
            if full:
                self.status["next_run"] = int(nxt)
            while time.time() < nxt and not self._stop.is_set():
                time.sleep(min(5, max(0.1, nxt - time.time())))
            if not self._stop.is_set():
                self.run_once_safe() if full else self.track_once_safe()

    def run_once_safe(self):
        try:
            self.run_once()
        except Exception as e:
            self.status["last_error"] = f"{type(e).__name__}: {e}"
            log.error("scan failed:\n%s", traceback.format_exc())

    def track_once_safe(self):
        if not self.lock.acquire(blocking=False):
            return
        try:
            self._track_accounts()
            self._exposure_notices()
            self._news_warnings()
            self.status["last_track"] = int(time.time())
        except Exception:
            log.error("tracking failed:\n%s", traceback.format_exc())
        finally:
            self.lock.release()

    def _track_accounts(self):
        """Walk every pending/open account trade through fresh 5m candles -> fill/partial/exit
        alerts within ~6 minutes, whatever the signal timeframe."""
        pairs = accounts.active_pairs()
        if not pairs:
            return
        data = self.provider.fetch(pairs, "5m", TRACK_DAYS)
        grouped: dict[int, list] = {}
        for pair in pairs:
            df = data.get(pair)
            if df is None or df.empty:
                continue
            ctx = TrackContext(pair, df)
            self.track_contexts[pair] = ctx
            for acc, t, kind, msg in accounts.advance_trades(ctx, settings.engine):
                grouped.setdefault(t["setup_id"], []).append((acc, t, kind, msg))
                db.add_event(t["setup_id"], kind, f"[{acc['name']}] {msg}")
        if settings.alert_lifecycle:
            for evs in grouped.values():
                notifier.send(notifier.account_events_message(evs))

    # ------------------------------------------------------------- data
    def _htf_data(self, htf: str) -> dict:
        cached = self._htf.get(htf)
        if cached and time.time() - cached[0] < HTF_REFRESH[htf]:
            return cached[1]
        data = self.provider.fetch(settings.pairs, htf, HTF_DAYS[htf])
        self._htf[htf] = (time.time(), data)
        return data

    # ------------------------------------------------------------- main
    def run_once(self) -> dict:
        if not self.lock.acquire(blocking=False):
            return {"skipped": True}
        t0 = time.time()
        self.status["running"] = True
        stats = {"pairs": 0, "candidates": 0, "approved": 0, "errors": []}
        try:
            P = settings.engine
            acct = account()
            for tf in settings.ltf_timeframes:
                htf = HTF_MAP[tf]
                ltf = self.provider.fetch(settings.pairs, tf, LTF_DAYS.get(tf, 30))
                hdata = self._htf_data(htf)
                prices = {p: float(df["close"].iloc[-1]) for p, df in ltf.items() if len(df)}
                for pair in settings.pairs:
                    df, hdf = ltf.get(pair), hdata.get(pair)
                    if df is None or hdf is None or len(df) < 60 or len(hdf) < 30:
                        stats["errors"].append(f"{pair} {tf}: insufficient data")
                        continue
                    try:
                        ctx = SeriesContext(pair, tf, htf, df, hdf, P)
                        self.contexts[(pair, tf)] = ctx
                        self._scan_series(ctx, acct, prices, stats)
                        stats["pairs"] += 1
                    except Exception as e:
                        log.error("%s %s failed:\n%s", pair, tf, traceback.format_exc())
                        stats["errors"].append(f"{pair} {tf}: {e}")
            try:
                self._track_accounts()
            except Exception as e:
                log.error("tracking failed:\n%s", traceback.format_exc())
                stats["errors"].append(f"tracking: {e}")
            self._check_audit()
            self._news_warnings()
            self._exposure_notices()
        finally:
            dur = round(time.time() - t0, 2)
            self.status.update(running=False, last_run=int(time.time()), last_duration=dur,
                               scans=self.status["scans"] + 1,
                               last_error="; ".join(stats["errors"][:5]) or None)
            db.execute("INSERT INTO scans(ts,duration,pairs,candidates,approved,errors) VALUES(?,?,?,?,?,?)",
                       (int(time.time()), dur, stats["pairs"], stats["candidates"], stats["approved"],
                        json.dumps(stats["errors"][:20])))
            self.lock.release()
        log.info("scan done in %.1fs: %s", time.time() - t0, {k: v for k, v in stats.items() if k != "errors"})
        return stats

    def _scan_series(self, ctx: SeriesContext, acct: dict, prices: dict, stats: dict):
        key = f"last_bar:{ctx.pair}:{ctx.tf}"
        last = db.kv_get(key, 0)
        n = len(ctx.B)
        start = max(n - CATCHUP_BARS, int(ctx.index_of(last + 1)) if last else 0, 100)
        for t in range(start, n):
            for cand, align in ctx.evaluate(t):
                stats["candidates"] += 1
                if self._process(ctx, cand, align, t, acct, prices):
                    stats["approved"] += 1
        self._advance_open(ctx)
        self._watch(ctx, last)
        db.kv_set(key, int(ctx.B.time[-1]))

    def _process(self, ctx, cand, align, t, acct, prices) -> bool:
        rec = to_record(ctx, cand, align, t, source="live", run_id=0, now=db.now(),
                        risk_amount=acct["risk_per_trade"], prices=prices, account_ccy=acct["currency"])
        existing = db.get_setup(rec["uid"], "live", 0)
        if existing and existing["decision"] != "REJECTED":
            return False
        if not cand.approved:
            if existing:
                db.update_setup(existing["id"], reject_reason=rec["reject_reason"], details=rec["details"],
                                last_bar_time=rec["last_bar_time"], detected_bar_time=rec["detected_bar_time"],
                                entry=rec["entry"], stop_loss=rec["stop_loss"], take_profit=rec["take_profit"],
                                poi_type=rec["poi_type"], liquidity_type=rec["liquidity_type"],
                                htf_zone=rec["htf_zone"])
            else:
                db.insert_setup(rec)
            return False

        dup = db.one("SELECT id FROM setups WHERE source='live' AND decision='APPROVED' AND pair=? AND timeframe=? "
                     "AND direction=? AND abs(entry-?)<?",
                     (rec["pair"], rec["timeframe"], rec["direction"], rec["entry"], pip_size(rec["pair"]) / 10))
        if dup:
            return False      # identical order already placed by an earlier structure
        suppressed = audit_active()
        rec["suppressed"] = int(suppressed)
        events = advance(rec, ctx, settings.engine)        # catch up if detected on an older bar
        skip, plans = None, []
        if rec["status"] == "PENDING" and not suppressed:  # size + guard per account
            plans = accounts.plan(rec, prices)
            if plans and all(pl["skip"] for pl in plans):
                skip = "; ".join(sorted({pl["skip"] for pl in plans}))
                rec.update(status="SKIPPED", reject_reason=skip)
        if existing:
            fields = {k: v for k, v in rec.items() if k not in ("uid", "source", "run_id")}
            db.update_setup(existing["id"], **fields)
            sid = existing["id"]
        else:
            sid = db.insert_setup(rec)
        rec["id"] = sid
        db.add_event(sid, "APPROVED", f"{rec['poi_type'].replace('_', ' ').title()} limit {rec['entry']:.6g} · SL {rec['stop_loss']:.6g} · TP {rec['take_profit']:.6g}")
        for kind, ts, msg in events:
            db.add_event(sid, kind, msg)
        if skip:
            db.add_event(sid, "SKIPPED", skip)
        elif rec["status"] == "PENDING" and not suppressed:
            if db.kv_get("live_since") is None:
                # first real alert with account trades: dashboard, analytics, audit streak and the
                # prop tracker all start counting from here (older test-era setups stay in the DB)
                go = rec["detected_at"]
                for k in ("live_since", "audit_reset_at", "prop_start"):
                    db.kv_set(k, go)
                db.add_event(sid, "GO_LIVE", "First live alert - statistics start from here")
            accounts.create(sid, rec, plans)
            rec["news"] = news.upcoming_for(rec["pair"], db.now(), 120)
            rec["plans"] = plans
            if notifier.send(notifier.setup_message(rec)):
                db.update_setup(sid, alerted=1)
                db.add_event(sid, "ALERT", "Telegram alert sent")
        elif suppressed:
            db.add_event(sid, "SUPPRESSED", "Alert suppressed: system in AUDIT state")
        return True

    def _exposure_notices(self):
        """Per account: the exposure cap is on OPEN positions. Tell the trader to cancel pending
        orders when the cap fills, and which orders to re-place when a slot frees up."""
        for acc in accounts.all_accounts():
            base = "FROM account_trades WHERE account_id=?"
            n_open = db.one(f"SELECT COUNT(*) n {base} AND status='TRIGGERED'", (acc["id"],))["n"]
            key = f"open_positions:{acc['id']}"
            prev = db.kv_get(key, 0)
            db.kv_set(key, n_open)
            cap = acc["max_open"]
            if (n_open >= cap) == (prev >= cap):
                continue
            pending = db.rows(f"SELECT setup_id id, pair, timeframe, direction, entry, stop_loss, take_profit, lots "
                              f"{base} AND status='PENDING' ORDER BY detected_bar_time", (acc["id"],))
            if n_open >= cap:
                notifier.send(notifier.cap_full_message(n_open, cap, pending, acc))
            elif pending:
                notifier.send(notifier.slot_free_message(n_open, cap, pending, acc))

    def _news_warnings(self):
        """Warn once per (trade, event) before high-impact news on a pending or open alerted trade:
        news spikes and slippage are the easiest way to breach a floating-loss rule."""
        now = db.now()
        rows = db.rows("SELECT setup_id id, pair, timeframe, direction, MAX(status) status, entry, stop_loss "
                       "FROM account_trades WHERE status IN ('PENDING','TRIGGERED') GROUP BY setup_id")
        for s in rows:
            for ev in news.upcoming_for(s["pair"], now):
                key = f"news_warned:{s['id']}:{ev['ts']}:{ev['currency']}"
                if db.kv_get(key):
                    continue
                db.kv_set(key, now)
                msg = notifier.news_message(s, ev, now)
                db.add_event(s["id"], "NEWS", msg.split("\n")[0].replace("<b>", "").replace("</b>", ""))
                notifier.send(msg)

    def _advance_open(self, ctx: SeriesContext):
        # 1) the system record of every setup (strategy statistics, no account rules)
        rows = db.rows("SELECT * FROM setups WHERE source='live' AND pair=? AND timeframe=? AND status IN (?,?)",
                       (ctx.pair, ctx.tf, *OPEN_STATUSES))
        for row in rows:
            rec = _parse(row)
            events = advance(rec, ctx, settings.engine)
            fields = {k: rec.get(k) for k in ("status", "triggered_at", "closed_at", "result_r", "pnl",
                                              "last_bar_time", "details", "partial_at")}
            db.update_setup(rec["id"], **fields)
            for kind, ts, msg in events:
                db.add_event(rec["id"], kind, msg)
        # 2) account trades are tracked separately on 5m candles (_track_accounts)

    def _watch(self, ctx: SeriesContext, last_bar: int):
        """Confirmation-entry protocol: price taps an unmitigated HTF POI that sits in the right
        HTF zone -> tell the trader to wait for a full LTF sequence (no limit order)."""
        if not last_bar:
            return
        n = len(ctx.B)
        ht = ctx.htf_index(n - 1)
        hctx = ctx.htf_ctx(ht)
        if hctx is None:
            return
        i0 = ctx.index_of(last_bar + 1)
        if i0 >= n:
            return
        lo, hi = float(ctx.B.l[i0:].min()), float(ctx.B.h[i0:].max())
        for direction, zones in (("BUY", hctx.demand_zones), ("SELL", hctx.supply_zones)):
            for prox, dist, ztime in zones:
                in_zone = (prox < hctx.equilibrium) if direction == "BUY" else (prox > hctx.equilibrium)
                touched = (lo <= prox and hi >= dist) if direction == "BUY" else (hi >= prox and lo <= dist)
                if not (in_zone and touched):
                    continue
                uid = f"{ctx.pair}|{ctx.htf}|WATCH|{direction}|{ztime}"
                if db.get_setup(uid, "live", 0):
                    continue
                rec = {"uid": uid, "source": "live", "run_id": 0, "pair": ctx.pair, "timeframe": ctx.tf,
                       "htf": ctx.htf, "direction": direction, "decision": "WATCH", "status": "WATCH",
                       "poi_type": "HTF_ORDER_BLOCK", "entry": prox, "stop_loss": dist,
                       "htf_zone": "DISCOUNT" if direction == "BUY" else "PREMIUM",
                       "detected_bar_time": int(ctx.B.time[-1]), "detected_at": db.now(),
                       "last_bar_time": int(ctx.B.time[-1]),
                       "details": {"zone": {"proximal": prox, "distal": dist, "time": ztime},
                                   "htf": {"range_low": hctx.range_low, "range_high": hctx.range_high,
                                           "equilibrium": hctx.equilibrium}}}
                sid = db.insert_setup(rec)
                db.add_event(sid, "WATCH", "HTF POI tapped - waiting for LTF confirmation")
                if settings.alert_watch and not audit_active():
                    notifier.send(notifier.watch_message(ctx.pair, ctx.htf, ctx.tf, direction, prox, dist))

    def _check_audit(self):
        streak = loss_streak()
        if streak >= AUDIT_LOSS_STREAK and not audit_active():
            db.kv_set("audit_state", "AUDIT")
            db.kv_set("audit_since", db.now())
            db.add_event(None, "AUDIT", f"{streak} consecutive losses - alerts suspended")
            notifier.send(notifier.audit_message(streak))
