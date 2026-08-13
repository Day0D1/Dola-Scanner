"""FastAPI web app for the Dola Options Scanner."""
from __future__ import annotations

import datetime as dt
import io
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
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
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

        # Persist today's daily snapshot for the history page + exports.
        try:
            store.upsert_daily_snapshot({
                "date": dt.date.today().isoformat(),
                "spx_signal": breadth.spx.signal,
                "spx_column": breadth.spx.column,
                "spx_level": breadth.spx.level,
                "spx_change": breadth.spx.change,
                "bpnya_column": breadth.bpnya.column,
                "bpnya_level": breadth.bpnya.level,
                "bpnya_change": breadth.bpnya.change,
                "vix_column": breadth.vix.column,
                "vix_level": breadth.vix.level,
                "vix_change": breadth.vix.change,
                "regime": breadth.regime,
                "risk": breadth.risk,
            })
        except Exception as e:  # noqa: BLE001
            print(f"[scan] daily snapshot upsert failed: {e}")

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

    def _pillar(p) -> dict:
        return {
            "column": p.column,
            "level": p.level,
            "change": p.change,
            "signal": p.signal,
        }

    return {
        "status": "ok",
        "scanning": scanning,
        "last_scan_at": last,
        "breadth": {
            "regime": b.regime,
            "risk": b.risk,
            "verdict": b.verdict,  # legacy
            "spx": _pillar(b.spx),
            "vix": _pillar(b.vix),
            "bpnya": _pillar(b.bpnya),
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


# Metadata for the breadth pillars (clickable index charts).
_INDEX_KEYS = {
    "SPX": {
        "source": "yfinance", "symbol": "^GSPC",
        "chart_type": "candlestick", "pnf_type": "percentage",
        "display_name": "$SPX - S&P 500",
    },
    "VIX": {
        "source": "yfinance", "symbol": "^VIX",
        "chart_type": "candlestick", "pnf_type": "percentage",
        "display_name": "$VIX - Volatility Index",
    },
    "BPNYA": {
        "source": "internal_bpnya", "symbol": None,
        "chart_type": "line", "pnf_type": "traditional",
        "display_name": "$BPNYA - NYSE Bullish Percent (self-computed)",
    },
}


def _bpnya_series_as_ohlc() -> pd.DataFrame:
    history = store.get_bpnya_history()
    if not history:
        return pd.DataFrame()
    df = pd.DataFrame(
        [(pd.Timestamp(d).date(), v, v, v, v, 0) for d, v in history],
        columns=["date", "open", "high", "low", "close", "volume"],
    ).set_index("date")
    return df


def _build_chart_payload(
    *, ohlc: pd.DataFrame, timeframe: str,
    bb_period: int, bb_stddev: float, rsi_period: int,
    pnf_box: float, pnf_reversal: int, pnf_type: str, chart_type: str,
) -> dict:
    bb = indicators.bollinger_bands(ohlc["close"], bb_period, bb_stddev)
    rsi_series = indicators.rsi(ohlc["close"], rsi_period)

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
            "volume": int(row["volume"]) if row["volume"] else 0,
            "bb_upper": None if pd.isna(bb["upper"].loc[d]) else round(float(bb["upper"].loc[d]), 2),
            "bb_middle": None if pd.isna(bb["middle"].loc[d]) else round(float(bb["middle"].loc[d]), 2),
            "bb_lower": None if pd.isna(bb["lower"].loc[d]) else round(float(bb["lower"].loc[d]), 2),
            "rsi": None if pd.isna(rsi_series.loc[d]) else round(float(rsi_series.loc[d]), 1),
        })

    if pnf_type == "traditional":
        pnf_cols = indicators.point_figure_traditional(ohlc["close"], pnf_box, pnf_reversal)
        idx_to_price = lambda i: round(i * pnf_box, 2)
    else:
        pnf_cols = indicators.point_figure(ohlc["close"], pnf_box, pnf_reversal)
        idx_to_price = lambda i: round(_idx_to_price(i, pnf_box), 2)

    if candles:
        window_start = candles[0]["date"]
        pnf_windowed = [c for c in pnf_cols if (c.end_date or "") >= window_start]
        if not pnf_windowed:
            pnf_windowed = pnf_cols[-1:] if pnf_cols else []
    else:
        pnf_windowed = pnf_cols

    pnf = [{
        "type": c.type,
        "top_idx": c.top_idx,
        "bottom_idx": c.bottom_idx,
        "top_price": idx_to_price(c.top_idx),
        "bottom_price": idx_to_price(c.bottom_idx),
        "start_date": c.start_date,
        "end_date": c.end_date,
    } for c in pnf_windowed]

    return {
        "timeframe": tf,
        "available_timeframes": list(_TIMEFRAMES.keys()),
        "chart_type": chart_type,
        "candles": candles,
        "pnf": pnf,
        "pnf_type": pnf_type,
        "pnf_box": pnf_box,
        "pnf_reversal": pnf_reversal,
        "settings": {
            "bb_period": bb_period,
            "bb_stddev": bb_stddev,
            "rsi_period": rsi_period,
            "rsi_oversold": config.RSI_OVERSOLD,
            "rsi_overbought": config.RSI_OVERBOUGHT,
            "pnf_box": pnf_box,
            "pnf_reversal": pnf_reversal,
            "pnf_type": pnf_type,
        },
    }


