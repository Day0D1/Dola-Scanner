// Dola Options Scanner - dashboard frontend

const $ = (id) => document.getElementById(id);

const DEFAULT_CHART_SETTINGS = {
  stock:  { bb_period: 20, bb_stddev: 2, rsi_period: 10, pnf_box: 1.0, pnf_reversal: 3, pnf_type: "percentage" },
  SPX:    { bb_period: 20, bb_stddev: 2, rsi_period: 10, pnf_box: 1.0, pnf_reversal: 3, pnf_type: "percentage" },
  VIX:    { bb_period: 20, bb_stddev: 2, rsi_period: 10, pnf_box: 1.0, pnf_reversal: 2, pnf_type: "traditional" },
  BPNYA:  { bb_period: 20, bb_stddev: 2, rsi_period: 10, pnf_box: 1.0, pnf_reversal: 2, pnf_type: "traditional" },
};

const state = {
  signals: [],
  breadth: null,
  settings: { rsi_oversold: 39, rsi_overbought: 70 },
  filter: {
    query: "",
    signal: "ALL",
    rsi: "ALL",
    pnf: "ALL",
    sector: "ALL",
  },
  sort: "signal",
  modal: { kind: null, key: null },   // kind: 'stock' | 'index', key: ticker or SPX/VIX/BPNYA
  timeframe: "3M",
  chartSettings: { ...DEFAULT_CHART_SETTINGS.stock },
};

// ---- API --------------------------------------------------------------

async function fetchScan() {
  const r = await fetch("/api/scan");
  return r.json();
}

async function fetchSchedule() {
  try { const r = await fetch("/api/schedule"); return r.json(); } catch { return null; }
}

async function triggerRefresh(notify = false) {
  const r = await fetch(`/api/scan/refresh?notify=${notify}`, { method: "POST" });
  return r.json();
}

function _settingsToQuery(s) {
  const parts = [];
  if (s.bb_period != null)    parts.push(`bb_period=${s.bb_period}`);
  if (s.bb_stddev != null)    parts.push(`bb_stddev=${s.bb_stddev}`);
  if (s.rsi_period != null)   parts.push(`rsi_period=${s.rsi_period}`);
  if (s.pnf_box != null)      parts.push(`pnf_box=${s.pnf_box}`);
  if (s.pnf_reversal != null) parts.push(`pnf_reversal=${s.pnf_reversal}`);
  return parts.join("&");
}

async function fetchChart(kind, key, timeframe, settings) {
  const path = kind === "index"
    ? `/api/index/${encodeURIComponent(key)}`
    : `/api/stock/${encodeURIComponent(key)}`;
  const q = `?timeframe=${encodeURIComponent(timeframe)}&${_settingsToQuery(settings || {})}`;
  const r = await fetch(path + q);
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.error || `fetch failed: ${r.status}`);
  }
  return r.json();
}

// ---- Formatting helpers ------------------------------------------------

function fmtTime(ts) {
  if (!ts) return "–";
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) +
    "  " + d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}

function fmtDateMDY(isoDate) {
  // "2026-08-12" -> "08/12/2026"
  if (!isoDate) return "";
  const parts = isoDate.split("-");
  if (parts.length !== 3) return isoDate;
  return `${parts[1]}/${parts[2]}/${parts[0]}`;
}

// ---- Breadth ----------------------------------------------------------

function renderModeBadge() {
  const el = $("modeBadge");
  if (!el) return;
  const mode = state.settings?.signal_mode || "both";
  if (mode === "both") {
    el.classList.add("hidden");
    return;
  }
  el.classList.remove("hidden");
  el.classList.remove("puts-only", "calls-only");
  if (mode === "puts_only") {
    el.classList.add("puts-only");
    el.textContent = "Puts only";
    el.title = "Only ELON candidates and SELL_PUTS entries are surfaced. MUSK/SELL_CALLS muted.";
  } else if (mode === "calls_only") {
    el.classList.add("calls-only");
    el.textContent = "Calls only";
    el.title = "Only MUSK candidates and SELL_CALLS entries are surfaced. ELON/SELL_PUTS muted.";
  }
}

