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
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import dataclasses
from concurrent.futures import ThreadPoolExecutor, as_completed

from scanner import config, data, ibd50, indicators, store, universe
from scanner.breadth import read_breadth
from scanner.notify import send_scan_summary
from scanner.signals import StockSignal, evaluate_stock
from scanner.strike import compute_strike


IBD50_WATCHLIST_NAME = "ibd50"


def _mask_signal(s: StockSignal) -> StockSignal:
    """Nullify candidate/entry_trigger fields the current SIGNAL_MODE hides."""
    mode = config.SIGNAL_MODE
    if mode == "both":
        return s
    new_candidate = s.candidate
    new_entry = s.entry_trigger
    if mode == "puts_only":
        if new_candidate == "MUSK":
            new_candidate = None
        if new_entry == "SELL_CALLS":
            new_entry = None
    elif mode == "calls_only":
        if new_candidate == "ELON":
            new_candidate = None
        if new_entry == "SELL_PUTS":
            new_entry = None
    if new_candidate is s.candidate and new_entry is s.entry_trigger:
        return s
    return dataclasses.replace(s, candidate=new_candidate, entry_trigger=new_entry)


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
    tickers: list[str] = []
    if config.USE_FULL_UNIVERSE:
        try:
            tickers = list(universe.get_universe_tickers())
        except Exception as e:  # noqa: BLE001
            print(f"[scan] universe fetch failed, falling back to MVP: {e}")
            tickers = list(config.MVP_UNIVERSE)
    else:
        tickers = list(config.MVP_UNIVERSE)
    if getattr(config, "MAJOR_WATCHLIST", None):
        tickers = sorted(set(tickers) | set(config.MAJOR_WATCHLIST))
    ibd = _ibd50_watchlist_set()
    if ibd:
        tickers = sorted(set(tickers) | ibd)
    return tickers


def _major_watchlist_set() -> set:
    return set(getattr(config, "MAJOR_WATCHLIST", []) or [])


def _ibd50_watchlist_set() -> set:
    try:
        row = store.get_watchlist(IBD50_WATCHLIST_NAME)
        return set(row["tickers"]) if row else set()
    except Exception as e:  # noqa: BLE001
        print(f"[ibd50] get_watchlist failed: {e}")
        return set()


def _refresh_ibd50(force: bool = False) -> dict:
    """Fetch the current IBD 50 list from CapForce and persist to DB.

    Returns a summary dict so API callers see what happened. If force=False
    and the DB already has today's snapshot, we skip the network call.
    """
    existing = store.get_watchlist(IBD50_WATCHLIST_NAME)
    if existing and not force:
        today_iso = dt.date.today().isoformat()
        if existing.get("as_of_date") == today_iso:
            return {"status": "cached", "as_of_date": today_iso, "count": len(existing["tickers"])}

    try:
        snap = ibd50.fetch_ibd50()
    except Exception as e:  # noqa: BLE001
        print(f"[ibd50] fetch failed: {e}")
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}

    store.upsert_watchlist(
        IBD50_WATCHLIST_NAME,
        snap.tickers,
        source_url=snap.source_url,
        as_of_date=snap.as_of_date,
        meta={
            "raw_count": snap.raw_count,
            "fetched_at": snap.fetched_at,
            "sectors": snap.sectors or {},
        },
    )
    # Invalidate the cached sector map so the new IBD sectors get merged in.
    global _TICKER_TO_SECTOR
    _TICKER_TO_SECTOR = None
    print(f"[ibd50] refreshed: {len(snap.tickers)} tickers as of {snap.as_of_date}")
    return {
        "status": "refreshed",
        "as_of_date": snap.as_of_date,
        "count": len(snap.tickers),
        "tickers": snap.tickers,
    }


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

        # BPNYA is now sourced from the user's daily CSV import + daily-entry
        # form (both marked source='import' or 'manual'). The scanner's own
        # ~333-stock X% metric diverged materially from the real NYSE Bullish
        # Percent Index (~2,400 stocks) and was overwriting weekend rows with
        # stale values. Reading breadth below falls back to the last imported
        # row when today is not yet entered, which is the correct behavior
        # until the user posts today's OHLC.

        # Now read breadth (uses the fresh BPNYA point we just stored).
        breadth = read_breadth()

        # Apply signal-mode filter (mask MUSK/SELL_CALLS in puts_only mode, etc.).
        # Full signals still go into scan_history for auditing; user-facing paths
        # (dashboard cache, dedup, Telegram) use the masked list.
        masked_signals = [_mask_signal(s) for s in signals]

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
        # Iterate the MASKED list so we only mark dedup for signal sides the
        # user actually sees. That way flipping SIGNAL_MODE back later doesn't
        # find the other side already 'alerted'.
        fresh_entries: set[str] = set()
        fresh_candidates: set[str] = set()
        if notify:
            for s in masked_signals:
                if s.entry_trigger and not store.was_entry_alerted_recently(s.ticker, s.entry_trigger):
                    fresh_entries.add(s.ticker)
                    # Record the last close as the trigger price for perf tracking.
                    store.mark_entry_alerted(
                        s.ticker, s.entry_trigger, now_utc, trigger_price=s.last_close
                    )
                if s.candidate and not store.was_candidate_alerted_recently(s.ticker, s.candidate):
                    fresh_candidates.add(s.ticker)
                    store.mark_candidate_alerted(s.ticker, s.candidate, now_utc)

        with _cache_lock:
            _cache["last_scan_at"] = time.time()
            _cache["breadth"] = breadth
            _cache["signals"] = masked_signals
            _cache["error"] = None
            _cache["fresh_entries"] = fresh_entries
            _cache["fresh_candidates"] = fresh_candidates

        if notify:
            try:
                send_scan_summary(
                    breadth, masked_signals, fresh_entries, fresh_candidates,
                    ibd50_tickers=_ibd50_watchlist_set(),
                )
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

    # Seed the IBD 50 list SYNCHRONOUSLY before kicking off the first scan, so
    # the merged universe includes IBD 50 tickers from the very first scan.
    # Costs a few extra seconds at startup; worth it. Errors are logged but
    # non-fatal — a missing IBD 50 seed just means the first scan skips those
    # tickers until the Monday cron catches up.
    try:
        existing = store.get_watchlist(IBD50_WATCHLIST_NAME)
        if not existing:
            _refresh_ibd50(force=True)
    except Exception as e:  # noqa: BLE001
        print(f"[ibd50] startup seed failed: {e}")

    # First scan on startup so the dashboard is populated immediately.
    _start_scan_bg(notify=False)

    # Hourly scan Mon-Fri 10:00 AM - 4:00 PM ET (intraday), plus an EOD scan at
    # 4:45 PM ET so any P&F flip from today's actual close fires an alert the
    # same evening instead of waiting for tomorrow's 10 AM scan. Weekly IBD 50
    # refresh runs Monday 8:00 AM ET (before the 10 AM scan) so the fresh list
    # is in the universe for the first scan of the week.
    _scheduler = BackgroundScheduler(timezone=ET)
    _scheduler.add_job(
        _scheduled_scan,
        CronTrigger(day_of_week="mon-fri", hour="10-16", minute=0, timezone=ET),
        id="hourly_scan",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _scheduled_scan,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=45, timezone=ET),
        id="eod_scan",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        lambda: _refresh_ibd50(force=True),
        CronTrigger(day_of_week="mon", hour=8, minute=0, timezone=ET),
        id="ibd50_refresh",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    for jid in ("hourly_scan", "eod_scan", "ibd50_refresh"):
        j = _scheduler.get_job(jid)
        if j:
            print(f"[scheduler] {jid} next run: {j.next_run_time}")
    try:
        yield
    finally:
        if _scheduler:
            _scheduler.shutdown(wait=False)


