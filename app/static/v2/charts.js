// Dola v2 — chart renderers.
// Plotly candlestick + RSI subplot with v2 dark palette + event markers.
// SVG Point & Figure — restyled from the legacy renderer.

// Read v2 CSS custom-properties so charts pick up palette changes automatically.
function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export const palette = {
  bg:      () => cssVar("--panel", "#0D141D"),
  grid:    () => cssVar("--border", "rgba(255,255,255,0.05)"),
  ink:     () => cssVar("--ink", "#eef1f7"),
  ink2:    () => cssVar("--ink-2", "#a3adc2"),
  ink3:    () => cssVar("--ink-3", "#6a7690"),
  bull:    () => cssVar("--bull", "#22c55e"),
  bear:    () => cssVar("--bear", "#ef4444"),
  warn:    () => cssVar("--warn", "#f59e0b"),
  accent:  () => cssVar("--accent", "#5b9bff"),
};

// ---- Candlestick + BB + RSI subplot ---------------------------

export function renderCandlestick(hostId, candles, settings, opts = {}) {
  if (!candles || !candles.length) {
    document.getElementById(hostId).innerHTML =
      '<div style="padding: var(--sp-6); color: var(--ink-3); text-align:center;">No candle data.</div>';
    return;
  }

  const x       = candles.map((c) => c.date);
  const bbUpper = candles.map((c) => c.bb_upper);
  const bbLower = candles.map((c) => c.bb_lower);
  const bbMid   = candles.map((c) => c.bb_middle);
  const rsi     = candles.map((c) => c.rsi);

  const rsiLo = settings?.rsi_oversold ?? 39;
  const rsiHi = settings?.rsi_overbought ?? 70;

  // User color overrides — passed by the caller from chart_settings.js. Any
  // key not supplied falls back to the v2 palette. Indices bypass this by
  // passing an empty object (see chart_detail.html).
  const C = opts.colors || {};
  const cCandleUp   = C.candle_up          || palette.bull();
  const cCandleDown = C.candle_down        || palette.bear();
  const cBBU        = C.bb_upper           || palette.accent();
  const cBBL        = C.bb_lower           || palette.accent();
  const cBBMid      = C.bb_middle          || palette.ink3();
  const cBBFill     = C.bb_fill            || "rgba(91, 155, 255, 0.06)";
  const cRSI        = C.rsi_line           || palette.accent();
  const cRSILo      = C.rsi_oversold_line  || palette.bull();
  const cRSIHi      = C.rsi_overbought_line|| palette.bear();
  const cEntry      = C.event_entry        || palette.warn();

  const traces = [
    // Upper BB (line) — filled band toward lower via fill: tonexty on lower trace.
    {
      type: "scatter", mode: "lines", x, y: bbUpper, name: "BB upper",
      line: { color: cBBU, width: 1, dash: "dot" },
      hoverinfo: "skip", showlegend: false, opacity: 0.75,
    },
    {
      type: "scatter", mode: "lines", x, y: bbLower, name: "BB lower",
      line: { color: cBBL, width: 1, dash: "dot" },
      fill: "tonexty", fillcolor: cBBFill,
      hoverinfo: "skip", showlegend: false, opacity: 0.75,
    },
    {
      type: "scatter", mode: "lines", x, y: bbMid, name: "BB mid",
      line: { color: cBBMid, width: 1, dash: "dash" },
      hoverinfo: "skip", showlegend: false, opacity: 0.6,
    },
    // Candles
    {
      type: "candlestick", x,
      open: candles.map((c) => c.open),
      high: candles.map((c) => c.high),
      low:  candles.map((c) => c.low),
      close: candles.map((c) => c.close),
      increasing: { line: { color: cCandleUp, width: 1 }, fillcolor: cCandleUp },
      decreasing: { line: { color: cCandleDown, width: 1 }, fillcolor: cCandleDown },
      name: "OHLC",
      hovertemplate:
        "<b>%{x|%b %d, %Y}</b><br>" +
        "O %{open:.2f}  H %{high:.2f}<br>" +
        "L %{low:.2f}  C %{close:.2f}" +
        "<extra></extra>",
      showlegend: false,
    },
    // RSI on yaxis2
    {
      type: "scatter", mode: "lines", x, y: rsi, name: "RSI",
      xaxis: "x", yaxis: "y2",
      line: { color: cRSI, width: 1.5 },
      hovertemplate: "RSI %{y:.1f}<extra></extra>",
      showlegend: false,
    },
  ];

  // Event markers on the price chart (annotations)
  const annotations = [];
  const shapes = [
    // RSI threshold lines
    { type: "line", xref: "paper", x0: 0, x1: 1, yref: "y2",
      y0: rsiLo, y1: rsiLo, line: { color: cRSILo, width: 1, dash: "dash" }, opacity: 0.55 },
    { type: "line", xref: "paper", x0: 0, x1: 1, yref: "y2",
      y0: rsiHi, y1: rsiHi, line: { color: cRSIHi, width: 1, dash: "dash" }, opacity: 0.55 },
    { type: "line", xref: "paper", x0: 0, x1: 1, yref: "y2",
      y0: 50, y1: 50, line: { color: palette.ink3(), width: 1, dash: "dot" }, opacity: 0.3 },
  ];

  // Optional event markers passed in from the caller (entry alerts, etc.)
  if (opts.events && opts.events.length) {
    for (const ev of opts.events) {
      const color = ev.kind === "ENTRY" ? cEntry
                  : ev.kind === "ACTIVATION" ? palette.accent()
                  : palette.ink3();
      annotations.push({
        x: ev.date, y: ev.price, xref: "x", yref: "y",
        text: ev.label, showarrow: true, arrowhead: 2, arrowsize: 1, arrowwidth: 1,
        arrowcolor: color,
        ax: 0, ay: -32,
        font: { color, size: 10, family: "JetBrains Mono, monospace" },
        bgcolor: cssVar("--panel-2", "#111A24"),
        bordercolor: color, borderwidth: 1, borderpad: 3,
        opacity: 0.95,
      });
    }
  }

  const layout = {
    dragmode: "pan",
    margin: { l: 8, r: 60, t: 10, b: 34 },
    paper_bgcolor: palette.bg(),
    plot_bgcolor: palette.bg(),
    font: { color: palette.ink(), family: "JetBrains Mono, monospace", size: 11 },
    hovermode: "x unified",
    hoverlabel: {
      bgcolor: cssVar("--panel-2", "#111A24"),
      bordercolor: cssVar("--border-2", "rgba(255,255,255,0.08)"),
      font: { family: "JetBrains Mono, monospace", color: palette.ink() },
    },
    xaxis: {
      rangeslider: { visible: false },
      gridcolor: palette.grid(), zerolinecolor: palette.grid(),
      type: "date",
      tickformat: "%b %d",
      hoverformat: "%b %d, %Y",
      tickfont: { color: palette.ink3() },
      // "across+marker" makes the spike span both subplots so the crosshair
      // reads price on the top chart AND RSI on the bottom at the same time.
      showspikes: true, spikemode: "across+marker", spikecolor: palette.accent(),
      spikethickness: 1.2, spikedash: "dot", spikesnap: "cursor",
    },
    yaxis: {
      domain: opts.noSubplot ? [0.0, 1.0] : [0.32, 1.0],
      gridcolor: palette.grid(), zerolinecolor: palette.grid(),
      tickformat: ",.2f", side: "right",
      tickfont: { color: palette.ink3() },
      showspikes: true, spikemode: "across+marker", spikecolor: palette.accent(),
      spikethickness: 1.2, spikedash: "dot", spikesnap: "cursor",
    },
    xaxis2: { anchor: "y2", matches: "x", showticklabels: false, gridcolor: palette.grid(),
              showspikes: !opts.noSubplot, spikemode: "across+marker", spikecolor: palette.accent(), spikethickness: 1.2, spikedash: "dot" },
    yaxis2: {
      domain: opts.noSubplot ? [0.0, 0.0001] : [0.0, 0.26],
      gridcolor: palette.grid(), zerolinecolor: palette.grid(), range: [0, 100],
      side: "right",
      tickvals: [rsiLo, 50, rsiHi],
      tickfont: { color: palette.ink3() },
      title: opts.noSubplot ? { text: "" } : { text: `RSI(${settings?.rsi_period ?? 10})`, font: { color: palette.ink3(), size: 10 } },
      visible: !opts.noSubplot,
      showspikes: !opts.noSubplot,
    },
    shapes, annotations,
  };

  // Drop the RSI trace when caller asks for no subplot (e.g. VIX)
  const finalTraces = opts.noSubplot ? traces.filter((t) => t.yaxis !== "y2") : traces;

  Plotly.newPlot(hostId, finalTraces, layout, {
    displayModeBar: false, responsive: true, scrollZoom: true,
  });
  // Show the crosshair cursor on the plot area for extra affordance.
  const host = document.getElementById(hostId);
  if (host) host.style.cursor = "crosshair";
}

