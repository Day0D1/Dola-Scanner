"""Command line entry points.

    python -m bigsam.cli scan                 # one live scan now
    python -m bigsam.cli backtest --tf 15m --days 59 [--pairs EURUSD,GBPUSD]
    python -m bigsam.cli telegram-chatid      # discover your chat id after messaging the bot
    python -m bigsam.cli telegram-test
    python -m bigsam.cli set-password <username>
"""
from __future__ import annotations

import argparse
import getpass
import json
import logging

import httpx

from . import db


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="bigsam")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    bt = sub.add_parser("backtest")
    bt.add_argument("--tf", default="15m", choices=["5m", "15m", "1h"])
    bt.add_argument("--days", type=int, default=59)
    bt.add_argument("--pairs", default="")
    sub.add_parser("telegram-chatid")
    sub.add_parser("telegram-test")
    sp = sub.add_parser("set-password")
    sp.add_argument("username")
    args = ap.parse_args()

    db.init()
    from .config import settings

    if args.cmd == "scan":
        from .scanner import Scanner
        print(json.dumps(Scanner().run_once(), indent=2))
    elif args.cmd == "backtest":
        from . import backtest
        pairs = [p.strip().upper() for p in args.pairs.split(",") if p.strip()] or settings.pairs
        rid = backtest.create_run(pairs, args.tf, args.days)
        print(json.dumps(backtest.run(rid, pairs, args.tf, args.days), indent=2))
        print(f"run id {rid} - open the dashboard Backtests page for full analytics")
    elif args.cmd == "telegram-chatid":
        if not settings.telegram_token:
            raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env first")
        r = httpx.get(f"https://api.telegram.org/bot{settings.telegram_token}/getUpdates", timeout=20).json()
        chats = {(u.get("message") or {}).get("chat", {}).get("id"): (u.get("message") or {}).get("chat", {})
                 for u in r.get("result", []) if u.get("message")}
        if not chats:
            print("No messages yet. Open your bot in Telegram, press Start / send any message, then rerun.")
        for cid, chat in chats.items():
            print(f"chat id: {cid}   ({chat.get('first_name') or chat.get('title') or ''})")
    elif args.cmd == "telegram-test":
        from . import notifier
        ok = notifier.send("✅ BigSam Alerts is connected. You will receive setup alerts here.")
        print("sent" if ok else "failed - check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
    elif args.cmd == "set-password":
        from .auth import set_password
        pw = getpass.getpass("New password: ")
        set_password(args.username, pw)
        print("password updated")


if __name__ == "__main__":
    main()