app = FastAPI(title="Dola Options Scanner", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Bust the browser cache for static assets whenever the app restarts. Passed
# into every template as `asset_v`; templates append `?v={{ asset_v }}` to
# <script>/<link> href attrs. Cheap, correct, no filesystem probing needed.
_ASSET_VERSION = str(int(time.time()))
templates.env.globals["asset_v"] = _ASSET_VERSION


# --- Helpers ---------------------------------------------------------------

def _idx_to_price(box_idx: int, box_pct: float = 1.0) -> float:
    return float(exp(box_idx * log(1.0 + box_pct / 100.0)))


def _price_to_box_idx(price: float, box_pct: float = 1.0) -> int:
    import math
    return int(math.floor(math.log(price) / math.log(1.0 + box_pct / 100.0)))


_TICKER_TO_SECTOR: Optional[dict] = None


def _get_ticker_to_sector() -> dict:
    """Merged sector map: S&P 500 GICS sectors + IBD 50 sectors from CapForce.
    Overlapping tickers keep the S&P GICS label (preferred). Missing IBD tickers
    fall back to CapForce's less-formal sector label so the UI never shows
    "Unknown" for a ticker whose sector is available anywhere.
    """
    global _TICKER_TO_SECTOR
    if _TICKER_TO_SECTOR is not None:
        return _TICKER_TO_SECTOR
    try:
        sp = universe.get_ticker_to_sector()
    except Exception:
        sp = {}
    merged = {}
    # Start with IBD 50 sectors (weaker labels), then overlay S&P GICS.
    try:
        row = store.get_watchlist(IBD50_WATCHLIST_NAME)
        if row and row.get("meta") and isinstance(row["meta"].get("sectors"), dict):
            merged.update(row["meta"]["sectors"])
    except Exception:
        pass
    merged.update(sp)
    _TICKER_TO_SECTOR = merged
    return _TICKER_TO_SECTOR


def _signal_to_dict(s: StockSignal) -> dict:
    watch = _major_watchlist_set()
    ibd = _ibd50_watchlist_set()
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
        "on_watchlist": s.ticker in watch,
        "on_ibd50": s.ticker in ibd,
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
        "settings": {
            "bb_period": config.BB_PERIOD,
            "bb_stddev": config.BB_STDDEV,
            "rsi_period": config.RSI_PERIOD,
            "rsi_oversold": config.RSI_OVERSOLD,
            "rsi_overbought": config.RSI_OVERBOUGHT,
            "signal_mode": config.SIGNAL_MODE,
        },
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


@app.get("/api/telegram_test")
def api_telegram_test(msg: Optional[str] = None):
    """Diagnostic: sends a message using the server's env vars and returns the raw result."""
    from scanner.notify import send_telegram
    text = msg or f"[Render diagnostic] Ping at {dt.datetime.now(ET).isoformat(timespec='seconds')} ET"
    token_len = len(config.TELEGRAM_BOT_TOKEN or "")
    chat_id = config.TELEGRAM_CHAT_ID
    try:
        result = send_telegram(text)
        return {"status": "sent", "token_len": token_len, "chat_id": chat_id, "result": result}
    except Exception as e:
        # Try to surface the underlying Telegram HTTP error text if there was one.
        detail = ""
        try:
            import requests
            r = requests.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                data={"chat_id": config.TELEGRAM_CHAT_ID, "text": "raw retry"},
                timeout=10,
            )
            detail = f" | raw-retry status={r.status_code} body={r.text[:400]}"
        except Exception as ee:
            detail = f" | raw-retry crashed: {ee}"
        return {
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
            "token_len": token_len,
            "chat_id": chat_id,
            "detail": detail,
        }


