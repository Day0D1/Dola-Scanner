// Dola v2 — shared utilities across all pages.
// Kept intentionally small: element helpers, formatters, API wrappers,
// market-status ticker. No framework, no build step.

export const $ = (id) => document.getElementById(id);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "html") node.innerHTML = v;
    else if (k === "dataset") for (const [dk, dv] of Object.entries(v)) node.dataset[dk] = dv;
    else if (v === true) node.setAttribute(k, "");
    else node.setAttribute(k, v);
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

// ---- Formatting ------------------------------------------------

export const fmt = {
  price(v, digits = 2) {
    if (v == null || Number.isNaN(v)) return "–";
    return "$" + Number(v).toFixed(digits);
  },
  num(v, digits = 2) {
    if (v == null || Number.isNaN(v)) return "–";
    return Number(v).toFixed(digits);
  },
  pct(v, digits = 2) {
    if (v == null || Number.isNaN(v)) return "–";
    const n = Number(v);
    const sign = n > 0 ? "+" : "";
    return `${sign}${n.toFixed(digits)}%`;
  },
  change(v, digits = 2) {
    if (v == null || Number.isNaN(v)) return "–";
    const n = Number(v);
    return (n > 0 ? "+" : "") + n.toFixed(digits);
  },
  boxes(v) {
    if (v == null) return "–";
    const n = Number(v);
    return (n > 0 ? "+" : "") + n;
  },
  clock(d = new Date(), tz = "America/New_York") {
    return d.toLocaleTimeString("en-US", {
      timeZone: tz, hour: "numeric", minute: "2-digit", hour12: true,
    }) + " ET";
  },
  date(iso) {
    if (!iso) return "";
    const p = String(iso).split("-");
    if (p.length !== 3) return iso;
    return `${p[1]}/${p[2]}/${p[0]}`;
  },
  timeAgo(ts) {
    if (!ts) return "–";
    // Accepts: Date, ISO/RFC string, unix seconds, unix ms. Auto-detects.
    let then;
    if (ts instanceof Date) then = ts;
    else if (typeof ts === "string") then = new Date(ts);
    else if (typeof ts === "number") then = new Date(ts > 1e12 ? ts : ts * 1000);
    else return "–";
    const ms = then.getTime();
    if (Number.isNaN(ms)) return "–";
    const s = Math.max(0, Math.floor((Date.now() - ms) / 1000));
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
  },
};

// Return a semantic CSS class for a signed change value (pos/neg).
export const signClass = (v) => v == null ? "" : (v > 0 ? "text-bull" : (v < 0 ? "text-bear" : ""));

// Map a P&F column letter to a CSS variant.
export const colClass = (c) => c === "X" ? "x" : (c === "O" ? "o" : "");

// ---- API wrappers ---------------------------------------------

async function j(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let err = "";
    try { err = (await r.json()).error || ""; } catch {}
    throw new Error(err || `HTTP ${r.status}`);
  }
  return r.json();
}

export const api = {
  scan: () => j("/api/scan"),
  refresh: (notify = false) => j(`/api/scan/refresh?notify=${notify}`, { method: "POST" }),
  schedule: () => j("/api/schedule"),
  ibd50: () => j("/api/watchlists/ibd50"),
  ibd50Refresh: () => j("/api/watchlists/ibd50/refresh", { method: "POST" }),
  marketStatus: () => j("/api/market/status"),
  recentEntries: (limit = 20) => j(`/api/entries/recent?limit=${limit}`),
  universeSummary: () => j("/api/universe/summary"),
  history: (limit = 90) => j(`/api/history?limit=${limit}`),
};

// ---- Market status ticker -------------------------------------
// The topbar pill needs (a) the label + color for market state and
// (b) a clock that ticks every second. We poll /api/market/status
// only every 60s; the clock updates locally between polls.

export function mountMarketPill(root) {
  if (!root) return;
  const dot   = el("span", { class: "dot" });
  const label = el("span", {}, "…");
  const clock = el("span", { class: "clock mono" }, "");
  root.replaceChildren(dot, label, clock);

  async function refreshStatus() {
    try {
      const s = await api.marketStatus();
      root.className = `market-pill ${(s.css_class || "closed")}`;
      label.textContent = s.label || "";
    } catch {
      root.className = "market-pill closed";
      label.textContent = "Status unknown";
    }
  }
  function tickClock() { clock.textContent = fmt.clock(); }

  refreshStatus();
  tickClock();
  setInterval(refreshStatus, 60_000);
  setInterval(tickClock, 1_000);
}

// ---- Sidebar collapse -----------------------------------------

export function wireSidebar() {
  const app = document.querySelector(".app");
  const toggle = document.querySelector("[data-sidebar-toggle]");
  const sidebar = document.querySelector(".sidebar");
  if (!app || !toggle) return;
  toggle.addEventListener("click", () => {
    if (window.innerWidth <= 768) sidebar?.classList.toggle("open");
    else app.classList.toggle("collapsed");
  });
  // Close mobile drawer when clicking outside
  document.addEventListener("click", (e) => {
    if (window.innerWidth > 768) return;
    if (!sidebar?.classList.contains("open")) return;
    if (sidebar.contains(e.target) || toggle.contains(e.target)) return;
    sidebar.classList.remove("open");
  });
}
