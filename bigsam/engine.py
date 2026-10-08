"""Market-structure pattern engine.

Implements the specification deterministically and without look-ahead: every analysis
is evaluated at a cutoff bar ``t`` and only uses pivots that are *confirmed* by ``t``.

Bearish analysis reuses the bullish logic on a price-mirrored series (prices negated,
highs<->lows swapped), so a bearish structure is literally a bullish one upside down.
That keeps the two directions perfectly symmetric.

Pipeline per (pair, timeframe, direction), spec section 5.2:
  1. LTF pattern scan    -> latest BOS, its protected extreme and the transactional sweep
  2. Liquidity validation-> structural liquidity (>= 50% fib depth, V/A geometry) vs inducement
  3. POI                 -> Order Block / Quasimodo Reversal behind the liquidity, in discount/premium
  4. HTF verification    -> 'where price is coming from' (done by ``htf_alignment``)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from .config import EngineParams


# --------------------------------------------------------------------------- bars

def _pivots(h: np.ndarray, l: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Fractal pivots. Pivot high at i: strictly above the k bars to the left and
    at least as high as the k bars to the right (confirmation arrives at bar i+k)."""
    n = len(h)
    ph = np.zeros(n, bool)
    pl = np.zeros(n, bool)
    if n < 2 * k + 1:
        return ph, pl
    wh = sliding_window_view(h, 2 * k + 1)
    wl = sliding_window_view(l, 2 * k + 1)
    ch, cl = wh[:, k], wl[:, k]
    ph[k:n - k] = (ch > wh[:, :k].max(1)) & (ch >= wh[:, k + 1:].max(1))
    pl[k:n - k] = (cl < wl[:, :k].min(1)) & (cl <= wl[:, k + 1:].min(1))
    return ph, pl


def _atr(h, l, c, period):
    prev = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    return pd.Series(tr).rolling(period, min_periods=1).mean().to_numpy()


@dataclass
class Bars:
    time: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    atr: np.ndarray
    ph: np.ndarray      # swing pivots
    pl: np.ndarray
    lph: np.ndarray     # liquidity pivots
    lpl: np.ndarray
    sign: int = 1       # +1 real prices, -1 mirrored (bearish analysis)
    pip: float = 0.0    # pip size (enables the min_stop_pips rule)

    @classmethod
    def from_df(cls, df: pd.DataFrame, P: EngineParams, pip: float = 0.0) -> "Bars":
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        ph, pl = _pivots(h, l, P.swing_k)
        lph, lpl = _pivots(h, l, P.liq_k)
        return cls(df["time"].to_numpy(np.int64), o, h, l, c, _atr(h, l, c, P.atr_period),
                   ph, pl, lph, lpl, 1, pip)

    def mirrored(self) -> "Bars":
        return Bars(self.time, -self.o, -self.l, -self.h, -self.c, self.atr,
                    self.pl, self.ph, self.lpl, self.lph, -self.sign, self.pip)

    def __len__(self):
        return len(self.time)


# --------------------------------------------------------------------------- results

@dataclass
class Liquidity:
    idx: int
    time: int
    price: float
    fib_depth: float          # 0 = range extreme (BOS high for buys), 1 = protected extreme
    pattern_type: str         # TYPE_1 / TYPE_2 / TYPE_3
    shape: str                # V_SHAPE / A_SHAPE / INVALID
    shape_detail: str         # OK / SINGLE_CANDLE / CONSOLIDATION / WEAK_LEG / UNCONFIRMED
    swept: bool
    classification: str = ""  # STRUCTURAL / INDUCEMENT / INVALID_GEOMETRY

    @property
    def is_valid(self) -> bool:
        return self.classification == "STRUCTURAL"


@dataclass
class POI:
    poi_type: str             # ORDER_BLOCK / QUASIMODO_REVERSAL
    idx: int
    time: int
    proximal: float           # entry edge
    distal: float
    mitigated: bool
    in_zone: bool = False     # entry sits in discount (buys) / premium (sells)
    behind_liquidity: bool = False