@app.get("/api/debug")
def api_debug():
    """Small stats snapshot for debugging on the live server."""
    with _cache_lock:
        cached_err = _cache["error"]
        cached_scan_at = _cache["last_scan_at"]
        cached_signals = len(_cache["signals"])
    try:
        import sqlite3
        c = sqlite3.connect(store.DB_PATH)
        counts = {
            "scan_history": c.execute("SELECT COUNT(*) FROM scan_history").fetchone()[0],
            "entry_alerts": c.execute("SELECT COUNT(*) FROM entry_alerts").fetchone()[0],
            "candidate_alerts": c.execute("SELECT COUNT(*) FROM candidate_alerts").fetchone()[0],
            "bpnya_history": c.execute("SELECT COUNT(*) FROM bpnya_history").fetchone()[0],
            "daily_snapshot": c.execute("SELECT COUNT(*) FROM daily_snapshot").fetchone()[0],
        }
        last_scans = [r[0] for r in c.execute(
            "SELECT scan_at FROM scan_history ORDER BY id DESC LIMIT 8"
        )]
    except Exception as e:
        counts = {"error": str(e)}
        last_scans = []
    scheduler_info = None
    if _scheduler:
        job = _scheduler.get_job("hourly_scan")
        if job:
            scheduler_info = {
                "next_run_at": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
            }
    return {
        "now_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "now_et":  dt.datetime.now(ET).isoformat(timespec="seconds"),
        "data_dir": str(store.DATA_DIR),
        "counts": counts,
        "last_scan_times": last_scans,
        "cached_error": cached_err,
        "cached_signals": cached_signals,
        "cached_last_scan_at": cached_scan_at,
        "scheduler": scheduler_info,
    }


@app.get("/api/tickers")
def api_tickers():
    """Return the searchable ticker list with sector info for autocomplete."""
    sector_map = _get_ticker_to_sector()
    with _cache_lock:
        sigs = list(_cache["signals"])
    if sigs:
        return {"tickers": [
            {"ticker": s.ticker, "sector": sector_map.get(s.ticker, "Unknown")}
            for s in sigs
        ]}
    # Fallback: build from the configured scan universe when cache is empty.
    try:
        tickers = _scan_universe_tickers()
    except Exception:
        tickers = list(config.MVP_UNIVERSE)
    return {"tickers": [
        {"ticker": t, "sector": sector_map.get(t, "Unknown")} for t in tickers
    ]}


@app.get("/api/schedule")
def api_schedule():
    if not _scheduler:
        return {"status": "not_started"}
    jobs = _scheduler.get_jobs()
    if not jobs:
        return {"status": "no_job"}
    upcoming = [(j, j.next_run_time) for j in jobs if j.next_run_time]
    if not upcoming:
        return {"status": "no_upcoming"}
    upcoming.sort(key=lambda x: x[1])
    next_job, next_time = upcoming[0]
    return {
        "status": "ok",
        "next_run_at": next_time.isoformat(),
        "next_job": next_job.id,
        "trigger": str(next_job.trigger),
        "all_jobs": [
            {"id": j.id, "next_run_at": (j.next_run_time.isoformat() if j.next_run_time else None)}
            for j in jobs
        ],
    }


@app.get("/api/watchlists/ibd50")
def api_ibd50_get():
    row = store.get_watchlist(IBD50_WATCHLIST_NAME)
    if not row:
        return {"status": "empty", "tickers": [], "as_of_date": None, "fetched_at": None}
    return {
        "status": "ok",
        "tickers": row["tickers"],
        "count": len(row["tickers"]),
        "as_of_date": row["as_of_date"],
        "fetched_at": row["fetched_at"],
        "source_url": row["source_url"],
    }


@app.post("/api/watchlists/ibd50/refresh")
def api_ibd50_refresh():
    """Manual trigger: fetch the current IBD 50 from CapForce and persist."""
    return _refresh_ibd50(force=True)


_TIMEFRAMES = {
    "1M": 22,
    "3M": 66,
    "6M": 132,
    "9M": 189,
    "1Y": 252,
    "1.5Y": 378,
    "2Y": 504,
    "ALL": 10_000,
}


def _lookback_for_days(days: int) -> int:
    """Ensure we fetch enough history to cover the requested view + BB warmup."""
    return max(days + 60, config.LOOKBACK_DAYS)


# Metadata for the breadth pillars (clickable index charts).
_INDEX_KEYS = {
    "SPX": {
        "source": "yfinance", "symbol": "^GSPC",
        "chart_type": "candlestick", "pnf_type": "percentage",
        "display_name": "$SPX - S&P 500",
    },
    "VIX": {
        "source": "yfinance", "symbol": "^VIX",
        "chart_type": "candlestick", "pnf_type": "traditional",
        "display_name": "$VIX - Volatility Index",
    },
    "BPNYA": {
        "source": "internal_bpnya", "symbol": None,
        # Rendered as candlestick to match StockCharts now that we store real
        # OHLC (via CSV import + daily manual entry). The internal series
        # provides open/high/low for every day; when a row is missing OHL (old
        # pre-schema rows), _bpnya_series_as_ohlc falls back to open=high=low=close.
        "chart_type": "candlestick", "pnf_type": "traditional",
        "display_name": "$BPNYA - NYSE Bullish Percent (self-computed)",
    },
}