function renderBreadth(b) {
  const risk = $("riskValue");
  const riskLbl = b.risk || "–";
  const changed = risk.textContent !== riskLbl;
  risk.textContent = riskLbl;
  risk.className = "risk-value " + (b.risk ? b.risk.toLowerCase() : "mixed");
  if (changed) {
    risk.classList.remove("number-anim");
    void risk.offsetWidth;  // reflow so animation retriggers
    risk.classList.add("number-anim");
  }

  const regimeEl = $("regimeValue");
  if (b.regime === "BUY") {
    regimeEl.innerHTML = '<span class="buy">BUY</span> (SPX in uptrend)';
  } else if (b.regime === "SELL") {
    regimeEl.innerHTML = '<span class="sell">SELL</span> (SPX in downtrend)';
  } else {
    regimeEl.textContent = "–";
  }

  const signalClass = (v) => v === "X" ? "v x" : (v === "O" ? "v o" : "v");
  const changeClass = (v) => v == null ? "v" : (v > 0 ? "v pos" : (v < 0 ? "v neg" : "v"));
  const fmtChange = (v) => v == null ? "–" : (v > 0 ? `+${v}` : `${v}`);
  const fmt = (v, digits) => v == null ? "–" : Number(v).toFixed(digits ?? 2);

  const set = (id, text, cls) => { const e = $(id); e.textContent = text; if (cls) e.className = cls; };

  // SPX
  const spx = b.spx || {};
  set("spxSignal", spx.signal || "–", signalClass(spx.column));
  set("spxLevel",  fmt(spx.level, 2), "v");
  set("spxChange", spx.change == null ? "–" : String(spx.change), "v");

  // BPNYA
  const bp = b.bpnya || {};
  set("bpnyaSignal", bp.signal || "–", signalClass(bp.column));
  set("bpnyaLevel",  bp.level == null ? "–" : fmt(bp.level, 2) + "%", "v");
  set("bpnyaChange", fmtChange(bp.change), changeClass(bp.change));

  // VIX
  const vix = b.vix || {};
  set("vixSignal", vix.signal || "–", signalClass(vix.column));
  set("vixLevel",  fmt(vix.level, 2), "v");
  set("vixChange", fmtChange(vix.change), changeClass(vix.change));
}

// ---- Filter / sort ----------------------------------------------------

function passesFilter(s) {
  const f = state.filter;
  if (f.query) {
    const q = f.query.toUpperCase();
    if (!s.ticker.toUpperCase().includes(q)) return false;
  }
  switch (f.signal) {
    case "MAJOR":       if (!s.on_watchlist) return false; break;
    case "ENTRIES":     if (!s.entry_trigger) return false; break;
    case "CANDIDATES":  if (!s.candidate) return false; break;
    case "ELON":        if (s.candidate !== "ELON") return false; break;
    case "MUSK":        if (s.candidate !== "MUSK") return false; break;
  }
  switch (f.rsi) {
    case "OVERSOLD":    if (!(s.rsi != null && s.rsi < 30)) return false; break;
    case "OVERBOUGHT":  if (!(s.rsi != null && s.rsi > 70)) return false; break;
    case "NEUTRAL":     if (!(s.rsi != null && s.rsi >= 30 && s.rsi <= 70)) return false; break;
  }
  if (f.pnf !== "ALL" && s.pnf_column !== f.pnf) return false;
  if (f.sector !== "ALL" && s.sector !== f.sector) return false;
  return true;
}

function signalStrength(s) {
  // Higher = stronger. Entries beat candidates beat plain.
  if (s.entry_trigger) return 3;
  if (s.candidate) return 2;
  return 1;
}

function sortSignals(list) {
  const arr = [...list];
  switch (state.sort) {
    case "ticker_asc":  arr.sort((a, b) => a.ticker.localeCompare(b.ticker)); break;
    case "ticker_desc": arr.sort((a, b) => b.ticker.localeCompare(a.ticker)); break;
    case "rsi_asc":     arr.sort((a, b) => (a.rsi ?? 999) - (b.rsi ?? 999)); break;
    case "rsi_desc":    arr.sort((a, b) => (b.rsi ?? -1) - (a.rsi ?? -1)); break;
    case "price_asc":   arr.sort((a, b) => (a.last_close ?? 0) - (b.last_close ?? 0)); break;
    case "price_desc":  arr.sort((a, b) => (b.last_close ?? 0) - (a.last_close ?? 0)); break;
    case "signal":
    default:
      arr.sort((a, b) => {
        const d = signalStrength(b) - signalStrength(a);
        if (d !== 0) return d;
        return a.ticker.localeCompare(b.ticker);
      });
  }
  return arr;
}

// ---- Cards ------------------------------------------------------------

