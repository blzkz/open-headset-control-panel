// High-level headset: capability discovery + typed feature access (port of openheadset/device.py).
import { Feature, FeatureNotSupported, Hidpp20, HidppError, featureName } from "./hidpp.js";

const bcd = (b) => (b >> 4) * 10 + (b & 0x0f);
const signed8 = (b) => (b > 127 ? b - 256 : b);

export class Headset {
  /** @param {HIDDevice} device @param {object} model entry of models.json */
  constructor(device, model) {
    this.device = device;
    this.model = model;
    this.hidpp = new Hidpp20(device);
    this.caps = { sidetone: false, equalizer: false, battery: "none", autoOff: false, lights: false };
    this.features = [];
    this.identity = { name: model.name, firmware: [], protocol: "" };
    this.eq = null;
    this._eqMode = null;
  }

  async probe() {
    const [maj, min] = await this.hidpp.ping();
    this.identity.protocol = `${maj}.${min}`;
    this.features = await this.hidpp.enumerateFeatures();
    const ids = new Set(this.features.map((f) => f.id));
    this.caps.sidetone = ids.has(Feature.SIDETONE);
    this.caps.equalizer = ids.has(Feature.EQUALIZER);
    this.caps.lights = ids.has(Feature.COLOR_LED) && this.model.lightZones.length > 0;
    this.caps.battery = ids.has(Feature.ADC) ? "adc" : ids.has(Feature.UNIFIED_BATTERY) ? "unified"
      : ids.has(Feature.BATTERY_STATUS) ? "status" : "none";
    if (ids.has(Feature.ADC)) {
      try {
        await this.getAutoOff();
        this.caps.autoOff = true;
      } catch (e) {
        if (!(e instanceof HidppError)) throw e;
      }
    }
    if (ids.has(Feature.NAME)) this.identity.name = await this._readName();
    if (ids.has(Feature.FW_VERSION)) this.identity.firmware = await this._readFirmware();
    if (this.caps.equalizer) this.eq = await this._readEqInfo();
  }

  featureList() {
    return this.features.map((f) => ({ ...f, name: featureName(f.id) }));
  }

  async _readName() {
    const len = (await this.hidpp.call(Feature.NAME, 0))[0];
    let bytes = [];
    while (bytes.length < len) {
      const chunk = await this.hidpp.call(Feature.NAME, 1, [bytes.length]);
      bytes = bytes.concat(Array.from(chunk.slice(0, len - bytes.length)));
    }
    return new TextDecoder().decode(new Uint8Array(bytes)).replace(/\0+$/, "");
  }

  async _readFirmware() {
    const count = (await this.hidpp.call(Feature.FW_VERSION, 0))[0];
    const out = [];
    for (let i = 0; i < count; i++) {
      const r = await this.hidpp.call(Feature.FW_VERSION, 1, [i]);
      const prefix = new TextDecoder().decode(r.slice(1, 4)).replace(/[\0 ]+$/, "");
      const build = bcd(r[6]) * 100 + bcd(r[7]);
      out.push(`${prefix} ${String(bcd(r[4])).padStart(2, "0")}.${String(bcd(r[5])).padStart(2, "0")}.B${String(build).padStart(4, "0")}`);
    }
    return out;
  }

  // -- equalizer (0x8310) -------------------------------------------------------------------
  async _readEqInfo() {
    const r = await this.hidpp.call(Feature.EQUALIZER, 0);
    const bands = r[0];
    let freqs = [];
    while (freqs.length < bands) {
      const c = await this.hidpp.call(Feature.EQUALIZER, 1, [freqs.length]);
      const start = c[0];
      const values = [];
      for (let i = 0; i < 7; i++) values.push((c[1 + 2 * i] << 8) | c[2 + 2 * i]);
      freqs = freqs.slice(0, start).concat(values.slice(0, Math.min(7, bands - start)));
    }
    return { bands, range: r[1], freqs };
  }

  async getEq() {
    const r = await this.hidpp.call(Feature.EQUALIZER, 2, [0]);
    return Array.from(r.slice(0, this.eq.bands), signed8);
  }

  async setEq(gains) {
    const g = gains.map((v) => Math.max(-this.eq.range, Math.min(this.eq.range, Math.round(v))));
    const body = g.map((v) => v & 0xff);
    // 0x02 is what the PRO X accepts; other models are unverified → fall back in order
    const modes = this._eqMode !== null ? [this._eqMode] : [0x02, 0x00, 0x01];
    for (let i = 0; i < modes.length; i++) {
      try {
        await this.hidpp.call(Feature.EQUALIZER, 3, [modes[i], ...body]);
        this._eqMode = modes[i];
        return g;
      } catch (e) {
        if (!(e instanceof HidppError) || e.code !== 2 || i === modes.length - 1) throw e;
      }
    }
    return g;
  }

  // -- sidetone (0x8300) ----------------------------------------------------------------------
  async getSidetone() {
    return (await this.hidpp.call(Feature.SIDETONE, 0))[0];
  }

  async setSidetone(level) {
    return (await this.hidpp.call(Feature.SIDETONE, 1, [Math.max(0, Math.min(100, Math.round(level)))]))[0];
  }

  // -- auto power-off (0x1F20 fn1/fn2) -----------------------------------------------------------
  async getAutoOff() {
    return (await this.hidpp.call(Feature.ADC, 1))[0];
  }