@dataclass
class Candidate:
    direction: str            # BUY / SELL
    bar_time: int             # cutoff bar the analysis was made on
    swing_high_time: int      # the swing extreme broken by the BOS ("previous swing high")
    swing_high_price: float
    swept_level_time: int | None
    swept_level_price: float | None
    sweep: bool
    sweep_type: str           # WICK / BODY / NONE
    protected_time: int
    protected_price: float
    bos_time: int
    bos_price: float          # level that was broken
    range_extreme_time: int
    range_extreme_price: float
    equilibrium: float
    liquidity: list[Liquidity] = field(default_factory=list)
    pois: list[POI] = field(default_factory=list)
    chosen_liquidity: Liquidity | None = None
    chosen_poi: POI | None = None
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    rr: float | None = None
    structure_rr: float | None = None
    checks: dict = field(default_factory=dict)
    reject_reason: str | None = None
    atr: float = 0.0
    bos_count: int = 1        # BOS events in the current leg since the liquidity sweep
    last_bos_time: int | None = None
    last_bos_price: float | None = None
    # every step of the leg: [{time, price (level broken), low_time, low_price (pullback before it)}]
    bos_chain: list = field(default_factory=list)

    @property
    def approved(self) -> bool:
        return self.reject_reason is None

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- helpers

def _last_true(mask: np.ndarray, lo: int, hi: int) -> int | None:
    """Largest index j in [lo, hi] with mask[j]."""
    if hi < lo:
        return None
    nz = np.flatnonzero(mask[lo:hi + 1])
    return int(lo + nz[-1]) if len(nz) else None


def _first_index(cond: np.ndarray, offset: int) -> int | None:
    nz = np.flatnonzero(cond)
    return int(offset + nz[0]) if len(nz) else None


def classify_v_shape(B: Bars, j: int, t: int, P: EngineParams) -> str:
    """Visual geometry rule (filter 2) for a trough at j (V). On mirrored bars it is an A.

    Returns OK, SINGLE_CANDLE, CONSOLIDATION, WEAK_LEG or UNCONFIRMED."""
    n = P.shape_bars
    if j - n < 0:
        return "UNCONFIRMED"
    if j + n > t:
        return "UNCONFIRMED"
    left = slice(j - n, j)
    right = slice(j + 1, j + n + 1)
    a = max(B.atr[j], 1e-12)
    down = int(np.sum(B.c[left] < B.o[left]) + (B.c[j] < B.o[j]))
    up = int(np.sum(B.c[right] > B.o[right]) + (B.c[j] > B.o[j]))
    left_leg = B.h[left].max() - B.l[j]
    right_leg = B.h[right].max() - B.l[j]
    if left_leg < P.shape_min_leg_atr * a and right_leg < P.shape_min_leg_atr * a:
        return "CONSOLIDATION"
    if down < P.shape_min_dir or up < P.shape_min_dir:
        return "SINGLE_CANDLE"
    if left_leg < P.shape_min_leg_atr * a or right_leg < P.shape_min_leg_atr * a:
        return "WEAK_LEG"
    # a single candle must not account for (nearly) the whole leg
    lb = np.abs(B.c[left] - B.o[left]).max()
    rb = np.abs(B.c[right] - B.o[right]).max()
    if lb > P.shape_max_single * left_leg or rb > P.shape_max_single * right_leg:
        return "SINGLE_CANDLE"
    return "OK"


def find_bos(B: Bars, t: int, P: EngineParams, lo: int | None = None):
    """Most recent bullish BOS at or before t: a candle BODY closing above a confirmed
    swing high. Returns list of (swing_high_idx, bos_idx) sharing the latest bos_idx,
    highest swing high first."""
    if lo is None:
        lo = max(0, t - P.lookback)
    k = P.swing_k
    hi_idx = np.flatnonzero(B.ph[lo:max(lo, t - k + 1)]) + lo
    events: list[tuple[int, int]] = []
    for i in hi_idx:
        b = _first_index(B.c[i + 1:t + 1] > B.h[i], i + 1)
        if b is not None:
            events.append((int(i), b))
    if not events:
        return []
    latest = max(b for _, b in events)
    same = [e for e in events if e[1] == latest]
    same.sort(key=lambda e: -B.h[e[0]])
    return same


