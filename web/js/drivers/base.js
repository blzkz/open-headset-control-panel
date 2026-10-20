// Shared pieces of the HeadsetControl driver ports (JavaScript twin of openheadset/drivers/base.py).
// Every driver is checked byte for byte against HeadsetControl's C++ by tests/web/golden.mjs.
import { SPECS } from "./specs.js";

export const Cap = Object.freeze({
  SIDETONE: "CAP_SIDETONE",
  BATTERY: "CAP_BATTERY_STATUS",
  NOTIFICATION_SOUND: "CAP_NOTIFICATION_SOUND",
  LIGHTS: "CAP_LIGHTS",
  INACTIVE_TIME: "CAP_INACTIVE_TIME",
  CHATMIX: "CAP_CHATMIX_STATUS",
  VOICE_PROMPTS: "CAP_VOICE_PROMPTS",
  ROTATE_TO_MUTE: "CAP_ROTATE_TO_MUTE",
  EQ_PRESET: "CAP_EQUALIZER_PRESET",
  EQUALIZER: "CAP_EQUALIZER",
  PARAMETRIC_EQ: "CAP_PARAMETRIC_EQUALIZER",
  MIC_MUTE_LED: "CAP_MICROPHONE_MUTE_LED_BRIGHTNESS",
  MIC_VOLUME: "CAP_MICROPHONE_VOLUME",
  VOLUME_LIMITER: "CAP_VOLUME_LIMITER",
  BT_WHEN_POWERED_ON: "CAP_BT_WHEN_POWERED_ON",
  BT_CALL_VOLUME: "CAP_BT_CALL_VOLUME",
  NOISE_FILTER: "CAP_NOISE_FILTER",
  SIDETONE_STATUS: "CAP_SIDETONE_STATUS",
});

export const BatteryStatus = Object.freeze({ UNAVAILABLE: 0, CHARGING: 1, AVAILABLE: 2, HIDERROR: 3, TIMEOUT: 4 });

export const ErrorCode = Object.freeze({
  TIMEOUT: "timeout",
  OFFLINE: "offline",
  PROTOCOL: "protocol",
  INVALID_PARAMETER: "invalid parameter",
  NOT_SUPPORTED: "not supported",
  HID: "hid",
});

export class DriverError extends Error {
  constructor(code, message = "") {
    super(message || code);
    this.code = code;
  }
}

export const timeout = (msg = "timeout") => new DriverError(ErrorCode.TIMEOUT, msg);
export const protocolError = (msg) => new DriverError(ErrorCode.PROTOCOL, msg);
export const invalid = (msg) => new DriverError(ErrorCode.INVALID_PARAMETER, msg);
export const offline = (msg = "device offline") => new DriverError(ErrorCode.OFFLINE, msg);
export const hidError = (msg) => new DriverError(ErrorCode.HID, msg);
export const notSupported = (msg = "not supported") => new DriverError(ErrorCode.NOT_SUPPORTED, msg);

// -- results ---------------------------------------------------------------------------------------
export const battery = (level, status, millivolts = null) => ({ level, status, millivolts });
export const sidetone = (current, deviceLevel = 0) => ({ current, deviceLevel });
export const chatmix = (level, game, chat) => ({ level, game, chat });
export const FilterType = Object.freeze({ LOW_SHELF: 0, LOW_PASS: 1, PEAKING: 2, HIGH_PASS: 3, HIGH_SHELF: 4, NOTCH: 5 });

// -- timing (swappable in tests) -------------------------------------------------------------------
let sleeper = null;
let clock = null;
export const sleepMs = (ms) => (sleeper ? sleeper(ms) : new Promise((r) => setTimeout(r, ms)));
export const setSleeper = (fn) => (sleeper = fn);
export const monotonicMs = () => Math.trunc(clock ? clock() * 1000 : performance.now());
export const setClock = (fn) => (clock = fn);

// -- C++-faithful arithmetic ---------------------------------------------------------------------------
export const cdiv = (a, b) => Math.trunc(a / b); // integer operands: exact, truncates toward zero
export const u8 = (v) => Math.trunc(v) & 0xff;
export const u16 = (v) => Math.trunc(v) & 0xffff;
export const int8 = (v) => (v & 0x80 ? (v & 0xff) - 256 : v & 0xff);
export const f32 = Math.fround;
export const fmul = (a, b) => f32(f32(a) * f32(b));
export const fadd = (a, b) => f32(f32(a) + f32(b));
export const fdiv = (a, b) => f32(f32(a) / f32(b));

/** C lround/llround: nearest integer, halfway cases away from zero. */
export function lround(x) {
  if (x < 0) return -lround(-x);
  const r = Math.floor(x);
  return r + (x - r >= 0.5 ? 1 : 0);
}

/** C roundf on a float32 value (half away from zero). */
export const roundf = (x) => (x >= 0 ? Math.floor(x + 0.5) : -Math.floor(-x + 0.5));

export function mapInt(value, inMin, inMax, outMin, outMax) {
  if (inMax === inMin) return outMin;
  const clamped = Math.max(Math.min(value, Math.max(inMin, inMax)), Math.min(inMin, inMax));
  return outMin + cdiv((clamped - inMin) * (outMax - outMin), inMax - inMin);
}