function makeStockCard(s) {
  const card = document.createElement("div");
  const cls = ["stock-card"];
  if (s.entry_trigger) {
    cls.push("enter");
    if (s.entry_trigger === "SELL_CALLS") cls.push("sellcalls");
  } else if (s.candidate === "ELON") cls.push("elon");
  else if (s.candidate === "MUSK") cls.push("musk");
  card.className = cls.join(" ");
  card.dataset.ticker = s.ticker;

  let badgeHtml = "";
  if (s.entry_trigger) {
    const direction = s.entry_trigger === "SELL_PUTS" ? "sell puts" : "sell calls";
    const bcls = s.entry_trigger === "SELL_CALLS" ? "badge enter sellcalls" : "badge enter";
    badgeHtml = `<span class="${bcls}">ENTER · ${direction}</span>`;
  } else if (s.candidate) {
    badgeHtml = `<span class="badge ${s.candidate.toLowerCase()}">${s.candidate}</span>`;
  }

  const lo = state.settings.rsi_oversold ?? 30;
  const hi = state.settings.rsi_overbought ?? 70;
  const rsiCls = s.rsi != null && s.rsi < lo ? "rsi-lo" : (s.rsi != null && s.rsi > hi ? "rsi-hi" : "");
  const pnfCls = s.pnf_column === "X" ? "pnf-x" : (s.pnf_column === "O" ? "pnf-o" : "");
  const price = s.last_close != null ? `$${s.last_close.toFixed(2)}` : "–";
  const rsiVal = s.rsi != null ? s.rsi.toFixed(1) : "–";
  const pnfVal = s.pnf_column || "–";

  const starHtml = s.on_watchlist ? '<span class="major-star" title="Major Watchlist">&#9733;</span>' : "";
  card.innerHTML = `
    <div class="row"><span class="ticker">${starHtml}${s.ticker}</span><span class="price">${price}</span></div>
    <div class="sector">${s.sector || ""}</div>
    <div class="meta">
      <span>RSI <span class="${rsiCls}">${rsiVal}</span></span>
      <span>P&amp;F <span class="${pnfCls}">${pnfVal}</span></span>
    </div>
    ${badgeHtml ? `<div style="margin-top:8px">${badgeHtml}</div>` : ""}
  `;
  card.addEventListener("click", () => openStockModal(s.ticker));
  return card;
}

function showSkeletonGrid() {
  // Populate the "All stocks" grid with 12 skeleton cards while the first scan runs.
  const grid = $("quietGrid");
  if (!grid || grid.dataset.skeletonShown === "1") return;
  grid.dataset.skeletonShown = "1";
  const cards = [];
  for (let i = 0; i < 12; i++) {
    cards.push('<div class="stock-card"><div class="row"><span class="skeleton" style="width:52px;height:16px;display:inline-block">&nbsp;</span><span class="skeleton" style="width:64px;height:15px;display:inline-block">&nbsp;</span></div><div class="sector"><span class="skeleton" style="width:100px;height:10px;display:inline-block">&nbsp;</span></div><div class="meta"><span class="skeleton" style="width:70px;height:12px;display:inline-block">&nbsp;</span><span class="skeleton" style="width:70px;height:12px;display:inline-block">&nbsp;</span></div></div>');
  }
  grid.innerHTML = cards.join("");
}

function paintSection(sectionId, countId, gridId, list) {
  const secEl = $(sectionId);
  $(countId).textContent = list.length;
  const gridEl = $(gridId);
  gridEl.dataset.skeletonShown = "";  // clear skeleton flag
  gridEl.innerHTML = "";
  for (const s of list) gridEl.appendChild(makeStockCard(s));
  if (sectionId !== "quietSection") {
    secEl.classList.toggle("hidden", list.length === 0);
  }
}

function renderCards() {
  const filtered = state.signals.filter(passesFilter);
  const sorted = sortSignals(filtered);

  const entries = sorted.filter(s => s.entry_trigger);
  const candidates = sorted.filter(s => s.candidate && !s.entry_trigger);
  // Major Watchlist: all major-watch stocks (sorted by signal strength via existing sort default).
  const majorAll = state.signals.filter(s => s.on_watchlist);
  const major = sortSignalStrength(majorAll);

  paintSection("entriesSection", "entriesCount", "entriesGrid", entries);
  paintSection("candidatesSection", "candidatesCount", "candidatesGrid", candidates);
  paintSection("majorSection", "majorCount", "majorGrid", major);
  paintSection("quietSection", "quietCount", "quietGrid", sorted);
  $("resultsCount").textContent = sorted.length;
}

