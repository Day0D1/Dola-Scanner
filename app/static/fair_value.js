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

function pickStep(range) {
  if (range <= 20) return 0.5;
  if (range <= 40) return 1;
  if (range <= 100) return 2;
  if (range <= 200) return 5;
  if (range <= 500) return 10;
  return 20;
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

function nearestLevel(price, levelsSet, step) {
  if (price == null) return null;
  const rounded = Math.round(price / step) * step;
  const rerounded = Math.round(rounded * 100) / 100;
  return levelsSet.has(rerounded) ? rerounded : null;
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
  const step = pickStep(hi - lo);
  const gridMin = Math.floor(lo / step) * step - step;
  const gridMax = Math.ceil(hi / step) * step + step;
  const levels = [];
  for (let p = gridMax; p >= gridMin; p -= step) levels.push(Math.round(p * 100) / 100);
  const levelSet = new Set(levels);

  const priceDigits = step >= 1 ? 0 : 2;
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
      const bbUpperLvl  = nearestLevel(d.bb_upper, levelSet, step);
      const bbLowerLvl  = nearestLevel(d.bb_lower, levelSet, step);
      const bbMiddleLvl = nearestLevel(d.bb_middle, levelSet, step);
      const closeLvl    = nearestLevel(d.close, levelSet, step);

      const isBBUpper  = price === bbUpperLvl;
      const isBBLower  = price === bbLowerLvl;
      const isBBMiddle = price === bbMiddleLvl;
      const isClose    = price === closeLvl;
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
    // pick nearest in TIMEFRAMES
    idx = TIMEFRAMES.reduce((bestIdx, val, i) =>
      Math.abs(val - currentDays) < Math.abs(TIMEFRAMES[bestIdx] - currentDays) ? i : bestIdx, 0);
  }
  const next = Math.min(TIMEFRAMES.length - 1, Math.max(0, idx + delta));
  currentDays = TIMEFRAMES[next];
  load();
}

document.getElementById("fvDays").addEventListener("change", (e) => {
  currentDays = parseInt(e.target.value, 10);
  load();
});
document.getElementById("fvRefresh").addEventListener("click", load);
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
