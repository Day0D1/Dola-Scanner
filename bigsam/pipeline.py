"""Glue shared by the live scanner and the backtester:

* ``SeriesContext``  – LTF + HTF bars for one pair/timeframe, with cached HTF contexts
* ``evaluate``       – run the engine at a cutoff bar and apply the HTF verification
* ``to_record``      – flatten a Candidate into a DB row
* ``advance``        – walk a pending/active setup forward through new candles
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import engine as E
from .config import TF_SECONDS, EngineParams
from .data import pip_size, price_decimals
from .risk import position_size
from .analytics import session_of

OPEN_STATUSES = ("PENDING", "TRIGGERED")
CLOSED_STATUSES = ("WIN", "PARTIAL_WIN", "LOSS", "EXPIRED", "INVALIDATED", "MISSED")
RESOLVED_STATUSES = ("WIN", "PARTIAL_WIN", "LOSS")
SNAPSHOT_BARS = 220
FORWARD_BARS = 400


@dataclass
class SeriesContext:
    pair: str
    tf: str
    htf: str
    df: pd.DataFrame
    hdf: pd.DataFrame
    P: EngineParams
    B: E.Bars = field(init=False)
    M: E.Bars = field(init=False)
    HB: E.Bars = field(init=False)
    HM: E.Bars = field(init=False)
    _htf_cache: dict = field(default_factory=dict, init=False)

    def __post_init__(self):
        self.B = E.Bars.from_df(self.df, self.P, pip_size(self.pair))
        self.M = self.B.mirrored()
        self.HB = E.Bars.from_df(self.hdf, self.P, pip_size(self.pair))
        self.HM = self.HB.mirrored()
        close_t = self.HB.time + TF_SECONDS[self.htf]
        self._htf_close = close_t

    def htf_index(self, t: int) -> int:
        """Last HTF bar fully closed when LTF bar t closes."""
        ltf_close = self.B.time[t] + TF_SECONDS[self.tf]
        return int(np.searchsorted(self._htf_close, ltf_close, side="right")) - 1

    def htf_ctx(self, ht: int):
        if ht not in self._htf_cache:
            self._htf_cache[ht] = E.htf_context(self.HB, self.HM, ht, self.P) if ht >= 0 else None
        return self._htf_cache[ht]

    def evaluate(self, t: int) -> list[tuple[E.Candidate, dict]]:
        out = []
        cands = E.analyze(self.B, t, self.P, self.M)
        if not cands:
            return out
        ht = self.htf_index(t)
        ctx = self.htf_ctx(ht)
        hatr = float(self.HB.atr[ht]) if ht >= 0 else 0.0
        for c in cands:
            align = E.htf_alignment(c, ctx, hatr, self.P)
            E.apply_htf(c, align)
            self._apply_filters(c)
            out.append((c, align))
        return out

    def _apply_filters(self, c: E.Candidate) -> None:
        """Optional research filters (configured in .env); applied after the spec rules."""
        if c.reject_reason is not None:
            return
        P = self.P
        liq = c.chosen_liquidity.pattern_type if c.chosen_liquidity else None
        if c.chosen_poi and c.chosen_poi.poi_type not in P.poi_types:
            c.reject_reason = f"Filtered: {c.chosen_poi.poi_type.replace('_', ' ').title()} POIs disabled"
        elif liq and liq in P.exclude_liquidity_types:
            c.reject_reason = f"Filtered: {liq.replace('_', ' ').title()} liquidity excluded"
        elif session_of(c.bar_time).upper() in P.exclude_sessions:
            c.reject_reason = f"Filtered: {session_of(c.bar_time)} session excluded"

    def candles(self, start: int, end: int) -> list[list]:
        start, end = max(0, start), min(len(self.B) - 1, end)
        d = self.df.iloc[start:end + 1]
        return d[["time", "open", "high", "low", "close"]].values.tolist()

    def index_of(self, ts: int) -> int:
        return int(np.searchsorted(self.B.time, ts, side="left"))


def setup_uid(pair: str, tf: str, c: E.Candidate) -> str:
    return f"{pair}|{tf}|{c.direction}|{c.protected_time}|{c.bos_time}"


def trade_key(rec: dict) -> tuple:
    """Identity of the actual order: same pair/side/entry (to 0.1 pip) = same trade."""
    return (rec["pair"], rec["timeframe"], rec["direction"], round(rec["entry"] / (pip_size(rec["pair"]) / 10)))


def to_record(ctx: SeriesContext, c: E.Candidate, align: dict, t: int, *, source: str,
              run_id: int, now: int, risk_amount: float, prices: dict, account_ccy: str) -> dict:
    liq = c.chosen_liquidity
    poi = c.chosen_poi
    rec = {
        "uid": setup_uid(ctx.pair, ctx.tf, c), "source": source, "run_id": run_id,
        "pair": ctx.pair, "timeframe": ctx.tf, "htf": ctx.htf, "direction": c.direction,
        "decision": "APPROVED" if c.approved else "REJECTED",
        "status": "PENDING" if c.approved else "REJECTED",
        "reject_reason": c.reject_reason,
        "poi_type": poi.poi_type if poi else None,
        "liquidity_type": liq.pattern_type if liq else None,
        "liquidity_shape": liq.shape if liq else None,
        "liquidity_depth": liq.fib_depth if liq else None,
        "entry": c.entry, "stop_loss": c.stop_loss, "take_profit": c.take_profit,
        "rr": c.rr, "structure_rr": c.structure_rr,
        "protected_price": c.protected_price, "protected_time": c.protected_time,
        "bos_price": c.bos_price, "bos_time": c.bos_time,
        "range_extreme": c.range_extreme_price, "equilibrium": c.equilibrium,
        "htf_zone": align.get("zone"), "sweep_type": c.sweep_type,
        "detected_bar_time": c.bar_time, "detected_at": now, "last_bar_time": c.bar_time,
        "alerted": 0, "suppressed": 0,
    }
    details = {"candidate": c.to_dict(), "htf": align}
    if c.approved and c.entry is not None:
        rec["risk_amount"] = risk_amount
        if ctx.P.partial_at_r > 0 and ctx.P.partial_pct > 0:
            rec["partial_price"] = c.entry + ctx.P.partial_at_r * (c.entry - c.stop_loss)
        size = position_size(ctx.pair, c.entry, c.stop_loss, risk_amount, prices, account_ccy)
        rec["lots"], rec["stop_pips"] = size["lots"], size["stop_pips"]
        first = min(ctx.index_of(c.swing_high_time), ctx.index_of(c.swept_level_time or c.swing_high_time))
        start = max(first - 30, t - SNAPSHOT_BARS)
        details["candles"] = ctx.candles(start, t)
    elif c.entry is not None:
        rec["stop_pips"] = round(abs(c.entry - c.stop_loss) / pip_size(ctx.pair), 1)
    rec["details"] = details
    return rec


def advance(rec: dict, ctx: SeriesContext, P: EngineParams, upto: int | None = None) -> list[tuple[str, int, str]]:
    """Advance an open setup through bars after ``rec['last_bar_time']``.

    Fill rules (conservative):
      * PENDING  -> TRIGGERED when price trades through the limit entry.
                    On the fill bar only the stop is checked (unknown intrabar order).
      * PENDING  -> MISSED when the take-profit level prints before a fill.
      * PENDING  -> EXPIRED after ``expiry_bars`` LTF bars without a fill.
      * TRIGGERED-> LOSS at stop (checked first when both levels print in one bar) / WIN at target.
      * Partial (if ``partial_price`` is set): at that level ``partial_pct`` is closed and the stop
        moves to entry from the next bar. Then WIN at target (blended R) or PARTIAL_WIN if the
        runner is stopped at entry.
    """
    if rec["status"] not in OPEN_STATUSES:
        return []
    B = ctx.B
    end = len(B) - 1 if upto is None else upto
    start = int(np.searchsorted(B.time, rec["last_bar_time"], side="right"))
    det_idx = ctx.index_of(rec["detected_bar_time"])
    buy = rec["direction"] == "BUY"
    entry, sl, tp = rec["entry"], rec["stop_loss"], rec["take_profit"]
    rr = rec.get("rr") or P.rr_target
    # A pending order is cancelled only once price runs beyond the target, the range extreme that
    # existed at detection AND the 3R level - so the order lives for the same window whatever
    # RR_TARGET is (a closer target must not mean more cancellations).
    rx = rec.get("range_extreme") or tp
    r3 = entry + 3.0 * (entry - sl)
    cancel = max(tp, rx, r3) if buy else min(tp, rx, r3)
    part = rec.get("partial_price")
    part_r = round(abs(part - entry) / abs(entry - sl), 4) if part is not None else 0.0
    frac = P.partial_pct / 100.0
    dp = price_decimals(rec["pair"])
    fx = lambda v: f"{v:.{dp}f}"
    events = []
    for k in range(start, end + 1):
        hi, lo, ts = B.h[k], B.l[k], int(B.time[k])
        rec["last_bar_time"] = ts
        if rec["status"] == "PENDING":
            hit_tp = hi > cancel if buy else lo < cancel
            filled = lo <= entry if buy else hi >= entry
            if filled:
                rec["status"] = "TRIGGERED"
                rec["triggered_at"] = ts
                events.append(("TRIGGERED", ts, f"Limit filled at {fx(entry)}"))
                if (lo <= sl) if buy else (hi >= sl):
                    _close(rec, "LOSS", -1.0, ts)
                    events.append(("LOSS", ts, f"Stop loss hit at {fx(sl)} (-1R)"))
                    break
                continue
            if hit_tp:
                _close(rec, "MISSED", 0.0, ts)
                events.append(("MISSED", ts, "Price expanded beyond the target/range before the limit filled"))
                break
            if k - det_idx >= P.expiry_bars:
                _close(rec, "EXPIRED", 0.0, ts)
                events.append(("EXPIRED", ts, f"Limit order expired after {P.expiry_bars} bars"))
                break
        else:
            stop = entry if rec.get("partial_at") else sl
            if (lo <= stop) if buy else (hi >= stop):
                if rec.get("partial_at"):
                    r = round(frac * part_r, 4)
                    _close(rec, "PARTIAL_WIN", r, ts)
                    events.append(("PARTIAL_WIN", ts, f"Runner stopped at entry {fx(entry)} after the partial (+{r:g}R)"))
                else:
                    _close(rec, "LOSS", -1.0, ts)
                    events.append(("LOSS", ts, f"Stop loss hit at {fx(sl)} (-1R)"))
                break
            if (hi >= tp) if buy else (lo <= tp):
                r = round(frac * part_r + (1 - frac) * rr, 4) if part is not None else float(rr)
                _close(rec, "WIN", r, ts)
                events.append(("WIN", ts, f"Take profit hit at {fx(tp)} (+{r:g}R)"))
                break
            if part is not None and not rec.get("partial_at") and ((hi >= part) if buy else (lo <= part)):
                rec["partial_at"] = ts     # stop moves to entry from the next bar on
                events.append(("PARTIAL", ts, f"Take {P.partial_pct:g}% off at {fx(part)} (+{part_r:g}R) "
                                              f"and move the stop to entry {fx(entry)}"))
    if rec["status"] in CLOSED_STATUSES and rec.get("details") is not None:
        d = rec["details"] if isinstance(rec["details"], dict) else None
        if d is not None:
            close_idx = ctx.index_of(rec["closed_at"])
            d["forward"] = ctx.candles(det_idx + 1, min(close_idx + 10, det_idx + FORWARD_BARS))
    return events


def _close(rec: dict, status: str, r: float, ts: int) -> None:
    rec["status"] = status
    rec["closed_at"] = ts
    rec["result_r"] = r
    if rec.get("risk_amount") is not None:
        rec["pnl"] = round(r * rec["risk_amount"], 2)
