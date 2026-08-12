"""FastAPI web app for the Dola Options Scanner."""
from __future__ import annotations

import datetime as dt
import threading
import time
from contextlib import asynccontextmanager
from math import exp, log
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from concurrent.futures import ThreadPoolExecutor, as_completed

from scanner import config, data, indicators, store, universe
from scanner.breadth import read_breadth
from scanner.notify import send_scan_summary
from scanner.signals import StockSignal, evaluate_stock


ET = ZoneInfo("America/New_York")


BASE_DIR = Path(__file__).parent


# --- Shared cache ----------------------------------------------------------

_cache: dict = {
    "last_scan_at": None,
    "scanning": False,
    "breadth": None,
    "signals": [],
    "error": None,
    "fresh_entries": set(),
    "fresh_candidates": set(),
}
_cache_lock = threading.Lock()


def _scan_one_ticker(ticker: str) -> Optional[StockSignal]:
    try:
        ohlc = data.fetch_stock(ticker, config.LOOKBACK_DAYS)
        if ohlc.empty:
            return None
        return evaluate_stock(ticker, ohlc)
    except Exception as e:  # noqa: BLE001
        print(f"[scan] {ticker} failed: {e}")
        return None


def _scan_universe_tickers() -> list[str]:
    if config.USE_FULL_UNIVERSE:
        try:
            return universe.get_universe_tickers()
        except Exception as e:  # noqa: BLE001
            print(f"[scan] universe fetch failed, falling back to MVP: {e}")
    return config.MVP_UNIVERSE


def _run_scan_sync(notify: bool = True) -> None:
    with _cache_lock:
        if _cache["scanning"]:
            return
        _cache["scanning"] = True
    try:
        tickers = _scan_universe_tickers()

        signals: list[StockSignal] = []
        with ThreadPoolExecutor(max_workers=config.SCAN_WORKERS) as ex:
            futures = {ex.submit(_scan_one_ticker, t): t for t in tickers}
            for fut in as_completed(futures):
                sig = fut.result()
                if sig:
                    signals.append(sig)
        signals.sort(key=lambda s: s.ticker)

        # Compute today's $BPNYA from freshly-computed P&F states.
        pnf_scored = [s for s in signals if s.pnf_column in ("X", "O")]
        if pnf_scored:
            x_count = sum(1 for s in pnf_scored if s.pnf_column == "X")
            today_pct = round(100.0 * x_count / len(pnf_scored), 2)
            today_iso = dt.date.today().isoformat()
            try:
                store.upsert_bpnya(today_iso, today_pct, len(pnf_scored))
            except Exception as e:  # noqa: BLE001
                print(f"[scan] BPNYA upsert failed: {e}")

        # Now read breadth (uses the fresh BPNYA point we just stored).
        breadth = read_breadth()

        now_utc = dt.datetime.now(dt.timezone.utc)

        # Persist every scan to history (regardless of notify).
        try:
            store.record_scan(now_utc, breadth, signals)
        except Exception as e:  # noqa: BLE001
            print(f"[scan] store.record_scan failed: {e}")

        # Compute freshness for notification de-dup (only when we'll notify).
        fresh_entries: set[str] = set()
        fresh_candidates: set[str] = set()
        if notify:
            for s in signals:
                if s.entry_trigger and not store.was_entry_alerted_recently(s.ticker, s.entry_trigger):
                    fresh_entries.add(s.ticker)
                    store.mark_entry_alerted(s.ticker, s.entry_trigger, now_utc)
                if s.candidate and not store.was_candidate_alerted_recently(s.ticker, s.candidate):
                    fresh_candidates.add(s.ticker)
                    store.mark_candidate_alerted(s.ticker, s.candidate, now_utc)

        with _cache_lock:
            _cache["last_scan_at"] = time.time()
            _cache["breadth"] = breadth
            _cache["signals"] = signals
            _cache["error"] = None
            _cache["fresh_entries"] = fresh_entries
            _cache["fresh_candidates"] = fresh_candidates

        if notify:
            try:
                send_scan_summary(breadth, signals, fresh_entries, fresh_candidates)
            except Exception as e:  # noqa: BLE001
                print(f"[scan] telegram send failed: {e}")
    except Exception as e:  # noqa: BLE001
        with _cache_lock:
            _cache["error"] = f"{type(e).__name__}: {e}"
    finally:
        with _cache_lock:
            _cache["scanning"] = False


def _start_scan_bg(notify: bool = True) -> None:
    threading.Thread(target=_run_scan_sync, args=(notify,), daemon=True).start()


# --- Scheduler -------------------------------------------------------------

_scheduler: Optional[BackgroundScheduler] = None


def _scheduled_scan() -> None:
    print(f"[scheduler] hourly scan tick at {dt.datetime.now(ET).isoformat(timespec='seconds')}")
    _run_scan_sync(notify=True)


# --- FastAPI app -----------------------------------------------------------