function sortSignalStrength(list) {
  const arr = [...list];
  arr.sort((a, b) => {
    const d = signalStrength(b) - signalStrength(a);
    if (d !== 0) return d;
    return a.ticker.localeCompare(b.ticker);
  });
  return arr;
}

function populateSectorSelect() {
  const cur = state.filter.sector;
  const sel = $("sectorSelect");
  const seen = new Set();
  const sectors = [];
  for (const s of state.signals) {
    if (s.sector && !seen.has(s.sector)) { seen.add(s.sector); sectors.push(s.sector); }
  }
  sectors.sort();
  sel.innerHTML = '<option value="ALL">All sectors</option>' +
    sectors.map(sec => `<option value="${sec}">${sec}</option>`).join("");
  sel.value = cur;
}

// ---- Poll loop --------------------------------------------------------

async function refreshUI() {
  const scan = await fetchScan();
  if (scan.status === "error") {
    const risk = $("riskValue");
    if (risk) { risk.textContent = "ERROR"; risk.className = "risk-value high"; }
    return;
  }
  if (scan.status === "loading") {
    $("scanTime").textContent = scan.scanning ? "scanning…" : "loading…";
    if (scan.scanning) showSkeletonGrid();
    if (scan.scanning) $("refreshBtn")?.classList.add("pulsing");
    return;
  }
  $("refreshBtn")?.classList.remove("pulsing");
  $("scanTime").textContent = (scan.scanning ? "scanning…  " : "last scan  ") + fmtTime(scan.last_scan_at);
  renderBreadth(scan.breadth);
  state.signals = scan.signals;
  state.breadth = scan.breadth;
  if (scan.settings) state.settings = scan.settings;
  renderModeBadge();
  populateSectorSelect();
  renderCards();

  const sch = await fetchSchedule();
  if (sch?.next_run_at) {
    const t = new Date(sch.next_run_at);
    $("nextScan").textContent = "next auto-scan  " +
      t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) +
      "  " + t.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  } else {
    $("nextScan").textContent = "auto-scan idle";
  }
}

// ---- Modal + charts ---------------------------------------------------

function openStockModal(ticker)  { openChartModal("stock", ticker); }
function openIndexModal(key)     { openChartModal("index", key); }

function openChartModal(kind, key) {
  state.modal = { kind, key };
  state.timeframe = "3M";
  state.chartSettings = { ...(DEFAULT_CHART_SETTINGS[key] || DEFAULT_CHART_SETTINGS.stock) };
  updateTimeframeChips();
  populateSettingsPanel();
  $("settingsPanel").classList.add("hidden");
  $("modal").classList.remove("hidden");
  $("modalTicker").textContent = key;
  $("modalMeta").textContent = "loading…";
  $("candlestickChart").innerHTML = "";
  $("pnfChart").innerHTML = "";
  if ($("pnfChartFV")) $("pnfChartFV").innerHTML = "";
  // Show/hide Fair Value link (stocks only, not indices).
  const fvLink = $("fvLink");
  if (fvLink) {
    if (kind === "stock") {
      fvLink.href = `/fair_value/${encodeURIComponent(key)}`;
      fvLink.style.display = "";
    } else {
      fvLink.style.display = "none";
    }
  }
  loadCharts();
}

function closeModal() {
  $("modal").classList.add("hidden");
  state.modal = { kind: null, key: null };
}

function loadCharts() {
  const { kind, key } = state.modal;
  if (!kind || !key) return;
  fetchChart(kind, key, state.timeframe, state.chartSettings)
    .then(renderChartModal)
    .catch((e) => { $("modalMeta").textContent = "error: " + e.message; });
}

function updateTimeframeChips() {
  document.querySelectorAll("#timeframeChips .chip").forEach(c => {
    c.classList.toggle("active", c.dataset.tf === state.timeframe);
  });
}

