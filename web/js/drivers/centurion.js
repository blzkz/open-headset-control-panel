// Logitech "Centurion" headsets: G522 LIGHTSPEED and PRO X 2 LIGHTSPEED (port of openheadset/drivers/centurion.py).
import {
  BatteryStatus as BS, Driver, DriverError, ErrorCode, FilterType, battery, driver, f32, fdiv, fmul, int8, invalid,
  lround, mapInt, notSupported, offline, protocolError, sidetone, timeout, u8, zeros,
} from "./base.js";

const DEVICE_TIMEOUT = 5000;
const FRAME_SIZE = 64;
const SOFTWARE_ID = 0x01;
const BRIDGE_FN = 0x10;
const POLL_ATTEMPTS = 8;
const MAX_SINGLE_BRIDGE_PAYLOAD = 56;
const MAX_CONTINUATION_PAYLOAD = 60;

const F_ROOT = 0x0000, F_FEATURE_SET = 0x0001, F_BRIDGE = 0x0003;
const F_BATTERY_SOC = 0x0104, F_AUTO_SLEEP = 0x0108;
const F_ADVANCED_PARA_EQ = 0x020d, F_MIC_MUTE_LED = 0x0601, F_VOICE_PROMPTS = 0x060b;
const F_LIGHTING = 0x0621, F_ONBOARD_EQ = 0x0636, F_SIDETONE = 0x0604;
const be16 = (v) => [(v >> 8) & 0xff, v & 0xff];

class Centurion extends Driver {
  static PREFIX = [0x51];
  static EXACT_DIRECT_LENGTH = false;
  static DETECT_CONNECTION_EVENTS = false;
  static PROBE_OFFLINE_ON_TIMEOUT = false;
  static LOOKUP_FEATURES_BY_ID = false;

  constructor(productId = 0) {
    super(productId);
    this._discovered = false;
    this._bridge = null;
    this._features = new Map(); // feature id -> sub-device index
  }

  get _c() {
    return this.constructor;
  }

  // -- framing ------------------------------------------------------------------------------
  _frame(payload, flags = 0) {
    const p = this._c.PREFIX;
    const frame = zeros(FRAME_SIZE);
    frame.splice(0, p.length, ...p);
    frame[p.length] = u8(payload.length + 1);
    frame[p.length + 1] = flags;
    const n = Math.min(payload.length, FRAME_SIZE - p.length - 2);
    for (let i = 0; i < n; i++) frame[p.length + 2 + i] = payload[i];
    return frame;
  }

  _extract(raw) {
    const p = this._c.PREFIX;
    const frame = Array.from(raw);
    while (frame.length < FRAME_SIZE) frame.push(0);
    if (frame.length < p.length + 3 || p.some((b, i) => frame[i] !== b)) throw protocolError("Unexpected Centurion frame prefix");
    const cpl = frame[p.length];
    if (cpl <= 1 || cpl + p.length + 1 > frame.length) throw protocolError("Invalid Centurion frame length");
    return frame.slice(p.length + 2, p.length + 1 + cpl);
  }

  static _isConnectionEvent(reply, bridge) {
    return reply.length >= 4 && reply[0] === bridge && reply[1] === 0x00;
  }

  // -- direct requests ----------------------------------------------------------------------
  async _request(h, featureIndex, fn, params = [], matchFunction = false) {
    const fnSw = (fn & 0xf0) | SOFTWARE_ID;
    const payload = this._c.EXACT_DIRECT_LENGTH ? [featureIndex, fnSw, ...params]
      : [featureIndex, fnSw, ...params.slice(0, 62), ...zeros(62 - Math.min(params.length, 62))];
    await h.write(this._frame(payload), FRAME_SIZE);
    for (let i = 0; i < POLL_ATTEMPTS; i++) {
      const reply = this._extract(await h.read(FRAME_SIZE, DEVICE_TIMEOUT));
      if (reply.length < 2) continue;
      if (this._c.DETECT_CONNECTION_EVENTS && this._bridge !== null && Centurion._isConnectionEvent(reply, this._bridge)) {
        if (reply[3] === 0x00) throw offline("Headset is powered off or not connected");
        continue;
      }
      if (reply[0] !== featureIndex) continue;
      if (matchFunction && reply[1] !== fnSw) continue;
      return reply.slice(2);
    }
    throw timeout("Timed out waiting for Centurion feature response");
  }

