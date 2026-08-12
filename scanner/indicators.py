"""Technical indicators: Bollinger Bands (10,2), RSI (5, Wilder), Point & Figure."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Literal, Optional

import numpy as np
import pandas as pd


def bollinger_bands(closes: pd.Series, period: int = 10, num_std: float = 2.0) -> pd.DataFrame:
    """Population-stddev Bollinger Bands (matches StockCharts default)."""
    middle = closes.rolling(window=period, min_periods=period).mean()
    std = closes.rolling(window=period, min_periods=period).std(ddof=0)
    return pd.DataFrame({
        "upper": middle + num_std * std,
        "middle": middle,
        "lower": middle - num_std * std,
    })


def rsi(closes: pd.Series, period: int = 5) -> pd.Series:
    """RSI using Wilder's smoothing (StockCharts convention)."""
    n = len(closes)
    result = pd.Series([np.nan] * n, index=closes.index, dtype=float)
    if n < period + 1:
        return result

    deltas = closes.diff()
    gains = deltas.clip(lower=0).fillna(0)
    losses = (-deltas.clip(upper=0)).fillna(0)

    avg_gain = gains.iloc[1 : period + 1].mean()
    avg_loss = losses.iloc[1 : period + 1].mean()

    def _rsi_from(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        rs = ag / al
        return 100.0 - (100.0 / (1.0 + rs))

    result.iloc[period] = _rsi_from(avg_gain, avg_loss)

    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains.iloc[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses.iloc[i]) / period
        result.iloc[i] = _rsi_from(avg_gain, avg_loss)

    return result


@dataclass
class PnFColumn:
    type: Literal["X", "O"]
    top_idx: int
    bottom_idx: int
    start_date: Optional[str] = None  # ISO date when this column began
    end_date: Optional[str] = None    # ISO date of the most recent bar in this column


def _price_to_box_idx(price: float, box_pct: float) -> int:
    return int(math.floor(math.log(price) / math.log(1.0 + box_pct / 100.0)))


def _pnf_from_boxes(box_indices: List[int], dates: List[str], reversal: int) -> List[PnFColumn]:
    """Core P&F walk over pre-computed box indices. Shared by all box-size variants."""
    if len(box_indices) < 2:
        return []

    columns: List[PnFColumn] = []
    prev_box = box_indices[0]

    for i in range(1, len(box_indices)):
        cur_box = box_indices[i]
        date_str = dates[i]

        if not columns:
            if cur_box > prev_box:
                columns.append(PnFColumn("X", top_idx=cur_box, bottom_idx=prev_box + 1,
                                         start_date=date_str, end_date=date_str))
            elif cur_box < prev_box:
                columns.append(PnFColumn("O", top_idx=prev_box - 1, bottom_idx=cur_box,
                                         start_date=date_str, end_date=date_str))
            prev_box = cur_box
            continue

        col = columns[-1]
        if col.type == "X":
            if cur_box > col.top_idx:
                col.top_idx = cur_box
                col.end_date = date_str
            elif cur_box <= col.top_idx - reversal:
                columns.append(PnFColumn("O", top_idx=col.top_idx - 1, bottom_idx=cur_box,
                                         start_date=date_str, end_date=date_str))
            else:
                col.end_date = date_str
        else:
            if cur_box < col.bottom_idx:
                col.bottom_idx = cur_box
                col.end_date = date_str
            elif cur_box >= col.bottom_idx + reversal:
                columns.append(PnFColumn("X", top_idx=cur_box, bottom_idx=col.bottom_idx + 1,
                                         start_date=date_str, end_date=date_str))
            else:
                col.end_date = date_str
        prev_box = cur_box

    return columns


def point_figure(closes: pd.Series, box_pct: float = 1.0, reversal: int = 2) -> List[PnFColumn]:
    """
    Close-Only Point & Figure with PERCENTAGE scaling (used for price series).
    Log-space box grid: box_n covers [(1 + pct/100)^n, (1 + pct/100)^(n+1)).
    Reversal is measured in boxes.
    """
    closes = closes.dropna()
    if len(closes) < 2:
        return []
    dates = [str(d) for d in closes.index]
    boxes = [_price_to_box_idx(float(v), box_pct) for v in closes]
    return _pnf_from_boxes(boxes, dates, reversal)


def point_figure_traditional(
    values: pd.Series, box_size: float = 2.0, reversal: int = 3
) -> List[PnFColumn]:
    """
    Traditional (fixed-size linear) Point & Figure — used for bounded series like
    Bullish Percent Indices (0-100). Each box is `box_size` units wide.
    Defaults match StockCharts' typical BPNYA rendering (box=2, reversal=3).
    """
    values = values.dropna()
    if len(values) < 2:
        return []
    dates = [str(d) for d in values.index]
    boxes = [int(math.floor(float(v) / box_size)) for v in values]
    return _pnf_from_boxes(boxes, dates, reversal)


def current_pnf_column(
    closes: pd.Series, box_pct: float = 1.0, reversal: int = 2
) -> Optional[Literal["X", "O"]]:
    cols = point_figure(closes, box_pct, reversal)
    if not cols:
        return None
    return cols[-1].type