// ---- Point & Figure — v2 SVG renderer -------------------------

export function renderPnF(hostId, columns, box, pnfType, opts = {}) {
  const host = document.getElementById(hostId);
  if (!host) return;
  if (!columns || !columns.length) {
    host.innerHTML = '<div style="padding: var(--sp-6); color: var(--ink-3); text-align:center;">No P&F data.</div>';
    return;
  }

  let minIdx = Infinity, maxIdx = -Infinity;
  for (const c of columns) {
    if (c.bottom_idx < minIdx) minIdx = c.bottom_idx;
    if (c.top_idx > maxIdx)    maxIdx = c.top_idx;
  }

  const boxH = 18;
  const boxW = 26;
  const labelW = 78;
  const rightW = 78;
  const padTop = 14;
  const padBottom = 32;
  const rows = maxIdx - minIdx + 1;
  const height = rows * boxH + padTop + padBottom;
  const width = labelW + columns.length * boxW + rightW + 10;

  const priceOfIdx = (i) => pnfType === "traditional"
    ? i * box
    : Math.exp(i * Math.log(1 + box / 100));

  const P = palette;
  // Color overrides from chart_settings — indices pass empty {} to bypass.
  const CC = opts.colors || {};
  const cX        = CC.x               || P.bull();
  const cO        = CC.o               || P.bear();
  const cCurrent  = CC.current_column  || P.warn();
  const cGrid     = CC.grid            || P.grid();
  const cLabel    = CC.price_label     || P.ink3();

  const parts = [];
  parts.push(`<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg" style="display:block; min-width:${width}px; height:${height}px; background:${P.bg()}; cursor: crosshair;">`);

  // Grid lines + prices on both sides
  const step = Math.max(1, Math.floor(rows / 12));
  for (let idx = minIdx; idx <= maxIdx; idx += step) {
    const y = padTop + (maxIdx - idx) * boxH + boxH / 2;
    parts.push(`<line x1="${labelW}" y1="${y}" x2="${width - rightW}" y2="${y}" stroke="${cGrid}" stroke-width="1"/>`);
    const priceStr = priceOfIdx(idx).toFixed(2);
    parts.push(`<text x="${labelW - 6}" y="${y + 3}" text-anchor="end" font-size="10" fill="${cLabel}" font-family="JetBrains Mono, monospace">${priceStr}</text>`);
    parts.push(`<text x="${width - rightW + 6}" y="${y + 3}" text-anchor="start" font-size="10" fill="${cLabel}" font-family="JetBrains Mono, monospace">${priceStr}</text>`);
  }

  // X/O columns
  for (let ci = 0; ci < columns.length; ci++) {
    const c = columns[ci];
    const cx = labelW + ci * boxW + boxW / 2;
    const isCurrent = ci === columns.length - 1;
    for (let idx = c.bottom_idx; idx <= c.top_idx; idx++) {
      const cy = padTop + (maxIdx - idx) * boxH + boxH / 2;
      if (c.type === "X") {
        parts.push(`<line x1="${cx - 6}" y1="${cy - 6}" x2="${cx + 6}" y2="${cy + 6}" stroke="${cX}" stroke-width="2.1" stroke-linecap="round"/>`);
        parts.push(`<line x1="${cx - 6}" y1="${cy + 6}" x2="${cx + 6}" y2="${cy - 6}" stroke="${cX}" stroke-width="2.1" stroke-linecap="round"/>`);
      } else {
        parts.push(`<circle cx="${cx}" cy="${cy}" r="6" fill="none" stroke="${cO}" stroke-width="2.1"/>`);
      }
    }
    if (isCurrent) {
      const topY = padTop + (maxIdx - c.top_idx) * boxH;
      const botY = padTop + (maxIdx - c.bottom_idx) * boxH + boxH;
      parts.push(`<rect x="${cx - boxW / 2 + 1}" y="${topY}" width="${boxW - 2}" height="${botY - topY}" fill="none" stroke="${cCurrent}" stroke-width="1" stroke-dasharray="2 2" opacity="0.85"/>`);
    }
  }

  // Month markers on bottom axis
  const monthLabel = (dstr) => {
    if (!dstr) return "";
    const [y, m] = dstr.split("-");
    return `${m}/${y.slice(2)}`;
  };
  let prevKey = "";
  for (let ci = 0; ci < columns.length; ci++) {
    const c = columns[ci];
    const dstr = c.end_date || c.start_date || "";
    const key = dstr.slice(0, 7);
    if (key && key !== prevKey) {
      const cx = labelW + ci * boxW + boxW / 2;
      parts.push(`<line x1="${cx}" y1="${padTop + rows * boxH}" x2="${cx}" y2="${padTop + rows * boxH + 4}" stroke="${P.ink3()}" stroke-width="1"/>`);
      parts.push(`<text x="${cx}" y="${padTop + rows * boxH + 16}" text-anchor="middle" font-size="9" fill="${P.ink3()}" font-family="JetBrains Mono, monospace">${monthLabel(dstr)}</text>`);
      prevKey = key;
    }
  }

  // Crosshair overlay lines — rendered last so they draw on top. Positioned
  // off-screen initially; a mousemove listener attached below updates their
  // coordinates every frame. Ties into the SVG cursor: crosshair for a full
  // TradingView-style tracking experience.
  parts.push(`<line id="pnf-xhair-v" x1="-10" y1="0" x2="-10" y2="${height}" stroke="${P.accent()}" stroke-width="1" stroke-dasharray="3 3" opacity="0.7" pointer-events="none"/>`);
  parts.push(`<line id="pnf-xhair-h" x1="0" y1="-10" x2="${width}" y2="-10" stroke="${P.accent()}" stroke-width="1" stroke-dasharray="3 3" opacity="0.7" pointer-events="none"/>`);
  parts.push(`<text id="pnf-xhair-t" x="0" y="0" font-size="10" fill="${P.ink()}" font-family="JetBrains Mono, monospace" pointer-events="none"></text>`);
  parts.push("</svg>");
  host.innerHTML = parts.join("");

  // Wire the crosshair
  const svg = host.querySelector("svg");
  const vLine = host.querySelector("#pnf-xhair-v");
  const hLine = host.querySelector("#pnf-xhair-h");
  const tLbl  = host.querySelector("#pnf-xhair-t");
  if (svg && vLine && hLine && tLbl) {
    svg.addEventListener("mousemove", (e) => {
      const pt = svg.createSVGPoint();
      pt.x = e.clientX; pt.y = e.clientY;
      const cursor = pt.matrixTransform(svg.getScreenCTM().inverse());
      const x = Math.max(labelW, Math.min(cursor.x, width - rightW));
      const y = Math.max(padTop, Math.min(cursor.y, padTop + rows * boxH));
      vLine.setAttribute("x1", x); vLine.setAttribute("x2", x);
      hLine.setAttribute("y1", y); hLine.setAttribute("y2", y);
      // Reverse map y → box idx → price for the tooltip label
      const idxFromY = maxIdx - Math.floor((y - padTop) / boxH);
      const priceAtY = priceOfIdx(idxFromY);
      tLbl.setAttribute("x", x + 8);
      tLbl.setAttribute("y", y - 4);
      tLbl.textContent = priceAtY.toFixed(2);
    });
    svg.addEventListener("mouseleave", () => {
      vLine.setAttribute("x1", -10); vLine.setAttribute("x2", -10);
      hLine.setAttribute("y1", -10); hLine.setAttribute("y2", -10);
      tLbl.textContent = "";
    });
  }
}

