"""Telegram alerts + a tiny command bot (/status /open /stats /help)."""
from __future__ import annotations

import html
import logging
import os
import threading
import time
from datetime import datetime, timezone

import httpx

from .config import _env, settings
from .data import price_decimals

log = logging.getLogger("bigsam.telegram")
API = "https://api.telegram.org/bot{token}/{method}"
PUBLIC_URL = _env("PUBLIC_URL", "").rstrip("/")


def enabled() -> bool:
    return bool(settings.telegram_token and settings.telegram_chat_id)


def send(text: str, chat_id: str | None = None) -> bool:
    if not settings.telegram_token:
        log.info("telegram disabled; message:\n%s", text)
        return False
    chat = chat_id or settings.telegram_chat_id
    if not chat:
        return False
    try:
        r = httpx.post(API.format(token=settings.telegram_token, method="sendMessage"),
                       json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                             "disable_web_page_preview": True}, timeout=15)
        if r.status_code != 200:
            log.warning("telegram error %s: %s", r.status_code, r.text[:200])
        return r.status_code == 200
    except httpx.HTTPError as e:
        log.warning("telegram send failed: %s", e)
        return False


def _fmt(pair: str, x) -> str:
    return "—" if x is None else f"{x:.{price_decimals(pair)}f}"


def _ts(ts: int | None) -> str:
    return "—" if not ts else datetime.fromtimestamp(ts, timezone.utc).strftime("%d %b %H:%M UTC")


def _link(setup_id: int) -> str:
    return f'\n<a href="{PUBLIC_URL}/#/setup/{setup_id}">Open in dashboard</a>' if PUBLIC_URL else ""


def setup_message(s: dict) -> str:
    pair, buy = s["pair"], s["direction"] == "BUY"
    d = s.get("details") or {}
    cand = d.get("candidate", {}) if isinstance(d, dict) else {}
    htf = d.get("htf", {}) if isinstance(d, dict) else {}
    liq = cand.get("chosen_liquidity") or {}
    icon = "🟢" if buy else "🔴"
    zone_word = "Discount" if buy else "Premium"
    poi = (s.get("poi_type") or "").replace("_", " ").title()
    size = f"{s['lots']:.2f} lots" if s.get("lots") else "n/a"
    risk = f"${s['risk_amount']:,.0f}" if s.get("risk_amount") else "—"
    lines = [
        f"{icon} <b>{s['direction']} SETUP — {pair}</b> ({s['timeframe']}) · <i>Risk entry</i>",
        "",
        f"<b>Entry (limit):</b> <code>{_fmt(pair, s['entry'])}</code>  · POI: {poi}",
        f"<b>Stop loss:</b> <code>{_fmt(pair, s['stop_loss'])}</code>  ({s.get('stop_pips') or '—'} pips)",
        f"<b>Take profit:</b> <code>{_fmt(pair, s['take_profit'])}</code>  (1:{s.get('rr') or 3:g})",
        *(_plan_lines(s) or [f"<b>Size:</b> {size} · risk {risk}"]),
        *([] if s.get("plans") else
          [f"<b>Partial:</b> take {settings.engine.partial_pct:g}% off at <code>{_fmt(pair, s['partial_price'])}</code> (+{settings.engine.partial_at_r:g}R), then move the stop to entry"] if s.get("partial_price") is not None else []),
        "",
        f"• Sweep ({(s.get('sweep_type') or '').lower()}) of {_fmt(pair, cand.get('swept_level_price'))} "
        f"→ BOS through {_fmt(pair, s.get('bos_price'))}"
        + (f" → inducement {_fmt(pair, (cand.get('bos_chain') or [{}])[-1].get('low_price'))}"
           f" → BOS through {_fmt(pair, cand.get('last_bos_price'))} · entry back at the origin POI"
           if (cand.get("bos_count") or 1) >= 2 else ""),
        f"• Protected {'low' if buy else 'high'}: {_fmt(pair, s.get('protected_price'))} · "
        f"EQ 50%: {_fmt(pair, s.get('equilibrium'))}",
        f"• Structural liquidity: {_fmt(pair, liq.get('price'))} · {liq.get('shape', '—').replace('_', '-')} · "
        f"{round((liq.get('fib_depth') or 0) * 100)}% deep · {(liq.get('pattern_type') or '').replace('_', ' ').title()}",
        f"• Entry in LTF {zone_word} ✔ · HTF ({s.get('htf')}): {s.get('htf_zone')} "
        f"({htf.get('position_pct', '—')}% of range)",
        f"• Structure target RR: {s.get('structure_rr') or '—'}",
        *([""] + [f"⚠️ <b>High-impact news</b> {_ts(e['ts'])}: {e['currency']} {html.escape(e['title'])} — "
                  "consider waiting until after the release" for e in s.get("news") or []]),
        "",
        "<i>Always attach the stop loss to the order. "
        f"Detected on bar {_ts(s.get('detected_bar_time'))}. Not financial advice — verify before trading.</i>",
    ]
    return "\n".join(lines) + _link(s["id"])


