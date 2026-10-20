// HID++ 2.0 over WebHID. Mirrors openlogi/hidpp/protocol.py: feature indexes are
// resolved at runtime through IRoot, never hard-coded.

export const REPORT_LONG = 0x11;
const SOFTWARE_ID = 0x0b; // distinct from the desktop app (0x0A) and its CLI (0x0C)
const DEVICE_INDEX = 0xff;

export const Feature = Object.freeze({
  ROOT: 0x0000,
  FEATURE_SET: 0x0001,
  FW_VERSION: 0x0003,
  NAME: 0x0005,
  BATTERY_STATUS: 0x1000,
  UNIFIED_BATTERY: 0x1004,
  ADC: 0x1f20,
  COLOR_LED: 0x8070,
  RGB_EFFECTS: 0x8071,
  SIDETONE: 0x8300,
  EQUALIZER: 0x8310,
  HEADSET_OUT: 0x8320,
});

const FEATURE_NAMES = Object.fromEntries(
  Object.entries(Feature).map(([k, v]) => [v, k.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())]),
);
export const featureName = (id) => FEATURE_NAMES[id] ?? `Unknown 0x${id.toString(16).padStart(4, "0").toUpperCase()}`;

const ERRORS = ["NO_ERROR", "UNKNOWN", "INVALID_ARGUMENT", "OUT_OF_RANGE", "HARDWARE_ERROR", "LOGITECH_INTERNAL",
  "INVALID_FEATURE_INDEX", "INVALID_FUNCTION_ID", "BUSY", "UNSUPPORTED"];

export class HidppError extends Error {
  constructor(code, feature, fn, hidpp10 = false) {
    super(`feature[${feature}] fn${fn}: ${hidpp10 ? `HID++1.0 error 0x${code.toString(16)}` : ERRORS[code] ?? code}`);
    this.code = code;
    this.unreachable = hidpp10;
  }
}

export class HidppTimeout extends Error {
  constructor(feature, fn) {
    super(`no reply to feature[${feature}] fn${fn}`);
  }
}

export class FeatureNotSupported extends Error {}

export class Hidpp20 {
  /** @param {HIDDevice} device an opened WebHID device */
  constructor(device) {
    this.device = device;
    this.pending = null;
    this.queue = Promise.resolve();
    this.cache = new Map([[Feature.ROOT, 0]]);
    this.onEvent = null;
    this._listener = (e) => this._onReport(e);
    device.addEventListener("inputreport", this._listener);
  }

  dispose() {
    this.device.removeEventListener("inputreport", this._listener);
  }

  _onReport(event) {
    if (event.reportId !== REPORT_LONG && event.reportId !== 0x10) return;
    const d = new Uint8Array(event.data.buffer, event.data.byteOffset, event.data.byteLength);
    const p = this.pending;
    if (d[0] === DEVICE_INDEX && p) {
      if (d[1] === 0xff && d[2] === p.feature && d[3] >> 4 === p.fn) return p.reject(new HidppError(d[4], p.feature, p.fn));
      if (d[1] === 0x8f && d[2] === p.feature) return p.reject(new HidppError(d[4], p.feature, p.fn, true));
      if (d[1] === p.feature && d[2] >> 4 === p.fn && (d[2] & 0x0f) === SOFTWARE_ID) return p.resolve(d.slice(3));
    }
    this.onEvent?.({ feature: d[1], fn: d[2] >> 4, sw: d[2] & 0x0f, payload: d.slice(3) });
  }

  /** Send a request and resolve with the 16-byte reply payload. Requests are serialised. */
  request(feature, fn, params = [], timeout = 1000) {
    const run = () =>
      new Promise((resolve, reject) => {
        const data = new Uint8Array(19);
        data[0] = DEVICE_INDEX;
        data[1] = feature;
        data[2] = (fn << 4) | SOFTWARE_ID;
        data.set(Array.from(params).slice(0, 16), 3);
        const timer = setTimeout(() => {
          this.pending = null;
          reject(new HidppTimeout(feature, fn));
        }, timeout);
        const done = (fnResult) => (value) => {
          clearTimeout(timer);
          this.pending = null;
          fnResult(value);
        };
        this.pending = { feature, fn, resolve: done(resolve), reject: done(reject) };
        this.device.sendReport(REPORT_LONG, data).catch((err) => this.pending?.reject(err));
      });
    const next = this.queue.then(run, run);
    this.queue = next.catch(() => {});
    return next;
  }

  async ping() {
    const r = await this.request(0, 1, [0, 0, 0x5a]);
    return [r[0], r[1]];
  }

  async featureIndex(id) {
    if (!this.cache.has(id)) {
      const r = await this.request(0, 0, [id >> 8, id & 0xff]);
      this.cache.set(id, r[0] || -1);
    }
    const idx = this.cache.get(id);
    if (idx < 0) throw new FeatureNotSupported(`feature 0x${id.toString(16)} not supported`);
    return idx;
  }

  async call(id, fn, params = []) {
    return this.request(await this.featureIndex(id), fn, params);
  }

  async enumerateFeatures() {
    const fs = await this.featureIndex(Feature.FEATURE_SET);
    const count = (await this.request(fs, 0))[0];
    const list = [{ index: 0, id: Feature.ROOT }];
    for (let i = 1; i <= count; i++) {
      const r = await this.request(fs, 1, [i]);
      const id = (r[0] << 8) | r[1];
      list.push({ index: i, id, type: r[2], version: r[3] });
      if (!this.cache.has(id)) this.cache.set(id, i);
    }
    return list;
  }
}