// ---- Line chart (for BPNYA which is a % series) --------------

export function renderLine(hostId, candles, opts = {}) {
  const P = palette;
  const x = candles.map((c) => c.date);
  const y = candles.map((c) => c.close);
  const traces = [{
    type: "scatter", mode: "lines", x, y,
    line: { color: P.accent(), width: 1.6 },
    fill: "tozeroy", fillcolor: "rgba(91, 155, 255, 0.08)",
    hovertemplate: "%{x|%b %d, %Y}<br>%{y:.2f}" + (opts.unit || "") + "<extra></extra>",
    showlegend: false,
  }];
  const layout = {
    dragmode: "pan",
    margin: { l: 8, r: 60, t: 10, b: 34 },
    paper_bgcolor: P.bg(), plot_bgcolor: P.bg(),
    font: { color: P.ink(), family: "JetBrains Mono, monospace", size: 11 },
    xaxis: {
      gridcolor: P.grid(), zerolinecolor: P.grid(),
      type: "date", tickformat: "%b %d", hoverformat: "%b %d, %Y",
      tickfont: { color: P.ink3() },
      showspikes: true, spikemode: "across+marker", spikecolor: P.accent(),
      spikethickness: 1.2, spikedash: "dot", spikesnap: "cursor",
    },
    yaxis: {
      gridcolor: P.grid(), zerolinecolor: P.grid(), side: "right",
      tickfont: { color: P.ink3() },
      range: opts.yRange || undefined,
      showspikes: true, spikemode: "across+marker", spikecolor: P.accent(),
      spikethickness: 1.2, spikedash: "dot", spikesnap: "cursor",
    },
    hovermode: "x unified",
    hoverlabel: { bgcolor: cssVar("--panel-2", "#111A24"), font: { family: "JetBrains Mono, monospace" } },
  };
  Plotly.newPlot(hostId, traces, layout, { displayModeBar: false, responsive: true, scrollZoom: true });
  const host = document.getElementById(hostId);
  if (host) host.style.cursor = "crosshair";
}