function renderChartModal(payload) {
  const s = payload.signal;
  const last = payload.candles[payload.candles.length - 1];
  const isPct = state.modal.kind === "index" && state.modal.key === "BPNYA";
  const suffix = isPct ? "%" : "";
  const meta = [];
  meta.push(`${isPct ? "" : "$"}${last.close.toFixed(2)}${suffix}`);
  if (last.rsi != null) meta.push(`RSI(${payload.settings.rsi_period}) ${last.rsi.toFixed(1)}`);
  const pnfCol = payload.pnf.length ? payload.pnf[payload.pnf.length - 1].type : "–";
  meta.push(`P&F ${pnfCol}`);
  meta.push(`TF ${payload.timeframe}`);
  if (s?.candidate) meta.push(`${s.candidate} candidate`);
  if (s?.entry_trigger) meta.push(`ENTER ${s.entry_trigger.replace("_", " ").toLowerCase()}`);
  $("modalTicker").textContent = payload.display_name || payload.ticker;
  $("modalMeta").textContent = meta.join("  ·  ");

  const boxUnit = payload.pnf_type === "traditional" ? "pt" : "%";
  const pnfSubtitleText =
    `Close-Only · ${payload.pnf_box} ${boxUnit} box · ${payload.pnf_reversal}-box reversal · ${payload.pnf_type}`;
  $("pnfSubtitle").textContent = pnfSubtitleText;
  if ($("pnfSubtitleFV")) {
    $("pnfSubtitleFV").textContent =
      `${payload.pnf_box} ${boxUnit} box · ${payload.pnf_reversal}-box reversal · built on BB(${payload.settings.bb_period}) middle`;
  }

  if (payload.chart_type === "line") {
    renderLineChart(payload.candles, payload.settings);
  } else {
    renderCandlestick(payload.candles, payload.settings);
  }
  renderPnF(payload.pnf, payload.pnf_box, payload.pnf_type, "pnfChart");
  renderPnF(payload.pnf_fair_value || [], payload.pnf_box, payload.pnf_type, "pnfChartFV");
}

function renderCandlestick(candles, settings) {
  const x = candles.map((c) => c.date);
  const open = candles.map((c) => c.open);
  const high = candles.map((c) => c.high);
  const low = candles.map((c) => c.low);
  const close = candles.map((c) => c.close);
  const bbUpper = candles.map((c) => c.bb_upper);
  const bbMiddle = candles.map((c) => c.bb_middle);
  const bbLower = candles.map((c) => c.bb_lower);
  const rsi = candles.map((c) => c.rsi);

  // Candles have hover with OHLC; BB traces are silent (no label, no hover) per user request.
  const traces = [
    {
      type: "candlestick",
      x, open, high, low, close,
      name: "",
      xhoverformat: "%m/%d/%Y",
      increasing: { line: { color: "#22c55e" }, fillcolor: "rgba(34,197,94,0.4)" },
      decreasing: { line: { color: "#ef4444" }, fillcolor: "rgba(239,68,68,0.4)" },
      xaxis: "x", yaxis: "y",
      showlegend: false,
    },
    { type: "scatter", mode: "lines", x, y: bbUpper,  name: "",
      line: { color: "#8b93a4", width: 1, dash: "dot" }, hoverinfo: "skip", showlegend: false, xaxis: "x", yaxis: "y" },
    { type: "scatter", mode: "lines", x, y: bbMiddle, name: "",
      line: { color: "#4c8dff", width: 1 }, hoverinfo: "skip", showlegend: false, xaxis: "x", yaxis: "y" },
    { type: "scatter", mode: "lines", x, y: bbLower,  name: "",
      line: { color: "#8b93a4", width: 1, dash: "dot" }, hoverinfo: "skip", showlegend: false, xaxis: "x", yaxis: "y" },
    { type: "scatter", mode: "lines", x, y: rsi, name: "",
      line: { color: "#eab308", width: 1.5 }, hoverinfo: "skip", showlegend: false, xaxis: "x", yaxis: "y2" },
  ];

  const layout = {
    dragmode: "pan",
    margin: { l: 20, r: 60, t: 12, b: 34 },
    paper_bgcolor: "#12151d",
    plot_bgcolor: "#12151d",
    font: { color: "#e6e8ec", family: "ui-monospace, Menlo, Consolas, monospace", size: 11 },
    showlegend: false,
    hovermode: "x unified",
    grid: { rows: 2, columns: 1, pattern: "independent", roworder: "top to bottom" },
    xaxis: {
      rangeslider: { visible: false },
      gridcolor: "#232833",
      zerolinecolor: "#232833",
      type: "date",
      tickformat: "%m/%d/%Y",
      hoverformat: "%m/%d/%Y",
      showspikes: true, spikemode: "across", spikecolor: "#4c8dff", spikethickness: 1,
    },
    yaxis: {
      domain: [0.35, 1.0], gridcolor: "#232833", zerolinecolor: "#232833",
      tickformat: ".2f",
      side: "right",
      showspikes: true, spikemode: "across", spikecolor: "#4c8dff", spikethickness: 1, spikedash: "dot",
    },
    xaxis2: { anchor: "y2", matches: "x", showticklabels: false, gridcolor: "#232833" },
    yaxis2: {
      domain: [0.0, 0.28], gridcolor: "#232833", zerolinecolor: "#232833", range: [0, 100],
      side: "right",
      tickvals: [settings?.rsi_oversold ?? 30, 50, settings?.rsi_overbought ?? 70],
      title: { text: `RSI(${settings?.rsi_period ?? 5})`, font: { color: "#8b93a4" } },
    },
    shapes: [
      { type: "line", xref: "paper", x0: 0, x1: 1, yref: "y2",
        y0: settings?.rsi_oversold ?? 30, y1: settings?.rsi_oversold ?? 30,
        line: { color: "#22c55e", width: 1, dash: "dash" } },
      { type: "line", xref: "paper", x0: 0, x1: 1, yref: "y2",
        y0: settings?.rsi_overbought ?? 70, y1: settings?.rsi_overbought ?? 70,
        line: { color: "#ef4444", width: 1, dash: "dash" } },
    ],
  };

  Plotly.newPlot("candlestickChart", traces, layout, {
    displayModeBar: false,
    responsive: true,
    scrollZoom: true,
  });
}