def _structure(B: Bars, t: int, P: EngineParams, i: int, b: int):
    """Sweep + BOS + protected extreme for the event (swing high i broken at b)."""
    p = i + 1 + int(np.argmin(B.l[i + 1:b + 1]))
    p_low = B.l[p]
    # the previous swing low (the one that produced the swing high) must be swept
    lo = max(0, t - P.lookback - 50)
    j1 = _last_true(B.pl, lo, i - 1)
    sweep, sweep_type = False, "NONE"
    if j1 is not None and p_low < B.l[j1]:
        sweep = True
        sweep_type = "BODY" if np.any(B.c[i + 1:b] < B.l[j1]) else "WICK"
    intact = (p >= t) or bool(B.l[p + 1:t + 1].min() >= p_low)
    return p, j1, sweep, sweep_type, intact


def bos_events(B: Bars, t: int, P: EngineParams) -> list[tuple[int, int, int]]:
    """All bullish BOS events in the window as (swing_high_idx, bos_idx, pullback_low_idx),
    one per BOS candle (the highest swing high it broke), oldest first."""
    lo = max(0, t - P.lookback)
    k = P.swing_k
    by_b: dict[int, int] = {}
    for i in np.flatnonzero(B.ph[lo:max(lo, t - k + 1)]) + lo:
        i = int(i)
        b = _first_index(B.c[i + 1:t + 1] > B.h[i], i + 1)
        if b is not None and (b not in by_b or B.h[i] > B.h[by_b[b]]):
            by_b[b] = i
    out = []
    for b in sorted(by_b):
        i = by_b[b]
        out.append((i, b, i + 1 + int(np.argmin(B.l[i + 1:b + 1]))))
    return out


def bos_chain(B: Bars, t: int, P: EngineParams):
    """The current trend leg as a chain of BOS events with rising pullback lows:

        sweep -> BOS1 -> drop-down (higher low) -> BOS2 -> ... -> latest BOS

    Walks back from the latest BOS; each earlier event must have broken structure before the
    next swing high formed and have a pullback low at or below the next one. Stops at the first
    event that swept liquidity (the origin). Returns (chain oldest-first, origin_sweep_info)."""
    events = bos_events(B, t, P)
    if not events:
        return [], None
    chain = [events[-1]]
    for ev in reversed(events[:-1]):
        cur = chain[0]
        info = _structure(B, t, P, cur[0], cur[1])
        if info[2]:                      # current head already swept liquidity -> origin found
            break
        if ev[1] > cur[0]:               # broke after the next high formed: internal noise, skip
            continue
        if B.l[ev[2]] > B.l[cur[2]]:     # lower low between legs -> trend leg broken
            break
        chain.insert(0, ev)
    head = chain[0]
    p, j1, sweep, sweep_type, _ = _structure(B, t, P, head[0], head[1])
    return chain, (p, j1, sweep, sweep_type)


def reversal_structure(B: Bars, t: int, P: EngineParams):
    """'Down -> Up' reversal into an origin POI (the user's sketch):

        lower high LH -> drop sweeps the prior low into the ORIGIN low O (the POI)
        -> break 1: price trades above LH (a wick is enough)
        -> drop-down: higher low D (inducement)
        -> break 2: candle BODY closes above the break-1 high H2 (BOS)
        -> price returns to the origin POI, sweeping D on the way -> entry

    Returns dict or None. Uses only pivots confirmed by t."""
    events = bos_events(B, t, P)
    if not events:
        return None
    i2, b2, d = events[-1]                       # latest body BOS and its drop-down low
    lo = max(0, t - P.lookback)
    for lh in np.flatnonzero(B.ph[lo:i2])[::-1] + lo:   # candidate lower highs, most recent first
        lh = int(lh)
        if B.h[lh] >= B.h[i2]:
            break                                # a higher high stands before: not a reversal leg
        o = lh + 1 + int(np.argmin(B.l[lh + 1:i2 + 1]))
        if B.l[o] >= B.l[d]:
            continue                             # drop-down must be a higher low than the origin
        j1 = _last_true(B.pl, max(0, lo - 50), lh - 1)
        if j1 is None or B.l[o] >= B.l[j1]:
            continue                             # origin must sweep the previous swing low
        k1 = _first_index(B.h[o + 1:i2 + 1] > B.h[lh], o + 1)
        if k1 is None:
            continue
        body1 = bool(np.any(B.c[o + 1:i2 + 1] > B.h[lh]))
        sweep_type = "BODY" if np.any(B.c[lh + 1:o] < B.l[j1]) else "WICK"
        n_bos = 1 + sum(1 for (ei, eb, ep) in events if eb > k1 and ei >= o)
        return {"lh": lh, "o": o, "j1": j1, "k1": k1, "break1": "BODY" if body1 else "WICK",
                "i2": i2, "b2": b2, "d": d, "sweep_type": sweep_type, "bos_count": n_bos}
    return None


