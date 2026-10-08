"""Runtime configuration, loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str) -> str:
    """BIGSAM_<NAME> wins over <NAME>, so BigSam can share a process/container with another app
    (e.g. mounted inside Dola Scanner) without clashing on TELEGRAM_*, SECRET_KEY, DATA_DIR..."""
    return os.getenv(f"BIGSAM_{name}", os.getenv(name, default)).strip()


def _bool(name: str, default: bool) -> bool:
    return _env(name, str(default)).lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str) -> list[str]:
    return [x.strip().upper() for x in _env(name, default).split(",") if x.strip()]


DEFAULT_PAIRS = (
    "EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD,USDCAD,NZDUSD,"
    "EURJPY,GBPJPY,EURGBP,AUDJPY,EURAUD,GBPAUD,EURCHF,CADJPY,GBPCHF"
)

# LTF -> HTF mapping (spec section 5: LTF 15m/1h, HTF 4h/Daily)
HTF_MAP = {"5m": "1h", "15m": "4h", "1h": "1d"}

TF_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


@dataclass
class EngineParams:
    """Quantitative knobs for the pattern engine. Defaults follow the spec."""

    swing_k: int = 3            # pivot strength for swing structure (bars each side)
    liq_k: int = 2              # pivot strength for liquidity points
    lookback: int = 220         # bars of history examined per analysis
    atr_period: int = 14
    min_fib_depth: float = 0.50  # structural liquidity must sit at/deeper than 50%
    shape_bars: int = 3         # candles inspected on each side of a V/A point
    shape_min_dir: int = 2      # directional candles required on each side
    shape_min_leg_atr: float = 0.8  # leg displacement (in ATR) to count as aggressive
    shape_max_single: float = 0.85  # a single candle may not make up more than this of a leg
    ob_search_back: int = 5     # bars before protected extreme searched for the OB
    ob_search_fwd: int = 3
    sl_buffer_atr: float = 0.10  # stop placed this far beyond protected extreme
    min_stop_atr: float = 0.30
    # stops tighter than this are dominated by spread - rejected
    min_stop_pips: float = field(default_factory=lambda: float(_env("MIN_STOP_PIPS", "5")))
    rr_target: float = field(default_factory=lambda: float(_env("RR_TARGET", "3")))  # spec: 1:3 minimum
    # fixed: TP = entry + rr_target * risk.  structure: TP = range extreme (BOS high/low),
    # setup rejected unless that gives at least rr_target.
    tp_mode: str = field(default_factory=lambda: _env("TP_MODE", "fixed").lower())
    # BOS chain: require this many breaks of structure (sweep -> BOS -> drop-down -> BOS ...)
    # before the POI is traded. 1 = original single-BOS model.
    min_bos: int = field(default_factory=lambda: int(_env("MIN_BOS", "2")))
    # which POI to trade once the chain is complete:
    #   origin = the original OB/QMR at the swept low (drop-down lows become liquidity)
    #   latest = the OB at the final drop-down low that launched the last BOS
    #   reversal = sweep into origin POI -> break 1 (wick ok) -> drop-down -> BOS -> back to origin POI
    chain_poi: str = field(default_factory=lambda: _env("CHAIN_POI", "reversal").lower())
    # reversal mode: opposite-direction BOS required leading into the POI (0 = off)
    prior_trend_bos: int = field(default_factory=lambda: int(_env("PRIOR_TREND_BOS", "0")))
    # spec §3: a POI needs validated structural liquidity in front of it. Set false to let the
    # BOS chain alone confirm the POI (useful with CHAIN_POI=latest).
    require_liquidity: bool = field(default_factory=lambda: _bool("REQUIRE_LIQUIDITY", True))
    # Partial profit: close PARTIAL_PCT % of the position at +PARTIAL_AT_R, move the stop to entry,
    # let the rest run to the full target. PARTIAL_AT_R=0 disables (hold everything to TP/SL).
    partial_at_r: float = field(default_factory=lambda: float(_env("PARTIAL_AT_R", "1.5")))
    partial_pct: float = field(default_factory=lambda: float(_env("PARTIAL_PCT", "50")))
    # when both an order block and a QMR qualify: order_block (prefer OB) | closest (nearest to price)
    poi_preference: str = field(default_factory=lambda: _env("POI_PREFERENCE", "order_block").lower())
    expiry_bars: int = 160      # pending limit orders expire after this many LTF bars
    # Optional research filters (off by default = spec-pure). See README "Validation results".
    exclude_liquidity_types: list = field(default_factory=lambda: _list("EXCLUDE_LIQUIDITY_TYPES", ""))
    exclude_sessions: list = field(default_factory=lambda: _list("EXCLUDE_SESSIONS", ""))
    poi_types: list = field(default_factory=lambda: _list("POI_TYPES", "ORDER_BLOCK,QUASIMODO_REVERSAL"))
    htf_poi_tolerance_atr: float = 0.25


@dataclass
class Settings:
    data_provider: str = field(default_factory=lambda: _env("DATA_PROVIDER", "yfinance"))
    oanda_token: str = field(default_factory=lambda: _env("OANDA_TOKEN", ""))
    oanda_env: str = field(default_factory=lambda: _env("OANDA_ENV", "practice"))
    pairs: list[str] = field(default_factory=lambda: _list("PAIRS", DEFAULT_PAIRS))
    ltf_timeframes: list[str] = field(default_factory=lambda: [t.lower() for t in _list("LTF_TIMEFRAMES", "15m,1h")])
    scan_interval_minutes: int = field(default_factory=lambda: int(_env("SCAN_INTERVAL_MINUTES", "15")))
    scan_delay_seconds: int = field(default_factory=lambda: int(_env("SCAN_DELAY_SECONDS", "75")))

    telegram_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID", ""))
    alert_watch: bool = field(default_factory=lambda: _bool("ALERT_WATCH", True))
    alert_lifecycle: bool = field(default_factory=lambda: _bool("ALERT_LIFECYCLE", True))

    account_size: float = field(default_factory=lambda: float(_env("ACCOUNT_SIZE", "100000")))
    max_drawdown_pct: float = field(default_factory=lambda: float(_env("MAX_DRAWDOWN_PCT", "10")))
    account_currency: str = field(default_factory=lambda: _env("ACCOUNT_CURRENCY", "USD").upper())

    admin_username: str = field(default_factory=lambda: _env("ADMIN_USERNAME", "admin"))
    admin_password: str = field(default_factory=lambda: _env("ADMIN_PASSWORD", ""))
    secret_key: str = field(default_factory=lambda: _env("SECRET_KEY", ""))
    host: str = field(default_factory=lambda: _env("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("PORT", "8000")))
    db_path: str = field(default_factory=lambda: _env(
        "DB_PATH", str(Path(_env("DATA_DIR", str(ROOT / "data"))) / "bigsam.db")))   # DATA_DIR = persistent disk
    run_scanner: bool = field(default_factory=lambda: _bool("RUN_SCANNER", True))

    engine: EngineParams = field(default_factory=EngineParams)


settings = Settings()
