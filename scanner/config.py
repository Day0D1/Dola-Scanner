"""Configuration and strategy parameters (locked from spec v1.0)."""
import os

from dotenv import load_dotenv

load_dotenv()

POLYGON_API_KEY = os.environ["POLYGON_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

BB_PERIOD = 10
BB_STDDEV = 2.0

RSI_PERIOD = 5
RSI_OVERSOLD = 30.0
RSI_OVERBOUGHT = 70.0

PNF_BOX_PCT = 1.0
PNF_REVERSAL = 2

SPX_YF_SYMBOL = "^GSPC"
VIX_YF_SYMBOL = "^VIX"

MVP_UNIVERSE = [
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "META",
    "NVDA",
    "TSLA",
    "JPM",
    "XOM",
    "UNH",
]

# When true, scans use top 30 per GICS sector from S&P 500 (~300 stocks).
# When false, uses the 10-ticker MVP_UNIVERSE for fast iteration.
USE_FULL_UNIVERSE = os.getenv("USE_FULL_UNIVERSE", "true").lower() == "true"

# Concurrent workers for the parallel per-ticker Polygon fetch.
SCAN_WORKERS = int(os.getenv("SCAN_WORKERS", "16"))

LOOKBACK_DAYS = 400
