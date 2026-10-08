"""High-impact news calendar (ForexFactory weekly feed) for pre-news warnings."""
from __future__ import annotations

import logging
import time
from datetime import datetime

import httpx

from .config import _bool, _env

log = logging.getLogger("bigsam.news")
FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
ENABLED = _bool("NEWS_FILTER", True)
WARN_MINUTES = int(_env("NEWS_WARN_MINUTES", "30"))
_cache: dict = {"at": 0.0, "events": []}


def high_impact(refresh_s: int = 4 * 3600) -> list[dict]:
    """[{title, currency, ts}] for this week's high-impact events. Cached; failures keep the old list."""
    if time.time() - _cache["at"] < refresh_s and _cache["events"]:
        return _cache["events"]
    try:
        data = httpx.get(FEED, timeout=20).json()
        events = []
        for e in data:
            if e.get("impact") != "High":
                continue
            try:
                ts = int(datetime.fromisoformat(e["date"]).timestamp())
            except (KeyError, ValueError):
                continue
            events.append({"title": e.get("title", ""), "currency": e.get("country", "").upper(), "ts": ts})
        _cache.update(at=time.time(), events=events)
    except Exception as ex:   # feed is rate limited / occasionally down
        log.warning("news feed unavailable: %s", ex)
        _cache["at"] = time.time() - refresh_s + 900       # retry in 15 min
    return _cache["events"]


def upcoming_for(pair: str, now: int, minutes: int = WARN_MINUTES) -> list[dict]:
    """High-impact events for either currency of ``pair`` starting within the next ``minutes``."""
    if not ENABLED:
        return []
    ccys = {pair[:3], pair[3:]}
    return [e for e in high_impact() if e["currency"] in ccys and 0 <= e["ts"] - now <= minutes * 60]
