// Dola v2 — reusable component builders (vanilla, no framework).
// Each helper returns an HTMLElement so callers can compose freely.

import { el, fmt, signClass, colClass } from "./shared.js";

// ---- Badges ----------------------------------------------------

// A small semantic label. Kind: "bull" | "bear" | "warn" | "accent" | "neutral" | "solid-bull" | "solid-bear".
export function badge(text, kind = "neutral") {
  const cls = kind.startsWith("solid-") ? `badge solid ${kind.slice(6)}` : `badge ${kind === "neutral" ? "" : kind}`.trim();
  return el("span", { class: cls }, text);
}

// ★ Major Watchlist marker.
export const majorTag = () => el("span", { class: "tag-star", title: "Major Watchlist" }, "★");

// 🔥 IBD 50 marker.
export const ibdTag = () => el("span", { class: "tag-fire", title: "IBD 50" }, "🔥");

// P&F column marker: X (bull) or O (bear). Empty span if col is null.
export function colMarker(col) {
  if (!col) return el("span", { class: "col-marker" }, "–");
  return el("span", { class: `col-marker ${colClass(col)}` }, col);
}

// Setup badge (ELON / MUSK).
export function setupBadge(setup) {
  if (setup === "ELON") return badge("ELON", "bull");
  if (setup === "MUSK") return badge("MUSK", "bear");
  return el("span");
}

// Action badge (SELL PUTS / SELL CALLS).
export function actionBadge(direction) {
  if (direction === "SELL_PUTS") return badge("SELL PUTS", "solid-bull");
  if (direction === "SELL_CALLS") return badge("SELL CALLS", "solid-bear");
  return el("span");
}

// Risk badge with warn/bull/bear/neutral coloring.
export function riskBadge(risk) {
  if (!risk) return badge("–", "neutral");
  const kind = risk === "LOW" ? "bull" : risk === "MEDIUM" ? "warn" : risk === "HIGH" ? "bear" : "neutral";
  return badge(risk, kind);
}

// Ticker cell with optional badges before the symbol.
export function tickerCell(sig) {
  const parts = [];
  if (sig.on_watchlist) parts.push(majorTag());
  if (sig.on_ibd50)     parts.push(ibdTag());
  parts.push(el("span", { class: "mono", style: { fontWeight: 700 } }, sig.ticker));
  return el("span", { class: "row-2" }, ...parts);
}

// ---- Market pillar card (SPX / BPNYA / VIX) --------------------
// pillar: { column, level, change, signal } from breadth payload
// opts:   { label, sublabel, unit, kind: "spx" | "bpnya" | "vix" }
// Clicking navigates to the index's chart page.
export function pillarCard(pillar, opts) {
  const p = pillar || {};
  const change = p.change;
  const changeCls = signClass(change);
  const state = p.column === "X" ? "state-bull" : (p.column === "O" ? "state-bear" : "");
  const level = p.level == null ? "–" : (opts.unit === "%" ? Number(p.level).toFixed(2) + "%" : Number(p.level).toFixed(2));
  const chgText = change == null
    ? "–"
    : (opts.kind === "spx" ? `${fmt.boxes(change)} boxes` : (opts.unit === "%" ? `${fmt.change(change)}%` : fmt.change(change)));
  const key = (opts.kind || "").toUpperCase();  // SPX / BPNYA / VIX
  return el("div", {
      class: `card clickable ${state}`,
      dataset: { pillar: opts.kind },
      title: `Open ${opts.label} chart`,
      onClick: () => { if (key) window.location.href = `/v2/charts/${key}`; },
    },
    el("div", { class: "row-flex row-between" },
      el("div", { class: "stack-1" },
        el("div", { class: "card-label" }, opts.label),
        el("div", { class: "text-ink-3", style: { fontSize: "var(--fs-xs)", marginTop: "2px" } }, opts.sublabel || ""),
      ),
      colMarker(p.column),
    ),
    el("div", { class: "card-value small mono" }, level),
    el("div", { class: "card-sub" },
      el("span", { class: `mono ${changeCls}` }, chgText),
      el("span", { class: "text-ink-3" }, "•"),
      el("span", { class: "text-ink-3 mono" }, `P&F ${p.column || "–"}`),
    ),
  );
}

