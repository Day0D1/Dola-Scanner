// Fair Value grid renderer

const T = window.FV_TICKER;
const TIMEFRAMES = [30, 60, 90, 180, 270, 252, 378, 504];
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
let currentDays = 90;

function fmtDate(iso) {
  const p = iso.split("-");
  if (p.length !== 3) return iso;
  return `${p[2]}-${MONTHS[parseInt(p[1], 10) - 1]}`;
}

// 1% log-scale grid: each level is exactly 1% above the previous, matching
// the percentage-based P&F box grid the app uses everywhere else.
const LOG_STEP = 1.01;
const LN_STEP = Math.log(LOG_STEP);

function priceToBoxIdx(price) {
  return Math.floor(Math.log(price) / LN_STEP);
}
function boxIdxToPrice(idx) {
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
  container.style.setProperty("--fv-cell-w", `${cellWidth(days.length)}px`);
  container.style.setProperty("--fv-font-size", cellFontSize(days.length));
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
});

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
