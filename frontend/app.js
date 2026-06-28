// Stage 6 — the race (playback + live leaderboard).
//
// Stage 5 rendered a backtest statically; Stage 6 animates it. A POST /api/backtest
// already returns the full per-day equity stream + trades for every strategy, so
// nothing new is fetched — a client-side clock (playback.js) just walks an index
// along the shared date axis and this module:
//   • draws the equity curves in over simulated time,
//   • sweeps a playhead across the price chart,
//   • reorders a live leaderboard by current portfolio value, and
//   • updates the current-date + bull/bear cycle-phase readout,
// with play/pause, a speed slider, a scrubber and jump-to-event buttons.

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const ROW_H = 33; // race-row height incl. border (keep in sync with styles.css)

  // ---- in-memory state (last result kept so re-rendering needs no refetch) ----
  const state = {
    strategies: [],          // from /api/strategies
    events: { halvings: [], notable: [] },
    result: null,            // last /api/backtest response
    price: null,             // last /api/price-data response
    axis: [],                // shared date axis (snapshot dates)
    tracks: [],              // per-strategy { name,color,pre[],after[],cumTrades[],score }
    rows: new Map(),         // strategy -> race-board row element
    equityRange: null,       // fixed y-range per lens (stable axis during the race)
    _prevIndex: -1,          // last drawn frame (for incremental equity updates)
  };
  let priceChart, equityChart, playback;

  // ---------------------------------------------------------------------------
  // bootstrap
  // ---------------------------------------------------------------------------
  async function init() {
    priceChart = window.Arena.createPriceChart($("price-chart"));
    equityChart = window.Arena.createEquityChart($("equity-chart"));
    playback = new window.Arena.Playback({ onTick: applyFrame, onState: onPlayState });

    checkHealth();
    await Promise.all([loadStrategies(), loadEvents()]);

    $("run").addEventListener("click", runBacktest);
    $("select-all").addEventListener("click", () => toggleAll(true));
    $("select-none").addEventListener("click", () => toggleAll(false));
    $("marker-strategy").addEventListener("change", () =>
      renderMarkers($("marker-strategy").value));
    $("log-scale").addEventListener("change", applyLogScale);
    document.querySelectorAll('input[name="equity-lens"]').forEach((el) =>
      el.addEventListener("change", () => {
        if (state.tracks.length) playback.seek(playback.index); // redraw in new lens
      }));

    // transport
    $("play").addEventListener("click", () => playback.toggle());
    $("scrub").addEventListener("input", () => playback.seek(Number($("scrub").value)));
    $("speed").addEventListener("input", () => {
      const v = Number($("speed").value);
      playback.setSpeed(v);
      $("speed-val").textContent = `${v} d/s`;
    });

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
    playback.pause();
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

      // wire up the race over the same per-day series
      buildPlaybackData();
      setupEquitySeries();
      setupRaceBoard();
      buildJumpButtons();
      $("transport").hidden = false;
      // park at the finish line so the full final result is visible until Play
      playback.seek(state.axis.length - 1);
      equityChart.chart.timeScale().fitContent();

      const r = state.result;
      status.textContent =
        `Ready — ${r.results.length} strategies, ${r.start} → ${r.end}. Press Play to race.`;
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
  // rendering — price chart (static context; the playhead sweeps over it)
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
        bands.push({ from: a.date, to: b.date, color: "rgba(63,185,80,0.06)", bull: true });
      } else if (a.kind === "top" && b.kind === "bottom") {
        bands.push({ from: a.date, to: b.date, color: "rgba(248,81,73,0.06)", bull: false });
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
  // playback data — precompute everything the per-frame loop needs, once
  // ---------------------------------------------------------------------------
  function currentLens() {
    const el = document.querySelector('input[name="equity-lens"]:checked');
    return el ? el.value : "after_tax";
  }

  function buildPlaybackData() {
    const results = state.result.results;
    state.axis = results.length ? results[0].snapshots.map((s) => s.date) : [];
    const idxOf = new Map(state.axis.map((d, i) => [d, i]));
    const scoreOf = new Map((state.result.leaderboard || []).map((r) => [r.name, r.score]));

    state.tracks = results.map((r, i) => {
      // cumulative trade count along the axis (so the live board can count up)
      const cum = new Array(state.axis.length).fill(0);
      for (const t of r.trades) {
        const ti = idxOf.get(t.date);
        if (ti != null) cum[ti] += 1;
      }
      for (let k = 1; k < cum.length; k++) cum[k] += cum[k - 1];
      return {
        name: r.strategy,
        color: window.Arena.colorFor(i),
        pre: r.snapshots.map((s) => ({ time: s.date, value: s.pre_tax_value })),
        after: r.snapshots.map((s) => ({ time: s.date, value: s.after_tax_value })),
        cumTrades: cum,
        score: scoreOf.get(r.strategy),
      };
    });

    computeEquityRange();
    state._prevIndex = -1;
    playback.setLength(state.axis.length);
  }

  // Fixed y-range per lens so the equity axis doesn't jump as lines draw in.
  function computeEquityRange() {
    const r = { pre_tax: { min: Infinity, max: -Infinity },
                after_tax: { min: Infinity, max: -Infinity } };
    for (const t of state.tracks) {
      for (const p of t.pre) {
        if (p.value < r.pre_tax.min) r.pre_tax.min = p.value;
        if (p.value > r.pre_tax.max) r.pre_tax.max = p.value;
      }
      for (const p of t.after) {
        if (p.value < r.after_tax.min) r.after_tax.min = p.value;
        if (p.value > r.after_tax.max) r.after_tax.max = p.value;
      }
    }
    for (const k of ["pre_tax", "after_tax"]) {
      const pad = (r[k].max - r[k].min) * 0.04 || 1;
      r[k].min -= pad;
      r[k].max += pad;
    }
    state.equityRange = r;
  }

  function setupEquitySeries() {
    for (const s of equityChart.series.values()) equityChart.chart.removeSeries(s);
    equityChart.series.clear();
    const legend = $("equity-legend");
    legend.innerHTML = "";

    state.tracks.forEach((t) => {
      const line = equityChart.chart.addLineSeries({
        color: t.color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
        autoscaleInfoProvider: () => {
          const range = state.equityRange && state.equityRange[currentLens()];
          return range ? { priceRange: { minValue: range.min, maxValue: range.max } } : null;
        },
      });
      equityChart.series.set(t.name, line);

      const tag = document.createElement("span");
      tag.className = "legend-item";
      tag.innerHTML = `<span class="dot" style="background:${t.color}"></span>${t.name}`;
      legend.appendChild(tag);
    });
  }

  function setupRaceBoard() {
    const board = $("race-board");
    board.innerHTML = "";
    board.style.height = `${state.tracks.length * ROW_H}px`;
    state.rows = new Map();
    state.tracks.forEach((t) => {
      const row = document.createElement("div");
      row.className = "race-row";
      row.innerHTML =
        `<span class="rank"></span>` +
        `<span class="name"><span class="dot" style="background:${t.color}"></span>` +
        `<span class="label">${t.name}</span></span>` +
        `<span class="ret"></span><span class="val"></span>` +
        `<span class="trd"></span><span class="score"></span>`;
      row.addEventListener("click", () => {
        $("marker-strategy").value = t.name;
        renderMarkers(t.name);
      });
      board.appendChild(row);
      state.rows.set(t.name, row);
    });
  }

  // ---------------------------------------------------------------------------
  // the per-frame loop — called by the playback clock
  // ---------------------------------------------------------------------------
  function applyFrame(index, isSeek) {
    if (!state.tracks.length) return;
    const lens = currentLens();

    state.tracks.forEach((t) => {
      const line = equityChart.series.get(t.name);
      if (!line) return;
      const pts = lens === "pre_tax" ? t.pre : t.after;
      if (isSeek) {
        line.setData(pts.slice(0, index + 1));
      } else {
        for (let j = state._prevIndex + 1; j <= index; j++) {
          if (pts[j]) line.update(pts[j]);
        }
      }
    });
    state._prevIndex = index;

    const date = state.axis[index];
    priceChart.overlay.setPlayhead(date);
    updateReadout(index, date);
    renderRaceFrame(index, lens);
  }

  function renderRaceFrame(index, lens) {
    const standings = state.tracks.map((t) => {
      const pts = lens === "pre_tax" ? t.pre : t.after;
      const v = (pts[index] || pts[pts.length - 1]).value;
      const init = t.pre[0] ? t.pre[0].value : 1;
      return {
        name: t.name, value: v, ret: init ? v / init - 1 : 0,
        trades: t.cumTrades[index] || 0, score: t.score,
      };
    });
    standings.sort((a, b) => b.value - a.value);

    standings.forEach((s, rank) => {
      const row = state.rows.get(s.name);
      if (!row) return;
      row.style.transform = `translateY(${rank * ROW_H}px)`;
      row.classList.toggle("lead", rank === 0);
      row.querySelector(".rank").textContent = rank + 1;
      const ret = row.querySelector(".ret");
      ret.textContent = pct(s.ret);
      ret.className = `ret ${cls(s.ret)}`;
      row.querySelector(".val").textContent = money(s.value);
      row.querySelector(".trd").textContent = s.trades;
      row.querySelector(".score").textContent = fmt(s.score, 2);
    });
  }

  function updateReadout(index, date) {
    $("play-date").textContent = date || "—";
    const ph = phaseAt(date);
    const el = $("play-phase");
    el.textContent = ph.label;
    el.className = `play-phase ${ph.phase}`;
    const scrub = $("scrub");
    if (document.activeElement !== scrub) scrub.value = index;
  }

  // bull/bear from the regime bands (between known tops/bottoms) + days since the
  // most recent halving. Outside a known band we don't claim a regime — we just
  // report the halving offset rather than mislabel the day.
  function phaseAt(date) {
    if (!date) return { phase: "neutral", label: "—" };
    let phase = "neutral", regime = "";
    for (const b of regimeBands()) {
      if (date >= b.from && date <= b.to) {
        phase = b.bull ? "bull" : "bear";
        regime = b.bull ? "Bull market" : "Bear market";
        break;
      }
    }
    let since = "";
    const past = (state.events.halvings || [])
      .map((h) => h.date).filter((d) => d <= date).sort();
    if (past.length) {
      const days = Math.round((Date.parse(date) - Date.parse(past[past.length - 1])) / 86400000);
      since = `${days}d since halving`;
    }
    return { phase, label: [regime, since].filter(Boolean).join(" · ") || "—" };
  }

  function onPlayState(st) {
    $("play").textContent = st.playing ? "❚❚ Pause" : "▶ Play";
    const scrub = $("scrub");
    scrub.max = Math.max(0, st.length - 1);
    if (document.activeElement !== scrub) scrub.value = st.index;
  }

  // ---------------------------------------------------------------------------
  // jump-to-event buttons (within the run window only)
  // ---------------------------------------------------------------------------
  function buildJumpButtons() {
    const box = $("jump-buttons");
    box.innerHTML = "";
    if (!state.axis.length) return;
    const first = state.axis[0], last = state.axis[state.axis.length - 1];
    const inRange = (d) => d >= first && d <= last;

    const mk = (label, date, kind) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      if (kind) b.className = kind;
      b.addEventListener("click", () => { playback.pause(); playback.seek(nearestIndex(date)); });
      box.appendChild(b);
    };

    mk("Start", first, "");
    for (const h of state.events.halvings || []) if (inRange(h.date)) mk(h.label, h.date, "halving");
    for (const e of state.events.notable || []) if (inRange(e.date)) mk(e.label, e.date, e.kind);
    mk("Latest", last, "");
  }

  function nearestIndex(date) {
    const a = state.axis;
    if (!a.length) return 0;
    if (date <= a[0]) return 0;
    if (date >= a[a.length - 1]) return a.length - 1;
    let lo = 0, hi = a.length - 1;
    while (lo < hi) {
      const m = (lo + hi) >> 1;
      if (a[m] < date) lo = m + 1; else hi = m;
    }
    return lo;
  }

  // ---- formatting helpers ----
  const fmt = (v, d = 2) => (v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(d));
  const pct = (v) => (v == null || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(1)}%`);
  const money = (v) =>
    v == null ? "—" : `$${Math.round(v).toLocaleString("en-US")}`;
  const cls = (v) => (v == null ? "" : v >= 0 ? "pos" : "neg");

  document.addEventListener("DOMContentLoaded", init);
})();
