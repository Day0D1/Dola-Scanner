"""Data fetching: Polygon.io for stocks, yfinance for indices."""
from __future__ import annotations

import pandas as pd
import requests

from scanner import config


POLYGON_BASE = "https://api.polygon.io"


def fetch_polygon_daily(ticker: str, start: str, end: str, adjusted: bool = True) -> pd.DataFrame:
    """Daily aggregates from Polygon. Returns DataFrame indexed by date."""
    url = f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"
    params = {
        "adjusted": str(adjusted).lower(),
        "sort": "asc",
        "limit": 50000,
        "apiKey": config.POLYGON_API_KEY,
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    # Polygon returns "OK" for real-time and "DELAYED" for delayed tiers; both are usable.
    if data.get("status") not in ("OK", "DELAYED") or not data.get("results"):
        return pd.DataFrame()

    df = pd.DataFrame(data["results"])
    df["date"] = (
        pd.to_datetime(df["t"], unit="ms")
        .dt.tz_localize("UTC")
        .dt.tz_convert("America/New_York")
        .dt.date
    )
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    return df.set_index("date")[["open", "high", "low", "close", "volume"]]


def fetch_yfinance_daily(symbol: str, period: str = "2y") -> pd.DataFrame:
    """Daily bars from Yahoo Finance for indices (^GSPC, ^VIX)."""
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval="1d", auto_adjust=True)
    if df.empty:
        return pd.DataFrame()
    df.columns = [c.lower() for c in df.columns]
    df.index = pd.to_datetime(df.index).date
    df.index.name = "date"
    return df[["open", "high", "low", "close", "volume"]]


def fetch_stock(ticker: str, lookback_days: int = 400) -> pd.DataFrame:
    end = pd.Timestamp.now(tz="America/New_York").date()
    start = end - pd.Timedelta(days=int(lookback_days * 1.5))
    return fetch_polygon_daily(ticker, str(start), str(end))


def fetch_index(symbol: str, lookback_days: int = 400) -> pd.DataFrame:
    years = max(2, int(lookback_days / 250) + 1)
    return fetch_yfinance_daily(symbol, period=f"{years}y")