function renderLineChart(candles, settings) {
  const x = candles.map((c) => c.date);
  const y = candles.map((c) => c.close);
  const traces = [
    {
      type: "scatter",
      mode: "lines+markers",
      x, y,
      name: "",
      line: { color: "#4c8dff", width: 2 },
      marker: { color: "#4c8dff", size: 5 },
      hovertemplate: "%{x|%m/%d/%Y}<br>%{y:.2f}%<extra></extra>",
    },
  ];
  const layout = {
    dragmode: "pan",
    margin: { l: 20, r: 60, t: 12, b: 34 },
    paper_bgcolor: "#12151d",
    plot_bgcolor: "#12151d",
    font: { color: "#e6e8ec", family: "ui-monospace, Menlo, Consolas, monospace", size: 11 },
    showlegend: false,
    xaxis: {
      gridcolor: "#232833", zerolinecolor: "#232833", type: "date",
      tickformat: "%m/%d/%Y", hoverformat: "%m/%d/%Y",
      showspikes: true, spikemode: "across", spikecolor: "#4c8dff", spikethickness: 1,
    },
    yaxis: {
      gridcolor: "#232833", zerolinecolor: "#232833", side: "right",
      range: [0, 100], tickvals: [0, 30, 50, 70, 100],
      title: { text: "% on buy signal", font: { color: "#8b93a4" } },
    },
    shapes: [
      { type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: 30, y1: 30, line: { color: "#22c55e", width: 1, dash: "dash" } },
      { type: "line", xref: "paper", x0: 0, x1: 1, yref: "y", y0: 70, y1: 70, line: { color: "#ef4444", width: 1, dash: "dash" } },
    ],
  };
  Plotly.newPlot("candlestickChart", traces, layout, { displayModeBar: false, responsive: true, scrollZoom: true });
}