  // -- bridge requests ----------------------------------------------------------------------
  async _bridgeRequest(h, subIndex, fn, params = []) {
    if (this._bridge === null) throw protocolError("Centurion bridge index not initialized");
    const bridge = this._bridge;
    const sub = [0x00, subIndex, (fn & 0xf0) | SOFTWARE_ID, ...params];
    const size = sub.length;
    if (size > 0x0fff) throw invalid("Centurion bridge message exceeds the 12-bit size limit");
    const prefix = [bridge, BRIDGE_FN | SOFTWARE_ID, (size >> 8) & 0x0f, size & 0xff];
    if (size <= MAX_SINGLE_BRIDGE_PAYLOAD) await h.write(this._frame([...prefix, ...sub]), FRAME_SIZE);
    else {
      for (let offset = 0, frag = 0; offset < size; frag++) {
        const chunk = Math.min(frag === 0 ? MAX_SINGLE_BRIDGE_PAYLOAD : MAX_CONTINUATION_PAYLOAD, size - offset);
        const more = offset + chunk < size;
        const body = [...(frag === 0 ? prefix : []), ...sub.slice(offset, offset + chunk)];
        await h.write(this._frame(body, u8((frag << 1) | (more ? 1 : 0))), FRAME_SIZE);
        offset += chunk;
      }
    }
    let ack = false;
    for (let i = 0; i < POLL_ATTEMPTS; i++) {
      let resp;
      try {
        resp = await h.read(FRAME_SIZE, DEVICE_TIMEOUT);
      } catch (e) {
        if (ack && this._c.PROBE_OFFLINE_ON_TIMEOUT && e instanceof DriverError && e.code === ErrorCode.TIMEOUT) {
          await this._probeOffline(h, e);
        }
        throw e;
      }
      const reply = this._extract(resp);
      if (this._c.PROBE_OFFLINE_ON_TIMEOUT && reply.length >= 4 && reply[0] === 0xff && reply[1] === bridge
        && reply[2] === (BRIDGE_FN | SOFTWARE_ID)) {
        await this._probeOffline(h, protocolError("Centurion bridge rejected the request"));
      }
      if (reply.length < 2 || reply[0] !== bridge) continue;
      if (this._c.DETECT_CONNECTION_EVENTS && Centurion._isConnectionEvent(reply, bridge)) {
        if (reply[3] === 0x00) throw offline("Headset is powered off or not connected");
        continue;
      }
      const fnSw = reply[1];
      if (fnSw >> 4 !== BRIDGE_FN >> 4) continue;
      if ((fnSw & 0x0f) === SOFTWARE_ID) {
        ack = true;
        continue;
      }
      if ((fnSw & 0x0f) !== 0) continue;
      if (!Centurion._isBridgeResponseFor(reply, subIndex)) continue;
      if (reply.length < 7) throw protocolError("Malformed Centurion bridge response");
      if (reply[5] === 0xff) throw protocolError("Centurion sub-device rejected the request");
      return reply.slice(7);
    }
    if (!ack) throw timeout("Timed out waiting for Centurion bridge acknowledgment");
    const failure = timeout("Timed out waiting for Centurion bridge response");
    if (this._c.PROBE_OFFLINE_ON_TIMEOUT) await this._probeOffline(h, failure);
    throw failure;
  }

  static _isBridgeResponseFor(reply, subIndex) {
    if (reply.length < 6 || reply[4] !== 0x00) return false;
    if (reply[5] === subIndex) return true;
    return reply[5] === 0xff && reply.length >= 7 && reply[6] === subIndex;
  }

