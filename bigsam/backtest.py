"""Walk-forward backtest: the exact live engine replayed bar by bar (no look-ahead)."""
from __future__ import annotations

import json
import logging
import time
import traceback

from . import analytics, db, prop
from .config import HTF_MAP, settings
from .data import get_provider
from .pipeline import OPEN_STATUSES, SeriesContext, advance, to_record, trade_key

log = logging.getLogger("bigsam.backtest")
WARMUP = 100


def create_run(pairs: list[str], tf: str, days: int) -> int:
    params = {"pairs": pairs, "timeframe": tf, "htf": HTF_MAP[tf], "days": days,
              "engine": settings.engine.__dict__}
    return db.execute("INSERT INTO backtest_runs(started_at,status,params,message) VALUES(?,?,?,?)",
                      (db.now(), "QUEUED", json.dumps(params), "queued"))


def _msg(run_id: int, status: str, message: str):
    db.execute("UPDATE backtest_runs SET status=?, message=? WHERE id=?", (status, message, run_id))


def run(run_id: int, pairs: list[str], tf: str, days: int) -> dict:
    from .scanner import account

    try:
        P = settings.engine
        htf = HTF_MAP[tf]
        acct = account()
        provider = get_provider()
        _msg(run_id, "RUNNING", f"downloading {tf} + {htf} data")
        ltf = provider.fetch(pairs, tf, days)
        hdata = provider.fetch(pairs, htf, days + (200 if htf == "1d" else 60))
        prices = {p: float(df["close"].iloc[-1]) for p, df in ltf.items() if len(df)}
        all_recs: list[dict] = []
        for n_pair, pair in enumerate(pairs, 1):
            df, hdf = ltf.get(pair), hdata.get(pair)
            if df is None or hdf is None or len(df) < WARMUP + 10:
                continue
            _msg(run_id, "RUNNING", f"{pair} ({n_pair}/{len(pairs)})")
            ctx = SeriesContext(pair, tf, htf, df, hdf, P)
            book: dict[str, dict] = {}
            open_recs: list[dict] = []
            taken: set[tuple] = set()
            for t in range(WARMUP, len(ctx.B)):
                if open_recs:
                    for rec in open_recs:
                        advance(rec, ctx, P, upto=t)
                    open_recs = [r for r in open_recs if r["status"] in OPEN_STATUSES]
                for cand, align in ctx.evaluate(t):
                    uid = f"{pair}|{tf}|{cand.direction}|{cand.protected_time}|{cand.bos_time}"
                    existing = book.get(uid)
                    if existing is not None and existing["decision"] != "REJECTED":
                        continue
                    if existing is not None and not cand.approved:
                        existing["reject_reason"] = cand.reject_reason
                        continue
                    rec = to_record(ctx, cand, align, t, source="backtest", run_id=run_id,
                                    now=int(ctx.B.time[t]), risk_amount=acct["risk_per_trade"],
                                    prices=prices, account_ccy=acct["currency"])
                    if not cand.approved:
                        rec["details"] = {"htf": align, "checks": cand.checks}
                    elif trade_key(rec) in taken:
                        continue          # same order already placed by an earlier structure
                    else:
                        taken.add(trade_key(rec))
                    book[uid] = rec
                    if cand.approved:
                        open_recs.append(rec)
            all_recs.extend(book.values())
        prop.apply_exposure_cap(all_recs)     # prop-firm exposure cap + no hedging, across all pairs
        db.bulk_insert_setups(all_recs)
        stats = analytics.compute(all_recs, acct["account_size"], acct["risk_per_trade"])
        compact = {"summary": stats["summary"], "funnel": stats["funnel"]}
        db.execute("UPDATE backtest_runs SET status=?, finished_at=?, stats=?, message=? WHERE id=?",
                   ("DONE", db.now(), json.dumps(compact), f"{len(all_recs)} candidates", run_id))
        return compact
    except Exception as e:
        log.error("backtest %s failed:\n%s", run_id, traceback.format_exc())
        db.execute("UPDATE backtest_runs SET status=?, finished_at=?, message=? WHERE id=?",
                   ("FAILED", db.now(), f"{type(e).__name__}: {e}", run_id))
        raise
