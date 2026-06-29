// Stage 7 — polish layer: winner summary, JSON export, persistent Hall of Fame,
// and the out-of-sample validators (walk-forward + start-date robustness).
//
// app.js owns the race; this module owns everything that turns a race into a
// *decision*: it declares the winner by the after-tax risk-adjusted definition,
// lets you export that winner's config as the bridge artifact for the future live
// bot, surfaces the accumulating Hall of Fame ledger (table + two charts), and runs
// the honesty checks that separate a real edge from curve-fit luck. Exposed as
// `window.ArenaExtras`; app.js calls `init()` once and `onResult(result, config)`
// after every race.

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const state = {
    result: null,     // last /api/backtest response
    config: null,     // the request body (asset/window/fee/tax/capital) for validators
    winner: null,     // { strategy, params } — leaderboard #1
  };

  function init() {
    $("run-walkforward").addEventListener("click", () => validate("walk-forward"));
    $("run-robustness").addEventListener("click", () => validate("robustness"));
    refreshHallOfFame(); // show whatever's accumulated across past sessions
  }

  function onResult(result, config) {
    state.result = result;
    state.config = config;
    const top = (result.leaderboard || [])[0];
    if (!top) return;
    const r = result.results.find((x) => x.strategy === top.name);
    state.winner = { strategy: top.name, params: r ? r.params : {} };

    // Compute + persist the result now, but keep the winner card and validators
    // HIDDEN until the race crosses the finish line — app.js calls revealWinner()
    // then. The win is a payoff at the end, not a spoiler shown before you Play.
    state._pending = { top, r };
    $("winner-card").hidden = true;
    $("validation").hidden = true;
    refreshHallOfFame(result.settings_sig);
  }

  // Reveal the winner card + out-of-sample validators once the race has reached
  // the finish (played to the end, or scrubbed there). Idempotent.
  function revealWinner() {
    if (!state._pending) return;
    const { top, r } = state._pending;
    renderWinner(top, r);
    $("validation").hidden = false;
    $("validation-out").innerHTML =
      `<p class="muted">Validate <strong>${top.name}</strong> — the winner — on unseen data.</p>`;
  }

  // ---------------------------------------------------------------------------
  // winner summary card
  // ---------------------------------------------------------------------------
  function renderWinner(top, result) {
    const card = $("winner-card");
    const at = result ? result.metrics.after_tax : {};
    const bh = bestBenchmark();
    let alphaRow = "";
    if (bh && top.name !== "buy_hold") {
      const aRet = (top.after_tax_return_pct - bh.ret) * 100;
      const aVal = top.after_tax_final - bh.final;
      alphaRow =
        stat("Alpha vs Buy&Hold",
          `${aRet >= 0 ? "+" : ""}${aRet.toFixed(1)} pts`,
          aRet >= 0 ? "pos" : "neg",
          `${aVal >= 0 ? "+" : ""}${money(aVal)} final`);
    } else if (top.name === "buy_hold") {
      alphaRow = stat("Alpha vs Buy&Hold", "—", "", "winner is the benchmark");
    }

    card.hidden = false;
    card.innerHTML =
      `<div class="winner-head">` +
        `<span class="trophy">🏆</span>` +
        `<div><div class="winner-name">${top.name}</div>` +
        `<div class="muted">best by after-tax, risk-adjusted score — not raw return</div></div>` +
        `<button type="button" id="export-winner-top" class="ghost">⤓ Export JSON</button>` +
      `</div>` +
      `<div class="winner-stats">` +
        stat("Std. score", fmt(top.score, 2)) +
        stat("After-tax CAGR", pct(top.after_tax_cagr), cls(top.after_tax_cagr)) +
        stat("Total return", pct(top.after_tax_return_pct), cls(top.after_tax_return_pct)) +
        stat("Final value", money(top.after_tax_final)) +
        stat("Max drawdown", pct(top.max_drawdown), "neg") +
        stat("Sharpe", fmt(at.sharpe, 2)) +
        stat("Trades", String(top.n_trades)) +
        stat("Tax paid", money(at.total_tax_paid)) +
        alphaRow +
      `</div>`;
    $("export-winner-top").addEventListener("click", exportWinner);
  }

  function stat(label, value, valueCls = "", sub = "") {
    return `<div class="wstat"><span class="wlabel">${label}</span>` +
      `<span class="wvalue ${valueCls}">${value}</span>` +
      (sub ? `<span class="wsub muted">${sub}</span>` : "") + `</div>`;
  }

  // After-tax Buy & Hold from the current result (the universal benchmark).
  function bestBenchmark() {
    if (!state.result) return null;
    const r = state.result.leaderboard.find((x) => x.name === "buy_hold");
    return r ? { ret: r.after_tax_return_pct, final: r.after_tax_final } : null;
  }

  // ---------------------------------------------------------------------------
  // export the winner as the bridge-artifact JSON (plan: contract to the live bot)
  // ---------------------------------------------------------------------------
  function exportWinner() {
    if (!state.winner || !state.result) return;
    const res = state.result;
    const top = res.leaderboard[0];
    const r = res.results.find((x) => x.strategy === top.name);
    const artifact = {
      artifact: "crypto-arena-winner",
      schema_version: res.schema_version,
      generated_at: new Date().toISOString(),
      strategy: state.winner.strategy,
      params: state.winner.params,
      asset: res.asset,
      window: { start: res.start, end: res.end },
      fee_pct: res.fee_pct,
      tax: res.tax,
      capital: res.capital,
      standardized_score: top.score,
      metrics_after_tax: r ? r.metrics.after_tax : null,
      note:
        "Game candidate only. Validate walk-forward + start-date robustness, then " +
        "paper-trade live before committing real capital. Tax figures are an " +
        "estimate for ranking, not for filing.",
    };
    const blob = new Blob([JSON.stringify(artifact, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `winner-${artifact.strategy}-${res.asset}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(a.href);
  }

  // ---------------------------------------------------------------------------
  // validators (walk-forward / robustness) — operate on the current winner
  // ---------------------------------------------------------------------------
  async function validate(kind) {
    if (!state.winner || !state.config) return;
    const out = $("validation-out");
    const c = state.config;
    const body = {
      strategy: state.winner.strategy,
      asset: c.asset, start: c.start, end: c.end,
      capital: c.capital, fee_pct: c.fee_pct, tax: c.tax,
    };
    if (kind === "robustness") body.params = state.winner.params;

    const btn = kind === "walk-forward" ? $("run-walkforward") : $("run-robustness");
    btn.disabled = true;
    out.innerHTML = `<p class="muted">Running ${kind} on <strong>${body.strategy}</strong>…</p>`;
    try {
      const res = await fetch(`/api/${kind}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await errText(res));
      const data = await res.json();
      out.innerHTML = kind === "walk-forward"
        ? renderWalkForward(data) : renderRobustness(data);
    } catch (err) {
      out.innerHTML = `<p class="err">${kind} failed: ${err.message}</p>`;
    } finally {
      btn.disabled = false;
    }
  }

  function verdictBadge(v) {
    return `<span class="verdict ${v}">${v === "robust" ? "✓ robust" : "⚠ fragile"}</span>`;
  }

  function renderWalkForward(d) {
    const rows = d.folds.map((f) =>
      `<tr><td>${f.fold}</td><td>${f.test_start} → ${f.test_end}</td>` +
      `<td class="num">${f.oos_score.toFixed(2)}</td>` +
      `<td class="num ${cls(f.alpha_vs_bh)}">${(f.alpha_vs_bh * 100).toFixed(1)} pts</td>` +
      `<td>${f.beats_bh ? "✓" : "·"}</td></tr>`).join("");
    return `<div class="val-summary">Walk-forward ${verdictBadge(d.verdict)} ` +
      `<span class="muted">tune in-sample → score out-of-sample, ${d.n_folds} folds · ` +
      `mean OOS score ${d.mean_oos_score.toFixed(2)} · beats B&H ${(d.beats_bh_rate * 100).toFixed(0)}% of folds</span></div>` +
      `<table class="val-table"><thead><tr><th>#</th><th>out-of-sample window</th>` +
      `<th class="num">OOS score</th><th class="num">alpha vs B&H</th><th>beat?</th></tr></thead>` +
      `<tbody>${rows}</tbody></table>` +
      `<p class="muted tiny">Walk-forward is the real test of an edge: params are tuned only on each fold's past, then judged on its untouched future.</p>`;
  }

  function renderRobustness(d) {
    const rows = d.runs.map((r) =>
      `<tr><td>${r.start}</td>` +
      `<td class="num">${r.score.toFixed(2)}</td>` +
      `<td class="num ${cls(r.return_pct)}">${pct(r.return_pct)}</td>` +
      `<td class="num ${cls(r.alpha_vs_bh)}">${(r.alpha_vs_bh * 100).toFixed(1)} pts</td>` +
      `<td>${r.beats_bh ? "✓" : "·"}</td></tr>`).join("");
    return `<div class="val-summary">Start-date robustness ${verdictBadge(d.verdict)} ` +
      `<span class="muted">${d.n_starts} entry dates · beats B&H ${(d.beats_bh_rate * 100).toFixed(0)}% · ` +
      `score ${d.score_min.toFixed(2)}–${d.score_max.toFixed(2)}</span></div>` +
      `<table class="val-table"><thead><tr><th>start</th><th class="num">score</th>` +
      `<th class="num">return</th><th class="num">alpha vs B&H</th><th>beat?</th></tr></thead>` +
      `<tbody>${rows}</tbody></table>` +
      `<p class="muted tiny">A strategy that only wins from one lucky entry date is fragile; a real edge wins from most.</p>`;
  }

  // ---------------------------------------------------------------------------
  // persistent Hall of Fame (table + ranked bars + best-score-over-time)
  // ---------------------------------------------------------------------------
  async function refreshHallOfFame(sig) {
    const meta = $("hof-meta");
    try {
      const res = await fetch("/api/hall-of-fame" + (sig ? `?settings_sig=${encodeURIComponent(sig)}` : ""));
      const data = await res.json();
      if (!data.n_runs) {
        meta.textContent = "No runs recorded yet — run a race to start building the ledger.";
        $("hof-table").innerHTML = "";
        $("hof-bars").innerHTML = "";
        $("hof-bestline").innerHTML = "";
        return;
      }
      meta.innerHTML =
        `${data.n_runs} run${data.n_runs === 1 ? "" : "s"} · ` +
        `${data.strategies.length} strateg${data.strategies.length === 1 ? "y" : "ies"} · ` +
        `<span class="sig">${settingsLabel(data.settings_sig)}</span>`;
      renderHofTable(data.strategies);
      renderHofBars(data.strategies);
      renderBestLine(data.best_over_time);
    } catch (err) {
      meta.textContent = `Hall of Fame unavailable: ${err.message}`;
    }
  }

  function settingsLabel(sig) {
    try {
      const s = JSON.parse(sig);
      const tax = s.tax && s.tax.enabled ? `tax ${(s.tax.rate * 100).toFixed(0)}%` : "no tax";
      return `${s.asset} · ${s.range[0]}→${s.range[1]} · fee ${(s.fee_pct * 100).toFixed(3)}% · ${tax}`;
    } catch {
      return "current settings";
    }
  }

  function renderHofTable(strategies) {
    const box = $("hof-table");
    box.innerHTML = "";
    strategies.forEach((e, i) => {
      const row = document.createElement("div");
      row.className = "hof-row" + (i === 0 ? " lead" : "");
      const expandable = e.configs.length > 1;
      row.innerHTML =
        `<span class="rank">${i + 1}</span>` +
        `<span class="name">${expandable ? '<span class="caret">▸</span>' : "<span class='caret'></span>"}${e.strategy}</span>` +
        `<span class="num">${fmt(e.best_score, 2)}</span>` +
        `<span class="num ${cls(e.after_tax_cagr)}">${pct(e.after_tax_cagr)}</span>` +
        `<span class="num neg">${pct(e.max_drawdown)}</span>` +
        `<span class="num">${e.times_tried}</span>` +
        `<span class="num">${e.margin_over_bh == null ? "—" : (e.margin_over_bh >= 0 ? "+" : "") + fmt(e.margin_over_bh, 2)}</span>`;
      box.appendChild(row);

      if (expandable) {
        const sub = document.createElement("div");
        sub.className = "hof-configs";
        sub.hidden = true;
        sub.innerHTML = e.configs.map((c) =>
          `<div class="cfg-row"><span class="cfg-params">${paramStr(c.params)}</span>` +
          `<span class="num">${fmt(c.score, 2)}</span>` +
          `<span class="num ${cls(c.after_tax_cagr)}">${pct(c.after_tax_cagr)}</span>` +
          `<span class="num muted">×${c.tries}</span></div>`).join("");
        box.appendChild(sub);
        row.classList.add("clickable");
        row.addEventListener("click", () => {
          sub.hidden = !sub.hidden;
          row.querySelector(".caret").textContent = sub.hidden ? "▸" : "▾";
        });
      }
    });
  }

  function paramStr(params) {
    const keys = Object.keys(params);
    if (!keys.length) return "(no params)";
    return keys.map((k) => `${k}=${params[k]}`).join("  ");
  }

  function renderHofBars(strategies) {
    const box = $("hof-bars");
    const scores = strategies.map((s) => s.best_score);
    const max = Math.max(...scores, 0.0001);
    const min = Math.min(...scores, 0);
    const span = max - min || 1;
    box.innerHTML = strategies.map((s) => {
      const w = ((s.best_score - min) / span) * 100;
      const col = s.best_score >= 0 ? "var(--accent)" : "var(--neg)";
      return `<div class="bar-row"><span class="bar-label">${s.strategy}</span>` +
        `<span class="bar-track"><span class="bar-fill" style="width:${Math.max(2, w).toFixed(1)}%;background:${col}"></span></span>` +
        `<span class="bar-val num">${fmt(s.best_score, 2)}</span></div>`;
    }).join("");
  }

  // A small inline-SVG step line: best score found vs experiment number.
  function renderBestLine(points) {
    const box = $("hof-bestline");
    if (!points || points.length < 2) {
      box.innerHTML = `<p class="muted tiny">Run a few races to chart the meta-game.</p>`;
      return;
    }
    const W = 320, H = 90, pad = 6;
    const xs = points.map((p) => p.n);
    const ys = points.map((p) => p.best_score);
    const xMin = Math.min(...xs), xMax = Math.max(...xs);
    const yMin = Math.min(...ys), yMax = Math.max(...ys);
    const xspan = xMax - xMin || 1, yspan = yMax - yMin || 1;
    const X = (x) => pad + ((x - xMin) / xspan) * (W - 2 * pad);
    const Y = (y) => H - pad - ((y - yMin) / yspan) * (H - 2 * pad);
    const d = points.map((p, i) => `${i ? "L" : "M"}${X(p.n).toFixed(1)},${Y(p.best_score).toFixed(1)}`).join(" ");
    const dots = points.map((p) =>
      `<circle cx="${X(p.n).toFixed(1)}" cy="${Y(p.best_score).toFixed(1)}" r="2" fill="var(--accent)"/>`).join("");
    box.innerHTML =
      `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="bestline-svg">` +
      `<path d="${d}" fill="none" stroke="var(--accent)" stroke-width="1.6"/>${dots}</svg>` +
      `<div class="bestline-axis muted tiny"><span>1 run</span><span>best ${fmt(yMax, 2)}</span>` +
      `<span>${xMax} runs</span></div>`;
  }

  // ---- helpers ----
  async function errText(res) {
    try {
      const j = await res.json();
      return j.detail ? (typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail)) : `HTTP ${res.status}`;
    } catch {
      return `HTTP ${res.status}`;
    }
  }
  const fmt = (v, d = 2) => (v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(d));
  const pct = (v) => (v == null || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(1)}%`);
  const money = (v) => (v == null ? "—" : `$${Math.round(v).toLocaleString("en-US")}`);
  const cls = (v) => (v == null ? "" : v >= 0 ? "pos" : "neg");

  window.ArenaExtras = { init, onResult, revealWinner };
})();