  /** Always throws: offline when the bridge reports the headset gone, else ``failure``. */
  async _probeOffline(h, failure) {
    let status;
    try {
      status = await this._request(h, this._bridge, 0x00, [], true);
    } catch (e) {
      if (e instanceof DriverError && e.code === ErrorCode.OFFLINE) throw e;
      if (e instanceof DriverError) throw failure;
      throw e;
    }
    if (status.length >= 4 && status[2] === 0x00 && status[3] === 0x00) throw offline("Headset is powered off or not connected");
    throw failure;
  }

  // -- feature lookup -----------------------------------------------------------------------
  async _lookup(h, featureId) {
    if (this._features.has(featureId)) return this._features.get(featureId);
    if (this._bridge === null) {
      const r = await this._request(h, F_ROOT, 0x00, be16(F_BRIDGE));
      if (!r.length || r[0] === 0) throw protocolError("Centurion bridge feature not found");
      this._bridge = r[0];
    }
    const r = await this._bridgeRequest(h, F_ROOT, 0x00, be16(featureId));
    if (!r.length || (r[0] === 0 && featureId !== F_ROOT)) throw notSupported("Centurion feature not available on this device");
    this._features.set(featureId, r[0]);
    return r[0];
  }

  async _discover(h) {
    if (this._discovered) return;
    let r = await this._request(h, F_ROOT, 0x00, be16(F_FEATURE_SET));
    if (!r.length || r[0] === 0) throw protocolError("Centurion FeatureSet not found");
    const featureSet = r[0];
    let count = await this._request(h, featureSet, 0x00);
    if (!count.length) throw protocolError("Centurion FeatureSet count response was empty");
    let bridge = 0xff;
    for (let index = 0; index < count[0]; index++) {
      const fr = await this._request(h, featureSet, 0x10, [index]);
      if (fr.length >= 3 && ((fr[1] << 8) | fr[2]) === F_BRIDGE) {
        bridge = index;
        break;
      }
    }
    if (bridge === 0xff) throw protocolError("Centurion bridge feature not found");
    this._bridge = bridge;
    r = await this._bridgeRequest(h, F_ROOT, 0x00, be16(F_FEATURE_SET));
    if (!r.length || r[0] === 0) throw protocolError("Centurion sub-device FeatureSet not found");
    const subSet = r[0];
    count = await this._bridgeRequest(h, subSet, 0x00);
    if (!count.length) throw protocolError("Centurion sub-device FeatureSet count response was empty");
    this._features.clear();
    for (let index = 0; index < count[0]; index++) {
      const fr = await this._bridgeRequest(h, subSet, 0x10, [index]);
      if (fr.length >= 3) this._features.set((fr[1] << 8) | fr[2], index);
    }
    this._discovered = true;
  }

  async _feature(h, featureId, fn, params = []) {
    let index;
    if (this._c.LOOKUP_FEATURES_BY_ID) index = await this._lookup(h, featureId);
    else {
      await this._discover(h);
      if (!this._features.has(featureId)) throw notSupported("Centurion feature not available on this device");
      index = this._features.get(featureId);
    }
    return this._bridgeRequest(h, index, fn, params);
  }

  static _parseBattery(packet) {
    if (!packet.length) throw protocolError("Empty Centurion battery response");
    if (packet[0] > 100) throw protocolError("Centurion battery percentage out of range");
    const charging = packet.length >= 3 ? packet[2] : 0;
    return battery(packet[0], charging === 1 || charging === 2 ? BS.CHARGING : BS.AVAILABLE);
  }
}

// -- G522 LIGHTSPEED --------------------------------------------------------------------------
const Q = 22.0 / 32.0;
const G522_DEFAULT = [[20, 0, Q], [50, 0, Q], [125, 0, Q], [250, 0, Q], [500, 0, Q], [1000, 0, Q], [2500, 0, Q],
  [5000, 0, Q], [10000, 0, Q], [20000, 0, Q]];
