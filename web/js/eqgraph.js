// Interactive EQ curve on <canvas> (port of openlogi/ui/widgets/eqgraph.py).

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const M = { l: 52, r: 28, t: 28, b: 40 };

function tangents(ys) {
  const n = ys.length;
  const d = ys.slice(0, -1).map((y, i) => ys[i + 1] - y);
  const m = new Array(n).fill(0);
  m[0] = d[0];
  m[n - 1] = d[n - 2];
  for (let i = 1; i < n - 1; i++) m[i] = d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (d[i] === 0) {
      m[i] = m[i + 1] = 0;
      continue;
    }
    const a = m[i] / d[i];
    const b = m[i + 1] / d[i];
    const s = a * a + b * b;
    if (s > 9) {
      const t = 3 / Math.sqrt(s);
      m[i] = t * a * d[i];
      m[i + 1] = t * b * d[i];
    }
  }
  return m;
}

const fmtFreq = (hz) => (hz >= 1000 ? `${hz / 1000}k` : String(hz));

export class EqGraph extends EventTarget {
  constructor(canvas, freqs = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000], range = 12) {
    super();
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.freqs = freqs;
    this.range = range;
    this.gains = freqs.map(() => 0);
    this.shown = this.gains.slice();
    this.hover = -1;
    this.drag = -1;
    this.selected = -1;
    this.enabled = true;
    this._anim = null;
    canvas.tabIndex = 0;
    new ResizeObserver(() => this.draw()).observe(canvas);
    canvas.addEventListener("pointerdown", (e) => this._down(e));
    canvas.addEventListener("pointermove", (e) => this._move(e));
    canvas.addEventListener("pointerup", (e) => this._up(e));
    canvas.addEventListener("pointerleave", () => {
      this.hover = -1;
      this.draw();
    });
    canvas.addEventListener("dblclick", (e) => this._reset(e));
    canvas.addEventListener("wheel", (e) => this._wheel(e), { passive: false });
    canvas.addEventListener("keydown", (e) => this._key(e));
  }

  setBands(freqs, range) {
    this.freqs = freqs;
    this.range = range;
    this.gains = freqs.map(() => 0);
    this.shown = this.gains.slice();
    this.draw();
  }

  setGains(gains, animate = true) {
    if (this.drag >= 0 || gains.length !== this.freqs.length) return;
    const from = this.shown.slice();
    this.gains = gains.map((g) => Math.round(g));
    if (!animate) {
      this.shown = this.gains.slice();
      return this.draw();
    }
    const start = performance.now();
    cancelAnimationFrame(this._anim);
    const step = (now) => {
      const t = Math.min(1, (now - start) / 260);
      const e = 1 - (1 - t) ** 3;
      this.shown = from.map((a, i) => a + (this.gains[i] - a) * e);
      this.draw();
      if (t < 1) this._anim = requestAnimationFrame(step);
    };
    this._anim = requestAnimationFrame(step);
  }

  setEnabled(on) {
    this.enabled = on;
    this.canvas.classList.toggle("disabled", !on);
    this.draw();
  }

  // -- geometry ------------------------------------------------------------------------
  _plot() {
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;
    return { x: M.l, y: M.t, w: w - M.l - M.r, h: h - M.t - M.b };
  }

  _x(i) {
    const p = this._plot();
    return p.x + (p.w / this.freqs.length) * (i + 0.5);
  }

  _y(db) {
    const p = this._plot();
    return p.y + p.h / 2 - (db / this.range) * (p.h / 2);
  }

  _dbAt(y) {
    const p = this._plot();
    const db = ((p.y + p.h / 2 - y) / (p.h / 2)) * this.range;
    return Math.round(Math.max(-this.range, Math.min(this.range, db)));
  }

  _bandAt(x) {
    const p = this._plot();
    if (x < p.x - 10 || x > p.x + p.w + 10) return -1;
    return Math.max(0, Math.min(this.freqs.length - 1, Math.floor((x - p.x) / (p.w / this.freqs.length))));
  }

  // -- drawing --------------------------------------------------------------------------
  draw() {
    const c = this.canvas;
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth;
    const h = c.clientHeight;
    if (!w || !h) return;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
      c.width = Math.round(w * dpr);
      c.height = Math.round(h * dpr);
    }
    const g = this.ctx;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    const p = this._plot();
    const accent = this.enabled ? css("--accent") : css("--text3");
    g.font = `11px ${css("--font")}`;

    // grid + dB labels
    for (let db = -this.range; db <= this.range; db += 3) {
      const y = this._y(db);
      g.strokeStyle = db === 0 ? css("--text3") : "rgba(255,255,255,.055)";
      g.setLineDash(db === 0 ? [4, 4] : []);
      g.beginPath();
      g.moveTo(p.x, y);
      g.lineTo(p.x + p.w, y);
      g.stroke();
      if (db % 6 === 0) {
        g.fillStyle = css("--text3");
        g.textAlign = "right";
        g.textBaseline = "middle";
        g.fillText(db > 0 ? `+${db}` : String(db), M.l - 12, y);
      }
    }
    g.setLineDash([]);
    g.textAlign = "right";
    g.fillText("dB", M.l - 12, p.y - 16);

    // band tracks + labels
    this.freqs.forEach((f, i) => {
      const x = this._x(i);
      const active = [this.hover, this.drag, this.selected].includes(i);
      g.strokeStyle = active ? "rgba(255,255,255,.14)" : "rgba(255,255,255,.06)";
      g.lineWidth = active ? 2 : 1;
      g.beginPath();
      g.moveTo(x, p.y);
      g.lineTo(x, p.y + p.h);
      g.stroke();
      g.fillStyle = active ? css("--text") : css("--text2");
      g.textAlign = "center";
      g.textBaseline = "top";
      g.fillText(fmtFreq(f), x, p.y + p.h + 12);
    });
    g.lineWidth = 1;

    // curve
    const pts = this.shown.map((db, i) => [this._x(i), this._y(db)]);
    pts.unshift([p.x, pts[0][1]]);
    pts.push([p.x + p.w, pts[pts.length - 1][1]]);
    const m = tangents(pts.map((q) => q[1]));
    const path = new Path2D();
    path.moveTo(...pts[0]);
    for (let i = 0; i < pts.length - 1; i++) {
      const [ax, ay] = pts[i];
      const [bx, by] = pts[i + 1];
      const dx = bx - ax;
      path.bezierCurveTo(ax + dx / 3, ay + m[i] / 3, bx - dx / 3, by - m[i + 1] / 3, bx, by);
    }
    const zero = this._y(0);
    const fill = new Path2D(path);
    fill.lineTo(p.x + p.w, zero);
    fill.lineTo(p.x, zero);
    fill.closePath();
    const grad = g.createLinearGradient(0, p.y, 0, p.y + p.h);
    grad.addColorStop(0, hexAlpha(accent, 0.35));
    grad.addColorStop(0.5, hexAlpha(accent, 0.07));
    grad.addColorStop(1, hexAlpha(accent, 0.35));
    g.fillStyle = grad;
    g.fill(fill);
    g.lineCap = "round";
    g.strokeStyle = hexAlpha(accent, 0.24);
    g.lineWidth = 7;
    g.stroke(path);
    g.strokeStyle = accent;
    g.lineWidth = 2.4;
    g.stroke(path);

    // handles
    this.shown.forEach((db, i) => {
      const x = this._x(i);
      const y = this._y(db);
      const hot = i === this.hover || i === this.drag;
      const sel = i === this.selected && document.activeElement === this.canvas;
      const r = hot || sel ? 9 : 7;
      if (hot && this.enabled) {
        const halo = g.createRadialGradient(x, y, 0, x, y, r * 2.6);
        halo.addColorStop(0, hexAlpha(accent, 0.45));
        halo.addColorStop(1, "rgba(0,0,0,0)");
        g.fillStyle = halo;
        g.beginPath();
        g.arc(x, y, r * 2.6, 0, Math.PI * 2);
        g.fill();
      }
      g.lineWidth = 2.5;
      g.strokeStyle = accent;
      g.fillStyle = sel && this.enabled ? css("--accent") : css("--bg0");
      g.beginPath();
      g.arc(x, y, r, 0, Math.PI * 2);
      g.fill();
      g.stroke();
      if (!(sel && this.enabled)) {
        g.fillStyle = this.enabled ? "#fff" : css("--text3");
        g.beginPath();
        g.arc(x, y, r * 0.38, 0, Math.PI * 2);
        g.fill();
      }
    });

    // value bubble
    const idx = this.drag >= 0 ? this.drag : this.hover;
    if (idx >= 0 && this.enabled) {
      const v = this.gains[idx];
      const txt = v ? `${v > 0 ? "+" : ""}${v} dB` : "0 dB";
      g.font = `600 12px ${css("--font")}`;
      const bw = g.measureText(txt).width + 18;
      const x = this._x(idx);
      let by = this._y(this.shown[idx]) - 42;
      if (by < 2) by = this._y(this.shown[idx]) + 18;
      g.fillStyle = css("--bg4");
      roundRect(g, x - bw / 2, by, bw, 24, 4);
      g.fill();
      g.fillStyle = css("--text");
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText(txt, x, by + 12);
    }
  }

  // -- interaction -----------------------------------------------------------------------
  _set(i, db, live = true) {
    db = Math.max(-this.range, Math.min(this.range, db));
    if (this.gains[i] === db) return;
    cancelAnimationFrame(this._anim);
    this.gains[i] = db;
    this.shown = this.gains.slice();
    this.draw();
    if (live) this.dispatchEvent(new CustomEvent("input", { detail: this.gains.slice() }));
  }

  _commit() {
    this.dispatchEvent(new CustomEvent("change", { detail: this.gains.slice() }));
  }

  _pos(e) {
    const r = this.canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  _down(e) {
    if (!this.enabled || e.button !== 0) return;
    const [x, y] = this._pos(e);
    const i = this._bandAt(x);
    if (i < 0) return;
    this.canvas.setPointerCapture(e.pointerId);
    this.drag = this.selected = i;
    this._set(i, this._dbAt(y));
    this.canvas.style.cursor = "grabbing";
  }

  _move(e) {
    const [x, y] = this._pos(e);
    if (this.drag >= 0) return this._set(this.drag, this._dbAt(y));
    const i = this._bandAt(x);
    if (i !== this.hover) {
      this.hover = i;
      this.canvas.style.cursor = i >= 0 && this.enabled ? "grab" : "default";
      this.draw();
    }
  }

  _up() {
    if (this.drag < 0) return;
    this.drag = -1;
    this.canvas.style.cursor = "grab";
    this._commit();
    this.draw();
  }

  _reset(e) {
    const i = this._bandAt(this._pos(e)[0]);
    if (i >= 0 && this.enabled) {
      this._set(i, 0);
      this._commit();
    }
  }

  _wheel(e) {
    const i = this._bandAt(this._pos(e)[0]);
    if (i < 0 || !this.enabled) return;
    e.preventDefault();
    this.selected = i;
    this._set(i, this.gains[i] + (e.deltaY < 0 ? 1 : -1));
    this._commit();
  }

  _key(e) {
    if (!this.enabled) return;
    if (this.selected < 0) this.selected = 0;
    const steps = { ArrowUp: 1, ArrowDown: -1, PageUp: 3, PageDown: -3 };
    if (e.key === "ArrowLeft") this.selected = Math.max(0, this.selected - 1);
    else if (e.key === "ArrowRight") this.selected = Math.min(this.freqs.length - 1, this.selected + 1);
    else if (e.key in steps || e.key === "0") {
      this._set(this.selected, e.key === "0" ? 0 : this.gains[this.selected] + steps[e.key]);
      this._commit();
    } else return;
    e.preventDefault();
    this.draw();
  }
}

function hexAlpha(color, a) {
  const m = /^#?([0-9a-f]{6})$/i.exec(color.trim());
  if (!m) return color;
  const n = parseInt(m[1], 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
}

function roundRect(g, x, y, w, h, r) {
  g.beginPath();
  g.moveTo(x + r, y);
  g.arcTo(x + w, y, x + w, y + h, r);
  g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r);
  g.arcTo(x, y, x + w, y, r);
  g.closePath();
}