def prior_trend_bos(opp: Bars | None, o: int, P: EngineParams) -> int:
    """Opposite-direction BOS leading into the origin (e.g. BOS, BOS down before a buy).
    Counted on the mirrored series as a run of breaks at successively more extreme levels."""
    if opp is None or o < 2 * P.swing_k + 2:
        return 0
    ev = [e for e in bos_events(opp, o, P) if e[1] <= o]
    if not ev:
        return 0
    n, level = 1, opp.h[ev[-1][0]]
    for ei, eb, ep in reversed(ev[:-1]):
        if opp.h[ei] < level:
            n, level = n + 1, opp.h[ei]
    return n


def analyze_bullish(B: Bars, t: int, P: EngineParams, opp: Bars | None = None) -> Candidate | None:
    """Run the full bullish pipeline on (possibly mirrored) bars at cutoff t.
    ``opp`` is the opposite-direction series (used for the prior-trend rule)."""
    bos_count, last_bos, chain = 1, None, []
    allow_qmr = True
    liq_start = None        # first bar searched for liquidity (default: after the broken swing high)
    ob_end = None           # last bar the OB displacement may occur on (default: the BOS bar)
    prior_n = None
    if P.min_bos > 1 and P.chain_poi == "reversal":
        r = reversal_structure(B, t, P)
        if r is None:
            return None
        i, b, p, j1 = r["lh"], r["k1"], r["o"], r["j1"]
        sweep, sweep_type = True, r["sweep_type"]
        bos_count = r["bos_count"]
        last_bos = (r["i2"], r["b2"], r["d"])
        chain = [(r["lh"], r["k1"], r["o"]), last_bos]
        liq_start, ob_end = p + 1, r["b2"]
        if not (p >= t or B.l[p + 1:t + 1].min() >= B.l[p]):
            return None
        if P.prior_trend_bos > 0:
            prior_n = prior_trend_bos(opp, p, P)
    elif P.min_bos <= 1:
        events = find_bos(B, t, P)
        if not events:
            return None
        chosen = None
        for i, b in events:
            p, j1, sweep, sweep_type, intact = _structure(B, t, P, i, b)
            if not intact:
                continue
            if chosen is None or (sweep and not chosen[4]):
                chosen = (i, b, p, j1, sweep, sweep_type)
            if sweep:
                break
        if chosen is None:
            return None  # latest structure already invalidated (protected extreme breached)
        i, b, p, j1, sweep, sweep_type = chosen
    else:
        chain, origin = bos_chain(B, t, P)
        if not chain:
            return None
        p1, j1, sweep, sweep_type = origin
        bos_count = len(chain)
        last_bos = chain[-1]
        if P.chain_poi == "latest" and bos_count >= 2:
            # trade the POI at the final drop-down: structure = latest BOS leg
            i, b, p = last_bos
            allow_qmr = False
        else:
            # trade the original POI: structure = origin leg, later BOS legs extend the range
            i, b = chain[0][0], chain[0][1]
            p = p1
        if not (p >= t or B.l[p + 1:t + 1].min() >= B.l[p]):
            return None  # protected extreme breached

    s = B.sign
    rx = b + int(np.argmax(B.h[b:t + 1]))
    r_hi, p_low = B.h[rx], B.l[p]
    span = r_hi - p_low
    if span <= 0:
        return None
    eq = (r_hi + p_low) / 2.0
    atr_t = float(B.atr[t])

    def depth(price: float) -> float:
        return float((r_hi - price) / span)

    # ---- liquidity candidates (internal pivots inside the active range)
    liqs: list[Liquidity] = []
    ls = i + 1 if liq_start is None else liq_start
    for j in np.flatnonzero(B.lpl[ls:t - P.liq_k + 1]) + ls:
        j = int(j)
        if j == p or B.l[j] <= p_low:
            continue
        ptype = "TYPE_2" if j < p else ("TYPE_1" if j <= b else "TYPE_3")
        detail = classify_v_shape(B, j, t, P)
        swept = bool(j < t and B.l[j + 1:t + 1].min() < B.l[j])
        d = depth(B.l[j])
        shape = ("V_SHAPE" if s > 0 else "A_SHAPE") if detail == "OK" else "INVALID"
        if d < P.min_fib_depth:
            cls = "INDUCEMENT"
        elif shape == "INVALID":
            cls = "INVALID_GEOMETRY"
        else:
            cls = "STRUCTURAL"
        liqs.append(Liquidity(j, int(B.time[j]), float(s * B.l[j]), round(d, 4), ptype, shape,
                              detail, swept, cls))
    valid_liqs = [q for q in liqs if q.is_valid]

    # ---- POIs
    pois: list[POI] = []
    # Order block: last opposing candle before the impulse that caused the BOS
    oe = b if ob_end is None else ob_end
    for j in range(min(oe - 1, p + P.ob_search_fwd), max(0, p - P.ob_search_back) - 1, -1):
        if B.c[j] < B.o[j]:
            d_idx = _first_index(B.c[j + 1:oe + 1] > B.h[j], j + 1)
            if d_idx is None:
                continue
            prox, dist = B.o[j], min(B.l[j], p_low)
            mitig = bool(d_idx < t and B.l[d_idx + 1:t + 1].min() <= prox)
            pois.append(POI("ORDER_BLOCK", j, int(B.time[j]), float(s * prox), float(s * dist), mitig))
            break
    # Quasimodo reversal: the failed OB at the swept low
    if allow_qmr and sweep and j1 is not None:
        jj =_last_true(B.c < B.o, max(0, j1 - 3), j1)
        jq = jj if jj is not None else j1
        prox = B.o[jq] if jj is not None else max(B.o[j1], B.c[j1])
        prox = max(prox, B.l[j1])
        dist = B.l[j1]
        mitig = bool(b < t and B.l[b + 1:t + 1].min() <= prox)
        pois.append(POI("QUASIMODO_REVERSAL", jq, int(B.time[jq]), float(s * prox), float(s * dist), mitig))

    sl = p_low - P.sl_buffer_atr * atr_t
    min_stop = max(P.min_stop_atr * atr_t, P.min_stop_pips * B.pip)
    for q in pois:
        prox_m = q.proximal * s
        q.in_zone = bool(prox_m < eq)
        q.behind_liquidity = any(B.l[v.idx] > prox_m for v in valid_liqs) or not P.require_liquidity

    def poi_ok(q: POI) -> bool:
        return (not q.mitigated) and q.in_zone and q.behind_liquidity and (q.proximal * s - sl) >= min_stop

    good = [q for q in pois if poi_ok(q)]
    if good:
        obs = [q for q in good if q.poi_type == "ORDER_BLOCK"]
        if P.poi_preference == "order_block" and obs:
            best = obs[0]                                  # OBs outperformed QMRs in every backtest
        else:
            best = max(good, key=lambda q: q.proximal * s)   # closest valid POI (most likely fill)
    elif pois:
        best = max(pois, key=lambda q: (not q.mitigated) + q.in_zone + q.behind_liquidity)
    else:
        best = None

    cand = Candidate(
        direction="BUY" if s > 0 else "SELL",
        bar_time=int(B.time[t]),
        swing_high_time=int(B.time[i]), swing_high_price=float(s * B.h[i]),
        swept_level_time=int(B.time[j1]) if j1 is not None else None,
        swept_level_price=float(s * B.l[j1]) if j1 is not None else None,
        sweep=sweep, sweep_type=sweep_type,
        protected_time=int(B.time[p]), protected_price=float(s * p_low),
        bos_time=int(B.time[b]), bos_price=float(s * B.h[i]),
        range_extreme_time=int(B.time[rx]), range_extreme_price=float(s * r_hi),
        equilibrium=float(s * eq), liquidity=liqs, pois=pois, chosen_poi=best, atr=atr_t,
        bos_count=bos_count,
        last_bos_time=int(B.time[last_bos[1]]) if last_bos else int(B.time[b]),
        last_bos_price=float(s * B.h[last_bos[0]]) if last_bos else float(s * B.h[i]),
        bos_chain=[{"time": int(B.time[eb]), "price": float(s * B.h[ei]),
                    "low_time": int(B.time[ep]), "low_price": float(s * B.l[ep])}
                   for ei, eb, ep in (chain or [(i, b, p)])],
    )

    if best is not None:
        prox_m = best.proximal * s
        above = [v for v in valid_liqs if B.l[v.idx] > prox_m]
        cand.chosen_liquidity = min(above, key=lambda v: B.l[v.idx]) if above else (
            valid_liqs[-1] if valid_liqs else None)
        risk = prox_m - sl
        if risk > 0:
            cand.structure_rr = round(float((r_hi - prox_m) / risk), 2)
            if P.tp_mode == "structure":
                tp, cand.rr = r_hi, cand.structure_rr
            else:
                tp, cand.rr = prox_m + P.rr_target * risk, P.rr_target
            cand.entry, cand.stop_loss, cand.take_profit = float(s * prox_m), float(s * sl), float(s * tp)
    elif valid_liqs:
        cand.chosen_liquidity = valid_liqs[-1]

    # ---- truth table (spec section 6.3), evaluated in order
    deep = [q for q in liqs if q.fib_depth >= P.min_fib_depth]
    checks = {
        "sweep": sweep,
        "bos": True,
        "bos_count": bos_count >= P.min_bos,
        "structure_intact": True,
        "liquidity_depth": bool(deep),
        "liquidity_shape": bool(valid_liqs),
        "poi_found": best is not None,
        "poi_unmitigated": bool(best and not best.mitigated),
        "poi_in_zone": bool(best and best.in_zone),
        "poi_behind_liquidity": bool(best and best.behind_liquidity),
        "stop_valid": bool(best and cand.entry is not None and abs(cand.entry - cand.stop_loss) >= min_stop - 1e-12),
        "rr_valid": bool(cand.rr is not None and cand.rr >= P.rr_target - 1e-9),
    }
    if not P.require_liquidity:   # BOS chain acts as the confirmation instead (optional mode)
        checks["liquidity_depth"] = checks["liquidity_shape"] = True
    if prior_n is not None:
        checks["prior_trend"] = prior_n >= P.prior_trend_bos
    zone = "discount" if s > 0 else "premium"
    opp_word = "bearish" if s > 0 else "bullish"
    reasons = [
        ("sweep", "Lacks transactional sweep"),
        ("bos_count", f"Needs {P.min_bos} BOS before the POI (has {bos_count})"),
        ("prior_trend", f"Needs {P.prior_trend_bos} prior {opp_word} BOS into the POI (has {prior_n})"),
        ("liquidity_depth", f"No structural liquidity at >= 50% fib - lows are inducement traps"),
        ("liquidity_shape", "Structural liquidity geometry invalid (single candle / consolidation)"),
        ("poi_found", "No refined POI (order block / QMR) found"),
        ("poi_unmitigated", "POI already mitigated"),
        ("poi_in_zone", f"POI is not in {zone}"),
        ("poi_behind_liquidity", "POI sits in front of structural liquidity"),
        ("stop_valid", "Stop distance too small / invalid"),
        ("rr_valid", f"Risk-to-reward below 1:{P.rr_target:g}"),
    ]
    for key, msg in reasons:
        if not checks.get(key, True):
            cand.reject_reason = msg
            break
    cand.checks = checks
    return cand


