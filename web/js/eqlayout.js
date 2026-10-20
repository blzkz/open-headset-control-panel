// Equalizer band layouts (port of openheadset/presets.py): the built-in 10-band curves are
// expressed in the connected headset's bands, range and step.
export const STANDARD_FREQS = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];

export class EqLayout {
  constructor(freqs = STANDARD_FREQS, lo = -12, hi = 12, step = 1) {
    Object.assign(this, { freqs: [...freqs], lo, hi, step: step || 1 });
  }

  snap(value) {
    const v = Math.max(this.lo, Math.min(this.hi, Number(value)));
    return Math.round(Math.round(v / this.step) * this.step * 1e4) / 1e4;
  }

  equals(other) {
    return !!other && other.lo === this.lo && other.hi === this.hi && other.step === this.step
      && other.freqs.length === this.freqs.length && other.freqs.every((f, i) => f === this.freqs[i]);
  }
}

export function defaultFreqs(n) {
  if (n === STANDARD_FREQS.length) return [...STANDARD_FREQS];
  if (n <= 1) return new Array(n).fill(1000);
  return Array.from({ length: n }, (_, i) => {
    const f = 32 * 500 ** (i / (n - 1));
    return i ? Math.round(f / 10) * 10 : Math.round(f);
  });
}

function interpolate(gains, src, f) {
  const xs = src.map(Math.log);
  const x = Math.log(f);
  if (x <= xs[0]) return gains[0];
  if (x >= xs[xs.length - 1]) return gains[gains.length - 1];
  for (let i = 0; i < xs.length - 1; i++) {
    if (xs[i] <= x && x <= xs[i + 1]) return gains[i] + ((gains[i + 1] - gains[i]) * (x - xs[i])) / (xs[i + 1] - xs[i]);
  }
  return gains[gains.length - 1];
}

/** Same band count maps by index, otherwise by frequency; then clamp and snap to the step. */
export function fitGains(gains, layout, srcFreqs = null) {
  let g = gains.map(Number);
  if (!g.length) return layout.freqs.map(() => 0);
  if (g.length !== layout.freqs.length) {
    const src = srcFreqs && srcFreqs.length === g.length ? srcFreqs : defaultFreqs(g.length);
    g = layout.freqs.map((f) => interpolate(g, src, f));
  }
  return g.map((v) => layout.snap(v));
}