@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request):
    return templates.TemplateResponse(request, "history.html")


_HISTORY_COLS = [
    ("date",         "Date"),
    ("spx_signal",   "SPX Signal"),
    ("spx_column",   "SPX Level"),
    ("spx_change",   "SPX Change"),
    ("bpnya_column", "BPNYA Signal"),
    ("bpnya_level",  "BPNYA Level"),
    ("bpnya_change", "BPNYA Change"),
    ("vix_column",   "VIX Signal"),
    ("vix_level",    "VIX Level"),
    ("vix_change",   "VIX Change"),
    ("regime",       "Regime"),
    ("risk",         "Risk"),
]


@app.get("/api/history")
def api_history(limit: int = 200):
    return {
        "columns": _HISTORY_COLS,
        "rows": store.get_daily_history(limit=limit),
    }


def _history_dataframe(limit: int) -> pd.DataFrame:
    rows = store.get_daily_history(limit=limit)
    keys = [k for k, _ in _HISTORY_COLS]
    labels = [lbl for _, lbl in _HISTORY_COLS]
    if not rows:
        return pd.DataFrame(columns=labels)
    df = pd.DataFrame(rows)[keys]
    df.columns = labels
    return df


@app.get("/api/history.csv")
def api_history_csv(limit: int = 1000):
    df = _history_dataframe(limit)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    csv = buf.getvalue()
    fname = f"dola-history-{dt.date.today().isoformat()}.csv"
    return Response(
        content=csv,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/history.xlsx")
def api_history_xlsx(limit: int = 1000):
    df = _history_dataframe(limit)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="History")
    buf.seek(0)
    fname = f"dola-history-{dt.date.today().isoformat()}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/stock/{ticker}")
def api_stock(
    ticker: str,
    timeframe: str = "3M",
    bb_period: Optional[int] = None,
    bb_stddev: Optional[float] = None,
    rsi_period: Optional[int] = None,
    pnf_box: Optional[float] = None,
    pnf_reversal: Optional[int] = None,
):
    ticker = ticker.upper()
    ohlc = data.fetch_stock(ticker, config.LOOKBACK_DAYS)
    if ohlc.empty:
        return JSONResponse({"error": "no data"}, status_code=404)

    payload = _build_chart_payload(
        ohlc=ohlc, timeframe=timeframe,
        bb_period=bb_period or config.BB_PERIOD,
        bb_stddev=bb_stddev if bb_stddev is not None else config.BB_STDDEV,
        rsi_period=rsi_period or config.RSI_PERIOD,
        pnf_box=pnf_box if pnf_box is not None else config.PNF_BOX_PCT,
        pnf_reversal=pnf_reversal or config.PNF_REVERSAL,
        pnf_type="percentage",
        chart_type="candlestick",
    )
    payload["ticker"] = ticker
    payload["display_name"] = ticker

    with _cache_lock:
        sigs = list(_cache["signals"])
    match: Optional[dict] = None
    for s in sigs:
        if s.ticker == ticker:
            match = _signal_to_dict(s)
            break
    payload["signal"] = match
    return payload


@app.get("/api/index/{key}")
def api_index(
    key: str,
    timeframe: str = "3M",
    bb_period: Optional[int] = None,
    bb_stddev: Optional[float] = None,
    rsi_period: Optional[int] = None,
    pnf_box: Optional[float] = None,
    pnf_reversal: Optional[int] = None,
    pnf_type: Optional[str] = None,
):
    key = key.upper()
    meta = _INDEX_KEYS.get(key)
    if not meta:
        return JSONResponse({"error": f"unknown index '{key}'"}, status_code=404)

    if meta["source"] == "yfinance":
        ohlc = data.fetch_index(meta["symbol"], config.LOOKBACK_DAYS)
    elif meta["source"] == "internal_bpnya":
        ohlc = _bpnya_series_as_ohlc()
    else:
        ohlc = pd.DataFrame()

    if ohlc.empty:
        return JSONResponse(
            {"error": "no data yet - BPNYA history accumulates one row per scan; expect ~2 weeks for meaningful P&F"},
            status_code=404,
        )

    default_box = 2.0 if meta["pnf_type"] == "traditional" else config.PNF_BOX_PCT
    default_reversal = 3 if meta["pnf_type"] == "traditional" else config.PNF_REVERSAL

    payload = _build_chart_payload(
        ohlc=ohlc, timeframe=timeframe,
        bb_period=bb_period or config.BB_PERIOD,
        bb_stddev=bb_stddev if bb_stddev is not None else config.BB_STDDEV,
        rsi_period=rsi_period or config.RSI_PERIOD,
        pnf_box=pnf_box if pnf_box is not None else default_box,
        pnf_reversal=pnf_reversal or default_reversal,
        pnf_type=(pnf_type or meta["pnf_type"]).lower(),
        chart_type=meta["chart_type"],
    )
    payload["ticker"] = key
    payload["display_name"] = meta["display_name"]
    payload["signal"] = None
    return payload
