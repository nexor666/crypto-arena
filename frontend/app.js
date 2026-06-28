// Stage 5 — frontend core (static charts).
//
// Wires the controls to POST /api/backtest, then renders the full result
// statically: a candlestick price chart (200W MA + halving/cycle lines + bull/bear
// shading + the selected strategy's trade markers), a multi-line equity chart, and
// the after-tax leaderboard. No animation yet — that is Stage 6, which reuses these
// exact per-day series.

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  // ---- in-memory state (last result kept so re-rendering needs no refetch) ----
  const state = {
    strategies: [],          // from /api/strategies
    events: { halvings: [], notable: [] },
    result: null,            // last /api/backtest response
    price: null,             // last /api/price-data response
  };
  let priceChart, equityChart;

  // ---------------------------------------------------------------------------
  // bootstrap
  // ---------------------------------------------------------------------------
  async function init() {
    priceChart = window.Arena.createPriceChart($("price-chart"));
    equityChart = window.Arena.createEquityChart($("equity-chart"));

    checkHealth();
    await Promise.all([loadStrategies(), loadEvents()]);

    $("run").addEventListener("click", runBacktest);
    $("select-all").addEventListener("click", () => toggleAll(true));
    $("select-none").addEventListener("click", () => toggleAll(false));
    $("marker-strategy").addEventListener("change", () =>
      renderMarkers($("marker-strategy").value));
    $("log-scale").addEventListener("change", applyLogScale);
    document.querySelectorAll('input[name="equity-lens"]').forEach((el) =>
      el.addEventListener("change", () => renderEquity(currentLens())));
    applyLogScale();
  }

  async function checkHealth() {
    const el = $("health");
    try {
      const res = await fetch("/api/health");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      el.textContent = `${data.status} (stage ${data.stage})`;
      el.className = "ok";
    } catch (err) {
      el.textContent = `unreachable — ${err.message}`;
      el.className = "err";
    }
  }

  async function loadStrategies() {
    const box = $("strategy-list");
    try {
      const res = await fetch("/api/strategies");
      const data = await res.json();
      state.strategies = data.strategies;
      box.innerHTML = "<legend>Strategies</legend>";
      for (const s of data.strategies) {
        const label = document.createElement("label");
        label.className = "inline strat";
        label.title = s.description || "";
        label.innerHTML =
          `<input type="checkbox" value="${s.name}" checked /> ` +
          `${s.name} <span class="muted">(${s.universe})</span>`;
        box.appendChild(label);
      }
    } catch (err) {
      box.innerHTML = `<legend>Strategies</legend><p class="err">failed: ${err.message}</p>`;
    }
  }

  async function loadEvents() {
    try {
      const res = await fetch("/api/events");
      state.events = await res.json();
    } catch (err) {
      console.warn("events load failed:", err);
    }
  }

  function toggleAll(checked) {
    $("strategy-list").querySelectorAll('input[type="checkbox"]')
      .forEach((cb) => { cb.checked = checked; });
  }

  function selectedStrategies() {
    return [...$("strategy-list").querySelectorAll('input[type="checkbox"]:checked')]
      .map((cb) => ({ name: cb.value, params: {} }));
  }

  // ---------------------------------------------------------------------------
  // run
  // ---------------------------------------------------------------------------
  async function runBacktest() {
    const status = $("run-status");
    const strategies = selectedStrategies();
    if (strategies.length === 0) {
      status.textContent = "Pick at least one strategy.";
      status.className = "run-status err";
      return;
    }
    const asset = $("asset").value;
    const start = $("start").value || null;
    const end = $("end").value || null;
    const body = {
      asset,
      start,
      end,
      capital: Number($("capital").value),
      fee_pct: Number($("fee").value) / 100,
      tax: {
        enabled: $("tax-enabled").checked,
        rate: Number($("tax-rate").value) / 100,
      },
      strategies,
    };

    status.textContent = `Racing ${strategies.length} strateg${strategies.length === 1 ? "y" : "ies"}…`;
    status.className = "run-status";
    $("run").disabled = true;
    try {
      const [btRes, pdRes] = await Promise.all([
        fetch("/api/backtest", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
        fetch(`/api/price-data/${asset}` + qs({ start, end })),
      ]);
      if (!btRes.ok) throw new Error(await errText(btRes));
      if (!pdRes.ok) throw new Error(await errText(pdRes));
      state.result = await btRes.json();
      state.price = await pdRes.json();

      populateMarkerSelect();
      renderPrice();
      renderEquity(currentLens());
      renderLeaderboard();

      const r = state.result;
      status.textContent =
        `Done — ${r.results.length} strategies, ${r.start} → ${r.end}.`;
      status.className = "run-status ok";
    } catch (err) {
      status.textContent = `Failed: ${err.message}`;
      status.className = "run-status err";
    } finally {
      $("run").disabled = false;
    }
  }

  async function errText(res) {
    try {
      const j = await res.json();
      return j.detail ? JSON.stringify(j.detail) : `HTTP ${res.status}`;
    } catch {
      return `HTTP ${res.status}`;
    }
  }

  function qs(params) {
    const parts = Object.entries(params)
      .filter(([, v]) => v != null && v !== "")
      .map(([k, v]) => `${k}=${encodeURIComponent(v)}`);
    return parts.length ? `?${parts.join("&")}` : "";
  }

  // ---------------------------------------------------------------------------
  // rendering — price chart
  // ---------------------------------------------------------------------------
  function renderPrice() {
    const series = state.price.series;
    $("price-asset").textContent = `· ${state.price.asset}`;

    const candles = series.map((r) => ({
      time: r.date, open: r.open, high: r.high, low: r.low, close: r.close,
    }));
    priceChart.candles.setData(candles);

    const ma = series
      .filter((r) => r.ma_200w != null)
      .map((r) => ({ time: r.date, value: r.ma_200w }));
    priceChart.ma.setData(ma);

    // halving + cycle vertical lines, bull/bear regime bands
    const lines = [];
    for (const h of state.events.halvings || []) {
      lines.push({ time: h.date, color: "#f7931a", dashed: true });
    }
    const kindColor = {
      top: "rgba(248,81,73,0.55)",
      bottom: "rgba(63,185,80,0.55)",
      crash: "rgba(210,153,34,0.7)",
    };
    for (const e of state.events.notable || []) {
      lines.push({ time: e.date, color: kindColor[e.kind] || "#8b949e" });
    }
    priceChart.overlay.setData(lines, regimeBands());

    renderMarkers($("marker-strategy").value);
    priceChart.chart.timeScale().fitContent();
  }

  // bull = bottom→next top (green); bear = top→next bottom (red).
  function regimeBands() {
    const pts = (state.events.notable || [])
      .filter((e) => e.kind === "top" || e.kind === "bottom")
      .slice()
      .sort((a, b) => (a.date < b.date ? -1 : 1));
    const bands = [];
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i], b = pts[i + 1];
      if (a.kind === "bottom" && b.kind === "top") {
        bands.push({ from: a.date, to: b.date, color: "rgba(63,185,80,0.06)" });
      } else if (a.kind === "top" && b.kind === "bottom") {
        bands.push({ from: a.date, to: b.date, color: "rgba(248,81,73,0.06)" });
      }
    }
    return bands;
  }

  function renderMarkers(strategyName) {
    if (!state.result) return;
    const r = state.result.results.find((x) => x.strategy === strategyName);
    if (!r) { priceChart.candles.setMarkers([]); return; }
    const markers = r.trades.map((t) => ({
      time: t.date,
      position: t.side === "BUY" ? "belowBar" : "aboveBar",
      color: t.side === "BUY" ? "#3fb950" : "#f85149",
      shape: t.side === "BUY" ? "arrowUp" : "arrowDown",
      text: t.side === "BUY" ? "B" : "S",
    }));
    markers.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
    priceChart.candles.setMarkers(markers);
  }

  function populateMarkerSelect() {
    const sel = $("marker-strategy");
    const names = state.result.results.map((r) => r.strategy);
    sel.innerHTML = "";
    for (const n of names) {
      const opt = document.createElement("option");
      opt.value = n; opt.textContent = n;
      sel.appendChild(opt);
    }
    // prefer a low-turnover default so the price chart isn't swamped with arrows
    if (names.includes("buy_hold")) sel.value = "buy_hold";
  }

  function applyLogScale() {
    const mode = $("log-scale").checked
      ? window.LightweightCharts.PriceScaleMode.Logarithmic
      : window.LightweightCharts.PriceScaleMode.Normal;
    priceChart.chart.priceScale("right").applyOptions({ mode });
  }

  // ---------------------------------------------------------------------------
  // rendering — equity chart
  // ---------------------------------------------------------------------------
  function currentLens() {
    const el = document.querySelector('input[name="equity-lens"]:checked');
    return el ? el.value : "after_tax";
  }

  function renderEquity(lens) {
    if (!state.result) return;
    // tear down previous lines, rebuild from scratch (cheap, < a few k points)
    for (const s of equityChart.series.values()) equityChart.chart.removeSeries(s);
    equityChart.series.clear();

    const valueKey = lens === "pre_tax" ? "pre_tax_value" : "after_tax_value";
    const legend = $("equity-legend");
    legend.innerHTML = "";

    state.result.results.forEach((r, i) => {
      const color = window.Arena.colorFor(i);
      const line = equityChart.chart.addLineSeries({
        color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
      });
      line.setData(r.snapshots.map((s) => ({ time: s.date, value: s[valueKey] })));
      equityChart.series.set(r.strategy, line);

      const tag = document.createElement("span");
      tag.className = "legend-item";
      tag.innerHTML = `<span class="dot" style="background:${color}"></span>${r.strategy}`;
      legend.appendChild(tag);
    });
    equityChart.chart.timeScale().fitContent();
  }

  // ---------------------------------------------------------------------------
  // rendering — leaderboard
  // ---------------------------------------------------------------------------
  function renderLeaderboard() {
    const tbody = $("leaderboard").querySelector("tbody");
    tbody.innerHTML = "";
    state.result.leaderboard.forEach((row, i) => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td>${i + 1}</td>` +
        `<td>${row.name}</td>` +
        `<td>${fmt(row.score, 3)}</td>` +
        `<td class="${cls(row.after_tax_return_pct)}">${pct(row.after_tax_return_pct)}</td>` +
        `<td class="${cls(row.after_tax_cagr)}">${pct(row.after_tax_cagr)}</td>` +
        `<td class="neg">${pct(row.max_drawdown)}</td>` +
        `<td>${row.n_trades}</td>` +
        `<td>${money(row.after_tax_final)}</td>`;
      tr.addEventListener("click", () => {
        $("marker-strategy").value = row.name;
        renderMarkers(row.name);
      });
      tbody.appendChild(tr);
    });
  }

  // ---- formatting helpers ----
  const fmt = (v, d = 2) => (v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(d));
  const pct = (v) => (v == null || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(1)}%`);
  const money = (v) =>
    v == null ? "—" : `$${Math.round(v).toLocaleString("en-US")}`;
  const cls = (v) => (v == null ? "" : v >= 0 ? "pos" : "neg");

  document.addEventListener("DOMContentLoaded", init);
})();
