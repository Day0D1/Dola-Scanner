"""Market data providers. Every provider returns a DataFrame of CLOSED candles:

    columns: time (int, UTC epoch seconds of bar open), open, high, low, close
"""
from __future__ import annotations

import logging
import time as _time
from typing import Protocol

import httpx
import numpy as np
import pandas as pd

from .config import TF_SECONDS, settings

log = logging.getLogger("bigsam.data")

COLUMNS = ["time", "open", "high", "low", "close"]


def pip_size(pair: str) -> float:
    return 0.01 if pair.upper().endswith("JPY") else 0.0001


def price_decimals(pair: str) -> int:
    return 3 if pair.upper().endswith("JPY") else 5


def _drop_open_bar(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Remove the still-forming candle so the engine only sees closed bars."""
    if df.empty:
        return df
    now = int(_time.time())
    return df[df["time"] + TF_SECONDS[tf] <= now].reset_index(drop=True)


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Resample (e.g. 1h -> 4h) on UTC-aligned buckets."""
    if df.empty:
        return df
    sec = TF_SECONDS[tf]
    bucket = (df["time"] // sec) * sec
    out = df.groupby(bucket).agg(open=("open", "first"), high=("high", "max"),
                                 low=("low", "min"), close=("close", "last"))
    out = out.reset_index().rename(columns={"index": "time"})
    out.columns = COLUMNS
    return out


class Provider(Protocol):
    def fetch(self, pairs: list[str], tf: str, days: int) -> dict[str, pd.DataFrame]: ...


class YFinanceProvider:
    """Free, keyless. Intraday history limits: 5m/15m = 60 days, 1h = 730 days."""

    LIMITS = {"5m": 59, "15m": 59, "1h": 729, "1d": 3650}

    def fetch(self, pairs: list[str], tf: str, days: int) -> dict[str, pd.DataFrame]:
        import yfinance as yf

        src_tf = "1h" if tf == "4h" else tf
        days = min(days, self.LIMITS[src_tf])
        out = self._download(yf, pairs, tf, src_tf, days)
        # Yahoo intermittently drops a ticker from batched requests; retry those one by one.
        for pair in [p for p in pairs if len(out.get(p, ())) < 60]:
            out.update(self._download(yf, [pair], tf, src_tf, days))
        return out

    def _download(self, yf, pairs: list[str], tf: str, src_tf: str, days: int) -> dict[str, pd.DataFrame]:
        tickers = [f"{p}=X" for p in pairs]
        try:
            raw = yf.download(tickers, period=f"{days}d", interval=src_tf, progress=False,
                              auto_adjust=False, group_by="ticker", threads=True)
        except Exception as e:
            log.warning("yfinance download failed for %s: %s", pairs, e)
            return {}
        out: dict[str, pd.DataFrame] = {}
        for pair, tk in zip(pairs, tickers):
            try:
                sub = raw[tk] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                log.warning("no data for %s", pair)
                continue
            sub = sub[["Open", "High", "Low", "Close"]].dropna()
            if sub.empty:
                continue
            idx = sub.index
            if idx.tz is None:
                idx = idx.tz_localize("UTC")
            df = pd.DataFrame({
                "time": idx.tz_convert("UTC").as_unit("s").asi8.astype(np.int64),
                "open": sub["Open"].to_numpy(float), "high": sub["High"].to_numpy(float),
                "low": sub["Low"].to_numpy(float), "close": sub["Close"].to_numpy(float),
            })
            # Yahoo FX sometimes prints a bar whose high/low excludes open/close; repair it.
            df["high"] = df[["open", "high", "low", "close"]].max(axis=1)
            df["low"] = df[["open", "high", "low", "close"]].min(axis=1)
            df = df.drop_duplicates("time").sort_values("time").reset_index(drop=True)
            if tf == "4h":
                df = resample(df, "4h")
            out[pair] = _drop_open_bar(df, tf)
        return out


class OandaProvider:
    """OANDA v20 REST (practice or live token, read-only candle access)."""

    GRAN = {"5m": "M5", "15m": "M15", "1h": "H1", "4h": "H4", "1d": "D"}

    def __init__(self, token: str, env: str = "practice"):
        host = "api-fxpractice.oanda.com" if env == "practice" else "api-fxtrade.oanda.com"
        self.base = f"https://{host}/v3"
        self.headers = {"Authorization": f"Bearer {token}"}

    def fetch(self, pairs: list[str], tf: str, days: int) -> dict[str, pd.DataFrame]:
        count = min(5000, int(days * 86400 / TF_SECONDS[tf]) + 1)
        out: dict[str, pd.DataFrame] = {}
        with httpx.Client(timeout=30, headers=self.headers) as client:
            for pair in pairs:
                inst = f"{pair[:3]}_{pair[3:]}"
                params = {"granularity": self.GRAN[tf], "count": count, "price": "M"}
                if tf == "1d":
                    params.update(dailyAlignment=0, alignmentTimezone="UTC")
                r = client.get(f"{self.base}/instruments/{inst}/candles", params=params)
                if r.status_code != 200:
                    log.warning("oanda %s %s: %s", pair, tf, r.text[:200])
                    continue
                rows = [
                    (int(pd.Timestamp(c["time"]).timestamp()), float(c["mid"]["o"]), float(c["mid"]["h"]),
                     float(c["mid"]["l"]), float(c["mid"]["c"]))
                    for c in r.json().get("candles", []) if c.get("complete")
                ]
                out[pair] = pd.DataFrame(rows, columns=COLUMNS)
        return out


def get_provider() -> Provider:
    if settings.data_provider.lower() == "oanda":
        if not settings.oanda_token:
            raise RuntimeError("DATA_PROVIDER=oanda requires OANDA_TOKEN")
        return OandaProvider(settings.oanda_token, settings.oanda_env)
    return YFinanceProvider()
