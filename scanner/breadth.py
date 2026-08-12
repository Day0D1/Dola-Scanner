"""Market breadth gate: $SPX + $VIX + $BPNYA (self-computed)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

from scanner import config, data, indicators


BreadthVerdict = Literal["BULLISH", "BEARISH", "MIXED"]
PnFState = Optional[Literal["X", "O"]]

# StockCharts-style traditional P&F for percentage-based BP indices: box=2, reversal=3.
BPNYA_BOX_SIZE = 2.0
BPNYA_REVERSAL = 3


@dataclass
class BreadthReading:
    spx_trend: PnFState
    vix_trend: PnFState
    bpnya_trend: PnFState
    bpnya_pct: Optional[float]
    verdict: BreadthVerdict


def compute_bpnya_current_column() -> PnFState:
    """
    Traditional P&F on the accumulated BPNYA history. Returns None if <2 days.
    """
    from scanner import store  # local import to avoid circular dep

    history = store.get_bpnya_history()
    if len(history) < 2:
        return None
    series = pd.Series([h[1] for h in history], index=[h[0] for h in history])
    cols = indicators.point_figure_traditional(series, BPNYA_BOX_SIZE, BPNYA_REVERSAL)
    return cols[-1].type if cols else None


def read_breadth() -> BreadthReading:
    """
    BULLISH = SPX in X + VIX in O (+ BPNYA in X, when we have enough history)
    BEARISH = SPX in O + VIX in X (+ BPNYA in O)
    MIXED   = otherwise
    BPNYA remains permissive until we have enough history: it never blocks a
    verdict if it's None, only reinforces or contradicts.
    """
    from scanner import store

    spx = data.fetch_index(config.SPX_YF_SYMBOL, config.LOOKBACK_DAYS)
    vix = data.fetch_index(config.VIX_YF_SYMBOL, config.LOOKBACK_DAYS)

    spx_col: PnFState = (
        indicators.current_pnf_column(spx["close"], config.PNF_BOX_PCT, config.PNF_REVERSAL)
        if not spx.empty
        else None
    )
    vix_col: PnFState = (
        indicators.current_pnf_column(vix["close"], config.PNF_BOX_PCT, config.PNF_REVERSAL)
        if not vix.empty
        else None
    )

    bpnya_col = compute_bpnya_current_column()
    latest = store.get_bpnya_latest()
    bpnya_pct = latest["pct"] if latest else None

    if spx_col == "X" and vix_col == "O" and bpnya_col in (None, "X"):
        verdict: BreadthVerdict = "BULLISH"
    elif spx_col == "O" and vix_col == "X" and bpnya_col in (None, "O"):
        verdict = "BEARISH"
    else:
        verdict = "MIXED"

    return BreadthReading(spx_col, vix_col, bpnya_col, bpnya_pct, verdict)