const G522_PRESETS = [
  G522_DEFAULT,
  [[20, 3.0, Q], [50, 2.5, Q], [125, 1.75, Q], [250, 1.0, 1.0], [500, 0, Q], [1000, 0, Q], [2500, 0, Q], [5000, 0, Q],
    [10000, 0, Q], [20000, 0, Q]],
  [[20, 0, Q], [50, 0, Q], [250, 0.5, Q], [400, 0.5, Q], [800, 0.75, Q], [1500, 1.0, Q], [2500, 1.25, Q], [5000, 1.5, Q],
    [10000, 1.75, Q], [19000, 2.75, Q]],
  [[20, 0, Q], [50, 0, Q], [125, 0, Q], [400, -1.0, Q], [800, -1.0, Q], [1250, 3.75, 2.0], [2500, 4.5, 1.0],
    [5000, 1.75, Q], [10000, 1.5, Q], [19000, 1.5, Q]],
  [[20, 4.0, Q], [50, 3.0, Q], [125, 2.0, Q], [250, -0.5, Q], [500, -0.5, Q], [1000, 1.0, Q], [2500, 2.0, Q],
    [5000, 1.5, Q], [10000, 0.5, Q], [20000, -0.5, Q]],
];

class LogitechG522Lightspeed extends Centurion {
  static PREFIX = [0x50, 0x23];
  static EQ_FREQUENCIES = G522_DEFAULT.map(([f]) => f);
  static EXACT_DIRECT_LENGTH = true;
  static DETECT_CONNECTION_EVENTS = true;
  static PROBE_OFFLINE_ON_TIMEOUT = true;
  static LOOKUP_FEATURES_BY_ID = true;
  static LIGHTS_ON = [0x00, 0x00, 0x04, 0x00, 0xb8, 0xfc, 0xff, 0x00, 0xab, 0x64];

  async battery(h) {
    return Centurion._parseBattery(await this._feature(h, F_BATTERY_SOC, 0x00));
  }

  async setSidetone(h, level) {
    await this._feature(h, F_SIDETONE, 0x10, [0x01, 0xff, u8(mapInt(level, 0, 128, 0, 9))]);
    return sidetone(level);
  }

  async getSidetone(h) {
    const params = await this._feature(h, F_SIDETONE, 0x00);
    if (params.length < 4 || params[3] > 9) throw protocolError("Unexpected G522 sidetone reply");
    return sidetone(u8(mapInt(params[3], 0, 9, 0, 128)), params[3]);
  }

  async setLights(h, on) {
    await this._feature(h, F_LIGHTING, 0x30, on ? LogitechG522Lightspeed.LIGHTS_ON : zeros(10));
    return on;
  }

  setVoicePrompts(h, on) { return this._feature(h, F_VOICE_PROMPTS, 0x50, [0x00, on ? 1 : 0]); }

  async setInactiveTime(h, minutes) {
    const current = await this._feature(h, F_AUTO_SLEEP, 0x00);
    if (current.length < 3) throw protocolError("Unexpected G522 auto-sleep reply");
    await this._feature(h, F_AUTO_SLEEP, 0x10, [minutes, current[1], current[2]]);
    return minutes;
  }

  setMicMuteLed(h, brightness) { return this._feature(h, F_MIC_MUTE_LED, 0x20, [brightness ? 1 : 0]); }

  static _validate(gain, q) {
    if (!Number.isFinite(gain) || gain < -6.0 || gain > 6.0) throw invalid("Gain must be between -6 and +6 dB");
    if (!Number.isFinite(q)) throw invalid("Q factor must be between 0.031 and 7.969");
    const raw = lround(fmul(q, 32.0));
    if (raw < 1 || raw > 255) throw invalid("Q factor must be between 0.031 and 7.969");
  }