def analyze(B: Bars, t: int, P: EngineParams, mirrored: Bars | None = None) -> list[Candidate]:
    """Both directions at cutoff t."""
    out = []
    M = mirrored if mirrored is not None else B.mirrored()
    bull = analyze_bullish(B, t, P, opp=M)
    if bull:
        out.append(bull)
    bear = analyze_bullish(M, t, P, opp=B)
    if bear:
        out.append(bear)
    return out


# --------------------------------------------------------------------------- HTF

@dataclass
class HTFContext:
    range_low: float
    range_high: float
    equilibrium: float
    source: str                       # STRUCTURE_BULL / STRUCTURE_BEAR / SWING_RANGE
    demand_zones: list[tuple[float, float, int]]   # (proximal, distal, time)
    supply_zones: list[tuple[float, float, int]]


def _htf_zones(B: Bars, t: int, P: EngineParams, window: int = 150) -> list[tuple[float, float, int]]:
    """Unmitigated demand OBs (on mirrored bars: supply) as of bar t, real prices."""
    s = B.sign
    zones = []
    lo = max(0, t - window)
    for i in np.flatnonzero(B.ph[lo:max(lo, t - P.swing_k + 1)]) + lo:
        i = int(i)
        b = _first_index(B.c[i + 1:t + 1] > B.h[i], i + 1)
        if b is None:
            continue
        p = i + 1 + int(np.argmin(B.l[i + 1:b + 1]))
        for j in range(min(b - 1, p + P.ob_search_fwd), max(0, p - P.ob_search_back) - 1, -1):
            if B.c[j] < B.o[j]:
                d_idx = _first_index(B.c[j + 1:b + 1] > B.h[j], j + 1)
                if d_idx is None:
                    continue
                prox, dist = B.o[j], min(B.l[j], B.l[p])
                if d_idx >= t or B.l[d_idx + 1:t + 1].min() > prox:
                    zones.append((float(s * prox), float(s * dist), int(B.time[j])))
                break
    return zones


