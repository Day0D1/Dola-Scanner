"""Market breadth = regime (BUY/SELL from SPX) x risk (LOW/MED/HIGH from BPNYA + VIX)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

from scanner import config, data, indicators


Regime = Literal["BUY", "SELL"]
Risk = Literal["LOW", "MEDIUM", "HIGH"]
Column = Optional[Literal["X", "O"]]

# StockCharts convention for a traditional P&F on a bullish-percent index.
BPNYA_BOX_SIZE = 2.0
BPNYA_REVERSAL = 3


@dataclass
class PillarReading:
    """Snapshot of one breadth pillar (SPX, BPNYA, or VIX)."""
    column: Column                # current P&F column: X = rising, O = falling
    level: Optional[float]        # SPX price / BPNYA % / VIX close
    change: Optional[float]       # SPX = boxes in current column; others = day-over-day
    signal: Optional[str] = None  # "BUY"/"SELL" derived from column, for display


@dataclass
class BreadthReading:
    spx: PillarReading
    bpnya: PillarReading
    vix: PillarReading
    regime: Optional[Regime]      # BUY when SPX in X, SELL when SPX in O
    risk: Optional[Risk]          # LOW / MEDIUM / HIGH per the spec matrix

    # --- Backwards-compat accessors (used by pre-existing callers) ---
    @property
    def spx_trend(self) -> Column: return self.spx.column
    @property
    def bpnya_trend(self) -> Column: return self.bpnya.column
    @property
    def vix_trend(self) -> Column: return self.vix.column
    @property
    def bpnya_pct(self) -> Optional[float]: return self.bpnya.level

    @property
    def verdict(self) -> str:
        """Legacy label for Telegram/UI code that hasn't migrated to (regime, risk)."""
        if self.risk == "LOW":
            return "BULLISH" if self.regime == "BUY" else "BEARISH"
        if self.risk == "HIGH":
            return "BEARISH" if self.regime == "BUY" else "BULLISH"
        return "MIXED"


def compute_regime(spx_col: Column) -> Optional[Regime]:
    if spx_col == "X":
        return "BUY"
    if spx_col == "O":
        return "SELL"
    return None


def compute_risk(regime: Optional[Regime], bpnya_col: Column, vix_col: Column) -> Optional[Risk]:
    """
    BUY regime  (playing puts, wanting price up):
        LOW  = BPNYA in X + VIX in O   (both bullish confirm)
        HIGH = BPNYA in O + VIX in X   (both bearish contradict)
    SELL regime (playing calls, wanting price down): logic flips.
        LOW  = BPNYA in O + VIX in X
        HIGH = BPNYA in X + VIX in O
    """
    if regime is None:
        return None
    if regime == "BUY":
        if bpnya_col == "X" and vix_col == "O":
            return "LOW"
        if bpnya_col == "O" and vix_col == "X":
            return "HIGH"
        return "MEDIUM"
    # SELL
    if bpnya_col == "O" and vix_col == "X":
        return "LOW"
    if bpnya_col == "X" and vix_col == "O":
        return "HIGH"
    return "MEDIUM"


def _current_column_and_boxes(closes: pd.Series):
    cols = indicators.point_figure(closes, config.PNF_BOX_PCT, config.PNF_REVERSAL)
    if not cols:
        return None, None
    last = cols[-1]
    boxes = last.top_idx - last.bottom_idx + 1
    return last.type, boxes


def _bpnya_column_and_history():
    """Traditional P&F on the BPNYA % series; returns (column, latest, prior)."""
    from scanner import store
    history = store.get_bpnya_history()
    if not history:
        return None, None, None
    latest = history[-1][1]
    prior = history[-2][1] if len(history) >= 2 else None
    if len(history) < 2:
        return None, latest, prior
    series = pd.Series([h[1] for h in history], index=[h[0] for h in history])
    cols = indicators.point_figure_traditional(series, BPNYA_BOX_SIZE, BPNYA_REVERSAL)
    return (cols[-1].type if cols else None), latest, prior


def read_breadth() -> BreadthReading:
    spx_ohlc = data.fetch_index(config.SPX_YF_SYMBOL, config.LOOKBACK_DAYS)
    vix_ohlc = data.fetch_index(config.VIX_YF_SYMBOL, config.LOOKBACK_DAYS)

    # SPX pillar
    spx_col, spx_boxes = (None, None)
    spx_level: Optional[float] = None
    if not spx_ohlc.empty:
        spx_col, spx_boxes = _current_column_and_boxes(spx_ohlc["close"])
        spx_level = float(spx_ohlc["close"].iloc[-1])
    spx = PillarReading(
        column=spx_col,
        level=round(spx_level, 2) if spx_level is not None else None,
        change=spx_boxes,  # boxes in current column = trend maturity
        signal=("BUY" if spx_col == "X" else ("SELL" if spx_col == "O" else None)),
    )

    # VIX pillar
    vix_col, _ = (None, None)
    vix_level: Optional[float] = None
    vix_change: Optional[float] = None
    if not vix_ohlc.empty:
        vix_col, _ = _current_column_and_boxes(vix_ohlc["close"])
        vix_level = float(vix_ohlc["close"].iloc[-1])
        if len(vix_ohlc) >= 2:
            vix_change = round(vix_level - float(vix_ohlc["close"].iloc[-2]), 2)
    vix = PillarReading(
        column=vix_col,
        level=round(vix_level, 2) if vix_level is not None else None,
        change=vix_change,
        signal=vix_col,
    )

    # BPNYA pillar
    bpnya_col, bpnya_latest, bpnya_prior = _bpnya_column_and_history()
    bpnya_change: Optional[float] = None
    if bpnya_latest is not None and bpnya_prior is not None:
        bpnya_change = round(bpnya_latest - bpnya_prior, 2)
    bpnya = PillarReading(
        column=bpnya_col,
        level=round(bpnya_latest, 2) if bpnya_latest is not None else None,
        change=bpnya_change,
        signal=bpnya_col,
    )

    regime = compute_regime(spx_col)
    risk = compute_risk(regime, bpnya_col, vix_col)

    return BreadthReading(spx=spx, bpnya=bpnya, vix=vix, regime=regime, risk=risk)
