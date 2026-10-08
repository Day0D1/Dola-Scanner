/* BigSam Alerts dashboard — vanilla JS SPA (hash routing, no build step). */
(() => {
  "use strict";

  const $ = (sel, el = document) => el.querySelector(sel);
  const app = $("#app");
  const state = { user: null, charts: [], timer: null, setupsQuery: { decision: "APPROVED", status: "", pair: "", timeframe: "", direction: "", offset: 0 } };

  // ------------------------------------------------------------------ utils
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const dec = (pair) => (pair && pair.endsWith("JPY") ? 3 : 5);
  const px = (v, pair) => (v == null ? "—" : Number(v).toFixed(dec(pair)));
  const num = (v, d = 2) => (v == null ? "—" : Number(v).toFixed(d));
  const money = (v) => (v == null ? "—" : (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 }));
  const signR = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}R`);
  const cls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
  const title = (s) => String(s || "").replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
  const fmtTime = (ts, withDate = true) => {
    if (!ts) return "—";
    const d = new Date(ts * 1000);
    const t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return withDate ? `${d.toLocaleDateString([], { day: "2-digit", month: "short" })} ${t}` : t;
  };
  const ago = (ts) => {
    if (!ts) return "never";
    const s = Math.floor(Date.now() / 1000 - ts);
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
  };
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function toast(msg, ms = 3200) {
    const t = $("#toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(t._h);
    t._h = setTimeout(() => (t.hidden = true), ms);
  }

  async function api(path, opts = {}) {
    // relative to the page, so the app works at "/" and when mounted under a path ("/bigsamalerts/")
    const res = await fetch(path.replace(/^\//, ""), {
      method: opts.method || "GET",
      headers: opts.body ? { "Content-Type": "application/json" } : {},
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      credentials: "same-origin",
    });
    if (res.status === 401 && !path.endsWith("/login")) {
      state.user = null;
      location.hash = "#/login";
      throw new Error("not authenticated");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText);
    return data;
  }

  function destroyCharts() {
    state.charts.forEach((c) => { try { c.destroy ? c.destroy() : c.remove(); } catch (_) {} });
    state.charts = [];
  }

  // ------------------------------------------------------------------ icons
  const icon = {
    wallet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 7H5a2 2 0 0 1 0-4h13v4"/><path d="M3 5v14a2 2 0 0 0 2 2h15V7"/><circle cx="16" cy="14" r="1.5"/></svg>',
    dash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 13h8V3H3zM13 21h8v-8h-8zM13 3v6h8V3zM3 21h8v-4H3z"/></svg>',
    list: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>',
    chart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/></svg>',
    flask: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3"/></svg>',
    cog: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
    logo: '<svg width="16" height="16" viewBox="0 0 32 32"><path d="M5 23l7-8 5 4 10-12" stroke="#f5b942" stroke-width="3.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };

  // ------------------------------------------------------------------ layout
  function shell(active, content) {
    const link = (href, key, label, ic) => `<a href="${href}" class="${active === key ? "active" : ""}">${ic}<span>${label}</span></a>`;
    app.innerHTML = `
      <div class="shell">
        <aside class="side">
          <div class="brand"><div class="brand-mark">${icon.logo}</div><div>BigSam<small>Market structure</small></div></div>
          <nav class="nav">
            ${link("#/", "dash", "Dashboard", icon.dash)}
            ${link("#/history", "history", "History", icon.wallet)}
            ${link("#/setups", "setups", "Setups", icon.list)}
            ${link("#/analytics", "analytics", "Analytics", icon.chart)}
            ${link("#/backtests", "backtests", "Backtests", icon.flask)}
            ${link("#/settings", "settings", "Settings", icon.cog)}
          </nav>
          <div class="side-foot">
            <span>Signed in as <b>${esc(state.user)}</b></span>
            <button class="btn sm" id="logout">Sign out</button>
          </div>
        </aside>
        <main class="main" id="main">${content}</main>
      </div>`;
    const lo = $("#logout");
    if (lo) lo.onclick = async () => { await api("/api/logout", { method: "POST" }); state.user = null; location.hash = "#/login"; };
  }

  const loading = '<div class="empty"><span class="spin"></span></div>';

  // ------------------------------------------------------------------ login
  function renderLogin() {
    app.innerHTML = `
      <div class="login-wrap">
        <form class="panel login" id="loginForm" autocomplete="on">
          <div class="brand" style="padding:0"><div class="brand-mark">${icon.logo}</div><div>BigSam Alerts<small>Market structure scanner</small></div></div>
          <div><h1>Sign in</h1><p>Use the admin credentials from your <span class="mono">.env</span> file.</p></div>
          <label class="field">Username<input type="text" name="username" autocomplete="username" required autofocus></label>
          <label class="field">Password<input type="password" name="password" autocomplete="current-password" required></label>
          <div class="error" id="loginErr"></div>
          <button class="btn primary" type="submit">Sign in</button>
        </form>
      </div>`;
    $("#loginForm").onsubmit = async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      $("#loginErr").textContent = "";
      try {
        const r = await api("/api/login", { method: "POST", body: { username: f.get("username"), password: f.get("password") } });
        state.user = r.username;
        location.hash = "#/";
      } catch (err) {
        $("#loginErr").textContent = err.message;
      }
    };
  }

  // ------------------------------------------------------------------ shared bits
  function kpiStrip(s, extra = {}) {
    const pf = s.profit_factor == null ? "—" : num(s.profit_factor);
    return `
      <div class="kpis">
        <div class="kpi"><div class="label">Net result</div><div class="value ${cls(s.net_r)}">${signR(s.net_r)}</div><div class="sub">${money(s.net_pnl)}</div></div>
        <div class="kpi"><div class="label">Win rate</div><div class="value">${num(s.win_rate, 1)}%</div><div class="sub">${s.partial_wins ? `${s.full_wins} full + ${s.partial_wins} partial` : `${s.wins}W`} · ${s.losses}L</div></div>
        <div class="kpi"><div class="label">Expectancy</div><div class="value ${cls(s.expectancy_r)}">${signR(s.expectancy_r)}</div><div class="sub">per trade</div></div>
        <div class="kpi"><div class="label">Profit factor</div><div class="value">${pf}</div><div class="sub">gross win / loss</div></div>
        <div class="kpi"><div class="label">Trades</div><div class="value">${s.trades}</div><div class="sub">${extra.tradesSub || "resolved"}</div></div>
        <div class="kpi"><div class="label">Max drawdown</div><div class="value neg">${num(s.max_drawdown_r, 1)}R</div><div class="sub">worst streak ${s.max_loss_streak}L</div></div>
      </div>`;
  }

  function statusBadge(s) {
    return `<span class="badge ${esc(s)}">${esc(title(s))}</span>`;
  }
  function dirBadge(d) {
    return `<span class="badge ${d === "BUY" ? "buy" : "sell"}">${esc(d)}</span>`;
  }

  function setupsTable(items, opts = {}) {
    if (!items.length) return `<div class="empty">${opts.empty || "Nothing here yet."}</div>`;
    const rows = items.map((s) => `
      <tr data-id="${s.id}">
        <td>${fmtTime(s.detected_bar_time)}</td>
        <td><b>${esc(s.pair)}</b> <span class="dim">${esc(s.timeframe)}</span></td>
        <td>${dirBadge(s.direction)}</td>
        <td>${statusBadge(s.status)}</td>
        ${opts.reason ? `<td class="reason">${esc(s.reject_reason || "")}</td>` : `
        <td>${esc(title(s.poi_type))}</td>
        <td class="num">${px(s.entry, s.pair)}</td>
        <td class="num">${px(s.stop_loss, s.pair)}</td>
        <td class="num">${px(s.take_profit, s.pair)}</td>
        <td class="num">${s.lots == null ? "—" : num(s.lots)}</td>`}
        <td class="num ${cls(s.result_r)}">${s.result_r == null ? "" : signR(s.result_r)}</td>
      </tr>`).join("");
    return `
      <div class="table-wrap"><table>
        <thead><tr><th>Detected</th><th>Pair</th><th>Side</th><th>Status</th>
        ${opts.reason ? "<th>Reason</th>" : '<th>POI</th><th class="num">Entry</th><th class="num">Stop</th><th class="num">Target</th><th class="num">Lots</th>'}
        <th class="num">Result</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>`;
  }

  function bindRows(root = document) {
    root.querySelectorAll("tr[data-id]").forEach((tr) => (tr.onclick = () => (location.hash = `#/setup/${tr.dataset.id}`)));
  }

  function chartDefaults() {
    Chart.defaults.color = css("--muted");
    Chart.defaults.font.family = css("--sans");
    Chart.defaults.borderColor = css("--line");
  }

  function equityChart(canvas, equity) {
    chartDefaults();
    const pts = [{ x: "Start", y: 0 }, ...equity.map((e, i) => ({ x: `#${i + 1}`, y: e.r }))];
    const c = new Chart(canvas, {
      type: "line",
      data: {
        labels: pts.map((p) => p.x),
        datasets: [{
          data: pts.map((p) => p.y), borderColor: css("--accent"), borderWidth: 2, pointRadius: 0, tension: 0,
          fill: { target: "origin", above: "rgba(46,194,126,.10)", below: "rgba(240,84,92,.10)" },
        }],
      },
      options: {
        maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const e = equity[ctx.dataIndex - 1];
                return e ? `${signR(e.r)} cumulative · ${e.pair} ${signR(e.result)} · ${money(e.balance)}` : "Start";
              },
            },
          },
        },
        scales: {
          x: { ticks: { maxTicksLimit: 8 }, grid: { display: false } },
          y: { ticks: { callback: (v) => `${v}R` }, grid: { color: css("--line") } },
        },
      },
    });
    state.charts.push(c);
  }

  function barChart(canvas, rows, valueKey = "net_r", labelKey = "key", horizontal = true) {
    chartDefaults();
    const c = new Chart(canvas, {
      type: "bar",
      data: {
        labels: rows.map((r) => r[labelKey]),
        datasets: [{
          data: rows.map((r) => r[valueKey]),
          backgroundColor: rows.map((r) => (r[valueKey] >= 0 ? css("--buy") : css("--sell"))),
          borderRadius: 3, maxBarThickness: 22,
        }],
      },
      options: {
        indexAxis: horizontal ? "y" : "x", maintainAspectRatio: false, animation: false,
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (ctx) => { const r = rows[ctx.dataIndex]; return valueKey === "net_r" ? `${signR(r.net_r)} · ${r.n} trades · ${r.win_rate}% WR` : `${ctx.raw}`; } } } },
        scales: { x: { grid: { color: css("--line") } }, y: { grid: { display: false } } },
      },
    });
    state.charts.push(c);
  }

  function groupTable(rows) {
    if (!rows.length) return '<div class="empty">No resolved trades yet.</div>';
    return `<div class="table-wrap"><table><thead><tr><th>Group</th><th class="num">Trades</th><th class="num">Win %</th><th class="num">Net</th><th class="num">Exp.</th></tr></thead><tbody>
      ${rows.map((r) => `<tr style="cursor:default"><td>${esc(r.key)}</td><td class="num">${r.n}</td><td class="num">${num(r.win_rate, 1)}</td><td class="num ${cls(r.net_r)}">${signR(r.net_r)}</td><td class="num ${cls(r.expectancy)}">${signR(r.expectancy)}</td></tr>`).join("")}
    </tbody></table></div>`;
  }

  function meter(label, used, limit, sub, invert = false) {
    const pct = limit > 0 ? Math.max(0, Math.min(100, (100 * used) / limit)) : 0;
    const color = invert ? (pct >= 100 ? "var(--buy)" : "var(--info)") : pct >= 80 ? "var(--sell)" : pct >= 50 ? "var(--warn)" : "var(--buy)";
    return `<div class="meter"><div class="meter-top"><span>${label}</span><b class="mono">${sub}</b></div>
      <div class="track"><div class="fill" style="width:${pct}%;background:${color}"></div></div></div>`;
  }

  function propPanel(p) {
    const roomUsed = Math.max(0, p.high_water - p.equity);
    const roomMax = p.high_water - p.max_loss_floor;
    const cons = p.consistency_pct == null ? "—" : `${p.consistency_pct}%`;
    const el = p.eligibility || {};
    const tick = (ok) => `<span class="${ok ? "pos" : "dim"}">${ok ? "✓" : "○"}</span>`;
    return `<div class="panel" style="margin-bottom:16px"><div class="panel-head"><h2>Prop firm · ${esc(p.firm)}</h2>
      <span class="actions"><span class="badge ${p.payout_eligible ? "WIN" : ""}">${p.payout_eligible ? "Payout eligible" : "Payout not yet"}</span>
      <button class="btn sm" id="propPayout">Record payout</button><button class="btn sm" id="propReset">Start tracking now</button></span></div>
      <div class="panel-body"><div class="prop-grid">
        ${meter("Floating loss (closes account at limit)", -p.floating_now, p.floating_limit, `${money(p.floating_now)} / −${money(p.floating_limit)}`)}
        ${meter("Today's loss (resets 5 PM New York)", p.daily_used, p.daily_limit, `${money(-p.daily_used)} / −${money(p.daily_limit)}`)}
        ${meter("Trailing max loss", roomUsed, roomMax, `floor ${money(p.max_loss_floor)} · room ${money(p.room_to_floor)}`)}
        ${meter("Valid days (≥ " + money(p.valid_day_min) + " profit)", p.valid_days, p.valid_days_needed, `${p.valid_days} / ${p.valid_days_needed}`, true)}
        ${meter("Reward cycle", p.days_since_payout, p.payout_days, `${p.days_since_payout} / ${p.payout_days} days`, true)}
        <div class="meter"><div class="meter-top"><span>Consistency (best day ≤ ${p.consistency_max}%)</span><b class="mono">${cons}</b></div>
          <div class="note">Best day ${money(p.best_day)} of ${money(p.period_profit)} profit${p.min_profit_for_payout ? ` · need ≥ ${money(p.min_profit_for_payout)} total` : ""}</div></div>
      </div>
      <div class="statusbar" style="padding:10px 0 0;margin:0">
        <span>Equity ≈ <b>${money(p.equity)}</b></span><span>Risk/trade <b>${money(p.risk_per_trade)}</b> · max ${p.max_open} open</span>
        <span>Payout checks: ${tick(el.valid_days)} valid days ${tick(el.consistency)} consistency ${tick(el.cycle)} 14-day cycle ${tick(el.in_profit)} in profit</span>
        <span>Your share (90%) ≈ <b>${money(p.your_share)}</b></span>
      </div>
      <p class="note" style="margin:8px 0 0">Estimated from the alerts the system tracked, assuming you took every alert at the suggested size. Your broker dashboard is the source of truth.</p>
      </div></div>`;
  }

  // ------------------------------------------------------------------ dashboard
  async function renderDashboard() {
    shell("dash", `<div class="page-head"><div><h1>Dashboard</h1><p>Live scanner state, open setups and performance.</p></div>
      <div class="actions"><button class="btn" id="scanNow">Scan now</button></div></div><div id="dashBody">${loading}</div>`);
    $("#scanNow").onclick = async () => {
      try { const r = await api("/api/scan", { method: "POST" }); toast(r.started ? "Scan started — results in about a minute" : r.message); } catch (e) { toast(e.message); }
    };
    await loadDashboard();
    state.timer = setInterval(loadDashboard, 30000);
  }

  async function loadDashboard() {
    const body = $("#dashBody");
    if (!body) return;
    let d;
    try { d = await api("/api/overview"); } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    destroyCharts();
    const sc = d.scanner || {};
    const batchTrades = d.equity.slice(d.equity.length - d.current_batch.trades);
    const dots = Array.from({ length: 10 }, (_, i) => {
      const t = batchTrades[i];
      return `<i class="${t ? (t.result > 0 ? "win" : "loss") : ""}"></i>`;
    }).join("");
    const auditCls = d.audit.state === "AUDIT" ? "AUDIT" : "ACTIVE";
    body.innerHTML = `
      ${kpiStrip(d.summary, { tradesSub: `${d.funnel.pending} pending · ${d.funnel.active} active` })}
      <div class="panel statusbar">
        <span>${sc.running ? '<span class="spin"></span> <b>Scanning…</b>' : `Last scan <b>${ago(sc.last_run)}</b>`}${sc.last_duration ? ` <span class="dim">(${sc.last_duration}s)</span>` : ""}</span>
        <span>Next <b>${sc.next_run ? fmtTime(sc.next_run, false) : "—"}</b></span>
        <span>${(sc.pairs || []).length} pairs · <b>${(sc.timeframes || []).join(" / ")}</b></span>
        <span>System <span class="badge ${auditCls}">${esc(d.audit.state)}</span> <span class="dim">loss streak ${d.audit.loss_streak}/10</span></span>
        <span>Telegram <b class="${d.telegram ? "pos" : "neg"}">${d.telegram ? "connected" : "not configured"}</b></span>
        <span>Risk/trade <b>${money(d.account.risk_per_trade)}</b></span>
        <span>Batch ${d.current_batch.index} <span class="batch">${dots}</span></span>
        <span>24h <b>${d.last24h.candidates}</b> candidates · <b>${d.last24h.approved}</b> approved</span>
      </div>
      ${sc.last_error ? `<div class="panel statusbar" style="color:var(--warn)">Scanner warning: ${esc(sc.last_error)}</div>` : ""}
      ${d.prop && d.prop.enabled ? propPanel(d.prop) : ""}
      <div class="grid g-main">
        <div class="stack">
          <div class="panel"><div class="panel-head"><h2>Equity curve (R)</h2><span class="note">live resolved trades</span></div>
            <div class="panel-body"><div class="chart-box">${d.equity.length ? '<canvas id="eq"></canvas>' : '<div class="empty">No resolved live trades yet. Approved setups appear below as soon as the scanner finds them.</div>'}</div></div></div>
        </div>
        <div class="stack">
          <div class="panel"><div class="panel-head"><h2>Confirmation watch</h2><span class="note">HTF POI taps</span></div>
            <div class="panel-body">${d.watches.length ? d.watches.map((w) => `
              <a class="feed-item" href="#/setup/${w.id}" style="grid-template-columns:1fr auto"><span>${dirBadge(w.direction)} <b>${esc(w.pair)}</b> <span class="dim">${esc(w.htf)} zone ${px(Math.min(w.entry, w.stop_loss), w.pair)}–${px(Math.max(w.entry, w.stop_loss), w.pair)}</span></span><time>${ago(w.detected_at)}</time></a>`).join("") : '<div class="note">No HTF POI taps yet. When price enters an unmitigated 4h/Daily POI you will be told to wait for LTF confirmation.</div>'}</div></div>
          <div class="panel"><div class="panel-head"><h2>Activity</h2></div>
            <div class="panel-body"><div class="feed">${d.events.length ? d.events.map((e) => `
              <div class="feed-item"><time>${fmtTime(e.ts, false)}</time><div><span class="k ${["WIN", "PARTIAL", "PARTIAL_WIN"].includes(e.kind) ? "pos" : e.kind === "LOSS" || e.kind === "AUDIT" ? "neg" : "muted"}">${esc(e.kind)}</span>${e.pair ? `<a href="#/setup/${e.setup_id}"><b>${esc(e.pair)}</b> ${esc(e.timeframe || "")} ${esc(e.direction || "")}</a> · ` : ""}<span class="muted">${esc(e.message)}</span></div></div>`).join("") : '<div class="note">Events appear here as setups are approved, filled and closed.</div>'}</div></div></div>
        </div>
      </div>
      <div class="panel" style="margin-top:16px"><div class="panel-head"><h2>Open setups</h2><a class="btn sm" href="#/setups">All setups</a></div>
        <div class="panel-body" style="padding-top:8px">${setupsTable(d.open, { empty: "No pending limit orders or active trades." })}</div></div>`;
    if (d.equity.length) equityChart($("#eq"), d.equity);
    bindRows(body);
    const pay = $("#propPayout"), rst = $("#propReset");
    if (pay) pay.onclick = async () => { if (!confirm("Record that you received a payout? This starts a new reward cycle.")) return; await api("/api/prop/payout", { method: "POST" }); toast("Payout recorded — new cycle started"); loadDashboard(); };
    if (rst) rst.onclick = async () => { if (!confirm("Start prop tracking from now? Earlier trades will be ignored by the tracker.")) return; await api("/api/prop/reset", { method: "POST" }); toast("Prop tracking restarted"); loadDashboard(); };
  }

  // ------------------------------------------------------------------ setups list
  async function renderSetups() {
    const q = state.setupsQuery;
    const opt = (vals, cur) => vals.map(([v, l]) => `<option value="${v}" ${v === cur ? "selected" : ""}>${l}</option>`).join("");
    shell("setups", `
      <div class="page-head"><div><h1>Setups</h1><p>Every candidate the engine evaluated — approved and rejected, with the reason.</p></div></div>
      <div class="filters">
        <select id="fDecision">${opt([["", "All decisions"], ["APPROVED", "Approved"], ["REJECTED", "Rejected"], ["WATCH", "Confirmation watch"]], q.decision)}</select>
        <select id="fStatus">${opt([["", "Any status"], ["PENDING", "Pending"], ["TRIGGERED", "Active"], ["WIN", "Win"], ["PARTIAL_WIN", "Partial win"], ["LOSS", "Loss"], ["SKIPPED", "Skipped (prop rules)"], ["MISSED", "Missed"], ["EXPIRED", "Expired"]], q.status)}</select>
        <input type="text" id="fPair" placeholder="Pair e.g. EURUSD" value="${esc(q.pair)}" style="width:150px">
        <select id="fTf">${opt([["", "All TFs"], ["5m", "5m"], ["15m", "15m"], ["1h", "1h"]], q.timeframe)}</select>
        <select id="fDir">${opt([["", "Both sides"], ["BUY", "Buy"], ["SELL", "Sell"]], q.direction)}</select>
      </div>
      <div class="panel" id="setupsBody">${loading}</div>`);
    const upd = () => {
      Object.assign(q, { decision: $("#fDecision").value, status: $("#fStatus").value, pair: $("#fPair").value.trim().toUpperCase(), timeframe: $("#fTf").value, direction: $("#fDir").value, offset: 0 });
      loadSetups();
    };
    ["#fDecision", "#fStatus", "#fTf", "#fDir"].forEach((s) => ($(s).onchange = upd));
    $("#fPair").onchange = upd;
    await loadSetups();
  }

  async function loadSetups() {
    const q = state.setupsQuery;
    const params = new URLSearchParams({ source: "live", limit: 50, offset: q.offset });
    ["decision", "status", "pair", "timeframe", "direction"].forEach((k) => q[k] && params.set(k, q[k]));
    const body = $("#setupsBody");
    try {
      const r = await api(`/api/setups?${params}`);
      const reason = q.decision === "REJECTED";
      body.innerHTML = `<div class="panel-body" style="padding-top:6px">${setupsTable(r.items, { reason, empty: "No setups match these filters yet." })}</div>
        <div class="pager"><span>${r.total ? `${q.offset + 1}–${Math.min(q.offset + 50, r.total)} of ${r.total}` : "0 results"}</span>
        <span class="actions"><button class="btn sm" id="prev" ${q.offset === 0 ? "disabled" : ""}>Previous</button><button class="btn sm" id="next" ${q.offset + 50 >= r.total ? "disabled" : ""}>Next</button></span></div>`;
      $("#prev").onclick = () => { q.offset = Math.max(0, q.offset - 50); loadSetups(); };
      $("#next").onclick = () => { q.offset += 50; loadSetups(); };
      bindRows(body);
    } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }

  // ------------------------------------------------------------------ setup detail
  const CHECK_LABELS = {
    sweep: "Transactional sweep of previous swing extreme",
    bos: "Break of structure (candle body close)",
    bos_count: "Required BOS chain (break → drop-down → BOS)",
    prior_trend: "Prior opposite trend into the POI",
    structure_intact: "Protected extreme intact",
    liquidity_depth: "Liquidity at ≥ 50% fib depth",
    liquidity_shape: "V / A multi-candle geometry",
    poi_found: "Refined POI found (OB / QMR)",
    poi_unmitigated: "POI unmitigated",
    poi_in_zone: "POI in discount (buy) / premium (sell)",
    poi_behind_liquidity: "POI sits behind structural liquidity",
    stop_valid: "Valid stop distance",
    rr_valid: "Risk-to-reward ≥ 1:3",
    htf_aligned: "HTF alignment — where price is coming from",
  };

  async function renderSetup(id) {
    shell("setups", `<div id="detail">${loading}</div>`);
    let d;
    try { d = await api(`/api/setups/${id}`); } catch (e) { $("#detail").innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    const s = d.setup, det = d.details || {}, c = det.candidate || {}, htf = det.htf || {};
    const checks = c.checks || det.checks || {};
    const pair = s.pair;
    const back = s.source === "backtest" ? `#/analytics?run=${s.run_id}` : "#/setups";
    const liqs = (c.liquidity || []).slice().sort((a, b) => b.time - a.time);
    const isWatch = s.decision === "WATCH";
    $("#detail").innerHTML = `
      <div class="page-head">
        <div><a class="note" href="${back}">← Back</a>
          <h1 style="margin-top:6px">${esc(pair)} <span class="dim">${esc(s.timeframe)} → ${esc(s.htf || "")}</span> ${dirBadge(s.direction)} ${statusBadge(s.status)}</h1>
          <p>${isWatch ? "Confirmation-entry watch: HTF POI tapped, wait for a full LTF sequence." : s.decision === "APPROVED" ? "Risk entry — limit order at the POI boundary, stop beyond the protected extreme." : esc(s.reject_reason || "")}</p></div>
        <div class="actions">${s.source === "backtest" ? '<span class="badge">Backtest</span>' : ""}${s.suppressed ? '<span class="badge AUDIT">Suppressed (audit)</span>' : ""}</div>
      </div>
      <div class="levels" style="margin-bottom:16px">
        <div><span>Entry</span><b>${px(s.entry, pair)}</b></div>
        <div><span>Stop loss</span><b>${px(s.stop_loss, pair)}</b> <span class="dim">${s.stop_pips ? `${s.stop_pips}p` : ""}</span></div>
        <div><span>Take profit</span><b>${px(s.take_profit, pair)}</b> <span class="dim">${s.rr ? `1:${num(s.rr, 1)}` : ""}</span>${s.partial_price != null ? `<br><span class="dim">partial ${px(s.partial_price, pair)}</span>` : ""}</div>
        <div><span>${s.result_r != null ? "Result" : "Size"}</span><b class="${cls(s.result_r)}">${s.result_r != null ? signR(s.result_r) + (s.pnl != null ? ` · ${money(s.pnl)}` : "") : s.lots ? `${num(s.lots)} lots · ${money(s.risk_amount)}` : "—"}</b></div>
      </div>
      <div class="grid g-main">
        <div class="stack">
          <div class="panel"><div class="legend">
            <span><i style="background:var(--info)"></i>Entry</span><span><i style="background:var(--sell)"></i>Stop</span><span><i style="background:var(--buy)"></i>Target</span>
            <span><i style="background:var(--accent)"></i>Equilibrium 50%</span><span><i style="background:#b48cff"></i>Structural liquidity</span><span><i style="background:var(--muted)"></i>Protected / BOS level</span>
          </div><div class="panel-body"><div id="chart">${d.candles.length ? "" : '<div class="empty">No candle snapshot stored for this candidate.</div>'}</div></div></div>
          ${isWatch ? "" : `<div class="panel"><div class="panel-head"><h2>Liquidity map</h2><span class="note">internal pivots inside the active range</span></div><div class="panel-body" style="padding-top:6px">
            ${liqs.length ? `<div class="table-wrap"><table><thead><tr><th>Formed</th><th class="num">Price</th><th class="num">Fib depth</th><th>Type</th><th>Shape</th><th>Class</th><th>Swept</th></tr></thead><tbody>
            ${liqs.map((q) => `<tr style="cursor:default"><td>${fmtTime(q.time)}</td><td class="num">${px(q.price, pair)}</td><td class="num">${Math.round(q.fib_depth * 100)}%</td><td>${esc(title(q.pattern_type))}</td><td>${esc(q.shape_detail === "OK" ? title(q.shape) : title(q.shape_detail))}</td><td><span class="badge ${q.classification === "STRUCTURAL" ? "APPROVED" : q.classification === "INDUCEMENT" ? "TRIGGERED" : ""}">${esc(title(q.classification))}</span></td><td>${q.swept ? "yes" : "no"}</td></tr>`).join("")}
            </tbody></table></div>` : '<div class="note">No internal liquidity points recorded.</div>'}</div></div>`}
        </div>
        <div class="stack">
          ${Object.keys(checks).length ? `<div class="panel"><div class="panel-head"><h2>Validation truth table</h2></div><div class="panel-body checks">
            ${Object.keys(CHECK_LABELS).filter((k) => k in checks).map((k) => `<div class="check"><span class="ic ${checks[k] ? "ok" : "no"}">${checks[k] ? "✓" : "✕"}</span>${CHECK_LABELS[k]}</div>`).join("")}
          </div></div>` : ""}
          <div class="panel"><div class="panel-head"><h2>Structure</h2></div><div class="panel-body"><dl class="kv">
            ${isWatch ? `<dt>HTF zone</dt><dd>${px(Math.min(s.entry, s.stop_loss), pair)} – ${px(Math.max(s.entry, s.stop_loss), pair)}</dd><dt>HTF range</dt><dd>${px(htf.range_low, pair)} – ${px(htf.range_high, pair)}</dd><dt>HTF equilibrium</dt><dd>${px(htf.equilibrium, pair)}</dd>` : `
            <dt>Swept level</dt><dd>${px(c.swept_level_price, pair)} <span class="dim">${esc(title(s.sweep_type))}</span></dd>
            <dt>Protected ${s.direction === "BUY" ? "low" : "high"}</dt><dd>${px(s.protected_price, pair)}</dd>
            <dt>BOS level</dt><dd>${px(s.bos_price, pair)}</dd>
            <dt>BOS in leg</dt><dd>${c.bos_count ?? 1}${(c.bos_count || 1) >= 2 ? ` · last ${px(c.last_bos_price, pair)}` : ""}</dd>
            <dt>Range extreme</dt><dd>${px(s.range_extreme, pair)}</dd>
            <dt>Equilibrium 50%</dt><dd>${px(s.equilibrium, pair)}</dd>
            <dt>POI</dt><dd>${esc(title(s.poi_type))}</dd>
            <dt>Liquidity</dt><dd>${esc(title(s.liquidity_type))} · ${s.liquidity_depth != null ? Math.round(s.liquidity_depth * 100) + "%" : "—"}</dd>
            <dt>Structure RR</dt><dd>${s.structure_rr != null ? "1:" + num(s.structure_rr, 1) : "—"}</dd>
            <dt>HTF (${esc(s.htf || "")})</dt><dd>${esc(s.htf_zone || "—")}${htf.position_pct != null ? ` · ${htf.position_pct}%` : ""}</dd>
            <dt>HTF range</dt><dd>${px(htf.range_low, pair)} – ${px(htf.range_high, pair)}</dd>`}
          </dl></div></div>
          <div class="panel"><div class="panel-head"><h2>Timeline</h2></div><div class="panel-body"><div class="feed">
            <div class="feed-item"><time>${fmtTime(s.detected_bar_time, false)}</time><div><span class="k muted">DETECTED</span><span class="muted">bar ${fmtTime(s.detected_bar_time)}</span></div></div>
            ${d.events.map((e) => `<div class="feed-item"><time>${fmtTime(e.ts, false)}</time><div><span class="k ${e.kind === "WIN" ? "pos" : e.kind === "LOSS" ? "neg" : "muted"}">${esc(e.kind)}</span><span class="muted">${esc(e.message)}</span></div></div>`).join("")}
            ${s.triggered_at ? `<div class="feed-item"><time>${fmtTime(s.triggered_at, false)}</time><div><span class="k muted">FILLED</span><span class="muted">${fmtTime(s.triggered_at)}</span></div></div>` : ""}
            ${s.closed_at ? `<div class="feed-item"><time>${fmtTime(s.closed_at, false)}</time><div><span class="k ${cls(s.result_r)}">${esc(s.status)}</span><span class="muted">${fmtTime(s.closed_at)}</span></div></div>` : ""}
          </div></div></div>
        </div>
      </div>`;
    if (d.candles.length) drawSetupChart(d, liqs);
  }

  function drawSetupChart(d, liqs) {
    const s = d.setup, c = (d.details || {}).candidate || {};
    const el = $("#chart");
    const LW = window.LightweightCharts;
    const chart = LW.createChart(el, {
      autoSize: true,
      layout: { background: { type: "solid", color: css("--panel") }, textColor: css("--muted"), fontFamily: css("--mono") },
      grid: { vertLines: { color: "rgba(255,255,255,.03)" }, horzLines: { color: "rgba(255,255,255,.04)" } },
      rightPriceScale: { borderColor: css("--line") },
      timeScale: { borderColor: css("--line"), timeVisible: true, secondsVisible: false },
      crosshair: { mode: LW.CrosshairMode.Normal },
      localization: { priceFormatter: (p) => p.toFixed(d.decimals) },
    });
    state.charts.push(chart);
    const series = chart.addCandlestickSeries({
      upColor: css("--buy"), downColor: css("--sell"), borderVisible: false, wickUpColor: css("--buy"), wickDownColor: css("--sell"),
      priceFormat: { type: "price", precision: d.decimals, minMove: Math.pow(10, -d.decimals) },
    });
    const off = -new Date().getTimezoneOffset() * 60;   // render in local time
    const bars = d.candles.map((k) => ({ time: k[0] + off, open: k[1], high: k[2], low: k[3], close: k[4] }));
    series.setData(bars);
    const line = (price, color, t, style = 0, width = 1) => price != null && series.createPriceLine({ price, color, lineWidth: width, lineStyle: style, axisLabelVisible: true, title: t });
    line(s.entry, css("--info"), "Entry", 0, 2);
    line(s.stop_loss, css("--sell"), "SL", 0, 2);
    line(s.take_profit, css("--buy"), "TP", 0, 2);
    line(s.partial_price, css("--buy"), "Partial", 2, 1);
    line(s.equilibrium, css("--accent"), "EQ 50%", 2);
    line(s.protected_price, css("--muted"), "Protected", 1);
    line(s.bos_price, css("--muted"), "BOS", 1);
    const liq = c.chosen_liquidity;
    if (liq) line(liq.price, "#b48cff", "Str. liquidity", 2);
    const poi = c.chosen_poi;
    if (poi) line(poi.distal, "rgba(90,169,255,.6)", "POI distal", 3);

    const first = bars.length ? bars[0].time : 0;
    const snap = (ts) => {
      if (!ts) return null;
      const t = ts + off;
      if (t < first) return null;
      let best = null;
      for (const b of bars) { if (b.time <= t) best = b.time; else break; }
      return best;
    };
    const buy = s.direction === "BUY";
    const marks = [];
    const add = (ts, text, color, below, shape) => { const t = snap(ts); if (t) marks.push({ time: t, position: below ? "belowBar" : "aboveBar", color, shape, text }); };
    add(c.swept_level_time, "Swept lvl", css("--muted"), buy, "circle");
    const steps = c.bos_chain && c.bos_chain.length ? c.bos_chain : [{ time: s.bos_time || c.bos_time, low_time: s.protected_time || c.protected_time }];
    steps.forEach((st, k) => {
      add(st.low_time, k === 0 ? "Sweep / POI" : "Inducement", css("--accent"), buy, buy ? "arrowUp" : "arrowDown");
      add(st.time, steps.length > 1 ? `BOS ${k + 1}` : "BOS", css("--text"), !buy, "square");
    });
    if (liq) add(liq.time, "Liq", "#b48cff", buy, "circle");
    if (poi) add(poi.time, poi.poi_type === "ORDER_BLOCK" ? "OB" : "QMR", css("--info"), buy, "square");
    add(s.triggered_at, "Fill", css("--info"), buy, buy ? "arrowUp" : "arrowDown");
    add(s.partial_at, "Partial", css("--buy"), !buy, "circle");
    if (s.closed_at && ["WIN", "LOSS", "PARTIAL_WIN"].includes(s.status)) add(s.closed_at, s.status === "PARTIAL_WIN" ? "BE" : s.status, s.status === "LOSS" ? css("--sell") : css("--buy"), !buy, "circle");
    marks.sort((a, b) => a.time - b.time);
    series.setMarkers(marks);
    chart.timeScale().fitContent();
  }

  // ------------------------------------------------------------------ analytics
  async function renderAnalytics(params) {
    const runs = await api("/api/backtests").catch(() => []);
    const run = params.get("run");
    shell("analytics", `
      <div class="page-head"><div><h1>Analytics</h1><p>Performance of approved setups, broken down by every dimension of the strategy.</p></div>
        <div class="actions"><select id="srcSel"><option value="">Live trading</option>${runs.filter((r) => r.status === "DONE").map((r) => `<option value="${r.id}" ${String(r.id) === run ? "selected" : ""}>Backtest #${r.id} · ${esc(r.params.timeframe)} · ${r.params.days}d · ${r.params.pairs.length} pairs</option>`).join("")}</select></div></div>
      <div id="anBody">${loading}</div>`);
    $("#srcSel").onchange = (e) => (location.hash = e.target.value ? `#/analytics?run=${e.target.value}` : "#/analytics");
    const q = run ? `source=backtest&run_id=${run}` : "source=live";
    let a;
    try { a = await api(`/api/analytics?${q}`); } catch (e) { $("#anBody").innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    const f = a.funnel;
    const rejMax = Math.max(1, ...a.rejections.map((r) => r.n));
    $("#anBody").innerHTML = `
      ${kpiStrip(a.summary)}
      <div class="grid g-main" style="margin-bottom:16px">
        <div class="panel"><div class="panel-head"><h2>Equity curve</h2><span class="note">cumulative R · fixed risk per the 10-trade rule</span></div>
          <div class="panel-body"><div class="chart-box">${a.equity.length ? '<canvas id="eq"></canvas>' : '<div class="empty">No resolved trades.</div>'}</div></div></div>
        <div class="panel"><div class="panel-head"><h2>Signal funnel</h2></div><div class="panel-body"><dl class="kv">
          <dt>Candidates evaluated</dt><dd>${f.candidates}</dd>
          <dt>Rejected by rules</dt><dd>${f.rejected}</dd>
          <dt>Approved setups</dt><dd>${f.approved} <span class="dim">(${f.approval_rate ?? "—"}%)</span></dd>
          <dt>Limit orders filled</dt><dd>${f.triggered} <span class="dim">(${f.fill_rate ?? "—"}% fill)</span></dd>
          <dt>Missed / expired</dt><dd>${f.missed} / ${f.expired}</dd>
          <dt>Pending / active</dt><dd>${f.pending} / ${f.active}</dd>
          <dt>Resolved trades</dt><dd>${f.resolved}</dd>
          <dt>Max win streak</dt><dd>${a.summary.max_win_streak}</dd>
          <dt>Current streak</dt><dd class="${cls(a.summary.current_streak)}">${a.summary.current_streak > 0 ? "+" : ""}${a.summary.current_streak}</dd>
        </dl></div></div>
      </div>
      <div class="grid g-2" style="margin-bottom:16px">
        <div class="panel"><div class="panel-head"><h2>Net R by pair</h2></div><div class="panel-body"><div class="chart-box">${a.by_pair.length ? '<canvas id="byPair"></canvas>' : '<div class="empty">—</div>'}</div></div></div>
        <div class="panel"><div class="panel-head"><h2>10-trade batches</h2><span class="note">spec §7 · 3 wins in 10 = +2R</span></div><div class="panel-body"><div class="chart-box">${a.batches.length ? '<canvas id="batches"></canvas>' : '<div class="empty">—</div>'}</div></div></div>
      </div>
      <div class="grid g-3" style="margin-bottom:16px">
        <div class="panel"><div class="panel-head"><h2>By session</h2></div><div class="panel-body">${groupTable(a.by_session)}</div></div>
        <div class="panel"><div class="panel-head"><h2>By POI type</h2></div><div class="panel-body">${groupTable(a.by_poi)}</div></div>
        <div class="panel"><div class="panel-head"><h2>By liquidity type</h2></div><div class="panel-body">${groupTable(a.by_liquidity)}</div></div>
        <div class="panel"><div class="panel-head"><h2>By HTF location</h2></div><div class="panel-body">${groupTable(a.by_htf_zone)}</div></div>
        <div class="panel"><div class="panel-head"><h2>By direction</h2></div><div class="panel-body">${groupTable(a.by_direction)}</div></div>
        <div class="panel"><div class="panel-head"><h2>By timeframe / sweep</h2></div><div class="panel-body">${groupTable([...a.by_timeframe, ...a.by_sweep.map((r) => ({ ...r, key: `${r.key} sweep` }))])}</div></div>
      </div>
      <div class="panel"><div class="panel-head"><h2>Why candidates were rejected</h2><span class="note">first failing rule of the truth table</span></div><div class="panel-body">
        ${a.rejections.length ? a.rejections.map((r) => `<div class="bar-row"><span>${esc(r.reason)}</span><span class="track"><span class="fill" style="width:${(100 * r.n) / rejMax}%"></span></span><span class="num" style="text-align:right">${r.n}</span></div>`).join("") : '<div class="note">No rejections recorded.</div>'}
      </div></div>
      ${run ? `<div class="panel" style="margin-top:16px"><div class="panel-head"><h2>Backtest trades</h2></div><div class="panel-body" id="btTrades">${loading}</div></div>` : ""}`;
    if (a.equity.length) equityChart($("#eq"), a.equity);
    if (a.by_pair.length) barChart($("#byPair"), a.by_pair);
    if (a.batches.length) barChart($("#batches"), a.batches.map((b) => ({ ...b, key: `#${b.index}`, win_rate: Math.round((100 * b.wins) / b.n) })), "net_r", "key", false);
    if (run) {
      const r = await api(`/api/setups?source=backtest&run_id=${run}&decision=APPROVED&status=WIN,PARTIAL_WIN,LOSS,TRIGGERED&limit=500`);
      $("#btTrades").innerHTML = setupsTable(r.items);
      bindRows($("#btTrades"));
    }
  }

  // ------------------------------------------------------------------ backtests
  async function renderBacktests() {
    const st = await api("/api/settings");
    shell("backtests", `
      <div class="page-head"><div><h1>Backtests</h1><p>Replay the exact live engine bar-by-bar over history — no look-ahead — to validate the patterns.</p></div></div>
      <div class="panel" style="margin-bottom:16px"><div class="panel-body">
        <form id="btForm" class="filters" style="margin:0;align-items:flex-end">
          <label class="field" style="flex:1;min-width:240px">Pairs (comma separated, blank = all ${st.pairs.length})<input type="text" name="pairs" placeholder="${esc(st.pairs.slice(0, 4).join(","))}"></label>
          <label class="field">Timeframe<select name="tf"><option value="15m">15m (HTF 4h)</option><option value="1h">1h (HTF Daily)</option><option value="5m">5m (HTF 1h)</option></select></label>
          <label class="field">Days<input type="number" name="days" value="59" min="5" max="730" style="width:90px"></label>
          <button class="btn primary" type="submit">Run backtest</button>
        </form>
        <p class="note" style="margin:10px 0 0">Free Yahoo data limits intraday history: 5m/15m ≈ 59 days, 1h ≈ 2 years. Uses your current account size and max drawdown for $ figures.</p>
      </div></div>
      <div class="panel" id="btList">${loading}</div>`);
    $("#btForm").onsubmit = async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      try {
        const r = await api("/api/backtests", { method: "POST", body: { pairs: String(f.get("pairs") || "").split(",").map((s) => s.trim()).filter(Boolean), timeframe: f.get("tf"), days: Number(f.get("days")) } });
        toast(`Backtest #${r.run_id} started (${r.days} days)`);
        loadBacktests();
      } catch (err) { toast(err.message); }
    };
    await loadBacktests();
    state.timer = setInterval(loadBacktests, 4000);
  }

  async function loadBacktests() {
    const el = $("#btList");
    if (!el) return;
    const runs = await api("/api/backtests");
    if (!runs.length) { el.innerHTML = '<div class="empty">No backtests yet. Run one above.</div>'; return; }
    el.innerHTML = `<div class="table-wrap"><table><thead><tr><th>#</th><th>Started</th><th>Setup</th><th>Status</th><th class="num">Trades</th><th class="num">Win %</th><th class="num">Net</th><th class="num">Exp.</th><th class="num">PF</th><th class="num">Max DD</th><th></th></tr></thead><tbody>
      ${runs.map((r) => { const s = (r.stats || {}).summary || {}; return `<tr data-run="${r.id}">
        <td class="num">${r.id}</td><td>${fmtTime(r.started_at)}</td>
        <td>${esc(r.params.timeframe)} · ${r.params.days}d · ${r.params.pairs.length} pairs</td>
        <td>${r.status === "RUNNING" || r.status === "QUEUED" ? `<span class="spin"></span> <span class="muted">${esc(r.message || "")}</span>` : `<span class="badge ${r.status === "DONE" ? "APPROVED" : "LOSS"}">${esc(r.status)}</span>${r.status === "FAILED" ? ` <span class="muted">${esc(r.message || "")}</span>` : ""}`}</td>
        <td class="num">${s.trades ?? "—"}</td><td class="num">${s.win_rate != null ? num(s.win_rate, 1) : "—"}</td>
        <td class="num ${cls(s.net_r)}">${s.net_r != null ? signR(s.net_r) : "—"}</td><td class="num ${cls(s.expectancy_r)}">${s.expectancy_r != null ? signR(s.expectancy_r) : "—"}</td>
        <td class="num">${s.profit_factor ?? "—"}</td><td class="num">${s.max_drawdown_r != null ? num(s.max_drawdown_r, 1) + "R" : "—"}</td>
        <td><button class="btn sm danger" data-del="${r.id}">Delete</button></td></tr>`; }).join("")}
    </tbody></table></div>`;
    el.querySelectorAll("tr[data-run]").forEach((tr) => (tr.onclick = (e) => {
      if (e.target.dataset.del) return;
      location.hash = `#/analytics?run=${tr.dataset.run}`;
    }));
    el.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(`Delete backtest #${b.dataset.del} and its setups?`)) return;
      await api(`/api/backtests/${b.dataset.del}`, { method: "DELETE" });
      loadBacktests();
    }));
  }

  // ------------------------------------------------------------------ settings
  // ------------------------------------------------------------------ history (per-account trade ledger)
  const accMoney = (acc, v, sign = false) => {
    if (v == null) return "—";
    if ((acc.currency || "").toUpperCase() === "USC") return `${sign && v > 0 ? "+" : ""}${Math.round(v).toLocaleString()}¢`;
    return (v < 0 ? "-$" : sign && v > 0 ? "+$" : "$") + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  const usdNote = (acc, v) => ((acc.currency || "").toUpperCase() === "USC" && v != null ? ` <span class="dim">($${(v / 100).toFixed(2)})</span>` : "");
  const EXIT_LABEL = { WIN: "Take profit", LOSS: "Stop loss", PARTIAL_WIN: "Runner at entry", MISSED: "Missed", EXPIRED: "Expired", SKIPPED: "Skipped" };

  async function renderHistory(params) {
    shell("history", `<div class="page-head"><div><h1>History</h1><p>Every alert as it would be traded on each account, sized and managed by that account's risk profile.</p></div>
      <div class="actions" id="accTabs"></div></div><div id="histBody">${loading}</div>`);
    const accs = await api("/api/accounts");
    if (!accs.length) { $("#histBody").innerHTML = '<div class="empty">No accounts configured.</div>'; return; }
    const id = Number(params.get("account")) || accs[0].account.id;
    $("#accTabs").innerHTML = accs.map((a) => `<a class="btn ${a.account.id === id ? "primary" : ""}" href="#/history?account=${a.account.id}">${esc(a.account.name)}</a>`).join("");
    state.histFilter = state.histFilter || "taken";
    const load = async () => {
      const body = $("#histBody");
      if (!body) return;
      let h;
      try { h = await api(`/api/accounts/${id}/history`); } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
      destroyCharts();
      const acc = h.account, M = (v, s) => accMoney(acc, v, s);
      const open = h.items.filter((t) => t.status === "TRIGGERED");
      const pending = h.items.filter((t) => t.status === "PENDING");
      const shown = h.items.filter((t) => state.histFilter === "all" || (state.histFilter === "taken" ? ["WIN", "LOSS", "PARTIAL_WIN", "TRIGGERED"].includes(t.status) : t.status === state.histFilter));
      body.innerHTML = `
        <div class="kpis">
          <div class="kpi"><div class="label">Balance</div><div class="value">${M(h.balance)}</div><div class="sub">start ${M(acc.start_balance)}${usdNote(acc, h.balance)}</div></div>
          <div class="kpi"><div class="label">Equity (live)</div><div class="value ${cls(h.floating)}">${M(h.equity)}</div><div class="sub">open P&L ${M(h.floating, true)}</div></div>
          <div class="kpi"><div class="label">Net P&L</div><div class="value ${cls(h.net_pnl)}">${M(h.net_pnl, true)}</div><div class="sub">${h.return_pct > 0 ? "+" : ""}${h.return_pct}% · ${signR(h.net_r)}</div></div>
          <div class="kpi"><div class="label">Win rate</div><div class="value">${num(h.win_rate, 1)}%</div><div class="sub">${h.full_wins} full + ${h.partial_wins} partial · ${h.losses}L</div></div>
          <div class="kpi"><div class="label">Profit factor</div><div class="value">${h.profit_factor ?? "—"}</div><div class="sub">${h.trades} closed trades</div></div>
          <div class="kpi"><div class="label">Max drawdown</div><div class="value neg">${M(-h.max_dd)}</div><div class="sub">${h.max_dd_pct}% of start</div></div>
        </div>
        <div class="panel statusbar">
          <span>Risk now <b>${M(h.risk_now)}</b> (${acc.risk_pct}%${acc.compounding ? " of balance" : " fixed"})</span>
          <span>Exit plan <b>${acc.partial_at_r > 0 ? `${acc.partial_pct}% at +${acc.partial_at_r}R, SL→entry, rest to TP` : "hold to TP/SL"}</b></span>
          <span>Max open <b>${acc.max_open}</b></span><span>${acc.prop_rules ? '<span class="badge">Prop rules</span>' : '<span class="badge">Live</span>'}</span>
          <span><b>${open.length}</b> open · <b>${pending.length}</b> pending · <b>${h.counts.SKIPPED || 0}</b> skipped</span>
          <span class="dim">auto-refreshes every 30s</span>
        </div>
        <div class="grid g-main" style="margin-bottom:16px">
          <div class="panel"><div class="panel-head"><h2>Balance over time</h2></div><div class="panel-body"><div class="chart-box">${h.curve.length > 1 ? '<canvas id="balChart"></canvas>' : '<div class="empty">The curve starts with the first closed trade.</div>'}</div></div></div>
          <div class="panel"><div class="panel-head"><h2>Open positions</h2><span class="note">live P&L at last close</span></div><div class="panel-body">
            ${open.length ? open.map((t) => `<a class="feed-item" href="#/setup/${t.setup_id}" style="grid-template-columns:1fr auto"><span>${dirBadge(t.direction)} <b>${esc(t.pair)}</b> <span class="dim">${t.lots} @ ${px(t.entry, t.pair)}${t.partial_at ? " · partial banked" : ""}</span></span><b class="mono ${cls(t.unrealized)}">${M(t.unrealized, true)}</b></a>`).join("") : '<div class="note">No open positions.</div>'}
            ${pending.length ? `<div class="note" style="margin-top:10px">Pending limits: ${pending.map((t) => `${esc(t.pair)} ${t.direction} @ ${px(t.entry, t.pair)}`).join(" · ")}</div>` : ""}
          </div></div>
        </div>
        <div class="panel"><div class="panel-head"><h2>Trades</h2><span class="actions">
          <select id="histFilter">${[["taken", "Taken trades"], ["all", "Everything"], ["WIN", "Wins"], ["PARTIAL_WIN", "Partial wins"], ["LOSS", "Losses"], ["PENDING", "Pending"], ["SKIPPED", "Skipped"], ["MISSED", "Missed"], ["EXPIRED", "Expired"]].map(([v, l]) => `<option value="${v}" ${v === state.histFilter ? "selected" : ""}>${l}</option>`).join("")}</select>
          <button class="btn sm" id="csvBtn">Download CSV</button></span></div>
          <div class="panel-body" style="padding-top:8px">${shown.length ? `<div class="table-wrap"><table><thead><tr>
            <th>Alert</th><th>Pair</th><th>Side</th><th>Status</th><th class="num">Lots</th><th class="num">Entry</th><th class="num">SL</th><th class="num">TP</th>
            <th class="num">Partial</th><th>Exit</th><th class="num">R</th><th class="num">P&L</th><th class="num">Balance</th></tr></thead><tbody>
            ${shown.map((t) => `<tr data-id="${t.setup_id}" title="${esc(t.skip_reason || "")}">
              <td>${fmtTime(t.detected_bar_time)}</td><td><b>${esc(t.pair)}</b> <span class="dim">${esc(t.timeframe)}</span></td><td>${dirBadge(t.direction)}</td>
              <td>${statusBadge(t.status)}</td><td class="num">${t.lots ?? "—"}</td><td class="num">${px(t.entry, t.pair)}</td><td class="num">${px(t.stop_loss, t.pair)}</td>
              <td class="num">${px(t.take_profit, t.pair)}</td><td class="num">${t.partial_price != null ? px(t.partial_price, t.pair) + (t.partial_at ? " ✓" : "") : "—"}</td>
              <td>${t.status === "TRIGGERED" ? `open · ${px(t.last_price, t.pair)}` : esc(EXIT_LABEL[t.status] || "")}${t.exit_price != null ? ` <span class="dim">${px(t.exit_price, t.pair)}</span>` : ""}${t.closed_at ? ` <span class="dim">${fmtTime(t.closed_at)}</span>` : ""}</td>
              <td class="num ${cls(t.result_r)}">${t.result_r != null ? signR(t.result_r) : ""}</td>
              <td class="num ${cls(t.pnl ?? t.unrealized)}">${t.pnl != null ? M(t.pnl, true) : t.unrealized != null ? `<span class="dim">${M(t.unrealized, true)}</span>` : ""}</td>
              <td class="num">${t.balance_after != null ? M(t.balance_after) : ""}</td></tr>`).join("")}
            </tbody></table></div>` : '<div class="empty">No trades yet. New alerts appear here for every account, and fill/exit as price reaches the levels.</div>'}</div></div>`;
      if (h.curve.length > 1) {
        chartDefaults();
        const c = new Chart($("#balChart"), {
          type: "line",
          data: { labels: h.curve.map((p) => fmtTime(p.t)), datasets: [{ data: h.curve.map((p) => p.balance), borderColor: css("--accent"), borderWidth: 2, pointRadius: 2, tension: 0 }] },
          options: { maintainAspectRatio: false, animation: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (ctx) => { const p = h.curve[ctx.dataIndex]; return `${M(p.balance)}${p.pair ? ` · ${p.pair} ${M(p.pnl, true)}` : ""}`; } } } },
            scales: { x: { ticks: { maxTicksLimit: 6 }, grid: { display: false } }, y: { grid: { color: css("--line") }, ticks: { callback: (v) => M(v) } } } },
        });
        state.charts.push(c);
      }
      $("#histFilter").onchange = (e) => { state.histFilter = e.target.value; load(); };
      $("#csvBtn").onclick = () => {
        const cols = ["detected_bar_time", "pair", "timeframe", "direction", "status", "lots", "entry", "stop_loss", "take_profit", "partial_price", "triggered_at", "closed_at", "exit_price", "result_r", "pnl", "balance_after", "skip_reason"];
        const iso = (v) => (v ? new Date(v * 1000).toISOString() : "");
        const rows = h.items.map((t) => cols.map((k) => (k.endsWith("_at") || k === "detected_bar_time" ? iso(t[k]) : t[k] ?? "")).map((v) => `"${String(v).replace(/"/g, '""')}"`).join(","));
        const blob = new Blob([[cols.join(","), ...rows].join("\n")], { type: "text/csv" });
        const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `${acc.name.replace(/[^\w]+/g, "_")}_history.csv`; a.click();
      };
      bindRows(body);
    };
    await load();
    state.timer = setInterval(load, 30000);
  }

  async function renderSettings() {
    shell("settings", `<div class="page-head"><div><h1>Settings</h1><p>Risk model, alerts and system state.</p></div></div><div id="setBody">${loading}</div>`);
    const [st, accList] = await Promise.all([api("/api/settings"), api("/api/accounts")]);
    const a = st.account, tg = st.telegram, au = st.audit;
    const accForm = (s) => {
      const x = s.account;
      const f = (k, label, type = "number", extra = "") => `<label class="field">${label}<input type="${type}" name="${k}" value="${esc(x[k])}" ${extra}></label>`;
      return `<form class="panel accForm" data-id="${x.id}"><div class="panel-head"><h2>${esc(x.name)}</h2><span class="note">balance now ${accMoney(x, s.balance)}</span></div>
        <div class="panel-body"><div class="grid g-3" style="gap:12px">
          ${f("name", "Name", "text")}
          <label class="field">Currency<select name="currency"><option value="USD" ${x.currency === "USD" ? "selected" : ""}>USD ($)</option><option value="USC" ${x.currency === "USC" ? "selected" : ""}>USC (cents)</option></select></label>
          ${f("start_balance", "Starting balance", "number", 'step="any" min="1"')}
          ${f("risk_pct", "Risk per trade (%)", "number", 'step="0.05" min="0.05" max="10"')}
          <label class="field">Risk based on<select name="compounding"><option value="0" ${!x.compounding ? "selected" : ""}>Starting balance (fixed)</option><option value="1" ${x.compounding ? "selected" : ""}>Current balance (compounding)</option></select></label>
          ${f("max_open", "Max open positions", "number", 'min="1" max="20"')}
          ${f("partial_at_r", "Partial at +R (0 = hold to TP)", "number", 'step="0.25" min="0" max="10"')}
          ${f("partial_pct", "Partial size (%)", "number", 'step="5" min="0" max="100"')}
          <label class="field">Prop-firm rules<select name="prop_rules"><option value="1" ${x.prop_rules ? "selected" : ""}>On (tracker + firm limits)</option><option value="0" ${!x.prop_rules ? "selected" : ""}>Off</option></select></label>
        </div><div style="margin-top:12px" class="actions"><button class="btn primary" type="submit">Save</button>
          <label class="note"><input type="checkbox" name="active" ${x.active ? "checked" : ""}> active (receives alerts)</label></div></div></form>`;
    };
    $("#setBody").innerHTML = `
      <div class="panel" style="margin-bottom:16px"><div class="panel-head"><h2>Trading accounts</h2><span class="note">each alert is sized and managed per account</span></div>
        <div class="panel-body" style="padding-top:8px"><div class="grid g-2">${accList.map(accForm).join("")}</div>
        <p class="note">Changes apply to new alerts. Cent accounts use cent-lots (1.00 = 1,000 units) and show amounts in ¢.</p></div></div>
      <div class="grid g-2">
        <div class="panel"><div class="panel-head"><h2>Risk model · 10-trade rule</h2></div><div class="panel-body">
          <form id="acctForm" class="stack" style="gap:12px">
            <div class="grid g-2" style="gap:12px">
              <label class="field">Account size (${esc(a.currency)})<input type="number" name="size" step="any" min="1" value="${a.account_size}"></label>
              <label class="field">Max allowable drawdown (%)<input type="number" name="dd" step="any" min="0.1" max="100" value="${a.max_drawdown_pct}"></label>
            </div>
            <div class="note" id="riskPreview">Risk per trade = drawdown ÷ 10 = <b class="mono">${money(a.risk_per_trade)}</b></div>
            <div><button class="btn primary" type="submit">Save</button></div>
          </form>
          <p class="note">Position size is fixed per trade and is never reduced mid-drawdown (spec §7, rule 1). Lot sizes in alerts use this risk.</p>
        </div></div>
        <div class="panel"><div class="panel-head"><h2>System state</h2></div><div class="panel-body">
          <dl class="kv" style="margin-bottom:14px">
            <dt>State</dt><dd><span class="badge ${au.state}">${esc(au.state)}</span></dd>
            <dt>Consecutive losses</dt><dd>${au.loss_streak} / 10</dd>
          </dl>
          <p class="note">After 10 consecutive losses the system enters AUDIT: new alerts are suspended (still logged as shadow setups) until you review and reset.</p>
          <button class="btn ${au.state === "AUDIT" ? "primary" : ""}" id="auditReset">Reset audit state</button>
        </div></div>
        <div class="panel"><div class="panel-head"><h2>Telegram alerts</h2></div><div class="panel-body">
          <dl class="kv" style="margin-bottom:14px">
            <dt>Bot token</dt><dd class="${tg.token_set ? "pos" : "neg"}">${tg.token_set ? "set" : "missing"}</dd>
            <dt>Chat id</dt><dd class="${tg.chat_set ? "pos" : "neg"}">${tg.chat_set ? "set" : "missing"}</dd>
            <dt>Confirmation-watch alerts</dt><dd>${tg.watch_alerts ? "on" : "off"}</dd>
            <dt>Fill / TP / SL alerts</dt><dd>${tg.lifecycle_alerts ? "on" : "off"}</dd>
          </dl>
          <button class="btn" id="tgTest" ${tg.configured ? "" : "disabled"}>Send test message</button>
          ${tg.configured ? "" : '<p class="note">Set <span class="mono">TELEGRAM_BOT_TOKEN</span> and <span class="mono">TELEGRAM_CHAT_ID</span> in <span class="mono">.env</span>, then restart. See README for the 2-minute setup.</p>'}
        </div></div>
        <div class="panel"><div class="panel-head"><h2>Change password</h2></div><div class="panel-body">
          <form id="pwForm" class="stack" style="gap:12px">
            <label class="field">Current password<input type="password" name="current" autocomplete="current-password" required></label>
            <label class="field">New password (min 8 chars)<input type="password" name="new" autocomplete="new-password" minlength="8" required></label>
            <div><button class="btn" type="submit">Update password</button></div>
          </form>
        </div></div>
        <div class="panel"><div class="panel-head"><h2>Scanner universe</h2></div><div class="panel-body">
          <dl class="kv">
            <dt>Data provider</dt><dd>${esc(st.provider)}</dd>
            <dt>Scan interval</dt><dd>${st.scan_interval_minutes} min</dd>
            <dt>Timeframes</dt><dd>${st.timeframes.map((t) => `${t} → ${st.htf_map[t]}`).join(", ")}</dd>
            <dt>Pairs</dt><dd style="white-space:normal">${st.pairs.join(", ")}</dd>
          </dl><p class="note">Edit <span class="mono">PAIRS</span>, <span class="mono">LTF_TIMEFRAMES</span> and <span class="mono">DATA_PROVIDER</span> in <span class="mono">.env</span>.</p>
        </div></div>
        <div class="panel"><div class="panel-head"><h2>Engine parameters</h2></div><div class="panel-body">
          <dl class="kv">${Object.entries(st.engine).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
        </div></div>
      </div>`;
    const f = $("#acctForm");
    const preview = () => { const s = Number(f.size.value), dd = Number(f.dd.value); $("#riskPreview").innerHTML = `Risk per trade = drawdown ÷ 10 = <b class="mono">${money((s * dd) / 1000)}</b>`; };
    f.size.oninput = preview; f.dd.oninput = preview;
    f.onsubmit = async (e) => {
      e.preventDefault();
      try { await api("/api/settings/account", { method: "POST", body: { account_size: Number(f.size.value), max_drawdown_pct: Number(f.dd.value) } }); toast("Risk model saved"); } catch (err) { toast(err.message); }
    };
    $("#auditReset").onclick = async () => { await api("/api/audit/reset", { method: "POST" }); toast("Audit state reset"); renderSettings(); };
    $("#tgTest").onclick = async () => { try { await api("/api/telegram/test", { method: "POST" }); toast("Test message sent"); } catch (err) { toast(err.message); } };
    document.querySelectorAll(".accForm").forEach((form) => (form.onsubmit = async (e) => {
      e.preventDefault();
      const d = new FormData(form);
      const body = { name: d.get("name"), currency: d.get("currency"), start_balance: Number(d.get("start_balance")),
        risk_pct: Number(d.get("risk_pct")), compounding: Number(d.get("compounding")), max_open: Number(d.get("max_open")),
        partial_at_r: Number(d.get("partial_at_r")), partial_pct: Number(d.get("partial_pct")), prop_rules: Number(d.get("prop_rules")),
        active: d.get("active") ? 1 : 0 };
      try { await api(`/api/accounts/${form.dataset.id}`, { method: "POST", body }); toast("Account saved"); renderSettings(); } catch (err) { toast(err.message); }
    }));
    $("#pwForm").onsubmit = async (e) => {
      e.preventDefault();
      const p = new FormData(e.target);
      try { await api("/api/password", { method: "POST", body: { current: p.get("current"), new: p.get("new") } }); toast("Password updated"); e.target.reset(); } catch (err) { toast(err.message); }
    };
  }

  // ------------------------------------------------------------------ router
  async function route() {
    clearInterval(state.timer);
    destroyCharts();
    const [path, qs] = (location.hash.slice(1) || "/").split("?");
    const params = new URLSearchParams(qs || "");
    if (path === "/login") return renderLogin();
    if (!state.user) {
      try { state.user = (await api("/api/me")).username; } catch (_) { return; }
    }
    try {
      if (path === "/" || path === "") return await renderDashboard();
      if (path === "/setups") return await renderSetups();
      if (path.startsWith("/setup/")) return await renderSetup(path.split("/")[2]);
      if (path === "/analytics") return await renderAnalytics(params);
      if (path === "/backtests") return await renderBacktests();
      if (path === "/settings") return await renderSettings();
      if (path === "/history") return await renderHistory(params);
      location.hash = "#/";
    } catch (e) {
      if (e.message !== "not authenticated") toast(e.message);
    }
  }

  window.addEventListener("hashchange", route);
  route();
})();