def _bpnya_series_as_ohlc() -> pd.DataFrame:
    """Return BPNYA history as an OHLC DataFrame.

    Prefers the real Open/High/Low columns from the DB (populated via CSV
    import and the daily-entry form). When a legacy row only has close, falls
    back to open=high=low=close so the candlestick renderer still draws a
    valid one-tick candle for that day instead of NaN-ing out.
    """
    history = store.get_bpnya_history_ohlc()
    if not history:
        return pd.DataFrame()
    rows = []
    for d, o, h, l, c in history:
        o_v = float(o) if o is not None else float(c)
        h_v = float(h) if h is not None else float(c)
        l_v = float(l) if l is not None else float(c)
        rows.append((pd.Timestamp(d).date(), o_v, h_v, l_v, float(c), 0))
    df = pd.DataFrame(
        rows,
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

    # P&F uses CONFIRMED closes only (drops today's intraday bar during market
    # hours). Matches StockCharts EOD P&F behavior; keeps ENTER NOW alerts from
    # firing on unconfirmed intraday flips.
    confirmed_close = indicators.confirmed_closes(ohlc["close"])
    if pnf_type == "traditional":
        pnf_cols = indicators.point_figure_traditional(confirmed_close, pnf_box, pnf_reversal)
        idx_to_price = lambda i: round(i * pnf_box, 2)
    else:
        pnf_cols = indicators.point_figure(confirmed_close, pnf_box, pnf_reversal)
        idx_to_price = lambda i: round(_idx_to_price(i, pnf_box), 2)

    # Second P&F built on the middle Bollinger Band series (= fair value).
    # Also confirmed-close only so it matches the close P&F's timing behavior.
    bb_middle_series = indicators.confirmed_closes(bb["middle"].dropna())
    if len(bb_middle_series) >= 2:
        if pnf_type == "traditional":
            pnf_fv_cols = indicators.point_figure_traditional(
                bb_middle_series, pnf_box, pnf_reversal
            )
        else:
            pnf_fv_cols = indicators.point_figure(
                bb_middle_series, pnf_box, pnf_reversal
            )
    else:
        pnf_fv_cols = []

    def _window_pnf(cols_list: list) -> list:
        if not candles:
            return cols_list
        window_start = candles[0]["date"]
        windowed = [c for c in cols_list if (c.end_date or "") >= window_start]
        if not windowed and cols_list:
            windowed = cols_list[-1:]
        return windowed

    def _serialize_pnf(cols_list: list) -> list:
        return [{
            "type": c.type,
            "top_idx": c.top_idx,
            "bottom_idx": c.bottom_idx,
            "top_price": idx_to_price(c.top_idx),
            "bottom_price": idx_to_price(c.bottom_idx),
            "start_date": c.start_date,
            "end_date": c.end_date,
        } for c in cols_list]

    pnf = _serialize_pnf(_window_pnf(pnf_cols))
    pnf_fair_value = _serialize_pnf(_window_pnf(pnf_fv_cols))

    return {
        "timeframe": tf,
        "available_timeframes": list(_TIMEFRAMES.keys()),
        "chart_type": chart_type,
        "candles": candles,
        "pnf": pnf,
        "pnf_fair_value": pnf_fair_value,
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


@app.get("/performance", response_class=HTMLResponse)
def performance_page(request: Request):
    return templates.TemplateResponse(request, "performance.html")


# ============================================================
# v2 trading terminal — parallel redesign at /v2/*
# Everything under /v2/ is the new UI; legacy routes untouched.
# ============================================================

def _v2_ctx(current_page: str, page_title: Optional[str] = None) -> dict:
    """Common template context for every v2 page."""
    return {
        "current_page": current_page,
        "page_title": page_title,
        "signal_mode": config.SIGNAL_MODE,
    }


@app.get("/v2/", response_class=HTMLResponse)
@app.get("/v2", response_class=HTMLResponse)
def v2_overview(request: Request):
    return templates.TemplateResponse(request, "v2/overview.html", _v2_ctx("overview", "Overview"))


@app.get("/v2/dev/components", response_class=HTMLResponse)
def v2_dev_components(request: Request):
    return templates.TemplateResponse(request, "v2/dev_components.html", _v2_ctx("overview", "Components"))


# --- v2 API endpoints (thin wrappers over existing state, no logic changes) ---

_MARKET_STATES = {
    "OPEN":        {"label": "Market Open",  "css_class": "open"},
    "PRE_MARKET":  {"label": "Pre-Market",   "css_class": "pre-market"},
    "AFTER_HOURS": {"label": "After Hours",  "css_class": "after-hours"},
    "CLOSED":      {"label": "Market Closed","css_class": "closed"},
}


def _market_state_for(now_et: dt.datetime) -> str:
    """Map an ET-local datetime to a market state string."""
    # Weekend
    if now_et.weekday() >= 5:
        return "CLOSED"
    minutes = now_et.hour * 60 + now_et.minute
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return "PRE_MARKET"
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "OPEN"
    if 16 * 60 <= minutes < 20 * 60:
        return "AFTER_HOURS"
    return "CLOSED"


@app.get("/api/market/status")
def api_market_status():
    now_et = dt.datetime.now(ET)
    state = _market_state_for(now_et)
    meta = _MARKET_STATES[state]
    return {
        "status": state,
        "label": meta["label"],
        "css_class": meta["css_class"],
        "now_et": now_et.isoformat(timespec="seconds"),
    }


@app.get("/api/entries/recent")
def api_entries_recent(limit: int = 20):
    """Recent ENTER NOW alerts joined with the current scan cache for context.

    The dashboard uses this for the Latest Signals feed on the Overview page.
    """
    limit = max(1, min(limit, 200))
    rows = store.get_entry_alerts_all()[:limit]
    with _cache_lock:
        sigs_by_ticker = {s.ticker: s for s in _cache["signals"]}
    watch = _major_watchlist_set()
    ibd = _ibd50_watchlist_set()
    out = []
    for r in rows:
        s = sigs_by_ticker.get(r["ticker"])
        out.append({
            "fired_at": r["fired_at"],
            "ticker": r["ticker"],
            "direction": r["direction"],
            "trigger_price": r["trigger_price"],
            "strike_price": compute_strike(r["trigger_price"], r["direction"]),
            "last_close": (round(s.last_close, 2) if s else None),
            "rsi": (None if (s is None or pd.isna(s.rsi)) else round(s.rsi, 1)),
            "pnf_column": s.pnf_column if s else None,
            "candidate": s.candidate if s else None,
            "on_watchlist": r["ticker"] in watch,
            "on_ibd50": r["ticker"] in ibd,
        })
    return {"entries": out, "count": len(out)}


def _v2_stub(request: Request, page: str, title: str, milestone: str, subtitle: Optional[str] = None):
    ctx = _v2_ctx(page, title)
    ctx["stub_milestone"] = milestone
    ctx["stub_subtitle"] = subtitle
    return templates.TemplateResponse(request, "v2/_stub.html", ctx)


@app.get("/v2/scanner",     response_class=HTMLResponse)
def v2_scanner(request: Request):
    return templates.TemplateResponse(request, "v2/scanner.html", _v2_ctx("scanner", "Scanner"))
@app.get("/v2/watchlists",  response_class=HTMLResponse)
def v2_watchlists(request: Request):
    return templates.TemplateResponse(request, "v2/watchlists.html", _v2_ctx("watchlists", "Watchlists"))
@app.get("/v2/alerts",      response_class=HTMLResponse)
def v2_alerts(request: Request):
    return templates.TemplateResponse(request, "v2/alerts.html", _v2_ctx("alerts", "Alerts"))
@app.get("/v2/universe",    response_class=HTMLResponse)
def v2_universe(request: Request):
    return templates.TemplateResponse(request, "v2/universe.html", _v2_ctx("universe", "Universe"))
@app.get("/v2/settings",    response_class=HTMLResponse)
def v2_settings(request: Request):
    return templates.TemplateResponse(request, "v2/settings.html", _v2_ctx("settings", "Settings"))
@app.get("/v2/charts", response_class=HTMLResponse)
def v2_charts(request: Request):
    return templates.TemplateResponse(request, "v2/charts.html", _v2_ctx("charts", "Charts"))

@app.get("/v2/charts/{ticker}", response_class=HTMLResponse)
def v2_charts_detail(request: Request, ticker: str):
    ctx = _v2_ctx("charts", f"Chart · {ticker.upper()}")
    ctx["ticker"] = ticker.upper()
    return templates.TemplateResponse(request, "v2/chart_detail.html", ctx)

@app.get("/v2/time-series", response_class=HTMLResponse)
def v2_ts(request: Request):
    return templates.TemplateResponse(request, "v2/time_series.html", _v2_ctx("time-series", "Time Series"))

@app.get("/v2/time-series/{key}", response_class=HTMLResponse)
def v2_ts_detail(request: Request, key: str):
    ctx = _v2_ctx("time-series", f"Time Series · {key.upper()}")
    ctx["ticker"] = key.upper()
    return templates.TemplateResponse(request, "v2/time_series_detail.html", ctx)


@app.post("/api/bpnya/day")
async def api_bpnya_day(request: Request):
    """Add or update a single BPNYA day. JSON body: {date, open?, high?, low?, close}.
    Used by the daily-entry form on /v2/settings.
    """
    try:
        payload = await request.json()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"invalid JSON: {e}"}, status_code=400)
    d_raw = payload.get("date")
    c_raw = payload.get("close")
    if d_raw is None or c_raw is None:
        return JSONResponse({"error": "date and close are required"}, status_code=400)
    try:
        d = pd.to_datetime(d_raw, errors="raise").date().isoformat()
        c = float(c_raw)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"could not parse date/close: {e}"}, status_code=400)
    def _num(v):
        if v is None or v == "":
            return None
        try:
            return float(v)
        except Exception:
            return None
    o = _num(payload.get("open"))
    h = _num(payload.get("high"))
    l = _num(payload.get("low"))
    try:
        store.upsert_bpnya(d, c, universe_size=0, open_=o, high=h, low=l, source="manual")
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"upsert failed: {e}"}, status_code=500)
    return {"status": "ok", "date": d, "close": c, "open": o, "high": h, "low": l}


