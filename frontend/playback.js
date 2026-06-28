// Stage 6 — the race: a tiny client-side playback clock.
//
// All per-day data is already computed once server-side (Stage 4) and rendered by
// the Stage-5 series; this controller just walks an index along the shared date
// axis and fires callbacks so app.js can draw the equity curves in, sweep the price
// playhead, reorder the leaderboard, and update the date/phase readout. No new
// backend, no websockets — "compute once, animate client-side".
//
// `speed` is days-of-simulated-time per real second; the rAF loop accumulates
// fractional days so a slow speed still advances smoothly and a fast one can step
// multiple days per frame. Exposed on `window.Arena.Playback`.

(function () {
  "use strict";

  class Playback {
    constructor({ onTick, onState } = {}) {
      this.onTick = onTick || (() => {});   // (index, isSeek) => void
      this.onState = onState || (() => {}); // ({index,length,playing,speed}) => void
      this.length = 0;     // number of frames (= snapshots on the axis)
      this.index = 0;      // current frame, clamped to [0, length-1]
      this.playing = false;
      this.speed = 60;     // simulated days per real second
      this._raf = null;
      this._lastTs = 0;
      this._acc = 0;       // accumulated fractional days
      this._loop = this._loop.bind(this);
    }

    setLength(n) {
      this.length = Math.max(0, n | 0);
      this.index = Math.min(this.index, Math.max(0, this.length - 1));
      this._emit();
    }

    setSpeed(daysPerSec) {
      this.speed = Math.max(1, Number(daysPerSec) || 1);
      this._emit();
    }

    // Jump to a frame (scrubber / jump-to-event / lens change). isSeek=true tells
    // the tick handler to re-draw from scratch rather than append incrementally.
    seek(index, { silent = false } = {}) {
      if (this.length === 0) return;
      this.index = Math.max(0, Math.min(this.length - 1, Math.round(index)));
      if (!silent) this.onTick(this.index, true);
      this._emit();
    }

    play() {
      if (this.length === 0 || this.playing) return;
      // restart from the top if parked at the finish line
      if (this.index >= this.length - 1) {
        this.index = 0;
        this.onTick(0, true);
      }
      this.playing = true;
      this._lastTs = performance.now();
      this._acc = 0;
      this._raf = requestAnimationFrame(this._loop);
      this._emit();
    }

    pause() {
      this.playing = false;
      if (this._raf) {
        cancelAnimationFrame(this._raf);
        this._raf = null;
      }
      this._emit();
    }

    toggle() {
      this.playing ? this.pause() : this.play();
    }

    _loop(ts) {
      if (!this.playing) return;
      const dt = (ts - this._lastTs) / 1000;
      this._lastTs = ts;
      this._acc += dt * this.speed;
      if (this._acc >= 1) {
        const step = Math.floor(this._acc);
        this._acc -= step;
        this.index = Math.min(this.length - 1, this.index + step);
        this.onTick(this.index, false);
        this._emit();
        if (this.index >= this.length - 1) {
          this.pause();
          return;
        }
      }
      this._raf = requestAnimationFrame(this._loop);
    }

    _emit() {
      this.onState({
        index: this.index,
        length: this.length,
        playing: this.playing,
        speed: this.speed,
      });
    }
  }

  window.Arena = window.Arena || {};
  window.Arena.Playback = Playback;
})();
