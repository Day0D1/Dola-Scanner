"""Performance statistics computed from setup rows."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

RESOLVED = ("WIN", "PARTIAL_WIN", "LOSS")


def _won(s: dict) -> bool:
    return (s.get("result_r") or 0) > 0


def session_of(ts: int) -> str:
    h = datetime.fromtimestamp(ts, timezone.utc).hour
    if 7 <= h < 12:
        return "London"
    if 12 <= h < 16:
        return "London/NY overlap"
    if 16 <= h < 21:
        return "New York"
    return "Asia"


def _group(trades: list[dict], key) -> list[dict]:
    g: dict[str, list[dict]] = defaultdict(list)
    for s in trades:
        g[key(s) or "—"].append(s)
    out = []
    for k, items in g.items():
        wins = sum(1 for s in items if _won(s))
        net = sum(s["result_r"] or 0 for s in items)
        out.append({"key": k, "n": len(items), "wins": wins,
                    "win_rate": round(100 * wins / len(items), 1),
                    "net_r": round(net, 2), "expectancy": round(net / len(items), 2)})
    return sorted(out, key=lambda r: -r["net_r"])


def normalize_reason(r: str | None) -> str:
    if not r:
        return "—"
    return re.sub(r"\s*\(.*\)$", "", r)


def compute(setups: list[dict], account_size: float, risk_amount: float) -> dict:
    approved = [s for s in setups if s["decision"] == "APPROVED"]
    rejected = [s for s in setups if s["decision"] == "REJECTED"]
    trades = sorted([s for s in approved if s["status"] in RESOLVED], key=lambda s: s["closed_at"] or 0)
    n = len(trades)
    wins = [s for s in trades if _won(s)]
    gross_win = sum(s["result_r"] for s in wins)
    gross_loss = -sum(s["result_r"] for s in trades if (s["result_r"] or 0) < 0)
    net_r = gross_win - gross_loss

    equity, cum, peak, max_dd = [], 0.0, 0.0, 0.0
    streak = max_loss_streak = max_win_streak = 0
    for s in trades:
        r = s["result_r"] or 0.0
        cum += r
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        streak = (streak + 1 if streak > 0 else 1) if r > 0 else (streak - 1 if streak < 0 else -1)
        max_win_streak = max(max_win_streak, streak)
        max_loss_streak = max(max_loss_streak, -streak)
        pnl = s["pnl"] if s.get("pnl") is not None else r * risk_amount
        equity.append({"t": s["closed_at"], "r": round(cum, 2), "pair": s["pair"], "result": r,
                       "pnl": round(pnl, 2)})
    balance = account_size
    for e in equity:
        balance += e["pnl"]
        e["balance"] = round(balance, 2)

    batches = []
    for i in range(0, n, 10):
        chunk = trades[i:i + 10]
        batches.append({"index": i // 10 + 1, "n": len(chunk),
                        "wins": sum(1 for s in chunk if _won(s)),
                        "net_r": round(sum(s["result_r"] for s in chunk), 2)})

    status_counts = Counter(s["status"] for s in approved)
    triggered = sum(status_counts[k] for k in ("TRIGGERED", "WIN", "PARTIAL_WIN", "LOSS"))
    resolved_orders = triggered + status_counts["MISSED"] + status_counts["EXPIRED"]

    daily = defaultdict(float)
    for s in trades:
        daily[datetime.fromtimestamp(s["closed_at"], timezone.utc).strftime("%Y-%m-%d")] += s["result_r"]

    return {
        "summary": {
            "trades": n, "wins": len(wins), "losses": n - len(wins),
            "full_wins": sum(1 for s in trades if s["status"] == "WIN"),
            "partial_wins": sum(1 for s in trades if s["status"] == "PARTIAL_WIN"),
            "win_rate": round(100 * len(wins) / n, 1) if n else 0.0,
            "net_r": round(net_r, 2), "expectancy_r": round(net_r / n, 3) if n else 0.0,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
            "net_pnl": round(sum(e["pnl"] for e in equity), 2),
            "max_drawdown_r": round(max_dd, 2),
            "max_loss_streak": max_loss_streak, "max_win_streak": max_win_streak,
            "current_streak": streak,
            # win rate needed to break even at the realised average win size
            "breakeven_win_rate": round(100 / (1 + gross_win / len(wins)), 1) if wins else None,
        },
        "funnel": {
            "candidates": len(setups), "approved": len(approved), "rejected": len(rejected),
            "pending": status_counts["PENDING"], "triggered": triggered,
            "active": status_counts["TRIGGERED"], "missed": status_counts["MISSED"],
            "expired": status_counts["EXPIRED"], "resolved": n,
            "fill_rate": round(100 * triggered / resolved_orders, 1) if resolved_orders else None,
            "approval_rate": round(100 * len(approved) / len(setups), 1) if setups else None,
        },
        "equity": equity,
        "batches": batches,
        "current_batch": {"index": n // 10 + 1, "trades": n % 10,
                          "net_r": round(sum(s["result_r"] for s in trades[n - n % 10:]), 2) if n % 10 else 0.0},
        "by_pair": _group(trades, lambda s: s["pair"]),
        "by_timeframe": _group(trades, lambda s: s["timeframe"]),
        "by_direction": _group(trades, lambda s: s["direction"]),
        "by_poi": _group(trades, lambda s: (s["poi_type"] or "").replace("_", " ").title()),
        "by_liquidity": _group(trades, lambda s: (s["liquidity_type"] or "").replace("_", " ").title()),
        "by_htf_zone": _group(trades, lambda s: s["htf_zone"]),
        "by_sweep": _group(trades, lambda s: (s["sweep_type"] or "").title()),
        "by_session": _group(trades, lambda s: session_of(s["detected_bar_time"])),
        "rejections": [{"reason": k, "n": v} for k, v in
                       Counter(normalize_reason(s["reject_reason"]) for s in rejected).most_common()],
        "daily": [{"date": k, "net_r": round(v, 2)} for k, v in sorted(daily.items())],
    }
