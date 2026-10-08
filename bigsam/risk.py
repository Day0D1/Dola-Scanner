"""Spec section 7: the 10-Trades Rule and position sizing."""
from __future__ import annotations

from .data import pip_size

AUDIT_LOSS_STREAK = 10


def risk_per_trade(account_size: float, max_dd_pct: float) -> float:
    """Risk Per Trade = Maximum Allowable Drawdown / 10 (fixed, never reduced mid-drawdown)."""
    return account_size * (max_dd_pct / 100.0) / 10.0


def quote_to_account_rate(pair: str, prices: dict[str, float], account_ccy: str = "USD") -> float | None:
    """Rate converting 1 unit of the pair's quote currency into the account currency."""
    quote = pair[3:]
    if quote == account_ccy:
        return 1.0
    if pair[:3] == account_ccy and pair in prices:
        return 1.0 / prices[pair]
    direct, inverse = f"{quote}{account_ccy}", f"{account_ccy}{quote}"
    if direct in prices:
        return prices[direct]
    if inverse in prices:
        return 1.0 / prices[inverse]
    return None


def account_lots(pair: str, entry: float, stop: float, risk_amount: float, prices: dict[str, float],
                 currency: str = "USD", step: float = 0.01) -> dict:
    """Broker-ready size for one account. Rounded DOWN to the lot step so risk is never exceeded;
    if even the minimum lot is too big, the minimum is used and the real risk is reported.

    Cent accounts (currency 'USC'): 1.00 cent-lot = 1,000 units and balances are in US cents, so
    the pip value of a cent-lot in cents equals that of a standard lot in dollars - the same
    formula gives cent-lots directly."""
    base_ccy = "USD" if currency.upper() == "USC" else currency.upper()
    pips = abs(entry - stop) / pip_size(pair)
    rate = quote_to_account_rate(pair, {**prices, pair: entry}, base_ccy)
    if not pips or rate is None:
        return {"stop_pips": round(pips, 1), "lots": None, "risk": None, "min_lot": False}
    pip_value = 100_000 * pip_size(pair) * rate          # per lot, in account units
    raw = risk_amount / (pips * pip_value)
    lots = int(raw / step + 1e-9) * step
    min_lot = lots < step
    if min_lot:
        lots = step
    return {"stop_pips": round(pips, 1), "lots": round(lots, 2), "risk": round(lots * pips * pip_value, 2),
            "min_lot": min_lot, "pip_value": round(pip_value, 4)}


def position_size(pair: str, entry: float, stop: float, risk_amount: float,
                  prices: dict[str, float], account_ccy: str = "USD") -> dict:
    pips = abs(entry - stop) / pip_size(pair)
    rate = quote_to_account_rate(pair, {**prices, pair: entry}, account_ccy)
    if not pips or rate is None:
        return {"stop_pips": round(pips, 1), "lots": None, "pip_value": None}
    pip_value_per_lot = 100_000 * pip_size(pair) * rate
    lots = risk_amount / (pips * pip_value_per_lot)
    return {"stop_pips": round(pips, 1), "lots": round(lots, 2), "pip_value": round(pip_value_per_lot, 4)}
