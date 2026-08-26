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
    # P&F must use CONFIRMED closes only — today's intraday bar would falsely
    # flip the column when price briefly crosses a reversal threshold. StockCharts
    # only updates its P&F once today's real close is in.
    pnf_col: PnFState = indicators.current_pnf_column(
        indicators.confirmed_closes(closes), config.PNF_BOX_PCT, config.PNF_REVERSAL
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
    Look back CANDIDATE_LOOKBACK_DAYS for the most recent qualifying activation
    bar and return its type. A bar qualifies when:
      ELON = low < BB_lower AND RSI < RSI_OVERSOLD at that bar
      MUSK = high > BB_upper AND RSI > RSI_OVERBOUGHT at that bar
    If today's RSI has since recovered out of the zone, the candidate is STILL
    active — the entry fires on the P&F flip regardless of current RSI, and the
    alert body prints the current RSI so the user sees the full context.
    """
    n = len(ohlc)
    if n < 2:
        return None
    lookback = getattr(config, "CANDIDATE_LOOKBACK_DAYS", 60)
    start = max(0, n - lookback)

    for i in range(n - 1, start - 1, -1):
        rsi_i = rsi_series.iloc[i]
        if pd.isna(rsi_i):
            continue
        r = float(rsi_i)
        lb = bb["lower"].iloc[i]
        ub = bb["upper"].iloc[i]
        lo = float(ohlc["low"].iloc[i])
        hi = float(ohlc["high"].iloc[i])
        if not pd.isna(lb) and lo < float(lb) and r < config.RSI_OVERSOLD:
            return "ELON"
        if not pd.isna(ub) and hi > float(ub) and r > config.RSI_OVERBOUGHT:
            return "MUSK"

    return None
