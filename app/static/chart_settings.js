// Dola — universal chart settings (shared between legacy dashboard and v2 shell).
//
// User-editable defaults for Candlestick + P&F rendering: periods, thresholds,
// and colors for every drawn indicator. Persisted per-browser in localStorage.
//
// IMPORTANT: overrides are stock-only. Market breadth indicators (SPX, VIX,
// BPNYA) intentionally ignore user settings — their configs are locked so the
// breadth gate keeps computing the same way regardless of chart preferences.
// Query settingsForTicker(ticker) instead of getChartSettings() from render
// callers, and it returns null for indices so you skip the override.

const KEY = "dola_v2_chart_settings";

const INDEX_KEYS = new Set(["SPX", "VIX", "BPNYA"]);
export function isMarketIndex(ticker) {
  return INDEX_KEYS.has((ticker || "").toUpperCase());
}

export const DEFAULTS = Object.freeze({
  candlestick: {
    bb_period: 20,
    bb_stddev: 2,
    rsi_period: 10,
    rsi_oversold: 39,
    rsi_overbought: 70,
    colors: {
      candle_up: "#22c55e",
      candle_down: "#ef4444",
      bb_upper: "#5b9bff",
      bb_middle: "#6a7690",
      bb_lower: "#5b9bff",
      bb_fill: "rgba(91, 155, 255, 0.06)",
      rsi_line: "#5b9bff",
      rsi_oversold_line: "#22c55e",
      rsi_overbought_line: "#ef4444",
      event_entry: "#f59e0b",
    },
  },
  pnf: {
    box: 2.0,           // percent (stocks / SPX)
    reversal: 2,
    colors: {
      x: "#22c55e",
      o: "#ef4444",
      current_column: "#f59e0b",
      grid: "rgba(255, 255, 255, 0.05)",
      price_label: "#6a7690",
    },
  },
});

function deepClone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

function deepMerge(base, patch) {
  const out = { ...base };
  for (const k of Object.keys(patch || {})) {
    const v = patch[k];
    if (v && typeof v === "object" && !Array.isArray(v) && base[k] && typeof base[k] === "object") {
      out[k] = deepMerge(base[k], v);
    } else if (v !== undefined) {
      out[k] = v;
    }
  }
  return out;
}

export function getChartSettings() {
  const defs = deepClone(DEFAULTS);
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return defs;
    return deepMerge(defs, JSON.parse(raw));
  } catch {
    return defs;
  }
}

export function saveChartSettings(patch) {
  const current = getChartSettings();
  const next = deepMerge(current, patch);
  try { localStorage.setItem(KEY, JSON.stringify(next)); } catch {}
  window.dispatchEvent(new CustomEvent("dola:chartsettings", { detail: next }));
  return next;
}

export function resetChartSettings() {
  try { localStorage.removeItem(KEY); } catch {}
  const defs = deepClone(DEFAULTS);
  window.dispatchEvent(new CustomEvent("dola:chartsettings", { detail: defs }));
  return defs;
}

/** Returns the user's settings for a given ticker, or null for market indices. */
export function settingsForTicker(ticker) {
  if (isMarketIndex(ticker)) return null;
  return getChartSettings();
}

/** Query-string builder for /api/stock/{ticker}. Falls back to defaults. */
export function apiQueryFor(ticker, extra = {}) {
  const s = settingsForTicker(ticker);
  const params = { ...extra };
  if (s) {
    params.bb_period    = s.candlestick.bb_period;
    params.bb_stddev    = s.candlestick.bb_stddev;
    params.rsi_period   = s.candlestick.rsi_period;
    params.pnf_box      = s.pnf.box;
    params.pnf_reversal = s.pnf.reversal;
  }
  return Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join("&");
}