function renderPnF(columns, box, pnfType, targetId) {
  const container = document.getElementById(targetId || "pnfChart");
  if (!container) return;
  if (!columns.length) {
    container.innerHTML = '<div style="color:#8b93a4;padding:16px">No P&amp;F data yet.</div>';
    return;
  }

  let minIdx = Infinity, maxIdx = -Infinity;
  for (const c of columns) {
    if (c.bottom_idx < minIdx) minIdx = c.bottom_idx;
    if (c.top_idx > maxIdx) maxIdx = c.top_idx;
  }

  const boxH = 14;
  const boxW = 14;
  const labelW = 62;
  const rightLabelW = 62;
  const padTop = 12;
  const padBottom = 34;
  const rows = maxIdx - minIdx + 1;
  const height = rows * boxH + padTop + padBottom;
  const width = labelW + columns.length * boxW + rightLabelW + 10;

  const priceOfIdx = (i) => pnfType === "traditional"
    ? i * box
    : Math.exp(i * Math.log(1 + box / 100));

  const parts = [];
  parts.push(`<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg" style="display:block; min-width:${width}px; height:${height}px; background:#12151d">`);

  // Grid lines and price labels on BOTH sides.
  const step = Math.max(1, Math.floor(rows / 14));
  for (let idx = minIdx; idx <= maxIdx; idx += step) {
    const y = padTop + (maxIdx - idx) * boxH + boxH / 2;
    parts.push(`<line x1="${labelW}" y1="${y}" x2="${width - rightLabelW}" y2="${y}" stroke="#1f2430" stroke-width="1"/>`);
    const priceStr = priceOfIdx(idx).toFixed(2);
    parts.push(`<text x="${labelW - 6}" y="${y + 3}" text-anchor="end" font-size="10" fill="#8b93a4" font-family="ui-monospace, Menlo, Consolas, monospace">${priceStr}</text>`);
    parts.push(`<text x="${width - rightLabelW + 6}" y="${y + 3}" text-anchor="start" font-size="10" fill="#8b93a4" font-family="ui-monospace, Menlo, Consolas, monospace">${priceStr}</text>`);
  }

  // Columns of X/O markers.
  for (let ci = 0; ci < columns.length; ci++) {
    const c = columns[ci];
    const cx = labelW + ci * boxW + boxW / 2;
    const isCurrent = ci === columns.length - 1;
    for (let idx = c.bottom_idx; idx <= c.top_idx; idx++) {
      const cy = padTop + (maxIdx - idx) * boxH + boxH / 2;
      if (c.type === "X") {
        const color = "#22c55e";
        parts.push(`<line x1="${cx - 4}" y1="${cy - 4}" x2="${cx + 4}" y2="${cy + 4}" stroke="${color}" stroke-width="1.6"/>`);
        parts.push(`<line x1="${cx - 4}" y1="${cy + 4}" x2="${cx + 4}" y2="${cy - 4}" stroke="${color}" stroke-width="1.6"/>`);
      } else {
        parts.push(`<circle cx="${cx}" cy="${cy}" r="4" fill="none" stroke="#ef4444" stroke-width="1.6"/>`);
      }
    }
    if (isCurrent) {
      const topY = padTop + (maxIdx - c.top_idx) * boxH;
      const botY = padTop + (maxIdx - c.bottom_idx) * boxH + boxH;
      parts.push(`<rect x="${cx - boxW / 2}" y="${topY}" width="${boxW}" height="${botY - topY}" fill="none" stroke="#4c8dff" stroke-width="1" stroke-dasharray="2 2" opacity="0.7"/>`);
    }
  }

  // Date axis at bottom: mark month changes.
  const monthLabel = (dstr) => {
    if (!dstr) return "";
    const parts2 = dstr.split("-");
    if (parts2.length !== 3) return "";
    const y = parts2[0].slice(2);
    const m = parts2[1];
    return `${m}/${y}`;
  };
  let prevMonthKey = "";
  for (let ci = 0; ci < columns.length; ci++) {
    const c = columns[ci];
    const dstr = c.end_date || c.start_date || "";
    const key = dstr.slice(0, 7); // YYYY-MM
    const isLast = ci === columns.length - 1;
    if ((key && key !== prevMonthKey) || isLast) {
      const cx = labelW + ci * boxW + boxW / 2;
      const label = monthLabel(dstr);
      parts.push(`<line x1="${cx}" y1="${padTop + rows * boxH}" x2="${cx}" y2="${padTop + rows * boxH + 4}" stroke="#8b93a4" stroke-width="1"/>`);
      parts.push(`<text x="${cx}" y="${padTop + rows * boxH + 16}" text-anchor="middle" font-size="10" fill="#8b93a4" font-family="ui-monospace, Menlo, Consolas, monospace">${label}</text>`);
      prevMonthKey = key;
    }
  }
  // First column always gets a label
  if (columns[0]) {
    const dstr = columns[0].start_date || columns[0].end_date || "";
    const label = monthLabel(dstr);
    parts.push(`<text x="${labelW + boxW / 2}" y="${padTop + rows * boxH + 28}" text-anchor="middle" font-size="9" fill="#5a6070" font-family="ui-monospace, Menlo, Consolas, monospace">${fmtDateMDY(dstr)}</text>`);
  }
  // Last column full date
  const lastCol = columns[columns.length - 1];
  if (lastCol) {
    const dstr = lastCol.end_date || lastCol.start_date || "";
    const cx = labelW + (columns.length - 1) * boxW + boxW / 2;
    parts.push(`<text x="${cx}" y="${padTop + rows * boxH + 28}" text-anchor="middle" font-size="9" fill="#5a6070" font-family="ui-monospace, Menlo, Consolas, monospace">${fmtDateMDY(dstr)}</text>`);
  }

  parts.push("</svg>");
  container.innerHTML = parts.join("");
}

