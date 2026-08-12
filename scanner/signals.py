"""ELON / MUSK candidate detection and entry-trigger logic."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

from scanner import config, indicators


Candidate = Optional[Literal["ELON", "MUSK"]]
Entry = Optional[Literal["SELL_PUTS", "SELL_CALLS"]]
Pierce = Optional[Literal["UPPER", "LOWER"]]
PnFState = Optional[Literal["X", "O"]]


@dataclass
class StockSignal:
    ticker: str
    last_close: float
    rsi: float
    bb_upper: float
    bb_middle: float
    bb_lower: float
    pnf_column: PnFState
    band_pierce_today: Pierce
    candidate: Candidate
    entry_trigger: Entry


def evaluate_stock(ticker: str, ohlc: pd.DataFrame) -> Optional[StockSignal]:
    warmup = max(config.BB_PERIOD + 5, config.RSI_PERIOD + 5)
    if len(ohlc) < warmup:
        return None

    closes = ohlc["close"]
    highs = ohlc["high"]
    lows = ohlc["low"]

    bb = indicators.bollinger_bands(closes, config.BB_PERIOD, config.BB_STDDEV)
    rsi_series = indicators.rsi(closes, config.RSI_PERIOD)
    pnf_col: PnFState = indicators.current_pnf_column(
        closes, config.PNF_BOX_PCT, config.PNF_REVERSAL
    )

    last_close = float(closes.iloc[-1])
    last_rsi = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else float("nan")
    last_upper = float(bb["upper"].iloc[-1])
    last_middle = float(bb["middle"].iloc[-1])
    last_lower = float(bb["lower"].iloc[-1])

    pierce_today: Pierce = None
    if not pd.isna(last_upper):
        if float(highs.iloc[-1]) > last_upper:
            pierce_today = "UPPER"
        elif float(lows.iloc[-1]) < last_lower:
            pierce_today = "LOWER"

    candidate = _detect_candidate(ohlc, bb, rsi_series)

    entry: Entry = None
    if candidate == "ELON" and pnf_col == "X":
        entry = "SELL_PUTS"
    elif candidate == "MUSK" and pnf_col == "O":
        entry = "SELL_CALLS"

    return StockSignal(
        ticker=ticker,
        last_close=last_close,
        rsi=last_rsi,
        bb_upper=last_upper,
        bb_middle=last_middle,
        bb_lower=last_lower,
        pnf_column=pnf_col,
        band_pierce_today=pierce_today,
        candidate=candidate,
        entry_trigger=entry,
    )


def _detect_candidate(ohlc: pd.DataFrame, bb: pd.DataFrame, rsi_series: pd.Series) -> Candidate:
    """
    Option B semantics: a candidate stays active as long as RSI(5) stays in the zone
    (<30 for ELON, >70 for MUSK). It becomes active the moment any bar within the
    current zone-streak has a wick that pierces the corresponding band.
    """
    n = len(ohlc)
    if n == 0 or pd.isna(rsi_series.iloc[-1]):
        return None

    last_rsi = float(rsi_series.iloc[-1])

    if last_rsi < config.RSI_OVERSOLD:
        start = n - 1
        while start > 0 and rsi_series.iloc[start - 1] < config.RSI_OVERSOLD:
            start -= 1
        for i in range(start, n):
            lo = ohlc["low"].iloc[i]
            lb = bb["lower"].iloc[i]
            if not pd.isna(lb) and lo < lb:
                return "ELON"
    elif last_rsi > config.RSI_OVERBOUGHT:
        start = n - 1
        while start > 0 and rsi_series.iloc[start - 1] > config.RSI_OVERBOUGHT:
            start -= 1
        for i in range(start, n):
            hi = ohlc["high"].iloc[i]
            ub = bb["upper"].iloc[i]
            if not pd.isna(ub) and hi > ub:
                return "MUSK"

    return None