def _money(acc: dict, v: float | None, sign: bool = False) -> str:
    from .accounts import fmt_money
    return fmt_money(acc, v, sign)


def _plan_lines(s: dict) -> list[str]:
    """Per-account execution block for a setup alert."""
    out = []
    for pl in s.get("plans") or []:
        acc, pair = pl["account"], s["pair"]
        icon = "🏦" if acc["kind"] == "prop" else "💵"
        lot_word = "cent-lots" if acc["currency"].upper() == "USC" else "lots"
        out.append("")
        out.append(f"{icon} <b>{html.escape(acc['name'])}</b>")
        if pl["skip"]:
            out.append(f"   ⏭ {html.escape(pl['skip'])}")
            continue
        out.append(f"   Size <b>{pl['lots']:.2f} {lot_word}</b> · risk {_money(acc, pl['risk'])} "
                   f"({acc['risk_pct']:g}%{' of balance' if acc['compounding'] else ''})"
                   + (" ⚠️ min lot exceeds target risk" if pl["min_lot"] else ""))
        if pl["partial_price"] is not None:
            out.append(f"   Exit: close {acc['partial_pct']:g}% at <code>{_fmt(pair, pl['partial_price'])}</code> "
                       f"(+{acc['partial_at_r']:g}R), move SL to entry, rest to TP")
        else:
            out.append("   Exit: hold the full position to TP or SL")
        out.append(f"   Loss {_money(acc, -pl['risk'], True)} · full win {_money(acc, pl['reward'], True)}")
        if pl["cap_full"]:
            out.append(f"   ⛔ {acc['max_open']} positions already open: don't place yet (wait for 'slot free')")
    return out


def account_events_message(evs: list) -> str:
    """One lifecycle update for a setup, with a line per account."""
    _, t0, _, _ = evs[0]
    lines = [f"🔔 <b>{t0['pair']} {t0['direction']}</b> ({t0['timeframe']})"]
    for acc, t, kind, msg in evs:
        label = LIFECYCLE.get(kind, kind).replace("<b>", "").replace("</b>", "")
        res = ""
        if kind in ("WIN", "LOSS", "PARTIAL_WIN") and t.get("pnl") is not None:
            res = f" · {t.get('result_r') or 0:+g}R ({_money(acc, t['pnl'], True)})"
        lines.append(f"• <b>{html.escape(acc['name'])}</b>: {label}{res}")
        lines.append(f"   <i>{html.escape(msg)}</i>")
    return "\n".join(lines) + _link(t0["setup_id"])


def _orders(pending: list[dict]) -> str:
    return "\n".join(f"• {p['pair']} {p['direction']} ({p['timeframe']}) limit <code>{_fmt(p['pair'], p['entry'])}</code> · "
                     f"SL <code>{_fmt(p['pair'], p['stop_loss'])}</code> · TP <code>{_fmt(p['pair'], p['take_profit'])}</code>"
                     for p in pending)


def cap_full_message(n_open: int, cap: int, pending: list[dict], acc: dict | None = None) -> str:
    who = f" on {html.escape(acc['name'])}" if acc else ""
    body = (f"Cancel these pending orders now so a third position can't fill (1% floating-loss rule):\n{_orders(pending)}"
            if pending else "No other pending orders to cancel. Don't place new orders until a slot frees up.")
    return f"🛑 <b>{n_open}/{cap} positions open{who} — exposure cap reached</b>\n\n{body}"