def htf_context(B: Bars, M: Bars, t: int, P: EngineParams) -> HTFContext | None:
    """HTF dealing range + unmitigated POIs as of closed HTF bar t."""
    if t < 2 * P.swing_k + 2:
        return None
    best = None
    for bars, label in ((B, "STRUCTURE_BULL"), (M, "STRUCTURE_BEAR")):
        for i, b in find_bos(bars, t, P):
            p, _, _, _, intact = _structure(bars, t, P, i, b)
            if intact:
                rx = b + int(np.argmax(bars.h[b:t + 1]))
                ends = sorted([bars.sign * bars.l[p], bars.sign * bars.h[rx]])
                if best is None or b > best[0]:
                    best = (b, ends, label)
                break
    if best is not None:
        lo_, hi_ = best[1]
        source = best[2]
    else:
        lo = max(0, t - P.lookback)
        jh = _last_true(B.ph, lo, t - P.swing_k)
        jl = _last_true(B.pl, lo, t - P.swing_k)
        if jh is None or jl is None:
            return None
        lo_, hi_ = float(B.l[jl]), float(B.h[jh])
        source = "SWING_RANGE"
    if hi_ <= lo_:
        return None
    return HTFContext(float(lo_), float(hi_), float((lo_ + hi_) / 2), source,
                      _htf_zones(B, t, P), _htf_zones(M, t, P))


