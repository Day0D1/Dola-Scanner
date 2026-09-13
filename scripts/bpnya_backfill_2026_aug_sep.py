"""Backfill BPNYA OHLC for late-Aug through mid-Sep 2026.

Source: user-supplied StockCharts $BPNYA daily-price table (screenshots).
Reason for the fix:
  - The self-computed BPNYA scanner writes weekend rows and its scan values
    diverge from the true NYSE Bullish Percent Index. Rows to delete are
    Saturday/Sunday entries (no market) and scan-source rows that will be
    replaced with the authoritative OHLC.
  - The last authoritative import ended 2026-08-27, and the 2026-08-27 row
    itself carried a provisional low/close (56.117/56.246) that later settled
    to 56.052/56.052. Correcting it here.
  - Then appending the ten trading days from 2026-08-28 through 2026-09-11
    that the earlier PDF import missed.

Run: `python scripts/bpnya_backfill_2026_aug_sep.py`.
Idempotent: re-running upserts to the same values (source stays 'import').
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "state.db"

# Weekend / bad-scan rows to remove (BPNYA can't have data on non-trading days).
DELETE_DATES = [
    "2026-08-15",  # Sat
    "2026-08-16",  # Sun
    "2026-08-23",  # Sun
    "2026-09-12",  # Sat
    "2026-09-13",  # Sun
]

# Authoritative OHLC from StockCharts. Format: (date, open, high, low, close).
# Ordered chronologically so a hand-scan against the source is easier.
OHLC = [
    # Correction: 2026-08-27 provisional L/C → settled L/C
    ("2026-08-27", 56.570, 56.764, 56.052, 56.052),
    # Missing dates picked up from the screenshots
    ("2026-08-28", 56.015, 56.080, 55.239, 55.239),
    ("2026-08-31", 55.016, 55.016, 53.044, 53.109),
    ("2026-09-01", 53.044, 53.044, 51.329, 51.393),
    ("2026-09-02", 51.393, 51.523, 51.264, 51.393),
    ("2026-09-03", 51.492, 52.140, 51.492, 52.140),
    ("2026-09-04", 51.847, 51.847, 51.458, 51.782),
    # These three were 'scan' rows with bogus values; replace with real OHLC
    ("2026-09-08", 51.717, 51.717, 50.486, 50.681),
    ("2026-09-09", 50.032, 50.032, 48.931, 48.931),
    ("2026-09-10", 47.894, 48.218, 47.051, 47.051),
    ("2026-09-11", 47.307, 47.307, 46.398, 46.463),
]


def main() -> None:
    with sqlite3.connect(DB_PATH) as c:
        # 1) Drop weekend / bad-scan rows outright.
        for d in DELETE_DATES:
            c.execute(
                "DELETE FROM bpnya_history WHERE date=? AND source IN ('scan','manual')",
                (d,),
            )
        # 2) Upsert authoritative OHLC as source='import' so future scans
        #    can never overwrite it (upsert_bpnya's WHEN clause preserves
        #    import-source columns).
        now_iso = __import__("datetime").datetime.utcnow().isoformat()
        for d, o, h, l, cl in OHLC:
            c.execute(
                """
                INSERT INTO bpnya_history (date, pct, universe_size, updated_at, open, high, low, source)
                VALUES (?, ?, 0, ?, ?, ?, ?, 'import')
                ON CONFLICT(date) DO UPDATE SET
                    pct        = excluded.pct,
                    open       = excluded.open,
                    high       = excluded.high,
                    low        = excluded.low,
                    source     = 'import',
                    updated_at = excluded.updated_at
                """,
                (d, cl, now_iso, o, h, l),
            )
        c.commit()

    # Print a summary
    with sqlite3.connect(DB_PATH) as c:
        cutoff = "2026-08-01"
        rows = c.execute(
            "SELECT date, open, high, low, pct, source FROM bpnya_history "
            "WHERE date >= ? ORDER BY date ASC",
            (cutoff,),
        ).fetchall()
        print(f"BPNYA rows from {cutoff}:")
        for r in rows:
            print(f"  {r[0]}  O={r[1]}  H={r[2]}  L={r[3]}  C={r[4]}  ({r[5]})")


if __name__ == "__main__":
    main()
