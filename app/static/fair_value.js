// Fair Value grid renderer

const T = window.FV_TICKER;
const TIMEFRAMES = [30, 60, 90, 180, 270, 252, 378, 504];
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
let currentDays = 90;
let cellScale = 1.0;              // pinch/ctrl-wheel zoom multiplier
const CELL_SCALE_MIN = 0.5;
const CELL_SCALE_MAX = 3.0;

// Measure the topbar height for the sticky-header offset so freeze panes stick
// flush against the topbar regardless of theme/font tweaks.
function updateTopbarHeightVar() {
  const tb = document.querySelector('.topbar');
  if (tb) document.documentElement.style.setProperty('--topbar-h', `${tb.offsetHeight}px`);
}
window.addEventListener('load', updateTopbarHeightVar);
window.addEventListener('resize', updateTopbarHeightVar);
updateTopbarHeightVar();

function fmtDate(iso) {
  const p = iso.split("-");
  if (p.length !== 3) return iso;
  return `${p[2]}-${MONTHS[parseInt(p[1], 10) - 1]}`;
}

// 1% log-scale grid: each level is exactly 1% above the previous, matching
// the percentage-based P&F box grid the app uses everywhere else. For
// TRADITIONAL-scale instruments (VIX, BPNYA — fixed 1-point boxes) the grid
// switches to linear steps of the box size so the row count stays sane and
// each row corresponds to exactly one P&F box.
const LOG_STEP = 1.01;
const LN_STEP = Math.log(LOG_STEP);

// pnfMeta is the {pnf_type, box, reversal} object from the /api/fair_value
// response. Persist it for use across helpers.
let currentPnfMeta = { pnf_type: "percentage", box: 1.0 };

function isTraditional() { return currentPnfMeta.pnf_type === "traditional"; }

function priceToBoxIdx(price) {
  if (isTraditional()) return Math.floor(price / currentPnfMeta.box);
  return Math.floor(Math.log(price) / LN_STEP);
}
function boxIdxToPrice(idx) {
  if (isTraditional()) return idx * currentPnfMeta.box;
  return Math.exp(idx * LN_STEP);
}
function priceDigitsFor(price) {
  if (price >= 500) return 2;
  if (price >= 100) return 2;
  if (price >= 10)  return 2;
  if (price >= 1)   return 3;
  return 4;
}

function cellWidth(n) {
  if (n <= 30) return 44;
  if (n <= 60) return 34;
  if (n <= 90) return 28;
  if (n <= 180) return 20;
  if (n <= 270) return 16;
  if (n <= 378) return 13;
  return 11;
}

function cellFontSize(n) {
  if (n <= 60) return "11px";
  if (n <= 180) return "10px";
  return "9px";
}

function nearestLogLevel(price) {
  if (price == null) return null;
  return boxIdxToPrice(priceToBoxIdx(price));
}
function eqLevel(a, b) {
  if (a == null || b == null) return false;
  if (isTraditional()) return Math.abs(a - b) < currentPnfMeta.box * 0.5;
  return Math.abs(a - b) / Math.max(a, b) < 1e-6;
}

