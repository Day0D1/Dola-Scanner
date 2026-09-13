// Dola — Time Series color settings.
//
// User-editable colors for every themed cell in the Time Series grid:
// H/L/F markers, close cells (standalone, on-upper, on-lower, on-mid),
// intraday range, BOL-H / BOL-L header rows, RSI extremes. Persisted per
// browser in localStorage; applied by writing CSS custom properties on the
// .fv-container element. Fires "dola:tssettings" on save so the current
// grid can re-theme without a full reload.
//
// This is per-browser, not per-user or per-ticker — the settings apply to
// EVERY time series chart (legacy fair_value.html and v2 time_series_detail.html).

const KEY = "dola_ts_settings";

export const DEFAULTS = Object.freeze({
  hbb_bg: "#22c55e",            // BB upper marker (H) — green per user
  hbb_fg: "#ffffff",
  lbb_bg: "#ef4444",            // BB lower marker (L) — red per user
  lbb_fg: "#ffffff",
  fbb_bg: "#fbbf24",            // BB middle marker (F) — unchanged amber
  fbb_fg: "#111827",

  bol_h_bg: "rgba(34, 197, 94, 0.7)",   // BOL-H header row — green
  bol_l_bg: "rgba(239, 68, 68, 0.7)",   // BOL-L header row — red

  close_bg: "#ffffff",
  close_fg: "#0b0d12",
  close_on_upper_bg: "#ef4444",         // close at/above UBB — red per user
  close_on_upper_fg: "#ffffff",
  close_on_lower_bg: "#00e676",         // close at/below LBB — greenest green per user
  close_on_lower_fg: "#0b0d12",
  close_on_mid_bg: "#22d3ee",
  close_on_mid_fg: "#0b0d12",

  range_bg: "rgba(59, 130, 246, 0.3)",
  rsi_lo_bg: "rgba(34, 197, 94, 0.28)",
  rsi_hi_bg: "rgba(239, 68, 68, 0.28)",
});

// Map from settings key → CSS custom property name on .fv-container.
const CSS_MAP = {
  hbb_bg: "--ts-hbb-bg",           hbb_fg: "--ts-hbb-fg",
  lbb_bg: "--ts-lbb-bg",           lbb_fg: "--ts-lbb-fg",
  fbb_bg: "--ts-fbb-bg",           fbb_fg: "--ts-fbb-fg",
  bol_h_bg: "--ts-bol-h-bg",       bol_l_bg: "--ts-bol-l-bg",
  close_bg: "--ts-close-bg",       close_fg: "--ts-close-fg",
  close_on_upper_bg: "--ts-close-on-upper-bg",
  close_on_upper_fg: "--ts-close-on-upper-fg",
  close_on_lower_bg: "--ts-close-on-lower-bg",
  close_on_lower_fg: "--ts-close-on-lower-fg",
  close_on_mid_bg: "--ts-close-on-mid-bg",
  close_on_mid_fg: "--ts-close-on-mid-fg",
  range_bg: "--ts-range-bg",
  rsi_lo_bg: "--ts-rsi-lo-bg",
  rsi_hi_bg: "--ts-rsi-hi-bg",
};

function clone(obj) { return JSON.parse(JSON.stringify(obj)); }

export function getTsSettings() {
  const defs = clone(DEFAULTS);
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return defs;
    return { ...defs, ...JSON.parse(raw) };
  } catch { return defs; }
}

export function saveTsSettings(patch) {
  const next = { ...getTsSettings(), ...patch };
  try { localStorage.setItem(KEY, JSON.stringify(next)); } catch {}
  applyTsSettings();
  window.dispatchEvent(new CustomEvent("dola:tssettings", { detail: next }));
  return next;
}

export function resetTsSettings() {
  try { localStorage.removeItem(KEY); } catch {}
  applyTsSettings();
  window.dispatchEvent(new CustomEvent("dola:tssettings", { detail: clone(DEFAULTS) }));
  return clone(DEFAULTS);
}

/** Write current settings as CSS custom properties on every .fv-container. */
export function applyTsSettings() {
  const s = getTsSettings();
  const nodes = document.querySelectorAll(".fv-container");
  nodes.forEach((el) => {
    for (const [k, cssVar] of Object.entries(CSS_MAP)) {
      if (s[k] != null) el.style.setProperty(cssVar, s[k]);
    }
  });
}

// Apply as soon as the DOM is ready — the grid renders shortly after and
// picks up the vars from the .fv-container element.
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", applyTsSettings, { once: true });
} else {
  applyTsSettings();
}

// Bridge for non-module callers (fair_value.js is a plain script).
window.DolaTsSettings = { getTsSettings, saveTsSettings, resetTsSettings, applyTsSettings, DEFAULTS };
