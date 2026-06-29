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
    await Promise.all([loadStrategies(), loadEvents(), setDefaultDates()]);

    $("run").addEventListener("click", runBacktest);
    $("select-all").addEventListener("click", () => toggleAll(true));
    $("select-none").addEventListener("click", () => toggleAll(false));
    $("marker-strategy").addEventListener("change", () => {
      renderMarkersUpTo(true); renderFeedUpTo(true);
    });
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
    if (window.ArenaExtras) window.ArenaExtras.init();
  }

  // Pre-fill the End date with the latest available data day so the controls are
  // ready to race with no typing. Start already defaults to 2018-01-01 (the full
  // analysis window — short windows have too few cycles to be interesting).
  async function setDefaultDates() {
    if ($("end").value) return;
    try {
      const s = await (await fetch("/api/data/status")).json();
      const ends = Object.values(s.prices || {}).map((p) => p.end).filter(Boolean).sort();
      if (ends.length) $("end").value = ends[ends.length - 1];
    } catch { /* leave blank → backend uses latest */ }
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
        const wrap = document.createElement("div");
        wrap.className = "strat-wrap";

        const label = document.createElement("label");
        label.className = "inline strat";
        label.title = s.description || "";
        const hasParams = Object.keys(s.param_schema || {}).length > 0;
        label.innerHTML =
          `<input type="checkbox" value="${s.name}" checked /> ` +
          `${s.name} <span class="muted">(${s.universe})</span>` +
          (hasParams ? ` <button type="button" class="param-toggle" title="parameters">⚙</button>` : "");
        wrap.appendChild(label);

        // Per-strategy parameter sliders, built straight from the declared schema
        // (plan modularity principle 1) — collapsed by default to keep the panel tidy.
        if (hasParams) {
          const params = document.createElement("div");
          params.className = "params";
          params.dataset.strat = s.name;
          params.hidden = true;
          for (const [key, spec] of Object.entries(s.param_schema)) {
            const row = document.createElement("div");
            row.className = "param-row";
            const dec = spec.type === "int" ? 0 : decimals(spec.step);
            row.innerHTML =
              `<div class="param-head"><span>${spec.label || key}</span>` +
              `<span class="pv">${Number(spec.default).toFixed(dec)}</span></div>` +
              `<input type="range" data-key="${key}" data-dec="${dec}" ` +
              `min="${spec.min}" max="${spec.max}" step="${spec.step}" value="${spec.default}" />`;
            const slider = row.querySelector("input");
            slider.addEventListener("input", () => {
              row.querySelector(".pv").textContent = Number(slider.value).toFixed(dec);
            });
            params.appendChild(row);
          }
          wrap.appendChild(params);
          label.querySelector(".param-toggle").addEventListener("click", (e) => {
            e.preventDefault();
            params.hidden = !params.hidden;
          });
        }
        box.appendChild(wrap);
      }
    } catch (err) {
      box.innerHTML = `<legend>Strategies</legend><p class="err">failed: ${err.message}</p>`;
    }
  }

  // smallest sensible decimal count to display a slider step (0.05 -> 2, 1 -> 0)
  function decimals(step) {
    const s = String(step);
    return s.includes(".") ? s.split(".")[1].length : 0;
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
      .map((cb) => ({ name: cb.value, params: readParams(cb.value) }));
  }

  // Collect a strategy's current slider values into a param-override object.
  function readParams(name) {
    const box = $("strategy-list").querySelector(`.params[data-strat="${name}"]`);
    if (!box) return {};
    const out = {};
    box.querySelectorAll('input[type="range"]').forEach((s) => {
      out[s.dataset.key] = Number(s.value);
    });
    return out;
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
      $("feed").hidden = false;
      // start at the starting line: everyone tied at the opening capital, curves
      // flat, no trades drawn yet — so pressing Play reveals the race rather than
      // replaying a result you've already seen. (applyFrame pins the equity x-axis to
      // the full window so drawing-in from a single day-0 point doesn't squash it.)
      playback.seek(0);

      // Stage-7 extras: winner card, export, Hall of Fame refresh, validators.
      if (window.ArenaExtras) window.ArenaExtras.onResult(state.result, body);

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

    // Pin the price y-range to the WHOLE window so the axis doesn't rescale as the
    // candles reveal (and so the price isn't given away by the axis). Log scale →
    // pad multiplicatively; floor above 0.
    let lo = Infinity, hi = -Infinity;
    for (const r of series) {
      if (r.low != null && r.low < lo) lo = r.low;
      if (r.high != null && r.high > hi) hi = r.high;
    }
    if (lo < hi) {
      const range = { minValue: Math.max(lo * 0.9, 1e-6), maxValue: hi * 1.1 };
      priceChart.candles.applyOptions({
        autoscaleInfoProvider: () => ({ priceRange: range }),
      });
    }

    // The price itself is revealed in sync with the race (see applyFrame) so you
    // discover how Bitcoin moved at the same time as the bots — start it CLEARED.
    priceChart.candles.setData([]);
    priceChart.ma.setData([]);
    priceChart.candles.setMarkers([]);

    // halving + cycle vertical lines, bull/bear regime bands (the overlay clips
    // these to the playhead, so nothing ahead of "now" is revealed either).
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

  // Reveal the selected strategy's buy/sell arrows up to the current playback
  // frame, so they appear in sync as the race plays (slow the speed down and you
  // watch each decision happen). force=true rebuilds from scratch (strategy
  // changed, or a seek); otherwise we only touch the chart when the playhead has
  // newly crossed one or more trades — so most frames are free and a high-turnover
  // strategy's arrows trickle in over the run instead of all landing at once.
  function renderMarkersUpTo(force) {
    const track = state.tracks.find((t) => t.name === $("marker-strategy").value);
    if (!track || !track.marks) {
      priceChart.candles.setMarkers([]);
      state._shownMarks = 0; state._markName = null;
      return;
    }
    const idx = playback ? playback.index : 0;
    const count = countLE(track.marks, idx);   // # of marks whose axis-index <= idx
    if (!force && track.name === state._markName && count === state._shownMarks) return;
    priceChart.candles.setMarkers(track.marks.slice(0, count).map((x) => x.m));
    state._shownMarks = count;
    state._markName = track.name;
  }

  // Count of sorted marks whose axis index .i is <= idx (binary search).
  function countLE(marks, idx) {
    let lo = 0, hi = marks.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (marks[mid].i <= idx) lo = mid + 1; else hi = mid;
    }
    return lo;
  }

  // Play-by-play: the overlaid strategy's trades up to the current frame, newest
  // first, capped so a high-turnover strategy doesn't grow an unbounded list.
  // Same cheap pattern as the markers — only rebuild when the revealed count or
  // the selected strategy changes.
  const FEED_CAP = 40;
  function renderFeedUpTo(force) {
    const name = $("marker-strategy").value;
    $("feed-strategy").textContent = name || "—";
    const list = $("feed-list");
    const track = state.tracks.find((t) => t.name === name);
    if (!track || !track.feed) {
      list.innerHTML = ""; state._feedCount = 0; state._feedName = name; return;
    }
    const idx = playback ? playback.index : 0;
    const count = countLE(track.feed, idx);
    if (!force && name === state._feedName && count === state._feedCount) return;
    if (count === 0) {
      list.innerHTML = `<li class="feed-empty muted">waiting for ${escapeHtml(name)}'s first trade…</li>`;
    } else {
      const slice = track.feed.slice(Math.max(0, count - FEED_CAP), count).reverse();
      list.innerHTML = slice.map((f) => {
        const side = f.side === "BUY" ? "buy" : "sell";
        const why = f.reason ? `<span class="fr muted">${escapeHtml(f.reason)}</span>` : "";
        return `<li class="feed-row ${side}"><span class="fd">${f.date}</span>` +
          `<span class="fs">${f.side}</span>` +
          `<span class="fa">${money(f.proceeds)} @ ${money(f.price)}</span>${why}</li>`;
      }).join("");
    }
    state._feedCount = count; state._feedName = name;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
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
    // Default to the winner: "what the best strategy did" is the most interesting
    // thing to watch reveal on the chart (you can switch via this dropdown or by
    // clicking any leaderboard row). Falls back to buy_hold if there's no board.
    const winner = (state.result.leaderboard || [])[0];
    if (winner && names.includes(winner.name)) sel.value = winner.name;
    else if (names.includes("buy_hold")) sel.value = "buy_hold";
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
      // buy/sell arrow markers tagged with their axis index, sorted, so they can
      // be revealed in step with the playback clock (see renderMarkersUpTo); and a
      // parallel play-by-play feed (date/side/$/reason) for the same reveal.
      const marks = [];
      const feed = [];
      // Threshold strategies fire fractional orders EVERY day their condition holds,
      // so windows trail off into negligible "dust" trades. The arrow markers keep
      // them all (they cluster meaningfully), but the play-by-play feed is a
      // highlights log — skip trades below ~0.2% of opening capital so it reads as
      // the strategy's real moves, not a wall of $0 rows.
      const openVal = r.snapshots[0] ? r.snapshots[0].pre_tax_value : 0;
      const feedMin = Math.max(1, 0.002 * openVal);
      for (const t of r.trades) {
        const ti = idxOf.get(t.date);
        if (ti == null) continue;
        cum[ti] += 1;
        marks.push({ i: ti, m: {
          time: t.date,
          position: t.side === "BUY" ? "belowBar" : "aboveBar",
          color: t.side === "BUY" ? "#3fb950" : "#f85149",
          shape: t.side === "BUY" ? "arrowUp" : "arrowDown",
          text: t.side === "BUY" ? "B" : "S",
        } });
        if (t.proceeds >= feedMin) {
          feed.push({ i: ti, date: t.date, side: t.side, price: t.price, proceeds: t.proceeds, reason: t.reason });
        }
      }
      for (let k = 1; k < cum.length; k++) cum[k] += cum[k - 1];
      marks.sort((a, b) => a.i - b.i);
      feed.sort((a, b) => a.i - b.i);
      return {
        name: r.strategy,
        color: window.Arena.colorFor(i),
        pre: r.snapshots.map((s) => ({ time: s.date, value: s.pre_tax_value })),
        after: r.snapshots.map((s) => ({ time: s.date, value: s.after_tax_value })),
        cumTrades: cum,
        marks,
        feed,
        score: scoreOf.get(r.strategy),
      };
    });

    // price candles + 200W-MA, aligned to the race axis so they reveal in lockstep
    // with the playback clock (the price stays hidden until you Play).
    const priceByDate = new Map((state.price.series || []).map((r) => [r.date, r]));
    let lastC = null;
    state.candlesAxis = state.axis.map((d) => {
      const r = priceByDate.get(d);
      if (r) { lastC = { time: d, open: r.open, high: r.high, low: r.low, close: r.close }; return lastC; }
      return lastC ? { time: d, open: lastC.close, high: lastC.close, low: lastC.close, close: lastC.close } : null;
    });
    state.maAxis = state.axis.map((d) => {
      const r = priceByDate.get(d);
      return r && r.ma_200w != null ? { time: d, value: r.ma_200w } : null;
    });

    computeEquityRange();
    state._prevIndex = -1;
    state._shownMarks = 0;
    state._markName = null;
    state._feedCount = 0;
    state._feedName = null;
    state._revealed = false;
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

      // Clickable legend (plan HoF visual 3: toggle lines on/off as they accumulate).
      const tag = document.createElement("span");
      tag.className = "legend-item toggle";
      tag.title = "click to show/hide this line";
      tag.innerHTML = `<span class="dot" style="background:${t.color}"></span>${t.name}`;
      tag.addEventListener("click", () => {
        const vis = !(tag.classList.contains("off"));
        line.applyOptions({ visible: !vis });
        tag.classList.toggle("off", vis);
      });
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
        renderMarkersUpTo(true); renderFeedUpTo(true);
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

    // reveal the price candles + 200W-MA in lockstep, so Bitcoin's path is hidden
    // until you Play and is discovered alongside the bots' results (same draw-in
    // pattern as the equity curves: setData a slice on seek, update() on tick).
    if (state.candlesAxis) {
      if (isSeek) {
        priceChart.candles.setData(state.candlesAxis.slice(0, index + 1));
        priceChart.ma.setData(state.maAxis.slice(0, index + 1).filter((m) => m));
      } else {
        for (let j = state._prevIndex + 1; j <= index; j++) {
          if (state.candlesAxis[j]) priceChart.candles.update(state.candlesAxis[j]);
          if (state.maAxis[j]) priceChart.ma.update(state.maAxis[j]);
        }
      }
    }
    state._prevIndex = index;

    // Pin BOTH chart x-axes to the WHOLE window on every seek (incl. the initial
    // seek(0), lens switch, scrubber, jump-to-event). Seeking sets the series to a
    // short slice, which would otherwise collapse the time scale to those few bars
    // and re-expand jumpily; a fixed logical range keeps it stable left-to-right as
    // the curves/candles draw in. Ticks use update() (append), so they don't need it.
    if (isSeek && state.axis.length) {
      const lr = { from: 0, to: state.axis.length - 1 };
      equityChart.chart.timeScale().setVisibleLogicalRange(lr);
      priceChart.chart.timeScale().setVisibleLogicalRange(lr);
    }

    const date = state.axis[index];
    priceChart.overlay.setPlayhead(date);
    renderMarkersUpTo(isSeek);   // reveal trades up to this frame (rebuild on seek)
    renderFeedUpTo(isSeek);      // play-by-play for the overlaid strategy
    updateReadout(index, date);
    renderRaceFrame(index, lens);

    // crossing the finish line reveals the winner card + validators (once per run)
    if (index >= state.axis.length - 1 && !state._revealed) {
      state._revealed = true;
      if (window.ArenaExtras) window.ArenaExtras.revealWinner();
    }
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
