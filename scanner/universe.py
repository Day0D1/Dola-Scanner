"""Universe builder: top N per GICS sector from S&P 500 by market cap."""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import StringIO
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import requests

from scanner import config


DATA_DIR = Path(os.getenv("DATA_DIR") or (Path(__file__).resolve().parent.parent / "data"))
UNIVERSE_CACHE = DATA_DIR / "universe.json"
WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
CACHE_TTL_DAYS = 7


def _fetch_sp500() -> pd.DataFrame:
    """S&P 500 constituents from Wikipedia with GICS Sector."""
    r = requests.get(
        WIKI_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; DolaScanner/1.0)"},
        timeout=20,
    )
    r.raise_for_status()
    tables = pd.read_html(StringIO(r.text), header=0)
    df = tables[0]
    df = df.rename(columns={"Symbol": "symbol", "GICS Sector": "sector"})
    df["symbol"] = df["symbol"].astype(str).str.strip()
    return df[["symbol", "sector"]].dropna()


def _fetch_market_cap(symbol: str) -> float:
    try:
        url = f"https://api.polygon.io/v3/reference/tickers/{symbol}"
        r = requests.get(url, params={"apiKey": config.POLYGON_API_KEY}, timeout=10)
        if r.status_code != 200:
            return 0.0
        data = r.json().get("results", {}) or {}
        return float(data.get("market_cap") or 0.0)
    except Exception:
        return 0.0


def _fetch_market_caps_parallel(symbols: List[str], workers: int = 12) -> Dict[str, float]:
    caps: Dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_fetch_market_cap, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                caps[sym] = fut.result()
            except Exception:
                caps[sym] = 0.0
    return caps


def _cache_fresh() -> bool:
    if not UNIVERSE_CACHE.exists():
        return False
    age = time.time() - UNIVERSE_CACHE.stat().st_mtime
    return age < CACHE_TTL_DAYS * 24 * 3600


def _load_cache() -> Optional[dict]:
    try:
        with UNIVERSE_CACHE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(payload: dict) -> None:
    UNIVERSE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with UNIVERSE_CACHE.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def build_universe(top_n_per_sector: int = 30, force_rebuild: bool = False) -> dict:
    """
    Returns:
      {
        "built_at": ISO datetime,
        "top_n_per_sector": int,
        "sectors": { sector_name: [ticker, ...] },
        "all_tickers": [ticker, ...],  # deduped
      }
    """
    if not force_rebuild and _cache_fresh():
        cached = _load_cache()
        if cached and cached.get("top_n_per_sector", 0) >= top_n_per_sector:
            return cached

    sp500 = _fetch_sp500()
    caps = _fetch_market_caps_parallel(sp500["symbol"].tolist())
    sp500["market_cap"] = sp500["symbol"].map(caps).fillna(0.0)

    sectors: Dict[str, List[str]] = {}
    for sector, group in sp500.groupby("sector", sort=True):
        # Drop zero-cap rows (couldn't fetch); take top N by market cap
        good = group[group["market_cap"] > 0].nlargest(top_n_per_sector, "market_cap")
        sectors[str(sector)] = good["symbol"].tolist()

    all_tickers = sorted({t for lst in sectors.values() for t in lst})

    payload = {
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "top_n_per_sector": top_n_per_sector,
        "sectors": sectors,
        "all_tickers": all_tickers,
    }
    _save_cache(payload)
    return payload


def get_universe_tickers() -> List[str]:
    """Convenience: just the flat ticker list."""
    return build_universe()["all_tickers"]


def get_ticker_to_sector() -> Dict[str, str]:
    """Reverse map: ticker -> GICS sector."""
    u = build_universe()
    return {t: sector for sector, tickers in u["sectors"].items() for t in tickers}