// ---- Event wiring -----------------------------------------------------

document.addEventListener("click", (e) => {
  if (e.target.matches("[data-close]")) closeModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});

function setChipGroup(groupId, attr, value) {
  document.querySelectorAll(`#${groupId} .chip`).forEach(c => {
    c.classList.toggle("active", c.dataset[attr] === value);
  });
}

$("signalChips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  state.filter.signal = chip.dataset.signal;
  setChipGroup("signalChips", "signal", state.filter.signal);
  renderCards();
});
$("rsiChips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  state.filter.rsi = chip.dataset.rsi;
  setChipGroup("rsiChips", "rsi", state.filter.rsi);
  renderCards();
});
$("pnfChips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  state.filter.pnf = chip.dataset.pnf;
  setChipGroup("pnfChips", "pnf", state.filter.pnf);
  renderCards();
});
$("sectorSelect").addEventListener("change", (e) => {
  state.filter.sector = e.target.value;
  renderCards();
});
$("sortSelect").addEventListener("change", (e) => {
  state.sort = e.target.value;
  renderCards();
});
$("searchInput").addEventListener("input", (e) => {
  state.filter.query = e.target.value.trim();
  renderCards();
});

$("timeframeChips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  state.timeframe = chip.dataset.tf;
  updateTimeframeChips();
  $("candlestickChart").innerHTML = "";
  $("pnfChart").innerHTML = "";
  if ($("pnfChartFV")) $("pnfChartFV").innerHTML = "";
  loadCharts();
});

// Ticker search inside the modal: swap the modal content to another stock without closing.
if (window.attachTickerSearch) {
  window.attachTickerSearch(
    $("modalSearchInput"),
    $("modalSearchResults"),
    () => state.signals.map(s => ({ ticker: s.ticker, sector: s.sector })),
    (ticker) => { openStockModal(ticker); }
  );
}

// Pillar cards open the index chart modal.
document.querySelectorAll(".pillar.clickable").forEach(el => {
  el.addEventListener("click", () => openIndexModal(el.dataset.index));
});

// Settings panel wiring.
function populateSettingsPanel() {
  const s = state.chartSettings;
  $("setBBPeriod").value = s.bb_period;
  $("setBBStdDev").value = s.bb_stddev;
  $("setRSIPeriod").value = s.rsi_period;
  $("setPnFBox").value = s.pnf_box;
  $("setPnFReversal").value = s.pnf_reversal;
  $("setPnFBoxUnit").textContent = s.pnf_type === "traditional" ? "pt" : "%";
}

$("settingsToggle").addEventListener("click", () => {
  $("settingsPanel").classList.toggle("hidden");
});

$("settingsApply").addEventListener("click", () => {
  state.chartSettings = {
    ...state.chartSettings,
    bb_period:   Number($("setBBPeriod").value) || state.chartSettings.bb_period,
    bb_stddev:   Number($("setBBStdDev").value) || state.chartSettings.bb_stddev,
    rsi_period:  Number($("setRSIPeriod").value) || state.chartSettings.rsi_period,
    pnf_box:     Number($("setPnFBox").value) || state.chartSettings.pnf_box,
    pnf_reversal:Number($("setPnFReversal").value) || state.chartSettings.pnf_reversal,
  };
  $("candlestickChart").innerHTML = "";
  $("pnfChart").innerHTML = "";
  if ($("pnfChartFV")) $("pnfChartFV").innerHTML = "";
  loadCharts();
});

$("settingsReset").addEventListener("click", () => {
  const key = state.modal.key;
  state.chartSettings = { ...(DEFAULT_CHART_SETTINGS[key] || DEFAULT_CHART_SETTINGS.stock) };
  populateSettingsPanel();
  $("candlestickChart").innerHTML = "";
  $("pnfChart").innerHTML = "";
  if ($("pnfChartFV")) $("pnfChartFV").innerHTML = "";
  loadCharts();
});

$("refreshBtn").addEventListener("click", async () => {
  const btn = $("refreshBtn");
  btn.disabled = true;
  btn.textContent = "Scanning…";
  btn.classList.add("pulsing");
  try {
    await triggerRefresh(false);
    for (let i = 0; i < 90; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      const s = await fetchScan();
      if (!s.scanning) { await refreshUI(); break; }
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "Refresh scan";
    btn.classList.remove("pulsing");
  }
});

refreshUI();
setInterval(refreshUI, 20000);
