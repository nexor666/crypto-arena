// Stage 5 — TradingView Lightweight Charts setup.
//
// Two charts drive the static race view:
//   1. a candlestick PRICE chart with the 200-Week MA overlaid, halving + cycle
//      vertical lines, bull/bear regime shading, and the selected strategy's
//      buy/sell markers;
//   2. a multi-line EQUITY chart, one line per raced strategy.
//
// Everything here is pure rendering — app.js owns the data/state and calls these
// factories. Exposed on `window.Arena`. (The Stage-6 playback engine will animate
// these same series; the schema is already per-day so nothing here changes.)

(function () {
  "use strict";

  const LWC = window.LightweightCharts;

  // Shared dark theme matching styles.css.
  const THEME = {
    layout: { background: { color: "#0e1117" }, textColor: "#c9d1d9" },
    grid: {
      vertLines: { color: "rgba(139,148,158,0.08)" },
      horzLines: { color: "rgba(139,148,158,0.08)" },
    },
    rightPriceScale: { borderColor: "rgba(139,148,158,0.25)" },
    // minBarSpacing well below 1px so fitContent can show a full multi-year daily
    // window (≈3100+ bars) instead of clipping to the rightmost few years.
    timeScale: { borderColor: "rgba(139,148,158,0.25)", rightOffset: 4, minBarSpacing: 0.04 },
    crosshair: { mode: LWC.CrosshairMode.Normal },
    autoSize: false,
  };

  // A distinct, color-blind-friendlier palette for strategy equity lines.
  const PALETTE = [
    "#f7931a", "#3fb950", "#58a6ff", "#bc8cff", "#f778ba",
    "#56d4dd", "#d29922", "#ff7b72", "#7ee787", "#a5d6ff",
  ];

  function colorFor(index) {
    return PALETTE[index % PALETTE.length];
  }

  // ---------------------------------------------------------------------------
  // A single series primitive that paints (behind the candles) the bull/bear
  // regime bands and the halving / cycle-event vertical lines. Wrapped defensively
  // by the caller — if the primitive API ever shifts, charts still render.
  // ---------------------------------------------------------------------------
  class CycleOverlay {
    constructor() {
      this._lines = [];   // [{ time, color, dashed }]
      this._bands = [];   // [{ from, to, color }]
      this._chart = null;
    }
    setData(lines, bands) {
      this._lines = lines || [];
      this._bands = bands || [];
      if (this._requestUpdate) this._requestUpdate();
    }
    attached(params) {
      this._chart = params.chart;
      this._requestUpdate = params.requestUpdate;
    }
    detached() {
      this._chart = null;
      this._requestUpdate = null;
    }
    updateAllViews() {}
    paneViews() {
      return [{ renderer: () => ({ draw: (target) => this._draw(target) }) }];
    }
    _draw(target) {
      if (!this._chart) return;
      const timeScale = this._chart.timeScale();
      const x = (t) => {
        const c = timeScale.timeToCoordinate(t);
        return c === null ? null : c;
      };
      target.useBitmapCoordinateSpace((scope) => {
        const ctx = scope.context;
        const hr = scope.horizontalPixelRatio;
        const h = scope.bitmapSize.height;
        const w = scope.bitmapSize.width;

        // bull/bear regime bands first (drawn underneath the lines)
        for (const band of this._bands) {
          let x1 = x(band.from);
          let x2 = x(band.to);
          if (x1 === null && x2 === null) continue;
          // clamp an off-screen edge to the visible bounds so the band still shows
          if (x1 === null) x1 = 0;
          if (x2 === null) x2 = w / hr;
          const bx1 = Math.round(x1 * hr);
          const bx2 = Math.round(x2 * hr);
          ctx.fillStyle = band.color;
          ctx.fillRect(Math.min(bx1, bx2), 0, Math.abs(bx2 - bx1), h);
        }

        // halving / cycle vertical lines
        for (const line of this._lines) {
          const c = x(line.time);
          if (c === null) continue;
          const bx = Math.round(c * hr) + 0.5;
          ctx.save();
          ctx.beginPath();
          ctx.lineWidth = Math.max(1, Math.floor(hr));
          ctx.strokeStyle = line.color;
          if (line.dashed) ctx.setLineDash([Math.round(4 * hr), Math.round(4 * hr)]);
          ctx.moveTo(bx, 0);
          ctx.lineTo(bx, h);
          ctx.stroke();
          ctx.restore();
        }
      });
    }
  }

  // ---------------------------------------------------------------------------
  // Price chart: candles + 200W MA + a cycle overlay.
  // ---------------------------------------------------------------------------
  function createPriceChart(container) {
    const chart = LWC.createChart(container, {
      ...THEME,
      width: container.clientWidth,
      height: container.clientHeight || 360,
    });
    const candles = chart.addCandlestickSeries({
      upColor: "#3fb950", downColor: "#f85149",
      borderUpColor: "#3fb950", borderDownColor: "#f85149",
      wickUpColor: "#3fb950", wickDownColor: "#f85149",
      priceLineVisible: false,
    });
    const ma = chart.addLineSeries({
      color: "rgba(88,166,255,0.9)", lineWidth: 1,
      priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    const overlay = new CycleOverlay();
    try {
      candles.attachPrimitive(overlay);
    } catch (err) {
      console.warn("cycle overlay unavailable:", err);
    }

    autoResize(chart, container);
    return { chart, candles, ma, overlay };
  }

  // ---------------------------------------------------------------------------
  // Equity chart: a line series per strategy, added/removed on demand.
  // ---------------------------------------------------------------------------
  function createEquityChart(container) {
    const chart = LWC.createChart(container, {
      ...THEME,
      width: container.clientWidth,
      height: container.clientHeight || 320,
    });
    autoResize(chart, container);
    return { chart, series: new Map() };
  }

  function autoResize(chart, container) {
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) {
        const { width, height } = e.contentRect;
        if (width > 0 && height > 0) chart.applyOptions({ width, height });
      }
    });
    ro.observe(container);
  }

  window.Arena = { createPriceChart, createEquityChart, colorFor };
})();