  _writeEq(h, bands) {
    const params = [0, 0, 0];
    for (const [freq, gain, q] of bands) {
      const qRaw = Math.min(Math.max(lround(fmul(q, 32.0)), 1), 255);
      const gainRaw = Math.min(Math.max(lround(fdiv(gain, 0.05)) + 120, 0), 240);
      params.push(freq >> 8, freq & 0xff, qRaw, gainRaw >> 8, gainRaw & 0xff);
    }
    return this._feature(h, F_ADVANCED_PARA_EQ, 0x20, params);
  }

  async setEqPreset(h, preset) {
    if (preset >= G522_PRESETS.length) throw invalid("Device only supports presets 0-4");
    await this._writeEq(h, G522_PRESETS[preset].map(([f, g, q]) => [f, f32(g), f32(q)]));
  }

  async setEqualizer(h, gains) {
    if (gains.length !== 10) throw invalid("Equalizer requires 10 gain values");
    const bands = G522_DEFAULT.map(([freq, , q], i) => {
      const [g, qq] = [f32(gains[i]), f32(q)];
      LogitechG522Lightspeed._validate(g, qq);
      return [freq, g, qq];
    });
    await this._writeEq(h, bands);
  }

  async setParametricEq(h, bands) {
    if (bands.length > 10) throw invalid("Device only supports up to 10 equalizer bands");
    const out = G522_DEFAULT.map(([f, g, q]) => [f, f32(g), f32(q)]);
    bands.forEach((band, i) => {
      const freq = f32(band.frequency);
      if (band.type !== FilterType.PEAKING) throw invalid("This headset only supports peaking EQ bands");
      if (!Number.isFinite(freq) || freq < 20 || freq > 20000) throw invalid("Frequency must be between 20 Hz and 20000 Hz");
      out[i] = [lround(freq) & 0xffff, f32(band.gain), f32(band.q)];
      LogitechG522Lightspeed._validate(out[i][1], out[i][2]);
    });
    await this._writeEq(h, out);
  }
}
driver("Logitech G522 LIGHTSPEED", LogitechG522Lightspeed);

// -- PRO X 2 LIGHTSPEED -----------------------------------------------------------------------
const PROX2_PRESETS = [[0, 0, 0, 0, 0], [4, 2, 0, 0, 0], [-1, 2, 1, 3, 3], [-1, -1, 4, 3, 2], [0, 1, 1, 2, 4]];

function biquad(frequency, gainDb, q, sampleRate) {
  const amplitude = Math.pow(10.0, gainDb / 40.0);
  const w0 = (2.0 * 3.14159265358979323846 * frequency) / sampleRate;
  const cosW0 = Math.cos(w0);
  const alpha = Math.sin(w0) / (2.0 * q);
  const a0 = 1.0 + alpha / amplitude;
  return [(1.0 + alpha * amplitude) / a0, (-2.0 * cosW0) / a0, (1.0 - alpha * amplitude) / a0, (-2.0 * cosW0) / a0,
    (1.0 - alpha / amplitude) / a0];
}

const SCALES = [2147483648.0, 1073741824.0, 2147483648.0, 1073741824.0, 2147483648.0];
const clampI32 = (v) => Math.min(Math.max(v, -(2 ** 31)), 2 ** 31 - 1);

function quantize(coeffs) {
  const words = [];
  coeffs.forEach((c, i) => {
    const q = Number(BigInt(clampI32(lround(c * SCALES[i]))) & ~0xffn); // int64 & ~0xFF
    words.push(Number((BigInt(q) >> 16n) & 0xffffn), q & 0xffff);
  });
  return words;
}

function coefficientSection(bands, sampleRate, sectionType) {
  const raw = [];
  let maxB0 = 1.0;
  for (const [freq, gain, q] of bands) {
    const c = biquad(freq, gain, Math.max(0.1, q), sampleRate);
    maxB0 = Math.max(maxB0, Math.abs(c[0]));
    raw.push(c);
  }
  const rescale = Math.max(1.0, maxB0) * 1.19;
  const words = [bands.length];
  for (const c of raw) words.push(...quantize([c[0] / rescale, c[1] / rescale, c[2] / rescale, c[3], c[4]]));
  const rq = Number(BigInt(clampI32(lround(rescale * 67108864.0))) & 0xffffff00n);
  words.push((rq >> 16) & 0xffff, rq & 0xffff);
  const count = (bands.length * 10 + 3) & 0xffff;
  const out = [sectionType, 0x00, count & 0xff, count >> 8];
  for (const w of words) out.push(w & 0xff, (w >> 8) & 0xff);
  return out;
}

