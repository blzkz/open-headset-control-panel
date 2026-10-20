// Headsets driven by the HeadsetControl ports over WebHID (the web twin of openheadset/drivers/session.py
// and the service's driver backend).
import { REGISTRY, findDriver } from "./drivers/index.js";
import { BatteryStatus, Cap, DriverError, ErrorCode, hidError, offline, pad, timeout } from "./drivers/base.js";
import { EqLayout, defaultFreqs, fitGains } from "./eqlayout.js";

export const VENDOR_NAMES = {
  0x046d: "Logitech", 0x9886: "Logitech (ASTRO)", 0x1038: "SteelSeries", 0x0951: "HyperX", 0x03f0: "HyperX",
  0x1b1c: "Corsair", 0x1e7d: "ROCCAT", 0x3329: "Audeze", 0x054c: "Sony", 0x0b0e: "Jabra", 0x17ef: "Lenovo",
  0x047f: "Plantronics", 0x3837: "MCHOSE",
};

// name → [capability, driver method, value type]; momentary actions are never stored or re-applied
export const CONTROLS = {
  lights: [Cap.LIGHTS, "setLights", Boolean],
  voice_prompts: [Cap.VOICE_PROMPTS, "setVoicePrompts", Boolean],
  rotate_to_mute: [Cap.ROTATE_TO_MUTE, "setRotateToMute", Boolean],
  volume_limiter: [Cap.VOLUME_LIMITER, "setVolumeLimiter", Boolean],
  bt_when_powered_on: [Cap.BT_WHEN_POWERED_ON, "setBtWhenPoweredOn", Boolean],
  bt_call_volume: [Cap.BT_CALL_VOLUME, "setBtCallVolume", Number],
  noise_filter: [Cap.NOISE_FILTER, "setNoiseFilter", Number],
  mic_volume: [Cap.MIC_VOLUME, "setMicVolume", Number],
  mic_mute_led: [Cap.MIC_MUTE_LED, "setMicMuteLed", Number],
  eq_preset: [Cap.EQ_PRESET, "setEqPreset", Number],
  notification_sound: [Cap.NOTIFICATION_SOUND, "notificationSound", Number],
};
export const ACTIONS = new Set(["eq_preset", "notification_sound"]);

export const webDrivers = () => REGISTRY.filter((c) => c.WEB);
export const driverFor = (d) => {
  const cls = findDriver(d.vendorId, d.productId);
  return cls && cls.WEB ? cls : null;
};

function* walk(collections) {
  for (const c of collections || []) {
    yield c;
    yield* walk(c.children);
  }
}

const hasReport = (dev, kind, id) => [...walk(dev.collections)].some((c) => (c[kind] || []).some((r) => r.reportId === id));
const isTimeout = (e) => e instanceof DriverError && (e.code === ErrorCode.TIMEOUT || e.code === ErrorCode.OFFLINE);

/** hidapi-shaped access to the HID interfaces of one headset (``Handle`` of the drivers). */
class WebHidHandle {
  constructor(devices) {
    this.devices = devices;
    this.queue = []; // a fresh queue per call, like HeadsetControl opening the device for each command
    this.waiter = null;
    this.listeners = devices.map((d) => {
      const fn = (e) => this._onReport(e);
      d.addEventListener("inputreport", fn);
      return [d, fn];
    });
  }

  dispose() {
    for (const [d, fn] of this.listeners) d.removeEventListener("inputreport", fn);
    if (this.waiter) this.waiter.reject(timeout("closed"));
  }

  _onReport(e) {
    const data = Array.from(new Uint8Array(e.data.buffer, e.data.byteOffset, e.data.byteLength));
    const bytes = e.reportId ? [e.reportId, ...data] : data; // hidapi read: numbered reports keep their id
    if (this.waiter) {
      const w = this.waiter;
      this.waiter = null;
      w.resolve(bytes);
    } else if (this.queue.length < 256) this.queue.push(bytes);
  }

  _device(kind, id) {
    if (this.devices.length === 1) return this.devices[0];
    return this.devices.find((d) => hasReport(d, kind, id)) || this.devices[0];
  }

  async write(data, size = null) {
    const b = size === null ? Array.from(data) : pad(data, size);
    try {
      await this._device("outputReports", b[0]).sendReport(b[0], Uint8Array.from(b.slice(1)));
    } catch (e) {
      throw hidError(`sendReport: ${e.message || e}`);
    }
  }

  read(length, timeoutMs) {
    if (this.queue.length) return Promise.resolve(this.queue.shift().slice(0, length));
    return new Promise((resolve, reject) => {
      const timer = timeoutMs >= 0 ? setTimeout(() => {
        this.waiter = null;
        reject(timeout(`HID read timeout after ${timeoutMs}ms (no data)`));
      }, timeoutMs) : null;
      this.waiter = {
        resolve: (b) => {
          clearTimeout(timer);
          resolve(b.slice(0, length));
        },
        reject: (e) => {
          clearTimeout(timer);
          reject(e);
        },
      };
    });
  }

  async sendFeature(data, size = null) {
    const b = size === null ? Array.from(data) : pad(data, size);
    try {
      await this._device("featureReports", b[0]).sendFeatureReport(b[0], Uint8Array.from(b.slice(1)));
    } catch (e) {
      throw hidError(`sendFeatureReport: ${e.message || e}`);
    }
  }

  async getFeature(reportId, length) {
    let dv;
    try {
      dv = await this._device("featureReports", reportId).receiveFeatureReport(reportId);
    } catch (e) {
      throw hidError(`receiveFeatureReport: ${e.message || e}`);
    }
    let b = Array.from(new Uint8Array(dv.buffer, dv.byteOffset, dv.byteLength));
    if (reportId === 0 || b[0] !== reportId) b = [reportId, ...b]; // hidapi: buf[0] is the report id
    return b.slice(0, length);
  }