@app.get("/api/universe/tickers")
def api_universe_tickers():
    """Every ticker in the merged scan universe with its sector + list membership.

    Used by the v2 Universe page for the searchable/filterable table.
    """
    try:
        sp500 = set(universe.get_universe_tickers()) if config.USE_FULL_UNIVERSE else set(config.MVP_UNIVERSE)
    except Exception:
        sp500 = set(config.MVP_UNIVERSE)
    major = set(getattr(config, "MAJOR_WATCHLIST", []) or [])
    ibd = _ibd50_watchlist_set()
    sector_map = _get_ticker_to_sector()
    all_tickers = sorted(sp500 | major | ibd)
    return {
        "tickers": [
            {
                "ticker": t,
                "sector": sector_map.get(t, ""),
                "on_sp500": t in sp500,
                "on_major": t in major,
                "on_ibd50": t in ibd,
            }
            for t in all_tickers
        ]
    }


@app.get("/api/settings")
def api_settings():
    """Read-only exposure of live strategy config for the v2 Settings page."""
    return {
        "signal_mode":              config.SIGNAL_MODE,
        "bb_period":                config.BB_PERIOD,
        "bb_stddev":                config.BB_STDDEV,
        "rsi_period":               config.RSI_PERIOD,
        "rsi_oversold":             config.RSI_OVERSOLD,
        "rsi_overbought":           config.RSI_OVERBOUGHT,
        "candidate_lookback_days":  getattr(config, "CANDIDATE_LOOKBACK_DAYS", 60),
        "pnf_box_pct":              config.PNF_BOX_PCT,
        "pnf_reversal":             config.PNF_REVERSAL,
        "vix_pnf_box_size":         config.VIX_PNF_BOX_SIZE,
        "vix_pnf_reversal":         config.VIX_PNF_REVERSAL,
        "use_full_universe":        config.USE_FULL_UNIVERSE,
        "scan_workers":             config.SCAN_WORKERS,
        "lookback_days":            config.LOOKBACK_DAYS,
    }


