"""Prop-firm rules (Goat Funded Trader 'Goat Stride' / Instant HERO by default).

* ``guard``               - pre-alert checks: exposure cap and no hedging
* ``apply_exposure_cap``  - the same rules replayed over backtest trades (outcomes are independent,
                            so filtering afterwards is exact)
* ``tracker``             - live estimate of the account's limits from the trades the system tracks
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import _bool, _env

NY = ZoneInfo("America/New_York")
ACTIVE = ("PENDING", "TRIGGERED")


@dataclass
class PropRules:
    enabled: bool = field(default_factory=lambda: _bool("PROP_MODE", True))
    firm: str = field(default_factory=lambda: _env("PROP_FIRM", "Goat Funded Trader - Goat Stride"))
    max_open: int = field(default_factory=lambda: int(_env("PROP_MAX_OPEN", "2")))
    daily_loss_pct: float = field(default_factory=lambda: float(_env("PROP_DAILY_LOSS_PCT", "3")))
    max_loss_pct: float = field(default_factory=lambda: float(_env("PROP_MAX_LOSS_PCT", "5")))
    floating_pct: float = field(default_factory=lambda: float(_env("PROP_FLOATING_PCT", "1")))
    valid_day_pct: float = field(default_factory=lambda: float(_env("PROP_VALID_DAY_PCT", "0.5")))
    min_valid_days: int = field(default_factory=lambda: int(_env("PROP_MIN_VALID_DAYS", "6")))
    consistency_pct: float = field(default_factory=lambda: float(_env("PROP_CONSISTENCY_PCT", "15")))
    payout_days: int = field(default_factory=lambda: int(_env("PROP_PAYOUT_DAYS", "14")))
    split_pct: float = field(default_factory=lambda: float(_env("PROP_SPLIT_PCT", "90")))


rules = PropRules()


def trading_day(ts: int) -> str:
    """Prop-firm trading day: rolls over at 5 PM New York time."""
    return (datetime.fromtimestamp(ts, NY) + timedelta(hours=7)).strftime("%Y-%m-%d")


# ------------------------------------------------------------------ pre-alert guard

def guard(rec: dict, active: list[dict], R: PropRules = rules) -> str | None:
    """Reason to skip a new alert, else None. Only hedging blocks an alert: pending orders don't
    create floating loss, so the open-position cap is enforced at fill time (``fill_blocked``)."""
    if not R.enabled:
        return None
    for a in active:
        if a["pair"] == rec["pair"] and a["direction"] != rec["direction"]:
            return f"Skipped: opposite {a['direction']} already active on {rec['pair']} (hedging is prohibited)"
    return None


def fill_blocked(rec: dict, open_now: list[dict], R: PropRules = rules) -> str | None:
    """Reason an order must not fill now (it should have been cancelled), else None."""
    if not R.enabled:
        return None
    if any(a["pair"] == rec["pair"] and a["direction"] != rec["direction"] for a in open_now):
        return f"Cancelled: opposite position open on {rec['pair']} (hedging is prohibited)"
    if len(open_now) >= R.max_open:
        return f"Cancelled: {len(open_now)}/{R.max_open} positions already open when it would have filled"
    return None


def apply_exposure_cap(recs: list[dict], R: PropRules = rules) -> int:
    """Replay the open-position cap chronologically over backtest records (all pairs together).
    An order that would fill while ``max_open`` positions are open (or against an opposite open
    position on the same pair) counts as cancelled. Outcomes are independent, so this is exact."""
    if not R.enabled:
        return 0
    fills = sorted((r for r in recs if r["decision"] == "APPROVED" and r.get("triggered_at") is not None),
                   key=lambda r: r["triggered_at"])
    taken: list[dict] = []
    skipped = 0
    for r in fills:
        t = r["triggered_at"]
        open_now = [a for a in taken if a.get("closed_at") is None or a["closed_at"] > t]
        reason = fill_blocked(r, open_now, R)
        if reason:
            r.update(status="SKIPPED", reject_reason=reason, result_r=None, pnl=None)
            skipped += 1
        else:
            taken.append(r)
    return skipped


# ------------------------------------------------------------------ live tracker

def tracker(trades: list[dict], open_float: float, account_size: float, risk: float,
            since: int, last_payout: int | None, now: int, R: PropRules = rules) -> dict:
    """Estimate the prop limits from system-tracked trades (assumes every alert was taken).

    ``trades``: live resolved trades closed after ``since`` (pnl in account currency).
    ``open_float``: current unrealised P&L of open trades.
    """
    period_start = last_payout or since    # the firm resets the max-loss limit after each payout
    trades = sorted((s for s in trades if s["closed_at"] >= period_start), key=lambda s: s["closed_at"])
    balance, peak = account_size, account_size
    for s in trades:                       # trailing max loss follows the equity high-water mark
        balance += s["pnl"] or 0
        peak = max(peak, balance)
    equity = balance + open_float
    peak = max(peak, equity)
    floor = peak * (1 - R.max_loss_pct / 100)   # e.g. equity high $2,600 -> floor $2,470

    today = trading_day(now)
    daily: dict[str, float] = {}
    for s in trades:
        d = trading_day(s["closed_at"])
        daily[d] = daily.get(d, 0.0) + (s["pnl"] or 0)
    today_closed = daily.get(today, 0.0)
    daily_limit = account_size * R.daily_loss_pct / 100
    daily_used = max(0.0, -(today_closed + min(open_float, 0)))

    valid_min = account_size * R.valid_day_pct / 100
    valid_days = sorted(d for d, v in daily.items() if v >= valid_min)
    period_profit = sum(daily.values())
    best_day = max(daily.values()) if daily else 0.0
    consistency = round(100 * best_day / period_profit, 1) if period_profit > 0 else None
    days_since = (now - period_start) / 86400
    eligible = {
        "valid_days": len(valid_days) >= R.min_valid_days,
        "consistency": consistency is not None and consistency <= R.consistency_pct,
        "cycle": days_since >= R.payout_days,
        "in_profit": period_profit > 0,
    }
    floating_limit = account_size * R.floating_pct / 100
    return {
        "firm": R.firm, "enabled": R.enabled,
        "balance": round(balance, 2), "equity": round(equity, 2), "high_water": round(peak, 2),
        "max_loss_floor": round(floor, 2), "room_to_floor": round(equity - floor, 2),
        "daily_limit": round(daily_limit, 2), "daily_used": round(daily_used, 2),
        "today_closed": round(today_closed, 2),
        "floating_limit": round(floating_limit, 2), "floating_now": round(min(open_float, 0), 2),
        "risk_per_trade": round(risk, 2), "max_open": R.max_open,
        "valid_day_min": round(valid_min, 2), "valid_days": len(valid_days), "valid_days_needed": R.min_valid_days,
        "period_profit": round(period_profit, 2), "best_day": round(best_day, 2),
        "consistency_pct": consistency, "consistency_max": R.consistency_pct,
        "min_profit_for_payout": round(best_day / (R.consistency_pct / 100), 2) if best_day > 0 else None,
        "days_since_payout": round(days_since, 1), "payout_days": R.payout_days,
        "payout_eligible": all(eligible.values()), "eligibility": eligible,
        "your_share": round(max(period_profit, 0) * R.split_pct / 100, 2),
        "period_start": period_start, "trading_day": today,
    }