  async getInput() {
    return []; // WebHID cannot GET_REPORT an input report
  }
}

/** Same surface as device.js's HID++ ``Headset`` so the UI treats both alike. */
export class DriverHeadset {
  constructor(devices, cls, image) {
    this.devices = devices;
    this.device = devices[0];
    this.cls = cls;
    this.driver = new cls(devices[0].productId);
    this.capsMap = this.driver.capabilities();
    const has = (c) => c in this.capsMap;
    this.backend = "headsetcontrol";
    this.caps = {
      sidetone: has(Cap.SIDETONE),
      equalizer: has(Cap.EQUALIZER) && (!!cls.EQ || cls.EQ_FROM_DEVICE),
      battery: has(Cap.BATTERY) ? "device" : "none",
      autoOff: has(Cap.INACTIVE_TIME),
      lights: false, // the RGB page is for HID++ models; on/off lights live with the other controls
      chatmix: has(Cap.CHATMIX),
    };
    this.controls = Object.entries(CONTROLS).filter(([, [cap]]) => has(cap)).map(([name]) => name);
    const names = cls.EQ_PRESETS.map(([n]) => n);
    this.presets = has(Cap.EQ_PRESET)
      ? (names.length === cls.PRESET_COUNT ? names : Array.from({ length: cls.PRESET_COUNT }, (_, i) => `Preset ${i + 1}`))
      : [];
    this.model = {
      key: `hc:${cls.NAME}`, name: cls.NAME, image, verified: false, lightZones: [],
      vendor: VENDOR_NAMES[cls.VENDOR] || cls.VENDOR.toString(16), wireless: has(Cap.BATTERY),
    };
    this.identity = { name: devices[0].productName || cls.NAME, firmware: [], protocol: "HeadsetControl" };
    this.eq = null;
    this.layout = null;
    this.features = [];
    this._chain = Promise.resolve();
    this._lastPercent = -1;
  }

  _devicesFor(cap) {
    const [page, usage] = this.capsMap[cap] || [0, 0];
    if (page && usage) {
      const dev = this.devices.find((d) => [...walk(d.collections)].some((c) => c.usagePage === page && c.usage === usage));
      if (dev) return [dev];
    }
    return this.devices; // HeadsetControl would pick by USB interface: route each report to the interface declaring it
  }

  /** One driver call at a time: settings changed together must not interleave on the wire. */
  call(cap, method, ...args) {
    const run = async () => {
      if (!(cap in this.capsMap)) throw new DriverError(ErrorCode.NOT_SUPPORTED, `${this.cls.NAME}: ${cap} not supported`);
      const h = new WebHidHandle(this._devicesFor(cap));
      try {
        return await this.driver[method](h, ...args);
      } finally {
        h.dispose();
      }
    };
    const next = this._chain.then(run, run);
    this._chain = next.catch(() => {});
    return next;
  }

  async probe() {
    if (this.caps.battery !== "none") {
      try {
        await this.readBattery();
      } catch (e) {
        if (isTimeout(e)) throw e;
        console.info("battery unreadable, continuing without it", e);
      }
    }
    let eq = this.cls.EQ;
    if (this.caps.equalizer && this.cls.EQ_FROM_DEVICE) eq = await this.call(Cap.EQUALIZER, "equalizerInfo");
    if (this.caps.equalizer && eq) {
      const freqs = eq.freqs || (this.cls.EQ_FREQUENCIES.length === eq.bands ? this.cls.EQ_FREQUENCIES : defaultFreqs(eq.bands));
      this.layout = new EqLayout(freqs, eq.min, eq.max, eq.step);
      this.eq = { bands: eq.bands, range: Math.max(Math.abs(eq.min), Math.abs(eq.max)), freqs, step: eq.step };
    }
  }

  ping() {
    return this.caps.battery !== "none" ? this.readBattery() : Promise.resolve(null);
  }

  async readBattery() {
    if (this.caps.battery === "none") return null;
    const b = await this.call(Cap.BATTERY, "battery");
    if ([BatteryStatus.UNAVAILABLE, BatteryStatus.TIMEOUT, BatteryStatus.HIDERROR].includes(b.status)) throw offline("headset unavailable");
    if (b.level >= 0) this._lastPercent = Math.min(100, b.level);
    const charging = b.status === BatteryStatus.CHARGING;
    return { percent: this._lastPercent, mv: b.millivolts || 0, charging, full: charging && this._lastPercent >= 100, estimates: false };
  }

  async setSidetone(level) {
    const ui = Math.max(0, Math.min(100, Math.round(level)));
    await this.call(Cap.SIDETONE, "setSidetone", Math.round((ui * 128) / 100));
    return ui;
  }

  async getSidetone() {
    const s = await this.call(Cap.SIDETONE_STATUS, "getSidetone");
    return Math.round((s.current * 100) / 128);
  }

  async setEq(gains) {
    const g = this.layout ? fitGains(gains, this.layout) : gains;
    await this.call(Cap.EQUALIZER, "setEqualizer", g);
    return g;
  }

  setAutoOff(minutes) {
    return this.call(Cap.INACTIVE_TIME, "setInactiveTime", Math.max(0, Math.min(255, minutes | 0)));
  }

  chatmix() {
    return this.call(Cap.CHATMIX, "chatmix");
  }

  async setControl(name, value) {
    const [cap, method, kind] = CONTROLS[name];
    await this.call(cap, method, kind(value));
    return kind(value);
  }

  featureList() {
    return [];
  }

  async close() {
    for (const d of this.devices) {
      try {
        await d.close();
      } catch {
        /* already closed */
      }
    }
  }
}

export { isTimeout };