async function load() {
  const dropdown = document.getElementById("fvDays");
  const nearest = TIMEFRAMES.indexOf(currentDays) >= 0
    ? String(currentDays)
    : String(TIMEFRAMES.reduce((a, b) => Math.abs(b - currentDays) < Math.abs(a - currentDays) ? b : a));
  if (dropdown.value !== nearest) dropdown.value = nearest;

  document.getElementById("fvContainer").innerHTML = '<div class="fv-loading">Loading...</div>';
  try {
    const r = await fetch(`/api/fair_value/${encodeURIComponent(T)}?days=${currentDays}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    render(data);
  } catch (e) {
    document.getElementById("fvContainer").innerHTML =
      `<div class="fv-loading">Error loading data: ${e.message}</div>`;
  }
}

function render(data) {
  const days = data.days;
  if (!days || !days.length) {
    document.getElementById("fvContainer").innerHTML =
      '<div class="fv-loading">No data.</div>';
    return;
  }
  // Adopt this instrument's P&F scale for the price grid (log-% for stocks/SPX,
  // linear 1-pt for VIX/BPNYA). Falls back to percentage if the API didn't
  // include pnf_meta (backwards-compat).
  currentPnfMeta = data.pnf_meta || { pnf_type: "percentage", box: 1.0 };

  let lo = Infinity, hi = -Infinity;
  for (const d of days) {
    for (const p of [d.high, d.low, d.close, d.bb_upper, d.bb_lower, d.bb_middle]) {
      if (p != null) { lo = Math.min(lo, p); hi = Math.max(hi, p); }
    }
  }
  // Build 1% log-scale price grid from just-below lo to just-above hi.
  const minIdx = priceToBoxIdx(lo) - 1;
  const maxIdx = priceToBoxIdx(hi) + 1;
  const levels = [];
  for (let i = maxIdx; i >= minIdx; i--) levels.push(boxIdxToPrice(i));

  const priceDigits = priceDigitsFor(lo);
  const rsiLo = data.settings?.rsi_oversold ?? 30;
  const rsiHi = data.settings?.rsi_overbought ?? 70;

  const headerRows = [
    ["DATE",    (d) => ({ text: fmtDate(d.date), cls: "" })],
    ["TREND",   (d) => ({ text: d.trend || "-", cls: d.trend === "B" ? "trend-b" : (d.trend === "S" ? "trend-s" : "") })],
    ["COLUMN",  (d) => ({ text: d.column || "-", cls: d.column === "X" ? "col-x" : (d.column === "O" ? "col-o" : "") })],
    ["CHANGE",  (d) => ({ text: d.change == null ? "-" : (d.change > 0 ? `+${d.change}` : `${d.change}`),
                          cls: d.change == null ? "" : (d.change > 0 ? "change-pos" : (d.change < 0 ? "change-neg" : "")) })],
    ["RSI",     (d) => ({ text: d.rsi != null ? d.rsi.toFixed(0) : "-",
                          cls: (d.rsi != null && d.rsi < rsiLo) ? "rsi-lo" : ((d.rsi != null && d.rsi > rsiHi) ? "rsi-hi" : "") })],
    ["BOL-H",   (d) => ({ text: d.bb_upper != null ? d.bb_upper.toFixed(priceDigits) : "-", cls: "bol-h" })],
    ["BOL-L",   (d) => ({ text: d.bb_lower != null ? d.bb_lower.toFixed(priceDigits) : "-", cls: "bol-l" })],
    ["BPNYA",   (d) => ({ text: d.bpnya_column || "-", cls: d.bpnya_column === "X" ? "col-x" : (d.bpnya_column === "O" ? "col-o" : "") })],
    ["VIX",     (d) => ({ text: d.vix_column || "-",   cls: d.vix_column === "X" ? "col-x"   : (d.vix_column === "O" ? "col-o" : "") })],
    ["RISK",    (d) => ({ text: d.risk || "-",         cls: d.risk === "LOW" ? "risk-cell low" : (d.risk === "MEDIUM" ? "risk-cell medium" : (d.risk === "HIGH" ? "risk-cell high" : "")) })],
  ];

  let html = '<div class="fv-scroll"><table class="fv-table"><thead>';
  for (const [label, getter] of headerRows) {
    html += `<tr><th class="fv-rowlabel">${label}</th>`;
    for (const d of days) {
      const { text, cls } = getter(d);
      html += `<td class="${cls}">${text}</td>`;
    }
    html += `</tr>`;
  }
  html += `</thead><tbody>`;

  for (const price of levels) {
    html += `<tr><th class="fv-pricelabel">${price.toFixed(priceDigits)}</th>`;
    for (const d of days) {
      const bbUpperLvl  = nearestLogLevel(d.bb_upper);
      const bbLowerLvl  = nearestLogLevel(d.bb_lower);
      const bbMiddleLvl = nearestLogLevel(d.bb_middle);
      const closeLvl    = nearestLogLevel(d.close);

      const isBBUpper  = eqLevel(price, bbUpperLvl);
      const isBBLower  = eqLevel(price, bbLowerLvl);
      const isBBMiddle = eqLevel(price, bbMiddleLvl);
      const isClose    = eqLevel(price, closeLvl);
      const inRange    = price >= d.low && price <= d.high;

      let cls = "", text = "";
      if (isClose && isBBUpper) {
        cls = "close-on-upper"; text = d.close.toFixed(priceDigits);
      } else if (isClose && isBBLower) {
        cls = "close-on-lower"; text = d.close.toFixed(priceDigits);
      } else if (isClose && isBBMiddle) {
        cls = "close-on-mid"; text = d.close.toFixed(priceDigits);
      } else if (isBBUpper) {
        cls = "bb-upper"; text = "H";
      } else if (isBBLower) {
        cls = "bb-lower"; text = "L";
      } else if (isBBMiddle) {
        cls = "bb-middle"; text = "F";
      } else if (isClose) {
        cls = "close"; text = d.close.toFixed(priceDigits);
      } else if (inRange) {
        cls = "range";
      }
      html += `<td class="${cls}">${text}</td>`;
    }
    html += `</tr>`;
  }

  html += `</tbody></table></div>`;

  const container = document.getElementById("fvContainer");
  container.innerHTML = html;
  applyCellSize(days.length);
}

function applyCellSize(numDays) {
  const container = document.getElementById("fvContainer");
  if (!container) return;
  const baseW = cellWidth(numDays);
  const baseF = parseFloat(cellFontSize(numDays));
  const w = Math.max(6, Math.round(baseW * cellScale));
  const f = Math.max(7, Math.round(baseF * cellScale));
  container.style.setProperty("--fv-cell-w", `${w}px`);
  container.style.setProperty("--fv-font-size", `${f}px`);
}

function stepZoom(delta) {
  // delta = +1 zoom out (more days), -1 zoom in (fewer days)
  const cur = TIMEFRAMES.indexOf(currentDays);
  let idx = cur;
  if (cur < 0) {
    idx = TIMEFRAMES.reduce((bestIdx, val, i) =>
      Math.abs(val - currentDays) < Math.abs(TIMEFRAMES[bestIdx] - currentDays) ? i : bestIdx, 0);
  }
  const next = Math.min(TIMEFRAMES.length - 1, Math.max(0, idx + delta));
  currentDays = TIMEFRAMES[next];
  updateExportHref();
  load();
}

function updateExportHref() {
  const btn = document.getElementById("fvExport");
  if (btn) btn.href = `/api/fair_value/${encodeURIComponent(T)}/export.xlsx?days=${currentDays}`;
}

document.getElementById("fvDays").addEventListener("change", (e) => {
  currentDays = parseInt(e.target.value, 10);
  updateExportHref();
  load();
});
document.getElementById("fvRefresh").addEventListener("click", load);
updateExportHref();
document.getElementById("fvZoomIn").addEventListener("click", () => stepZoom(-1));
document.getElementById("fvZoomOut").addEventListener("click", () => stepZoom(+1));
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  if (e.key === "+" || e.key === "=") stepZoom(-1);
  if (e.key === "-" || e.key === "_") stepZoom(+1);
  // Ctrl+0 resets the pinch/wheel cell-size zoom
  if ((e.ctrlKey || e.metaKey) && e.key === "0") {
    e.preventDefault();
    cellScale = 1.0;
    const n = document.querySelectorAll("#fvContainer .fv-table thead tr:first-child td").length || 90;
    applyCellSize(n);
  }
});

// Pinch to zoom (touch) + Ctrl+wheel (desktop) — both scale cell size, mirroring
// the Excel spreadsheet feel the user asked for. Container-scoped so it doesn't
// hijack the whole page.
(function attachPinchZoom() {
  const container = document.getElementById("fvContainer");
  if (!container) return;

  function currentDayCount() {
    return document.querySelectorAll("#fvContainer .fv-table thead tr:first-child td").length || 90;
  }
  function nudge(mult) {
    cellScale = Math.max(CELL_SCALE_MIN, Math.min(CELL_SCALE_MAX, cellScale * mult));
    applyCellSize(currentDayCount());
  }

  container.addEventListener("wheel", (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    nudge(e.deltaY < 0 ? 1.1 : 1 / 1.1);
  }, { passive: false });

  let pinchStartDist = 0;
  let pinchStartScale = 1;
  function dist(t1, t2) {
    const dx = t1.clientX - t2.clientX;
    const dy = t1.clientY - t2.clientY;
    return Math.hypot(dx, dy);
  }
  container.addEventListener("touchstart", (e) => {
    if (e.touches.length === 2) {
      pinchStartDist = dist(e.touches[0], e.touches[1]);
      pinchStartScale = cellScale;
    }
  }, { passive: true });
  container.addEventListener("touchmove", (e) => {
    if (e.touches.length === 2 && pinchStartDist > 0) {
      e.preventDefault();
      const d = dist(e.touches[0], e.touches[1]);
      const s = pinchStartScale * (d / pinchStartDist);
      cellScale = Math.max(CELL_SCALE_MIN, Math.min(CELL_SCALE_MAX, s));
      applyCellSize(currentDayCount());
    }
  }, { passive: false });
  container.addEventListener("touchend", () => { pinchStartDist = 0; });
})();

// Wire the ticker search input to navigate to another stock's Fair Value page.
let allTickers = [];
fetch("/api/tickers")
  .then(r => r.json())
  .then(data => {
    allTickers = data.tickers || [];
    if (window.attachTickerSearch) {
      window.attachTickerSearch(
        document.getElementById("fvSearchInput"),
        document.getElementById("fvSearchResults"),
        () => allTickers,
        (ticker) => { window.location.href = `/fair_value/${encodeURIComponent(ticker)}`; }
      );
    }
  })
  .catch(() => {});

load();
