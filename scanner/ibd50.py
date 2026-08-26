"""IBD 50 watchlist fetcher.

Source: https://www.capforceetf.com/ffty/details  (CapForce IBD 50 ETF, official
issuer page). The page embeds a JSON array of holdings inside a <script> block;
we bracket-match the array and json.loads it. Then we drop non-equity rows
(cash, money-market funds like FXFXX, and the synthetic "CASH&OTHER" row).

Why this source: the CapForce IBD 50 ETF (ticker FFTY) is rebalanced weekly to
track the IBD 50 Index directly. Its holdings ARE the IBD 50 by definition, with
one or two extra rows for cash management. No login, no anti-bot, updated daily.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from typing import List, Optional

import requests


IBD50_SOURCE_URL = "https://www.capforceetf.com/ffty/details"

# Tickers to always exclude even if the page includes them. FXFXX is First
# American Treasury Obligations Fund (a money-market fund the ETF uses for cash
# management, not a tradable equity). CASH&OTHER is a synthetic row.
_EXCLUDE_TICKERS = {"FXFXX"}

# A valid US-listed ticker is 1-5 uppercase letters. This also filters out
# synthetic rows like "CASH&OTHER".
_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")


@dataclass
class IBD50Snapshot:
    tickers: List[str]
    as_of_date: Optional[str]  # ISO date string (YYYY-MM-DD)
    fetched_at: str            # ISO datetime UTC
    source_url: str
    raw_count: int             # holdings on the page (before filtering)


def fetch_ibd50() -> IBD50Snapshot:
    """Fetch and parse the current IBD 50 constituent list.

    Raises RuntimeError if the source page changes shape and we can no longer
    find the holdings JSON — better to fail loudly than silently return stale
    data.
    """
    r = requests.get(
        IBD50_SOURCE_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; DolaScanner/1.0)"},
        timeout=30,
    )
    r.raise_for_status()
    html = r.text

    # The holdings array is embedded inline. Anchor on the first "holding_name"
    # key, then bracket-match backward for '[' and forward for its matching ']'.
    anchor = html.find('"holding_name"')
    if anchor < 0:
        raise RuntimeError("IBD 50 source page shape changed: no holding_name key found")

    start = html.rfind("[", 0, anchor)
    if start < 0:
        raise RuntimeError("IBD 50 source page shape changed: no '[' before holdings")

    depth = 0
    end = -1
    for i in range(start, len(html)):
        c = html[i]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        raise RuntimeError("IBD 50 source page shape changed: unbalanced brackets")

    try:
        raw = json.loads(html[start : end + 1])
    except json.JSONDecodeError as e:
        raise RuntimeError(f"IBD 50 holdings JSON failed to parse: {e}") from e

    if not isinstance(raw, list) or not raw:
        raise RuntimeError("IBD 50 holdings JSON empty or not a list")

    tickers: List[str] = []
    for h in raw:
        t = str(h.get("holding_ticker") or "").strip().upper()
        if not t or t in _EXCLUDE_TICKERS:
            continue
        if not _TICKER_RE.match(t):
            continue
        tickers.append(t)

    if not tickers:
        raise RuntimeError("IBD 50 fetch returned zero tickers after filtering")

    # Best-effort as_of date (unix ms) from any holding. All rows share the same
    # rebalance date, so just take the first non-empty value we find.
    as_of_iso: Optional[str] = None
    for h in raw:
        ms = h.get("as_of_date")
        if isinstance(ms, (int, float)) and ms > 0:
            as_of_iso = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).date().isoformat()
            break

    return IBD50Snapshot(
        tickers=sorted(set(tickers)),
        as_of_date=as_of_iso,
        fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        source_url=IBD50_SOURCE_URL,
        raw_count=len(raw),
    )
