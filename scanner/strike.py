"""Strike-price helper — one place, one rule, used everywhere alerts are shown.

Convention (locked to the user's daily workflow):
  SELL_PUTS  → strike is 15% BELOW the trigger price  (bullish setup; sell puts
               at a strike we'd be OK owning at)
  SELL_CALLS → strike is 15% ABOVE the trigger price  (bearish setup; sell calls
               at a strike we don't expect price to reach)

The raw 15%-offset price is then rounded to the closest multiple of $5 so the
strike lands on a real, tradeable options-chain level. Uses standard "round
half up" (not banker's rounding) so exact halves always round to the wider
distance from trigger — puts-strike rounds DOWN, calls-strike rounds UP.
"""
from __future__ import annotations

import math
from typing import Optional


STRIKE_PCT_OFFSET = 15.0   # % away from the trigger price
STRIKE_STEP = 5.0          # round to nearest $5


def _round_half_up(v: float, step: float) -> float:
    """Round v to the nearest multiple of step. Ties go to the WIDER value
    from zero — matches how options traders eyeball strike selection."""
    return math.floor(v / step + 0.5) * step


def compute_strike(
    trigger_price: Optional[float],
    direction: Optional[str],
    pct: float = STRIKE_PCT_OFFSET,
    step: float = STRIKE_STEP,
) -> Optional[float]:
    """Return the strike price for a fired alert, or None if inputs invalid.

    Rounded so puts land BELOW their raw target and calls land ABOVE.
    """
    if trigger_price is None or trigger_price <= 0 or direction not in ("SELL_PUTS", "SELL_CALLS"):
        return None
    if direction == "SELL_PUTS":
        # 15% below, then round DOWN to the nearest $5 so the strike is at
        # least the target distance away (safer for the trade).
        raw = trigger_price * (1.0 - pct / 100.0)
        return math.floor(raw / step) * step
    # SELL_CALLS: 15% above, then round UP so strike is at least that far away.
    raw = trigger_price * (1.0 + pct / 100.0)
    return math.ceil(raw / step) * step