export function sidetoneToDiscrete(level, n) {
  if (level === 0) return 0;
  const step = Math.floor(128 / n);
  for (let i = 1; i < n; i++) if (level < step * i) return i - 1;
  return n - 1;
}

export function sidetoneWithToggle(level, deviceMin, deviceMax) {
  if (level === 0) return [false, 0];
  return [true, u8(deviceMin + cdiv((level - 1) * (deviceMax - deviceMin), 127))];
}

export function closest(value, allowed) {
  let best = allowed[0];
  let diff = Math.abs(value - allowed[0]);
  for (const a of allowed.slice(1)) {
    if (Math.abs(value - a) < diff) [best, diff] = [a, Math.abs(value - a)];
  }
  return best;
}

/** C++ roundToNearest: lower_bound, ties go to the lower neighbour. */
export function roundToNearest(value, sorted) {
  let i = 0;
  while (i < sorted.length && sorted[i] < value) i++;
  if (i === 0) return sorted[0];
  if (i === sorted.length) return sorted[sorted.length - 1];
  const [prev, next] = [sorted[i - 1], sorted[i]];
  return value - prev <= next - value ? prev : next;
}

export function voltageToPercent(mv, points) {
  if (mv <= points[0][1]) return points[0][0];
  if (mv >= points[points.length - 1][1]) return points[points.length - 1][0];
  for (let i = 1; i < points.length; i++) {
    if (mv <= points[i][1]) {
      const [[p0, v0], [p1, v1]] = [points[i - 1], points[i]];
      return u8(mapInt(mv, v0, v1, p0, p1));
    }
  }
  return points[points.length - 1][0];
}

export function chatmixFromLevel(level) {
  const game = level < 64 ? 100 : mapInt(level, 64, 128, 100, 0);
  const chat = level > 64 ? 100 : mapInt(level, 0, 64, 0, 100);
  return chatmix(level, game, chat);
}

// -- byte helpers ------------------------------------------------------------------------------------
/** ``bytes(data)[:size].ljust(size, b"\0")`` */
export function pad(data, size) {
  const out = new Array(size).fill(0);
  const src = Array.from(data);
  for (let i = 0; i < Math.min(size, src.length); i++) out[i] = src[i] & 0xff;
  return out;
}
export const zeros = (n) => new Array(n).fill(0);
export const le16 = (v) => [v & 0xff, (v >> 8) & 0xff];
export const rd16 = (b, off) => b[off] | (b[off + 1] << 8);
export const hex = (b) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");

// -- driver base ---------------------------------------------------------------------------------------
export class Driver {
  static NAME = "";
  static VENDOR = 0;
  static PIDS = [];
  static CAPS = {};
  static EQ = null;
  static PEQ = null;
  static EQ_PRESETS = [];
  static PRESET_COUNT = 0;
  static EQ_FREQUENCIES = [];
  static EQ_FROM_DEVICE = false;
  static TIMING_EXACT = true;
  static IMAGE = "";
  static WEB = true; // false: needs something WebHID cannot do (GET_REPORT on input reports)

  constructor(productId = 0) {
    this.productId = productId;
    this.state = {};
  }

  capabilities() {
    return { ...this.constructor.CAPS };
  }

  static has(cap) {
    return cap in this.CAPS;
  }

  async battery() { throw notSupported(); }
  async setSidetone() { throw notSupported(); }
  async getSidetone() { throw notSupported(); }
  async notificationSound() { throw notSupported(); }
  async setLights() { throw notSupported(); }
  async setInactiveTime() { throw notSupported(); }
  async chatmix() { throw notSupported(); }
  async setVoicePrompts() { throw notSupported(); }
  async setRotateToMute() { throw notSupported(); }
  async setEqPreset() { throw notSupported(); }
  async setEqualizer() { throw notSupported(); }
  async setParametricEq() { throw notSupported(); }
  async setMicMuteLed() { throw notSupported(); }
  async setMicVolume() { throw notSupported(); }
  async setVolumeLimiter() { throw notSupported(); }
  async setBtWhenPoweredOn() { throw notSupported(); }
  async setBtCallVolume() { throw notSupported(); }
  async setNoiseFilter() { throw notSupported(); }
}

export const REGISTRY = [];

/** Attach HeadsetControl's static data for ``name`` (generated specs) and register the class. */
export function driver(name, cls, { image = "", web = true } = {}) {
  const spec = SPECS[name];
  if (!spec) throw new Error(`no spec for ${name}`);
  Object.assign(cls, {
    NAME: name,
    VENDOR: spec.vendor,
    PIDS: spec.pids,
    CAPS: spec.caps,
    EQ: spec.eq,
    PEQ: spec.peq,
    EQ_PRESETS: spec.presets,
    PRESET_COUNT: spec.presetCount,
    IMAGE: image,
    WEB: web,
  });
  REGISTRY.push(cls);
  return cls;
}

export const findDriver = (vendor, product) => REGISTRY.find((c) => c.VENDOR === vendor && c.PIDS.includes(product));