@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _scheduler
    store.init_db()
    # First scan on startup so the dashboard is populated immediately.
    _start_scan_bg(notify=False)

    # Hourly scan Mon-Fri, 10:00 AM through 4:00 PM ET (tz pinned on the trigger too).
    _scheduler = BackgroundScheduler(timezone=ET)
    _scheduler.add_job(
        _scheduled_scan,
        CronTrigger(day_of_week="mon-fri", hour="10-16", minute=0, timezone=ET),
        id="hourly_scan",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    job = _scheduler.get_job("hourly_scan")
    if job:
        print(f"[scheduler] started, next run: {job.next_run_time}")
    try:
        yield
    finally:
        if _scheduler:
            _scheduler.shutdown(wait=False)


app = FastAPI(title="Dola Options Scanner", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


# --- Helpers ---------------------------------------------------------------

def _idx_to_price(box_idx: int, box_pct: float = 1.0) -> float:
    return float(exp(box_idx * log(1.0 + box_pct / 100.0)))


_TICKER_TO_SECTOR: Optional[dict] = None


def _get_ticker_to_sector() -> dict:
    global _TICKER_TO_SECTOR
    if _TICKER_TO_SECTOR is None:
        try:
            _TICKER_TO_SECTOR = universe.get_ticker_to_sector()
        except Exception:
            _TICKER_TO_SECTOR = {}
    return _TICKER_TO_SECTOR


def _signal_to_dict(s: StockSignal) -> dict:
    return {
        "ticker": s.ticker,
        "sector": _get_ticker_to_sector().get(s.ticker, "Unknown"),
        "last_close": round(s.last_close, 2),
        "rsi": None if pd.isna(s.rsi) else round(s.rsi, 1),
        "bb_upper": round(s.bb_upper, 2),
        "bb_middle": round(s.bb_middle, 2),
        "bb_lower": round(s.bb_lower, 2),
        "pnf_column": s.pnf_column,
        "candidate": s.candidate,
        "entry_trigger": s.entry_trigger,
        "band_pierce_today": s.band_pierce_today,
    }


# --- Routes ---------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")


@app.get("/api/scan")
def api_scan():
    with _cache_lock:
        b = _cache["breadth"]
        sigs = list(_cache["signals"])
        last = _cache["last_scan_at"]
        scanning = _cache["scanning"]
        err = _cache["error"]

    if err:
        return {"status": "error", "error": err, "scanning": scanning}
    if not b:
        return {"status": "loading", "scanning": scanning}

    return {
        "status": "ok",
        "scanning": scanning,
        "last_scan_at": last,
        "breadth": {
            "verdict": b.verdict,
            "spx": b.spx_trend,
            "vix": b.vix_trend,
            "bpnya": b.bpnya_trend,
            "bpnya_pct": b.bpnya_pct,
        },
        "signals": [_signal_to_dict(s) for s in sigs],
    }


@app.post("/api/scan/refresh")
def api_scan_refresh(notify: bool = False):
    with _cache_lock:
        if _cache["scanning"]:
            return {"status": "already_scanning"}
    _start_scan_bg(notify=notify)
    return {"status": "started"}


@app.get("/api/schedule")
def api_schedule():
    if not _scheduler:
        return {"status": "not_started"}
    job = _scheduler.get_job("hourly_scan")
    if not job:
        return {"status": "no_job"}
    return {
        "status": "ok",
        "next_run_at": job.next_run_time.isoformat() if job.next_run_time else None,
        "trigger": str(job.trigger),
    }


_TIMEFRAMES = {
    "1M": 22,
    "3M": 66,
    "6M": 132,
    "1Y": 252,
    "ALL": 10_000,
}


@app.get("/api/stock/{ticker}")
def api_stock(ticker: str, timeframe: str = "3M"):
    ticker = ticker.upper()
    ohlc = data.fetch_stock(ticker, config.LOOKBACK_DAYS)
    if ohlc.empty:
        return JSONResponse({"error": "no data"}, status_code=404)

    bb = indicators.bollinger_bands(ohlc["close"], config.BB_PERIOD, config.BB_STDDEV)
    rsi_series = indicators.rsi(ohlc["close"], config.RSI_PERIOD)

    tf = timeframe.upper()
    window = _TIMEFRAMES.get(tf, _TIMEFRAMES["3M"])
    tail = ohlc.tail(window)

    candles = []
    for d, row in tail.iterrows():
        candles.append({
            "date": str(d),
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
            "volume": int(row["volume"]),
            "bb_upper": None if pd.isna(bb["upper"].loc[d]) else round(float(bb["upper"].loc[d]), 2),
            "bb_middle": None if pd.isna(bb["middle"].loc[d]) else round(float(bb["middle"].loc[d]), 2),
            "bb_lower": None if pd.isna(bb["lower"].loc[d]) else round(float(bb["lower"].loc[d]), 2),
            "rsi": None if pd.isna(rsi_series.loc[d]) else round(float(rsi_series.loc[d]), 1),
        })

    pnf_cols = indicators.point_figure(ohlc["close"], config.PNF_BOX_PCT, config.PNF_REVERSAL)

    # Trim P&F to columns whose end_date falls in the requested window.
    if candles:
        window_start = candles[0]["date"]
        pnf_cols_windowed = [c for c in pnf_cols if (c.end_date or "") >= window_start]
        if not pnf_cols_windowed:
            pnf_cols_windowed = pnf_cols[-1:]  # always show at least the current column
    else:
        pnf_cols_windowed = pnf_cols

    pnf = [
        {
            "type": col.type,
            "top_idx": col.top_idx,
            "bottom_idx": col.bottom_idx,
            "top_price": round(_idx_to_price(col.top_idx, config.PNF_BOX_PCT), 2),
            "bottom_price": round(_idx_to_price(col.bottom_idx, config.PNF_BOX_PCT), 2),
            "start_date": col.start_date,
            "end_date": col.end_date,
        }
        for col in pnf_cols_windowed
    ]

    with _cache_lock:
        sigs = list(_cache["signals"])
    match: Optional[dict] = None
    for s in sigs:
        if s.ticker == ticker:
            match = _signal_to_dict(s)
            break

    return {
        "ticker": ticker,
        "timeframe": tf,
        "available_timeframes": list(_TIMEFRAMES.keys()),
        "candles": candles,
        "pnf": pnf,
        "pnf_box_pct": config.PNF_BOX_PCT,
        "signal": match,
    }
