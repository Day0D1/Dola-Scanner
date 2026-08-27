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

  const traces = [
    // Upper BB (line) — filled band toward lower via fill: tonexty on lower trace.
    {
      type: "scatter", mode: "lines", x, y: bbUpper, name: "BB upper",
      line: { color: palette.accent(), width: 1, dash: "dot" },
      hoverinfo: "skip", showlegend: false, opacity: 0.55,
    },
    {
      type: "scatter", mode: "lines", x, y: bbLower, name: "BB lower",
      line: { color: palette.accent(), width: 1, dash: "dot" },
      fill: "tonexty", fillcolor: "rgba(91, 155, 255, 0.06)",
      hoverinfo: "skip", showlegend: false, opacity: 0.55,
    },
    {
      type: "scatter", mode: "lines", x, y: bbMid, name: "BB mid",
      line: { color: palette.ink3(), width: 1, dash: "dash" },
      hoverinfo: "skip", showlegend: false, opacity: 0.5,
    },
    // Candles
    {
      type: "candlestick", x,
      open: candles.map((c) => c.open),
      high: candles.map((c) => c.high),
      low:  candles.map((c) => c.low),
      close: candles.map((c) => c.close),
      increasing: { line: { color: palette.bull(), width: 1 }, fillcolor: palette.bull() },
      decreasing: { line: { color: palette.bear(), width: 1 }, fillcolor: palette.bear() },
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
      line: { color: palette.accent(), width: 1.5 },
      hovertemplate: "RSI %{y:.1f}<extra></extra>",
      showlegend: false,
    },
  ];

  // Event markers on the price chart (annotations)
  const annotations = [];
  const shapes = [
    // RSI threshold lines
    { type: "line", xref: "paper", x0: 0, x1: 1, yref: "y2",
      y0: rsiLo, y1: rsiLo, line: { color: palette.bull(), width: 1, dash: "dash" }, opacity: 0.5 },
    { type: "line", xref: "paper", x0: 0, x1: 1, yref: "y2",
      y0: rsiHi, y1: rsiHi, line: { color: palette.bear(), width: 1, dash: "dash" }, opacity: 0.5 },
    { type: "line", xref: "paper", x0: 0, x1: 1, yref: "y2",
      y0: 50, y1: 50, line: { color: palette.ink3(), width: 1, dash: "dot" }, opacity: 0.3 },
  ];

  // Optional event markers passed in from the caller (entry alerts, etc.)
  if (opts.events && opts.events.length) {
    for (const ev of opts.events) {
      const color = ev.kind === "ENTRY" ? palette.warn()
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
      showspikes: true, spikemode: "across", spikecolor: palette.accent(),
      spikethickness: 1, spikedash: "dot",
    },
    yaxis: {
      domain: [0.32, 1.0],
      gridcolor: palette.grid(), zerolinecolor: palette.grid(),
      tickformat: ",.2f", side: "right",
      tickfont: { color: palette.ink3() },
      showspikes: true, spikemode: "across", spikecolor: palette.accent(),
      spikethickness: 1, spikedash: "dot",
    },
    xaxis2: { anchor: "y2", matches: "x", showticklabels: false, gridcolor: palette.grid() },
    yaxis2: {
      domain: [0.0, 0.26],
      gridcolor: palette.grid(), zerolinecolor: palette.grid(), range: [0, 100],
      side: "right",
      tickvals: [rsiLo, 50, rsiHi],
      tickfont: { color: palette.ink3() },
      title: { text: `RSI(${settings?.rsi_period ?? 10})`, font: { color: palette.ink3(), size: 10 } },
    },
    shapes, annotations,
  };

  Plotly.newPlot(hostId, traces, layout, {
    displayModeBar: false, responsive: true, scrollZoom: true,
  });
}

// ---- Point & Figure — v2 SVG renderer -------------------------

export function renderPnF(hostId, columns, box, pnfType) {
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

  const boxH = 12;
  const boxW = 12;
  const labelW = 66;
  const rightW = 66;
  const padTop = 12;
  const padBottom = 28;
  const rows = maxIdx - minIdx + 1;
  const height = rows * boxH + padTop + padBottom;
  const width = labelW + columns.length * boxW + rightW + 10;

  const priceOfIdx = (i) => pnfType === "traditional"
    ? i * box
    : Math.exp(i * Math.log(1 + box / 100));

  const P = palette;
  const parts = [];
  parts.push(`<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg" style="display:block; min-width:${width}px; height:${height}px; background:${P.bg()};">`);

  // Grid lines + prices on both sides
  const step = Math.max(1, Math.floor(rows / 12));
  for (let idx = minIdx; idx <= maxIdx; idx += step) {
    const y = padTop + (maxIdx - idx) * boxH + boxH / 2;
    parts.push(`<line x1="${labelW}" y1="${y}" x2="${width - rightW}" y2="${y}" stroke="${P.grid()}" stroke-width="1"/>`);
    const priceStr = priceOfIdx(idx).toFixed(2);
    parts.push(`<text x="${labelW - 6}" y="${y + 3}" text-anchor="end" font-size="10" fill="${P.ink3()}" font-family="JetBrains Mono, monospace">${priceStr}</text>`);
    parts.push(`<text x="${width - rightW + 6}" y="${y + 3}" text-anchor="start" font-size="10" fill="${P.ink3()}" font-family="JetBrains Mono, monospace">${priceStr}</text>`);
  }

  // X/O columns
  for (let ci = 0; ci < columns.length; ci++) {
    const c = columns[ci];
    const cx = labelW + ci * boxW + boxW / 2;
    const isCurrent = ci === columns.length - 1;
    for (let idx = c.bottom_idx; idx <= c.top_idx; idx++) {
      const cy = padTop + (maxIdx - idx) * boxH + boxH / 2;
      if (c.type === "X") {
        const color = P.bull();
        parts.push(`<line x1="${cx - 3.5}" y1="${cy - 3.5}" x2="${cx + 3.5}" y2="${cy + 3.5}" stroke="${color}" stroke-width="1.6"/>`);
        parts.push(`<line x1="${cx - 3.5}" y1="${cy + 3.5}" x2="${cx + 3.5}" y2="${cy - 3.5}" stroke="${color}" stroke-width="1.6"/>`);
      } else {
        parts.push(`<circle cx="${cx}" cy="${cy}" r="3.5" fill="none" stroke="${P.bear()}" stroke-width="1.6"/>`);
      }
    }
    if (isCurrent) {
      const topY = padTop + (maxIdx - c.top_idx) * boxH;
      const botY = padTop + (maxIdx - c.bottom_idx) * boxH + boxH;
      parts.push(`<rect x="${cx - boxW / 2 + 1}" y="${topY}" width="${boxW - 2}" height="${botY - topY}" fill="none" stroke="${P.warn()}" stroke-width="1" stroke-dasharray="2 2" opacity="0.75"/>`);
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

  parts.push("</svg>");
  host.innerHTML = parts.join("");
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
    },
    yaxis: {
      gridcolor: P.grid(), zerolinecolor: P.grid(), side: "right",
      tickfont: { color: P.ink3() },
      range: opts.yRange || undefined,
    },
    hovermode: "x unified",
    hoverlabel: { bgcolor: cssVar("--panel-2", "#111A24"), font: { family: "JetBrains Mono, monospace" } },
  };
  Plotly.newPlot(hostId, traces, layout, { displayModeBar: false, responsive: true, scrollZoom: true });
}
