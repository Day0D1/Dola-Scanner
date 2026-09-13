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
  // MM/DD/YY — matches the trader's mental model and how StockCharts displays.
  const p = iso.split("-");
  if (p.length !== 3) return iso;
  return `${p[1]}/${p[2]}/${p[0].slice(2)}`;
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
// Direction-aware snap. When the metric is FALLING, snap to the grid line
// ABOVE the raw price (the trader's mental model: "price hasn't arrived at the
// lower level yet, it's still coming from above"). When RISING, snap to the
// grid line BELOW ("hasn't arrived at the higher level yet").
//
// Direction is taken from prev when available; if prev is missing or exactly
// equal to curr (first day of the window, or a flat day) we PEEK FORWARD at
// next — that catches the leftmost-day and quiet-day edge cases the prior
// implementation was defaulting to floor for. Only when both neighbors are
// missing/equal do we fall back to floor.
function directionalLevel(prev, curr, next) {
  if (curr == null) return null;
  const floorIdx = priceToBoxIdx(curr);
  let dir = 0;  // -1 = falling → ceiling; +1 = rising → floor; 0 = flat → floor
  if (prev != null && prev !== curr) {
    dir = curr < prev ? -1 : +1;
  } else if (next != null && next !== curr) {
    // next above us → we're rising toward it; next below us → we're falling toward it
    dir = next < curr ? -1 : +1;
  }
  return dir === -1 ? boxIdxToPrice(floorIdx + 1) : boxIdxToPrice(floorIdx);
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

// Freeze-row cascade animation. As the scroll container scrolls down past its
// first screen of price rows, progressively shrink the sticky header rows so
// they collapse into each other and reveal more of the price grid below. At
// the top of the scroll they're full-size (22px each); past the collapse
// distance they compress to ~10px, letting the user see the "full picture
// from up to down" the way they asked. Restored on scroll back to the top.
const FV_ROW_H_MAX = 22;
const FV_ROW_H_MIN = 10;
const FV_COLLAPSE_DISTANCE = 500;   // px of scroll to fully collapse the header
function updateFvCascade(scrollTop) {
  const container = document.getElementById("fvContainer");
  if (!container) return;
  const t = Math.max(0, Math.min(1, scrollTop / FV_COLLAPSE_DISTANCE));
  // Easing: slower at the start, faster past halfway, so the header hangs on
  // while the user is doing small corrections and only truly collapses on
  // longer scrolls. Cubic ease-in.
  const eased = t * t * (3 - 2 * t);   // smoothstep
  const rowH = FV_ROW_H_MAX - (FV_ROW_H_MAX - FV_ROW_H_MIN) * eased;
  container.style.setProperty("--fv-row-h", `${rowH.toFixed(2)}px`);
  // Also progressively fade the header cells so the collapsed strip stays
  // legible-but-quiet — keeps focus on the price rows.
  const opacity = 1 - 0.35 * eased;
  const table = container.querySelector(".fv-table thead");
  if (table) table.style.opacity = opacity.toFixed(3);
}
// Time Series crosshair overlay. Two dashed lines follow the pointer across
// the grid, plus a floating tag that shows the row's price. Position updates
// on requestAnimationFrame so tracking stays smooth even when scrolled deep.
function attachFvCrosshair() {
  const scroll = document.querySelector("#fvContainer .fv-scroll");
  const table = document.querySelector("#fvContainer .fv-table");
  if (!scroll || !table || scroll.dataset.fvXhairWired === "1") return;
  scroll.dataset.fvXhairWired = "1";

  // Overlay elements — created once, kept alive across re-renders by re-parenting
  // if the .fv-scroll was replaced (each render() rebuilds it, so we re-inject).
  const vLine = document.createElement("div");
  vLine.className = "fv-xhair-v";
  const hLine = document.createElement("div");
  hLine.className = "fv-xhair-h";
  const tag = document.createElement("div");
  tag.className = "fv-xhair-tag";
  scroll.appendChild(vLine);
  scroll.appendChild(hLine);
  scroll.appendChild(tag);

  let pending = false;
  let lastX = 0, lastY = 0, lastPrice = "";

  function findPriceForRow(clientY) {
    // Reverse-map the cursor's Y coord to the tbody row it's over, then read
    // its <th class="fv-pricelabel">. Falls back to null if we're over the
    // header rows or below all rows.
    const tbodyRows = table.querySelectorAll("tbody tr");
    for (const row of tbodyRows) {
      const rect = row.getBoundingClientRect();
      if (clientY >= rect.top && clientY <= rect.bottom) {
        const label = row.querySelector(".fv-pricelabel");
        return label ? label.textContent.trim() : null;
      }
    }
    return null;
  }

  function apply() {
    pending = false;
    const scrollRect = scroll.getBoundingClientRect();
    // vLine.left = mouse X relative to scroll container's padding box
    const x = lastX - scrollRect.left + scroll.scrollLeft;
    const y = lastY - scrollRect.top  + scroll.scrollTop;
    vLine.style.left = `${x}px`;
    hLine.style.top  = `${y}px`;
    tag.style.left   = `${x + 10}px`;
    tag.style.top    = `${y - 22}px`;
    if (lastPrice != null) {
      tag.textContent = lastPrice;
      tag.style.display = lastPrice ? "" : "none";
    }
  }

  scroll.addEventListener("mousemove", (e) => {
    lastX = e.clientX;
    lastY = e.clientY;
    lastPrice = findPriceForRow(e.clientY);
    scroll.classList.add("xhair-on");
    if (!pending) { pending = true; requestAnimationFrame(apply); }
  });
  scroll.addEventListener("mouseleave", () => {
    scroll.classList.remove("xhair-on");
  });
}

function attachFvCascade() {
  const scroll = document.querySelector("#fvContainer .fv-scroll");
  if (!scroll || scroll.dataset.fvCascadeWired === "1") return;
  scroll.dataset.fvCascadeWired = "1";

  // Direct synchronous update on scroll — the computation is O(1) and the
  // browser already throttles scroll events. Skipping rAF here avoids the
  // "listener never fired" corner cases we saw in the embedded preview.
  scroll.addEventListener("scroll", () => updateFvCascade(scroll.scrollTop), { passive: true });

  // Safety net: while the container is mounted, keep the cascade in sync via
  // a lightweight rAF loop that re-reads scrollTop. Handles the cases where
  // the scroll event doesn't fire (programmatic scrolling from another tool,
  // reduced-motion, or hidden-tab throttling on the initial layout).
  let lastTop = -1;
  function tick() {
    if (!scroll.isConnected) return;   // node removed on re-render, stop
    const top = scroll.scrollTop;
    if (top !== lastTop) {
      lastTop = top;
      updateFvCascade(top);
    }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);

  updateFvCascade(scroll.scrollTop);
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

  // Pre-compute per-day snapped levels once (rather than inside the O(rows × days)
  // inner loop). Directional snap: falling metrics snap UP (ceiling), rising
  // metrics snap DOWN (floor). Passes both prev and next so leftmost-day and
  // flat-day cases fall through to the future-facing hint instead of floor.
  const daysMeta = days.map((d, i) => {
    const prev = i > 0                ? days[i - 1] : null;
    const next = i < days.length - 1  ? days[i + 1] : null;
    return {
      closeLvl:    directionalLevel(prev?.close,     d.close,     next?.close),
      bbUpperLvl:  directionalLevel(prev?.bb_upper,  d.bb_upper,  next?.bb_upper),
      bbLowerLvl:  directionalLevel(prev?.bb_lower,  d.bb_lower,  next?.bb_lower),
      bbMiddleLvl: directionalLevel(prev?.bb_middle, d.bb_middle, next?.bb_middle),
    };
  });

  for (const price of levels) {
    html += `<tr><th class="fv-pricelabel">${price.toFixed(priceDigits)}</th>`;
    for (let i = 0; i < days.length; i++) {
      const d = days[i];
      const m = daysMeta[i];
      const isBBUpper  = eqLevel(price, m.bbUpperLvl);
      const isBBLower  = eqLevel(price, m.bbLowerLvl);
      const isBBMiddle = eqLevel(price, m.bbMiddleLvl);
      const isClose    = eqLevel(price, m.closeLvl);
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
  // Re-apply the user's Time Series colors AFTER injecting fresh HTML — the
  // color vars live on .fv-container so a full innerHTML replace on that node
  // would wipe them; we set them as inline style properties on the container.
  if (window.DolaTsSettings) window.DolaTsSettings.applyTsSettings();
  // The .fv-scroll element is a fresh node after each render; wire the
  // cascade scroll listener and crosshair overlay onto it (both idempotent
  // via data flags on the node so re-renders re-arm cleanly).
  attachFvCascade();
  attachFvCrosshair();
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

// Re-theme on Time Series Settings save. applyTsSettings writes CSS vars onto
// every .fv-container, so no re-render is needed — the browser recomputes the
// tinted cells against the new vars.
window.addEventListener("dola:tssettings", () => {
  if (window.DolaTsSettings) window.DolaTsSettings.applyTsSettings();
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
