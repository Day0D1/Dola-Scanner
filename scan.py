"""Entry point: run one scan cycle."""
from __future__ import annotations

import sys
import traceback

from scanner import config, data
from scanner.breadth import read_breadth
from scanner.notify import send_scan_summary, send_telegram
from scanner.signals import evaluate_stock


def main() -> int:
    print("=== Options Scanner ===\n")

    print("Reading market breadth...")
    breadth = read_breadth()
    print(f"  Verdict: {breadth.verdict}")
    print(f"  SPX P&F: {breadth.spx_trend}   VIX P&F: {breadth.vix_trend}\n")

    signals = []
    for ticker in config.MVP_UNIVERSE:
        print(f"Scanning {ticker}...", end=" ", flush=True)
        try:
            ohlc = data.fetch_stock(ticker, config.LOOKBACK_DAYS)
            if ohlc.empty:
                print("no data")
                continue
            sig = evaluate_stock(ticker, ohlc)
            if not sig:
                print("insufficient history")
                continue
            signals.append(sig)
            if sig.entry_trigger:
                print(f"[ENTER {sig.entry_trigger}] RSI={sig.rsi:.1f} PnF={sig.pnf_column}")
            elif sig.candidate:
                print(f"[{sig.candidate} candidate] RSI={sig.rsi:.1f} PnF={sig.pnf_column}")
            else:
                pnf = sig.pnf_column or "?"
                print(f"quiet (RSI={sig.rsi:.1f} PnF={pnf})")
        except Exception as e:
            print(f"ERROR: {e}")

    print("\nSending Telegram summary...")
    try:
        send_scan_summary(breadth, signals)
        print("  sent.\n")
    except Exception as e:
        print(f"  FAILED: {e}\n")
        return 1

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        traceback.print_exc()
        try:
            send_telegram(f"[Scanner ERROR] {type(e).__name__}: {e}")
        except Exception:
            pass
        sys.exit(1)
