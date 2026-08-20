"""Configuration and strategy parameters (locked from spec v1.0)."""
import os

from dotenv import load_dotenv

load_dotenv()

POLYGON_API_KEY = os.environ["POLYGON_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

BB_PERIOD = 20
BB_STDDEV = 2.0

RSI_PERIOD = 10
# ELON activates when RSI < RSI_OVERSOLD (loosened from 30 to 39 to give the
# slower RSI(10) some breathing room). MUSK still uses 70 unless changed.
RSI_OVERSOLD = 39.0
RSI_OVERBOUGHT = 70.0

PNF_BOX_PCT = 1.0
PNF_REVERSAL = 3

# VIX uses TRADITIONAL scaling (fixed-point box) since its natural range is
# small and bounded — 1-point boxes are more intuitive than percentage.
VIX_PNF_BOX_SIZE = 1.0
VIX_PNF_REVERSAL = 2

# What signal side(s) to surface to the user (dashboard + Telegram):
#   "puts_only"  = only ELON candidates and SELL_PUTS entries
#   "calls_only" = only MUSK candidates and SELL_CALLS entries
#   "both"       = surface everything
# Detection logic still runs for all sides; muted sides simply don't reach the UI.
SIGNAL_MODE = os.getenv("SIGNAL_MODE", "puts_only").lower()

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

# Major Watchlist supplied by the user. These are priority stocks — merged into
# the scan universe on top of the top-30-per-GICS-sector list, tagged in the API
# response, promoted into their own dashboard section, and highlighted in Telegram.
MAJOR_WATCHLIST = [
    "AAPL", "AFL", "AIG", "AMAT", "AMD", "AMZN", "ANET", "APP", "ARM", "ASML",
    "AXP", "BA", "BBY", "BIDU", "BSX", "C", "CAH", "CARR", "CAT", "CDNS",
    "CF", "CHTR", "CI", "CMG", "CNC", "COHR", "COIN", "CRH", "CSCO", "CVS",
    "DDOG", "DECK", "DHR", "EBAY", "HRL", "HUM", "IBKR", "IBM", "INTC", "ISRG",
    "KHC", "KR", "LULU", "LUV", "LVS", "MAR", "MCHP", "META", "MGM", "MO",
    "NEE", "NFLX", "NOW", "NRG", "NVDA", "ON", "PCG", "PPG", "PSKY", "QCOM",
    "SHOP", "SLB", "SNDK", "SNPS", "SO", "STX", "TMUS", "TPR", "TSLA", "TSM",
    "TTD", "TTWO", "TXN", "UBER", "UNH", "UPS", "URI", "VRT", "VST", "WBD",
    "WDC", "WMB", "WMT",
]

# When true, scans use top 30 per GICS sector from S&P 500 (~300 stocks).
# When false, uses the 10-ticker MVP_UNIVERSE for fast iteration.
USE_FULL_UNIVERSE = os.getenv("USE_FULL_UNIVERSE", "true").lower() == "true"

# Concurrent workers for the parallel per-ticker Polygon fetch.
SCAN_WORKERS = int(os.getenv("SCAN_WORKERS", "16"))

LOOKBACK_DAYS = 400