@app.post("/api/bpnya/import")
async def api_bpnya_import(file: Optional[UploadFile] = File(None), body: Optional[str] = None):
    """Import historical BPNYA data (date + close pct) into bpnya_history.

    Accepts either a multipart file upload OR raw CSV in the request body.
    CSV format is flexible: needs a "Date" column and one of Close / Value /
    Pct / BPNYA / Percent. Anything else is ignored (StockCharts exports with
    Open/High/Low/Close are fine — we just take Close).

    Upserts by date, so re-importing overlapping days silently replaces.
    """
    # Get the CSV text
    if file is not None:
        raw = (await file.read()).decode("utf-8", errors="replace")
    elif body:
        raw = body
    else:
        return JSONResponse({"error": "provide a file upload or CSV body"}, status_code=400)

    from io import StringIO
    try:
        df = pd.read_csv(StringIO(raw))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"CSV parse failed: {e}"}, status_code=400)

    # Normalize column names
    df.columns = [str(c).strip().lower() for c in df.columns]
    if "date" not in df.columns:
        return JSONResponse({"error": "CSV must include a Date column"}, status_code=400)

    # Find the close column
    close_col = None
    for cand in ("close", "value", "pct", "bpnya", "percent", "%"):
        if cand in df.columns:
            close_col = cand
            break
    if close_col is None:
        # Fall back to the last numeric column
        for c in reversed(df.columns.tolist()):
            if c == "date":
                continue
            try:
                if pd.api.types.is_numeric_dtype(df[c]) or pd.to_numeric(df[c], errors="coerce").notna().any():
                    close_col = c
                    break
            except Exception:
                continue
    if close_col is None:
        return JSONResponse({"error": "CSV must include a Close / Value / Pct column"}, status_code=400)

    # Detect optional OHL columns so we persist real OHLC when the source has it.
    def _pick(*names):
        for n in names:
            if n in df.columns:
                return n
        return None
    open_col = _pick("open")
    high_col = _pick("high")
    low_col  = _pick("low")

    # Parse and upsert
    imported = 0
    skipped = 0
    errors: list = []
    first_date = None
    last_date = None
    ohlc_stored = 0
    for _, row in df.iterrows():
        d_raw = row.get("date")
        v_raw = row.get(close_col)
        try:
            d = pd.to_datetime(d_raw, errors="coerce")
            v = pd.to_numeric(v_raw, errors="coerce")
        except Exception:
            skipped += 1
            continue
        if pd.isna(d) or pd.isna(v):
            skipped += 1
            continue
        # Optional OHL
        o_val = pd.to_numeric(row.get(open_col), errors="coerce") if open_col else None
        h_val = pd.to_numeric(row.get(high_col), errors="coerce") if high_col else None
        l_val = pd.to_numeric(row.get(low_col),  errors="coerce") if low_col  else None
        o_val = None if o_val is None or pd.isna(o_val) else float(o_val)
        h_val = None if h_val is None or pd.isna(h_val) else float(h_val)
        l_val = None if l_val is None or pd.isna(l_val) else float(l_val)
        d_iso = d.date().isoformat()
        try:
            store.upsert_bpnya(d_iso, float(v), universe_size=0, open_=o_val, high=h_val, low=l_val, source="import")
            imported += 1
            if o_val is not None or h_val is not None or l_val is not None:
                ohlc_stored += 1
            first_date = d_iso if first_date is None else min(first_date, d_iso)
            last_date = d_iso if last_date is None else max(last_date, d_iso)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{d_iso}: {e}")

    return {
        "status": "ok",
        "imported": imported,
        "with_ohlc": ohlc_stored,
        "skipped": skipped,
        "close_column_used": close_col,
        "columns_detected": {"open": open_col, "high": high_col, "low": low_col},
        "date_range": [first_date, last_date],
        "errors_count": len(errors),
        "errors_sample": errors[:5],
    }


@app.get("/api/universe/summary")
def api_universe_summary():
    """Coverage counts for the Overview universe card.

    Reflects the same merge logic as _scan_universe_tickers(): S&P 500 base
    (top-30-per-sector) unioned with Major Watchlist and IBD 50.
    """
    try:
        sp500 = set(universe.get_universe_tickers()) if config.USE_FULL_UNIVERSE else set(config.MVP_UNIVERSE)
    except Exception:
        sp500 = set(config.MVP_UNIVERSE)
    major = set(getattr(config, "MAJOR_WATCHLIST", []) or [])
    ibd = _ibd50_watchlist_set()
    all_tickers = sp500 | major | ibd
    in_two = sum(1 for t in all_tickers if (int(t in sp500) + int(t in major) + int(t in ibd)) == 2)
    in_three = sum(1 for t in all_tickers if (int(t in sp500) + int(t in major) + int(t in ibd)) == 3)
    return {
        "total": len(all_tickers),
        "sp500": len(sp500),
        "major": len(major),
        "ibd50": len(ibd),
        "overlaps": {"in_two": in_two, "in_three": in_three},
    }


_PERF_CHECKPOINT_MONTHS = [1, 2, 3, 4, 5, 6]


def _price_on_or_before(ohlc: pd.DataFrame, target_date: dt.date) -> Optional[float]:
    """Find the close price on `target_date`, or the most recent trading day before it."""
    if ohlc.empty:
        return None
    matching = ohlc[[d <= target_date for d in ohlc.index]]
    if matching.empty:
        return None
    return round(float(matching["close"].iloc[-1]), 2)


def _build_performance_rows() -> list:
    alerts = store.get_entry_alerts_all()
    today = dt.date.today()

    # Cache OHLC per ticker so we only fetch once per stock.
    ohlc_cache: dict = {}

    def _ohlc_for(ticker: str) -> pd.DataFrame:
        if ticker not in ohlc_cache:
            # Need up to ~200 days back per checkpoint span; use max lookback for coverage.
            ohlc_cache[ticker] = data.fetch_stock(ticker, config.LOOKBACK_DAYS)
        return ohlc_cache[ticker]

    rows = []
    for a in alerts:
        ticker = a["ticker"]
        direction = a["direction"]
        fired_dt = dt.datetime.fromisoformat(a["fired_at"])
        fired_date = fired_dt.date()

        ohlc = _ohlc_for(ticker)
        trigger_price = a.get("trigger_price")
        if trigger_price is None:
            trigger_price = _price_on_or_before(ohlc, fired_date)

        checkpoints = []
        for months in _PERF_CHECKPOINT_MONTHS:
            target_date = (pd.Timestamp(fired_date) + pd.DateOffset(months=months)).date()
            due = target_date <= today
            price = _price_on_or_before(ohlc, target_date) if due else None
            change_pct = None
            favorable = None
            if price is not None and trigger_price:
                change_pct = round((price - trigger_price) / trigger_price * 100, 2)
                favorable = (change_pct > 0) if direction == "SELL_PUTS" else (change_pct < 0)
            checkpoints.append({
                "months": months,
                "target_date": target_date.isoformat(),
                "price": price,
                "change_pct": change_pct,
                "favorable": favorable,
                "due": due,
            })

        rows.append({
            "id": a["id"],
            "ticker": ticker,
            "direction": direction,
            "trigger_date": fired_date.isoformat(),
            "trigger_time_utc": a["fired_at"],
            "trigger_price": trigger_price,
            "strike_price": compute_strike(trigger_price, direction),
            "checkpoints": checkpoints,
        })

    return rows


@app.get("/api/performance")
def api_performance():
    rows = _build_performance_rows()
    return {"months": _PERF_CHECKPOINT_MONTHS, "rows": rows}