// ---- Regime card ----------------------------------------------
// breadth: full breadth object with .regime and .spx
// Regime is derived from SPX P&F, so clicking opens the SPX chart.
export function regimeCard(breadth) {
  const b = breadth || {};
  const regime = b.regime || "–";
  const kind = regime === "BUY" ? "bull" : regime === "SELL" ? "bear" : "neutral";
  const state = kind === "bull" ? "state-bull" : kind === "bear" ? "state-bear" : "";
  const spxCol = b.spx?.column || "–";
  const boxes = b.spx?.change == null ? "–" : `${fmt.boxes(b.spx.change)} boxes`;
  return el("div", {
      class: `card clickable ${state}`,
      title: "Open SPX chart (regime is derived from SPX P&F)",
      onClick: () => { window.location.href = "/v2/charts/SPX"; },
    },
    el("div", { class: "card-label" }, "Regime"),
    el("div", { class: `card-value value-${kind === "bull" ? "bull" : kind === "bear" ? "bear" : "ink"}` }, regime),
    el("div", { class: "card-sub" },
      el("span", { class: "text-ink-3" }, "SPX P&F"),
      colMarker(spxCol),
      el("span", { class: "mono text-ink-2" }, boxes),
    ),
  );
}

// ---- Risk card ------------------------------------------------
// Risk is a function of regime × BPNYA × VIX. Clicking opens the SPX Time
// Series in Visual Mode where the historical Regime + Risk strips live.
export function riskCard(breadth) {
  const b = breadth || {};
  const risk = b.risk || "–";
  const kind = risk === "LOW" ? "bull" : risk === "MEDIUM" ? "warn" : risk === "HIGH" ? "bear" : "neutral";
  const state = kind === "bull" ? "state-bull" : kind === "bear" ? "state-bear" : kind === "warn" ? "state-warn" : "";
  const bp = b.bpnya?.column || "–";
  const vx = b.vix?.column || "–";
  return el("div", {
      class: `card clickable ${state}`,
      title: "Open SPX Time Series — Visual Mode shows the Regime + Risk history strips",
      onClick: () => { window.location.href = "/v2/time-series/SPX"; },
    },
    el("div", { class: "card-label" }, "Risk"),
    el("div", { class: `card-value value-${kind === "bull" ? "bull" : kind === "bear" ? "bear" : kind === "warn" ? "warn" : "ink"}` }, risk),
    el("div", { class: "card-sub" },
      el("span", { class: "text-ink-3" }, "BPNYA"),
      colMarker(bp),
      el("span", { class: "text-ink-3" }, "•"),
      el("span", { class: "text-ink-3" }, "VIX"),
      colMarker(vx),
    ),
  );
}

// ---- Signal table row -----------------------------------------
// A single row for the Latest Signals feed.
// entry: { fired_at, ticker, direction, pnf_column, rsi, trigger_price, on_watchlist, on_ibd50 }
export function signalRow(entry, onClick) {
  const time = entry.fired_at ? fmt.timeAgo(entry.fired_at) : "–";
  const strikeTxt = entry.strike_price == null ? "–" : `$${Number(entry.strike_price).toFixed(0)}`;
  return el("tr", { onclick: () => onClick?.(entry.ticker), style: { cursor: onClick ? "pointer" : "" } },
    el("td", { class: "mono text-ink-3" }, time),
    el("td", { class: "ticker" }, tickerCell(entry)),
    el("td", {}, setupBadge(entry.candidate || (entry.direction === "SELL_PUTS" ? "ELON" : "MUSK"))),
    el("td", {}, actionBadge(entry.direction)),
    el("td", { class: "num mono text-warn", style: { fontWeight: 700 } }, strikeTxt),
    el("td", { class: "num" }, colMarker(entry.pnf_column)),
    el("td", { class: "num mono" }, fmt.num(entry.rsi, 1)),
    el("td", { class: "num mono text-ink" }, fmt.price(entry.trigger_price ?? entry.last_close)),
  );
}

// ---- Empty state ----------------------------------------------
export function emptyState(text, sub = "") {
  return el("div", { class: "stack-1", style: { padding: "var(--sp-6)", textAlign: "center", color: "var(--ink-3)" } },
    el("div", {}, text),
    sub ? el("div", { style: { fontSize: "var(--fs-xs)", color: "var(--ink-4)" } }, sub) : null,
  );
}

// ---- Loading skeleton ----------------------------------------
export function skeletonRow(cols = 7) {
  const tr = el("tr");
  for (let i = 0; i < cols; i++) {
    tr.appendChild(el("td", {}, el("div", { class: "skeleton", style: { height: "12px", width: `${40 + Math.random() * 40}%` } })));
  }
  return tr;
}