def slot_free_message(n_open: int, cap: int, pending: list[dict], acc: dict | None = None) -> str:
    who = f" on {html.escape(acc['name'])}" if acc else ""
    return (f"✅ <b>Slot free{who} — {n_open}/{cap} positions open</b>\n\n"
            f"These setups are still valid; you may re-place them:\n{_orders(pending)}")


def news_message(s: dict, ev: dict, now: int) -> str:
    pair, mins = s["pair"], max(0, round((ev["ts"] - now) / 60))
    if s["status"] == "PENDING":
        action = (f"Your pending {s['direction']} limit at <code>{_fmt(pair, s['entry'])}</code> could fill in the "
                  "spike with slippage. <b>Cancel it now</b>; re-place after the release only if the setup still holds.")
    else:
        action = (f"Your open {s['direction']} trade's stop is at <code>{_fmt(pair, s['stop_loss'])}</code>. "
                  "Slippage can push the loss past the stop; keep exposure within the 1% floating-loss limit.")
    return (f"⚠️ <b>High-impact news in {mins} min — {ev['currency']}</b>\n"
            f"{html.escape(ev['title'])} at {_ts(ev['ts'])}\n\n{pair} ({s['timeframe']}): {action}"
            + _link(s["id"]))


LIFECYCLE = {
    "TRIGGERED": "✅ <b>Filled</b>",
    "PARTIAL": "💰 <b>Partial profit</b> — close part of the position and move the stop to entry",
    "WIN": "🏆 <b>Take profit hit</b>",
    "PARTIAL_WIN": "🟰 <b>Runner stopped at entry</b> (partial already banked)",
    "LOSS": "❌ <b>Stop loss hit</b>",
    "MISSED": "↗️ <b>Missed</b> (target reached before fill — cancel the limit)",
    "EXPIRED": "⌛ <b>Expired</b> (cancel the limit order)",
    "INVALIDATED": "🚫 <b>Invalidated</b>",
}


def lifecycle_message(s: dict, kind: str, msg: str) -> str:
    head = LIFECYCLE.get(kind, kind)
    r = s.get("result_r")
    final = ("WIN", "LOSS", "PARTIAL_WIN")
    tail = f" · {r:+g}R" if kind in final and r is not None else ""
    if kind in final and s.get("pnl") is not None:
        tail += f" (${s['pnl']:+,.0f})"
    return (f"{head} — {s['pair']} {s['direction']} ({s['timeframe']}){tail}\n{html.escape(msg)}"
            + _link(s["id"]))


def watch_message(pair: str, htf: str, ltf: str, direction: str, prox: float, dist: float) -> str:
    kind = "demand" if direction == "BUY" else "supply"
    lo, hi = sorted([prox, dist])
    return (f"👀 <b>Confirmation watch — {pair}</b>\n"
            f"Price tapped an unmitigated {htf} {kind} POI ({_fmt(pair, lo)} – {_fmt(pair, hi)}).\n"
            f"No limit order. Wait for a full {ltf} {direction.lower()} sequence: "
            f"sweep → BOS → structural liquidity → LTF POI.")


def audit_message(streak: int) -> str:
    return (f"🚨 <b>AUDIT TRIGGERED</b> — {streak} consecutive losses.\n"
            "New setup alerts are suspended (still logged as shadow setups). "
            "Review detection logic & data feed, then reset the audit from the dashboard Settings page.")


# ------------------------------------------------------------------ command bot

class CommandBot(threading.Thread):
    """Long-polls getUpdates and answers commands from the configured chat only."""

    def __init__(self, handler):
        super().__init__(daemon=True, name="telegram-bot")
        self.handler = handler
        self.offset = 0
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        url = API.format(token=settings.telegram_token, method="getUpdates")
        while not self._stop.is_set():
            try:
                r = httpx.get(url, params={"timeout": 50, "offset": self.offset}, timeout=60)
                for upd in r.json().get("result", []):
                    self.offset = upd["update_id"] + 1
                    m = upd.get("message") or {}
                    chat = str((m.get("chat") or {}).get("id", ""))
                    text = (m.get("text") or "").strip()
                    if chat != str(settings.telegram_chat_id) or not text.startswith("/"):
                        continue
                    reply = self.handler(text.split()[0].split("@")[0].lower())
                    if reply:
                        send(reply)
            except Exception as e:  # network hiccups must not kill the thread
                log.debug("bot poll error: %s", e)
                time.sleep(5)