def _performance_dataframe() -> pd.DataFrame:
    rows = _build_performance_rows()
    if not rows:
        cols = ["Trigger Date", "Ticker", "Direction", "Trigger Price"]
        for m in _PERF_CHECKPOINT_MONTHS:
            cols += [f"+{m}M Date", f"+{m}M Price", f"+{m}M %"]
        return pd.DataFrame(columns=cols)

    out = []
    for r in rows:
        row = {
            "Trigger Date": r["trigger_date"],
            "Ticker": r["ticker"],
            "Direction": r["direction"],
            "Trigger Price": r["trigger_price"],
            "Strike Price": r["strike_price"],
        }
        for cp in r["checkpoints"]:
            m = cp["months"]
            row[f"+{m}M Date"] = cp["target_date"]
            row[f"+{m}M Price"] = cp["price"]
            row[f"+{m}M %"] = cp["change_pct"]
        out.append(row)
    return pd.DataFrame(out)


@app.get("/api/performance.csv")
def api_performance_csv():
    df = _performance_dataframe()
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    fname = f"dola-performance-{dt.date.today().isoformat()}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/performance.xlsx")
def api_performance_xlsx():
    df = _performance_dataframe()
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Performance")
    buf.seek(0)
    fname = f"dola-performance-{dt.date.today().isoformat()}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


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
    tf_days = _TIMEFRAMES.get(timeframe.upper(), _TIMEFRAMES["3M"])
    ohlc = data.fetch_stock(ticker, _lookback_for_days(tf_days))
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


def _spx_regime_for_dates(dates: list[str]) -> dict:
    """Return {date_str: 'B' | 'S' | None} for each date using SPX daily P&F."""
    spx = data.fetch_index(_INDEX_KEYS["SPX"]["symbol"], config.LOOKBACK_DAYS)
    if spx.empty:
        return {d: None for d in dates}
    cols = indicators.point_figure(
        spx["close"], config.PNF_BOX_PCT, config.PNF_REVERSAL
    )
    out = {}
    for d in dates:
        found = None
        for c in cols:
            if c.start_date and c.end_date and c.start_date <= d <= c.end_date:
                found = "B" if c.type == "X" else "S"
                break
        out[d] = found
    return out


def _vix_column_for_dates(dates: list[str]) -> dict:
    """Return {date_str: 'X' | 'O' | None} for each date using VIX traditional P&F."""
    vix = data.fetch_index(_INDEX_KEYS["VIX"]["symbol"], config.LOOKBACK_DAYS)
    if vix.empty:
        return {d: None for d in dates}
    cols = indicators.point_figure_traditional(
        vix["close"], config.VIX_PNF_BOX_SIZE, config.VIX_PNF_REVERSAL
    )
    out = {}
    for d in dates:
        found = None
        for c in cols:
            if c.start_date and c.end_date and c.start_date <= d <= c.end_date:
                found = c.type
                break
        out[d] = found
    return out


def _daily_snapshot_by_date() -> dict:
    """Return {date_str: {bpnya_column, risk, ...}} from the daily_snapshot table."""
    rows = store.get_daily_history(limit=2000)
    return {r["date"]: r for r in rows}


@app.get("/fair_value/{ticker}", response_class=HTMLResponse)
def fair_value_page(request: Request, ticker: str):
    return templates.TemplateResponse(request, "fair_value.html", {"ticker": ticker.upper()})


def _fair_value_ohlc_and_pnf_meta(key: str, lookback: int):
    """Route a fair-value key to its OHLC source and P&F configuration.

    Returns (ohlc, pnf_meta) where pnf_meta describes how to compute the P&F
    column and CHANGE-in-boxes for this instrument. Stocks and SPX use
    percentage-scaled boxes; VIX and BPNYA use fixed 1-pt boxes (traditional).
    """
    upper = key.upper()
    meta = _INDEX_KEYS.get(upper)
    if meta is None:
        return data.fetch_stock(upper, lookback), {
            "pnf_type": "percentage",
            "box": config.PNF_BOX_PCT,
            "reversal": config.PNF_REVERSAL,
        }
    if meta["source"] == "yfinance":
        ohlc = data.fetch_index(meta["symbol"], lookback)
    elif meta["source"] == "internal_bpnya":
        ohlc = _bpnya_series_as_ohlc()
    else:
        ohlc = pd.DataFrame()

    if upper == "SPX":
        return ohlc, {"pnf_type": "percentage", "box": config.PNF_BOX_PCT, "reversal": config.PNF_REVERSAL}
    if upper == "VIX":
        return ohlc, {"pnf_type": "traditional", "box": config.VIX_PNF_BOX_SIZE, "reversal": config.VIX_PNF_REVERSAL}
    if upper == "BPNYA":
        from scanner.breadth import BPNYA_BOX_SIZE, BPNYA_REVERSAL
        return ohlc, {"pnf_type": "traditional", "box": BPNYA_BOX_SIZE, "reversal": BPNYA_REVERSAL}
    # Any other index falls back to percentage.
    return ohlc, {"pnf_type": "percentage", "box": config.PNF_BOX_PCT, "reversal": config.PNF_REVERSAL}


