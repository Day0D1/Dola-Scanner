"""Sanity tests for indicators. Run: python -m tests.test_indicators"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from scanner import indicators


def _approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return math.isclose(a, b, rel_tol=tol, abs_tol=tol)


def test_bollinger_10_2_constant_series():
    closes = pd.Series([100.0] * 15)
    bb = indicators.bollinger_bands(closes, period=10, num_std=2.0)
    # Constant series: middle = 100, std = 0, upper = lower = 100
    assert _approx(bb["middle"].iloc[-1], 100.0)
    assert _approx(bb["upper"].iloc[-1], 100.0)
    assert _approx(bb["lower"].iloc[-1], 100.0)
    print("PASS  bollinger_10_2_constant_series")


def test_bollinger_10_2_known_values():
    # Known series: closes 1..10 -> mean 5.5, population std = sqrt(mean((x - 5.5)^2))
    closes = pd.Series([1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    bb = indicators.bollinger_bands(closes, period=10, num_std=2.0)
    expected_mean = 5.5
    var = sum((x - expected_mean) ** 2 for x in closes) / len(closes)
    expected_std = math.sqrt(var)
    assert _approx(bb["middle"].iloc[-1], expected_mean, tol=1e-9)
    assert _approx(bb["upper"].iloc[-1], expected_mean + 2 * expected_std)
    assert _approx(bb["lower"].iloc[-1], expected_mean - 2 * expected_std)
    print("PASS  bollinger_10_2_known_values")


def test_rsi_5_all_gains_gives_100():
    closes = pd.Series([100.0, 101, 102, 103, 104, 105, 106])
    r = indicators.rsi(closes, period=5)
    assert _approx(r.iloc[-1], 100.0)
    print("PASS  rsi_5_all_gains_gives_100")


def test_rsi_5_all_losses_gives_0():
    closes = pd.Series([100.0, 99, 98, 97, 96, 95, 94])
    r = indicators.rsi(closes, period=5)
    assert _approx(r.iloc[-1], 0.0)
    print("PASS  rsi_5_all_losses_gives_0")


def test_pnf_uptrend_stays_x():
    # 20 bars, +2% every day - always in X column
    closes = pd.Series([100.0 * (1.02 ** i) for i in range(20)])
    col = indicators.current_pnf_column(closes, box_pct=1.0, reversal=2)
    assert col == "X", f"expected X, got {col}"
    print("PASS  pnf_uptrend_stays_x")


def test_pnf_downtrend_stays_o():
    closes = pd.Series([100.0 * (0.98 ** i) for i in range(20)])
    col = indicators.current_pnf_column(closes, box_pct=1.0, reversal=2)
    assert col == "O", f"expected O, got {col}"
    print("PASS  pnf_downtrend_stays_o")


def test_pnf_reversal_x_to_o():
    # Up 5%, then down 3% - 3% > 2% reversal threshold, should flip to O
    up = [100.0 * (1.01 ** i) for i in range(6)]      # 100 -> ~105
    down = [up[-1] * (0.99 ** (i + 1)) for i in range(4)]  # drop ~4%
    closes = pd.Series(up + down)
    col = indicators.current_pnf_column(closes, box_pct=1.0, reversal=2)
    assert col == "O", f"expected O after reversal, got {col}"
    print("PASS  pnf_reversal_x_to_o")


def test_pnf_no_reversal_below_threshold():
    # Up 5%, then drop 1.5% - below the 2% reversal threshold, should stay X
    up = [100.0 * (1.01 ** i) for i in range(6)]
    down = [up[-1] * 0.985]
    closes = pd.Series(up + down)
    col = indicators.current_pnf_column(closes, box_pct=1.0, reversal=2)
    assert col == "X", f"expected X (no reversal), got {col}"
    print("PASS  pnf_no_reversal_below_threshold")


if __name__ == "__main__":
    tests = [
        test_bollinger_10_2_constant_series,
        test_bollinger_10_2_known_values,
        test_rsi_5_all_gains_gives_100,
        test_rsi_5_all_losses_gives_0,
        test_pnf_uptrend_stays_x,
        test_pnf_downtrend_stays_o,
        test_pnf_reversal_x_to_o,
        test_pnf_no_reversal_below_threshold,
    ]
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            sys.exit(1)
    print("\nAll tests passed.")