  async setAutoOff(minutes) {
    return (await this.hidpp.call(Feature.ADC, 2, [Math.max(0, Math.min(255, minutes | 0))]))[0];
  }

  // -- battery --------------------------------------------------------------------------------
  /** @returns {{mv:number, charging:boolean, full:boolean, percent?:number}|null} */
  async readBattery() {
    if (this.caps.battery === "adc") {
      const r = await this.hidpp.call(Feature.ADC, 0);
      return { mv: (r[0] << 8) | r[1], charging: !!(r[2] & 0x02), full: !!(r[2] & 0x04) };
    }
    if (this.caps.battery === "unified") {
      const r = await this.hidpp.call(Feature.UNIFIED_BATTERY, 1);
      return { mv: 0, percent: r[0], charging: r[2] === 1 || r[2] === 2, full: r[2] === 3 };
    }
    if (this.caps.battery === "status") {
      const r = await this.hidpp.call(Feature.BATTERY_STATUS, 0);
      return { mv: 0, percent: r[0], charging: [1, 2, 4].includes(r[2]), full: r[2] === 3 };
    }
    return null;
  }

  // -- lighting (0x8070 setZoneEffect) -------------------------------------------------------------
  async setLights(mode, rgb, brightness = 100, periodMs = 4000) {
    const modeId = { off: 0, static: 1, breathing: 2 }[mode] ?? 1;
    for (const [zone] of this.model.lightZones) {
      await this.hidpp.call(Feature.COLOR_LED, 3, [zone, modeId, ...rgb, periodMs >> 8, periodMs & 0xff, 0,
        Math.max(1, Math.min(100, brightness))]);
    }
  }

  async close() {
    this.hidpp.dispose();
    try {
      await this.device.close();
    } catch {
      /* already closed */
    }
  }
}

export { FeatureNotSupported, HidppError };

// -- battery estimation (port of openheadset/battery.py) -----------------------------------------
const CHARGING_OFFSET_MV = 170;
const CHARGE_HOURS = 3;
const FULL_HOURS = 20;

export function percentFromMv(mv, curve, charging = false) {
  if (charging) mv -= CHARGING_OFFSET_MV;
  if (mv <= curve[0][0]) return 0;
  if (mv >= curve[curve.length - 1][0]) return 100;
  for (let i = 1; i < curve.length; i++) {
    const [v1, p1] = curve[i];
    if (mv <= v1) {
      const [v0, p0] = curve[i - 1];
      return p0 + ((p1 - p0) * (mv - v0)) / (v1 - v0);
    }
  }
  return 100;
}

export class BatteryEstimator {
  constructor(curve, storageKey) {
    this.curve = curve;
    this.key = storageKey;
    this.pct = null;
    this.pctTime = null;
    this.mv = null;
    this.charging = null;
    this.rawFlag = null;
    this.rawSince = 0;
    this.transitionAt = -Infinity;
    this.lastT = null;
    this.raiseVotes = 0;
    try {
      const saved = JSON.parse(localStorage.getItem(this.key) || "null");
      if (saved) [this.pct, this.pctTime] = [saved.percent, saved.t];
    } catch {
      /* private mode */
    }
  }

  update(mv, charging, complete, now = Date.now() / 1000) {
    const dt = this.lastT === null ? 0 : Math.max(0, now - this.lastT);
    this.lastT = now;
    if (this.pct !== null && (this.pctTime === null || now - this.pctTime > 600)) this.pct = null; // stale
    this.pctTime = now;
    if (charging !== this.rawFlag) [this.rawFlag, this.rawSince] = [charging, now];
    if (this.charging === null) this.charging = charging;
    else if (charging !== this.charging && now - this.rawSince >= 8) {
      this.charging = charging;
      this.transitionAt = now;
      this.mv = null;
      this.raiseVotes = 0;
    }
    const settling = now - this.transitionAt < 45 || charging !== this.charging;
    let full = false;
    if (this.charging) {
      if (this.pct === null) this.pct = complete ? 100 : percentFromMv(mv, this.curve, true);
      else this.pct += (dt / 3600 / CHARGE_HOURS) * 100;
      full = complete || (!settling && mv >= 4180);
      this.pct = full ? 100 : Math.min(this.pct, 99);
    } else if (!settling) {
      this.mv = this.mv === null ? mv : this.mv + 0.3 * (mv - this.mv);
      const est = percentFromMv(this.mv, this.curve);
      if (this.pct === null || est <= this.pct) [this.pct, this.raiseVotes] = [est, 0];
      else if (est - this.pct > 8) {
        if (++this.raiseVotes >= 4) [this.pct, this.raiseVotes] = [est, 0];
      } else this.raiseVotes = 0;
    } else if (this.pct === null) this.pct = percentFromMv(mv, this.curve);
    try {
      localStorage.setItem(this.key, JSON.stringify({ percent: this.pct, t: now }));
    } catch {
      /* ignore */
    }
    return { percent: Math.round(this.pct), mv, charging: !!this.charging, full };
  }

  get transitionPending() {
    return this.rawFlag !== null && this.rawFlag !== this.charging;
  }
}

export const hoursLeft = (b) => (b.charging ? null : (FULL_HOURS * b.percent) / 100);
export const hoursToFull = (b) => (!b.charging || b.full ? null : (CHARGE_HOURS * (100 - b.percent)) / 100);
