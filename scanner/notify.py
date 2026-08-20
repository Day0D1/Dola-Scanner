"""Telegram alert delivery."""
from __future__ import annotations

import datetime as dt
from typing import List, Optional, Set
from zoneinfo import ZoneInfo

import requests

from scanner import config
from scanner.breadth import BreadthReading
from scanner.signals import StockSignal  # noqa: F401


TELEGRAM_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
ET = ZoneInfo("America/New_York")


def send_telegram(text: str, silent: bool = False) -> dict:
    r = requests.post(
        f"{TELEGRAM_API}/sendMessage",
        data={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": str(silent).lower(),
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def format_scan_summary(
    breadth: BreadthReading,
    signals: List[StockSignal],
    fresh_entries: Optional[Set[str]] = None,
    fresh_candidates: Optional[Set[str]] = None,
) -> str:
    fresh_entries = fresh_entries or set()
    fresh_candidates = fresh_candidates or set()

    entries = [s for s in signals if s.entry_trigger]
    candidates = [s for s in signals if s.candidate and not s.entry_trigger]
    quiet = [s for s in signals if not s.candidate and not s.entry_trigger]

    now = dt.datetime.now(ET).strftime("%I:%M %p ET  %a %b %d")

    regime = breadth.regime or "?"
    risk = breadth.risk or "?"
    spx = f"SPX={breadth.spx.column or '?'}"
    if breadth.spx.change is not None:
        spx += f"({breadth.spx.change} boxes)"
    vix = f"VIX={breadth.vix.column or '?'}"
    if breadth.vix.level is not None:
        vix += f" @ {breadth.vix.level:.2f}"
        if breadth.vix.change is not None:
            vix += f" ({'+' if breadth.vix.change > 0 else ''}{breadth.vix.change})"
    bpnya = f"BPNYA={breadth.bpnya.column or '?'}"
    if breadth.bpnya.level is not None:
        bpnya += f" @ {breadth.bpnya.level:.1f}%"
        if breadth.bpnya.change is not None:
            bpnya += f" ({'+' if breadth.bpnya.change > 0 else ''}{breadth.bpnya.change})"

    lines = [
        f"<b>Options Scanner</b>  <i>{now}</i>",
        f"Regime: <b>{regime}</b>   Risk: <b>{risk}</b>",
        f"  {spx}",
        f"  {bpnya}",
        f"  {vix}",
        f"Universe: {len(signals)} stocks",
        "",
    ]

    watch = set(getattr(config, "MAJOR_WATCHLIST", []) or [])
    star = lambda t: "★ " if t in watch else ""

    if entries:
        lines.append(f"<b>&gt;&gt;&gt; ENTER NOW ({len(entries)}) &lt;&lt;&lt;</b>")
        for s in entries:
            arrow = "sell puts" if s.entry_trigger == "SELL_PUTS" else "sell calls"
            tag = "  <b>[NEW]</b>" if s.ticker in fresh_entries else "  <i>(already alerted)</i>"
            lines.append(f"  {star(s.ticker)}<b>{s.ticker}</b>  ${s.last_close:.2f}  --&gt;  <b>{arrow}</b>{tag}")
            lines.append(
                f"    RSI(5) {s.rsi:.1f}  |  P&amp;F {s.pnf_column}  |  "
                f"BB[{s.bb_lower:.2f} / {s.bb_middle:.2f} / {s.bb_upper:.2f}]"
            )
        lines.append("")

    if candidates:
        lines.append(f"<b>Active candidates ({len(candidates)})</b>")
        for s in candidates:
            need = "P&amp;F flip to X" if s.candidate == "ELON" else "P&amp;F flip to O"
            tag = "  <b>[NEW]</b>" if s.ticker in fresh_candidates else ""
            lines.append(
                f"  <b>{s.candidate}</b>  {star(s.ticker)}{s.ticker}  ${s.last_close:.2f}  "
                f"(RSI {s.rsi:.1f}, P&amp;F {s.pnf_column or '?'}, waiting on {need}){tag}"
            )
        lines.append("")
    lines.append("<i>★ = Major Watchlist ticker</i>" if watch else "")

    if not entries and not candidates:
        lines.append(f"<i>All quiet. {len(quiet)} stocks scanned, no candidates or entries.</i>")

    return "\n".join(lines)


def send_scan_summary(
    breadth: BreadthReading,
    signals: List[StockSignal],
    fresh_entries: Optional[Set[str]] = None,
    fresh_candidates: Optional[Set[str]] = None,
) -> None:
    """
    Silent if nothing fresh (or nothing at all). Sound only when at least one
    entry or candidate is being announced for the first time.
    """
    fresh_entries = fresh_entries or set()
    fresh_candidates = fresh_candidates or set()
    has_fresh = bool(fresh_entries or fresh_candidates)
    send_telegram(
        format_scan_summary(breadth, signals, fresh_entries, fresh_candidates),
        silent=not has_fresh,
    )