def _box_idx(price: float, pnf_meta: dict) -> int:
    """Which P&F box a price sits in, respecting box scale (log-% or linear)."""
    if pnf_meta["pnf_type"] == "traditional":
        return int(price // pnf_meta["box"])
    return _price_to_box_idx(price, pnf_meta["box"])


@app.get("/api/fair_value/{ticker}")
def api_fair_value(
    ticker: str,
    days: int = 30,
    bb_period: Optional[int] = None,
    bb_stddev: Optional[float] = None,
    rsi_period: Optional[int] = None,
):
    ticker = ticker.upper()
    ohlc, pnf_meta = _fair_value_ohlc_and_pnf_meta(ticker, _lookback_for_days(days))
    if ohlc.empty:
        return JSONResponse({"error": "no data"}, status_code=404)

    bp = bb_period or config.BB_PERIOD
    bs = bb_stddev if bb_stddev is not None else config.BB_STDDEV
    rp = rsi_period or config.RSI_PERIOD

    bb = indicators.bollinger_bands(ohlc["close"], bp, bs)
    rsi_series = indicators.rsi(ohlc["close"], rp)
    if pnf_meta["pnf_type"] == "traditional":
        pnf_cols = indicators.point_figure_traditional(ohlc["close"], pnf_meta["box"], pnf_meta["reversal"])
    else:
        pnf_cols = indicators.point_figure(ohlc["close"], pnf_meta["box"], pnf_meta["reversal"])

    tail = ohlc.tail(max(1, days))
    date_strs = [str(d) for d in tail.index]
    spx_regime = _spx_regime_for_dates(date_strs)
    vix_col_map = _vix_column_for_dates(date_strs)
    daily_snap = _daily_snapshot_by_date()

    def _col_type_for(d_str: str) -> Optional[str]:
        for c in pnf_cols:
            if c.start_date and c.end_date and c.start_date <= d_str <= c.end_date:
                return c.type
        return None

    days_data: list[dict] = []
    prev_close: Optional[float] = None
    for d, row in tail.iterrows():
        d_str = str(d)
        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        bb_u = None if pd.isna(bb["upper"].loc[d]) else float(bb["upper"].loc[d])
        bb_l = None if pd.isna(bb["lower"].loc[d]) else float(bb["lower"].loc[d])
        bb_m = None if pd.isna(bb["middle"].loc[d]) else float(bb["middle"].loc[d])
        rsi_val = None if pd.isna(rsi_series.loc[d]) else float(rsi_series.loc[d])
        col_type = _col_type_for(d_str)

        # CHANGE = signed day-over-day P&F box move. Positive = up-boxes today.
        # Uses the same box scale that P&F uses for THIS instrument (log-% for
        # stocks/SPX, linear 1-pt for VIX/BPNYA) so the count is meaningful.
        change: Optional[int] = None
        if prev_close is not None and prev_close > 0 and close > 0:
            change = _box_idx(close, pnf_meta) - _box_idx(prev_close, pnf_meta)

        snap = daily_snap.get(d_str, {})
        days_data.append({
            "date": d_str,
            "trend": spx_regime.get(d_str),
            "column": col_type,
            "change": change,
            "rsi": None if rsi_val is None else round(rsi_val, 1),
            "bb_upper": None if bb_u is None else round(bb_u, 2),
            "bb_lower": None if bb_l is None else round(bb_l, 2),
            "bb_middle": None if bb_m is None else round(bb_m, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "pierced_upper": bb_u is not None and high > bb_u,
            "pierced_lower": bb_l is not None and low < bb_l,
            "vix_column": snap.get("vix_column") or vix_col_map.get(d_str),
            "bpnya_column": snap.get("bpnya_column"),
            "risk": snap.get("risk"),
        })
        prev_close = close

    return {
        "ticker": ticker,
        "days": days_data,
        "pnf_meta": pnf_meta,
        "settings": {
            "bb_period": bp,
            "bb_stddev": bs,
            "rsi_period": rp,
            "rsi_oversold": config.RSI_OVERSOLD,
            "rsi_overbought": config.RSI_OVERBOUGHT,
        },
    }


_TS_COLUMNS = [
    ("date", "Date"),
    ("trend", "Trend"),
    ("column", "Column"),
    ("change", "Change (boxes)"),
    ("rsi", "RSI"),
    ("bb_upper", "BB Upper"),
    ("bb_middle", "BB Middle"),
    ("bb_lower", "BB Lower"),
    ("high", "High"),
    ("low", "Low"),
    ("close", "Close"),
    ("vix_column", "VIX"),
    ("bpnya_column", "BPNYA"),
    ("risk", "Risk"),
]


@app.get("/api/fair_value/{ticker}/export.xlsx")
def api_fair_value_xlsx(ticker: str, days: int = 90):
    payload = api_fair_value(ticker=ticker, days=days)
    if isinstance(payload, JSONResponse):
        return payload
    rows = payload.get("days") or []
    keys = [k for k, _ in _TS_COLUMNS]
    labels = [lbl for _, lbl in _TS_COLUMNS]
    df = pd.DataFrame([{k: r.get(k) for k in keys} for r in rows])
    if not df.empty:
        df = df[keys]
        df.columns = labels
    else:
        df = pd.DataFrame(columns=labels)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=ticker.upper()[:31])
    buf.seek(0)
    fname = f"dola-timeseries-{ticker.upper()}-{dt.date.today().isoformat()}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


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

    tf_days = _TIMEFRAMES.get(timeframe.upper(), _TIMEFRAMES["3M"])
    lookback = _lookback_for_days(tf_days)
    if meta["source"] == "yfinance":
        ohlc = data.fetch_index(meta["symbol"], lookback)
    elif meta["source"] == "internal_bpnya":
        ohlc = _bpnya_series_as_ohlc()
    else:
        ohlc = pd.DataFrame()

    if ohlc.empty:
        return JSONResponse(
            {"error": "no data yet - BPNYA history accumulates one row per scan; expect ~2 weeks for meaningful P&F"},
            status_code=404,
        )

    # Per-index P&F defaults. VIX and BPNYA both use traditional 1-pt / 2-rev;
    # SPX percentage 1/2; other stocks use the global percentage defaults.
    if meta["pnf_type"] == "traditional":
        default_box = 1.0
        default_reversal = 2
    else:
        default_box = config.PNF_BOX_PCT
        default_reversal = config.PNF_REVERSAL

    default_bb_period = config.BB_PERIOD
    default_bb_stddev = config.BB_STDDEV
    default_rsi_period = config.RSI_PERIOD
    if key == "VIX":
        default_box = config.VIX_PNF_BOX_SIZE
        default_reversal = config.VIX_PNF_REVERSAL

    payload = _build_chart_payload(
        ohlc=ohlc, timeframe=timeframe,
        bb_period=bb_period or default_bb_period,
        bb_stddev=bb_stddev if bb_stddev is not None else default_bb_stddev,
        rsi_period=rsi_period or default_rsi_period,
        pnf_box=pnf_box if pnf_box is not None else default_box,
        pnf_reversal=pnf_reversal or default_reversal,
        pnf_type=(pnf_type or meta["pnf_type"]).lower(),
        chart_type=meta["chart_type"],
    )
    payload["ticker"] = key
    payload["display_name"] = meta["display_name"]
    payload["signal"] = None
    return payload