/** ``bands`` are [frequency, int8 gain, uint8 q] triples. */
export function onboardEqPayload(slot, bands) {
  const out = [slot, bands.length & 0xff];
  for (const [freq, gain, q] of bands) out.push((freq >> 8) & 0xff, freq & 0xff, gain & 0xff, q);
  out.push(0x05, 0x5a, 0xe3, 0x00, 0x03, 0x0e, 0x00, 0x02, 0x00, 0x00, 0x00);
  out.push(...coefficientSection(bands, 48000.0, 0x01), ...coefficientSection(bands, 16000.0, 0x02));
  return out;
}

class LogitechGProX2Lightspeed extends Centurion {
  static EXACT_DIRECT_LENGTH = true;
  static DETECT_CONNECTION_EVENTS = true;
  static PROBE_OFFLINE_ON_TIMEOUT = true;
  static LOOKUP_FEATURES_BY_ID = true;
  static EQ_FROM_DEVICE = true;

  async equalizerInfo(h) {
    const d = await this._descriptor(h);
    return { bands: d.bands.length, baseline: 0, step: 1.0, min: Math.trunc(d.min), max: Math.trunc(d.max), freqs: d.bands };
  }

  async battery(h) {
    let packet;
    try {
      packet = await this._feature(h, F_BATTERY_SOC, 0x00);
    } catch (e) {
      if (!(e instanceof DriverError) || e.code !== ErrorCode.NOT_SUPPORTED) throw e;
      return this._legacyBattery(h);
    }
    return Centurion._parseBattery(packet);
  }

  async _legacyBattery(h) {
    const request = zeros(FRAME_SIZE);
    request.splice(0, 10, 0x51, 0x08, 0x00, 0x03, 0x1a, 0x00, 0x03, 0x00, 0x04, 0x0a);
    await h.write(request, FRAME_SIZE);
    for (let i = 0; i < 4; i++) {
      const p = Array.from(await h.read(FRAME_SIZE, DEVICE_TIMEOUT));
      while (p.length < FRAME_SIZE) p.push(0);
      if (p[0] === 0x51 && p[1] === 0x05 && p[6] === 0x00) throw offline("Headset is powered off or not connected");
      if (p[0] === 0x51 && (p[1] === 0x05 || p[1] === 0x03)) continue;
      if (!(p[0] === 0x51 && p[1] === 0x0b && p[8] === 0x04)) continue;
      if (p[10] > 100) throw protocolError("Battery percentage out of range");
      return battery(p[10], p[12] === 0x02 ? BS.CHARGING : BS.AVAILABLE);
    }
    throw protocolError("Battery response packet not received");
  }

  async setSidetone(h, level) {
    const mapped = u8(mapInt(level, 0, 128, 0, 100));
    const state = await this._feature(h, F_SIDETONE, 0x00);
    await this._feature(h, F_SIDETONE, 0x10, state.length >= 4 ? [0x01, 0xff, mapped] : [0x01, mapped]);
    return sidetone(level);
  }

  async setInactiveTime(h, minutes) {
    await this._feature(h, F_AUTO_SLEEP, 0x10, [minutes]);
    return minutes;
  }