def htf_alignment(cand: Candidate, ctx: HTFContext | None, htf_atr: float, P: EngineParams) -> dict:
    """Spec 5.1: buys only from HTF discount / unmitigated demand; sells mirror."""
    if ctx is None:
        return {"aligned": False, "zone": "UNKNOWN", "detail": "insufficient HTF history"}
    price = cand.protected_price
    pct = (price - ctx.range_low) / (ctx.range_high - ctx.range_low)
    tol = P.htf_poi_tolerance_atr * htf_atr
    if cand.direction == "BUY":
        in_zone = price < ctx.equilibrium
        at_poi = any(d - tol <= price <= pr + tol for pr, d, _ in ctx.demand_zones)
        zone = "DISCOUNT" if in_zone else "PREMIUM"
        poi_label = "DEMAND_POI"
    else:
        in_zone = price > ctx.equilibrium
        at_poi = any(pr - tol <= price <= d + tol for pr, d, _ in ctx.supply_zones)
        zone = "PREMIUM" if in_zone else "DISCOUNT"
        poi_label = "SUPPLY_POI"
    aligned = in_zone or at_poi
    label = zone + (f"+{poi_label}" if at_poi else "")
    return {
        "aligned": bool(aligned), "zone": label, "position_pct": round(float(pct) * 100, 1),
        "range_low": ctx.range_low, "range_high": ctx.range_high,
        "equilibrium": ctx.equilibrium, "source": ctx.source, "at_poi": bool(at_poi),
    }


def apply_htf(cand: Candidate, align: dict) -> None:
    cand.checks["htf_aligned"] = align["aligned"]
    if cand.reject_reason is None and not align["aligned"]:
        cand.reject_reason = f"HTF alignment trap (wrong zone: {align['zone']})"