  // -- equalizer ------------------------------------------------------------------------------
  async _advancedDescriptor(h) {
    const info = await this._feature(h, F_ADVANCED_PARA_EQ, 0x00);
    if (info.length < 5) throw protocolError("Advanced EQ info response was too short");
    const slot = await this._feature(h, F_ADVANCED_PARA_EQ, 0x30, [0x00]);
    if (!slot.length) throw protocolError("Advanced EQ active slot response was empty");
    const params = await this._feature(h, F_ADVANCED_PARA_EQ, 0x10, [0x00, slot[0]]);
    const bands = [];
    for (let offset = 0; offset + 2 < params.length; offset += 3) {
      const freq = (params[offset] << 8) | params[offset + 1];
      if (freq === 0) break;
      bands.push(freq);
    }
    if (!bands.length) throw protocolError("Advanced EQ band response was empty");
    return { onboard: false, slot: slot[0], min: int8(info[3]), max: int8(info[4]), bands };
  }

  async _onboardDescriptor(h) {
    const info = await this._feature(h, F_ONBOARD_EQ, 0x00);
    if (info.length < 5) throw protocolError("Onboard EQ info response was too short");
    const params = await this._feature(h, F_ONBOARD_EQ, 0x10, [0x00]);
    if (params.length < 2) throw protocolError("Onboard EQ parameter response was too short");
    const bands = [];
    let offset = 2;
    for (let b = 0; b < params[1]; b++) {
      if (offset + 3 >= params.length) break;
      bands.push((params[offset] << 8) | params[offset + 1]);
      offset += 4;
    }
    if (!bands.length) throw protocolError("Onboard EQ band response was empty");
    return { onboard: true, slot: 0, min: -12.0, max: 12.0, bands };
  }

  async _descriptor(h) {
    try {
      return await this._advancedDescriptor(h);
    } catch (e) {
      if (!(e instanceof DriverError)) throw e;
      return this._onboardDescriptor(h);
    }
  }

  async _write(h, desc, bands) {
    if (!desc.onboard) {
      const payload = [0x00, desc.slot];
      for (const [freq, gain] of bands) payload.push((freq >> 8) & 0xff, freq & 0xff, gain & 0xff);
      await this._feature(h, F_ADVANCED_PARA_EQ, 0x20, payload);
    } else await this._feature(h, F_ONBOARD_EQ, 0x20, onboardEqPayload(desc.slot, bands));
  }

  async setEqualizer(h, gains) {
    const desc = await this._descriptor(h);
    if (gains.length !== desc.bands.length) throw invalid("Equalizer requires one gain value per playback band");
    const bands = desc.bands.map((freq, i) => {
      const gain = f32(gains[i]);
      if (gain < desc.min || gain > desc.max) throw invalid("Equalizer gain is outside the supported range");
      return [freq, Math.trunc(gain), 1]; // static_cast<int8_t>(float) truncates
    });
    await this._write(h, desc, bands);
  }

  setEqPreset(h, preset) {
    if (preset >= PROX2_PRESETS.length) throw invalid("Device only supports presets 0-4");
    return this.setEqualizer(h, [...PROX2_PRESETS[preset]]);
  }

  async setParametricEq(h, bands) {
    const desc = await this._descriptor(h);
    if (bands.length !== desc.bands.length) throw invalid("Parametric equalizer requires exactly one entry per playback band");
    const out = bands.map((band) => {
      const [freq, gain, q] = [f32(band.frequency), f32(band.gain), f32(band.q)];
      if (band.type !== FilterType.PEAKING) throw invalid("This headset only supports peaking EQ bands");
      if (Math.abs(f32(q - 1.0)) > f32(0.001)) throw invalid("This headset uses a fixed Q factor of 1.0");
      if (freq < 20.0 || freq > 20000.0) throw invalid("Frequency must be between 20 Hz and 20000 Hz");
      if (gain < desc.min || gain > desc.max) throw invalid("Gain is outside the supported range");
      return [Math.trunc(freq) & 0xffff, Math.trunc(gain), Math.min(Math.max(lround(q), 1), 255)];
    });
    await this._write(h, desc, out);
  }
}
driver("Logitech G PRO X 2 LIGHTSPEED", LogitechGProX2Lightspeed, { image: "headset-pro-x-2.png" });
