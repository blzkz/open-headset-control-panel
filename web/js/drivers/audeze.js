// Audeze Maxwell / Maxwell 2 (port of openheadset/drivers/audeze.py).
// Desktop only in practice: replies are polled with GET_REPORT on input reports, which WebHID lacks.
import {
  BatteryStatus as BS, Driver, DriverError, ErrorCode, battery, chatmix, driver, f32, invalid, le16, mapInt, monotonicMs,
  protocolError, rd16, sidetone, sleepMs, timeout, u8,
} from "./base.js";

const MSG_SIZE = 62;
const REPORT_OUT = 0x06, REPORT_IN = 0x07, FRAGMENT_OFFSET = 3, MAX_FRAGMENT = MSG_SIZE - FRAGMENT_OFFSET;
const ROUTE_HEADSET = 0x80;
const MARKER = 0x05, TYPE_REQUEST = 0x5a, TYPE_RESPONSE = 0x5b, TYPE_RESULT = 0x5d;
const HEADER_SIZE = 4, MAX_MESSAGE_SIZE = 1024;
const CMD_WRITE_KEY = [0x00, 0x09], CMD_READ_KEY = [0x01, 0x09];
const CMD_WRITE_PARAMETER = [0x82, 0x2c], CMD_READ_PARAMETER = [0x83, 0x2c], CMD_BATTERY = [0xd6, 0x0c];
const KEY_EQ_PRESET = 0x00, KEY_VOICE_PROMPT_LEVEL = 0x20, KEY_MIC_MUTE = 0x22, KEY_NOISE_FILTER = 0x24;
const KEY_VOLUME_LIMITER = 0x28, KEY_SIDETONE_LEVEL = 0x2c, KEY_SIDETONE_MAX_LEVEL = 0x2d;
const PARAM_AUTO_POWER_OFF = 0x01, PARAM_SIDETONE_ENABLED = 0x07, PARAM_CHATMIX = 0x0b;
const DEFAULT_SIDETONE_MAX = 31, CHATMIX_MAX = 20;

function buildReports(message) {
  const reports = [];
  for (let off = 0; off < message.length; off += MAX_FRAGMENT) {
    const chunk = message.slice(off, off + MAX_FRAGMENT);
    const r = [REPORT_OUT, chunk.length, ROUTE_HEADSET, ...chunk];
    while (r.length < MSG_SIZE) r.push(0);
    reports.push(r);
  }
  return reports;
}

function fragment(report) {
  if (report.length < FRAGMENT_OFFSET) return [];
  const n = report[1];
  if (n > MAX_FRAGMENT || FRAGMENT_OFFSET + n > report.length) return [];
  return report.slice(FRAGMENT_OFFSET, FRAGMENT_OFFSET + n);
}

function extract(stream) {
  while (stream.length) {
    if (stream[0] !== MARKER) {
      stream.shift();
      continue;
    }
    if (stream.length < HEADER_SIZE) break;
    const size = HEADER_SIZE + rd16(stream, 2);
    if (size > MAX_MESSAGE_SIZE) {
      stream.shift();
      continue;
    }
    if (stream.length < size) break;
    return stream.splice(0, size);
  }
  return [];
}

const isMsg = (m, typ, cmd) => m.length >= HEADER_SIZE + 2 && m[1] === typ && m[HEADER_SIZE] === cmd[0] && m[HEADER_SIZE + 1] === cmd[1];
const hasId = (m, offset, id) => m.length >= offset + 2 && rd16(m, offset) === id;

class Maxwell extends Driver {
  static RESPONSE_TIMEOUT_MS = 1250;
  static POLL_INTERVAL_MS = 50;

  async _await(h, accept, timeoutMs) {
    const deadline = monotonicMs() + timeoutMs;
    const stream = [];
    for (;;) {
      for (let m = extract(stream); m.length; m = extract(stream)) if (accept(m)) return m;
      if (monotonicMs() >= deadline) throw timeout("No matching reply from the Maxwell");
      await sleepMs(Maxwell.POLL_INTERVAL_MS);
      const report = await h.getInput(REPORT_IN, MSG_SIZE);
      stream.push(...fragment(Array.from(report).slice(0, MSG_SIZE)));
    }
  }

  async _request(h, body, accept, { attempts = 3, timeoutMs = null, fragmentIntervalMs = 0 } = {}) {
    const reports = buildReports([MARKER, TYPE_REQUEST, ...le16(body.length), ...body]);
    const wait = timeoutMs ?? this.constructor.RESPONSE_TIMEOUT_MS;
    let error = timeout("No matching reply from the Maxwell");
    for (let a = 0; a < attempts; a++) {
      for (let i = 0; i < reports.length; i++) {
        if (i > 0 && fragmentIntervalMs > 0) await sleepMs(fragmentIntervalMs);
        await h.write(reports[i], MSG_SIZE);
      }
      try {
        return await this._await(h, accept, wait);
      } catch (e) {
        if (!(e instanceof DriverError)) throw e;
        error = e;
        if (e.code !== ErrorCode.TIMEOUT) break;
      }
    }
    throw error;
  }

  async _readKey(h, key) {
    const reply = await this._request(h, [...CMD_READ_KEY, key, 0x00], (m) => isMsg(m, TYPE_RESPONSE, CMD_READ_KEY) && hasId(m, 6, key));
    if (reply.length <= 9 || reply[8] !== 0) throw protocolError(`Maxwell rejected reading key 0x${key.toString(16)}`);
    return reply[9];
  }

  _writeKey(h, key, value) {
    return this._request(h, [...CMD_WRITE_KEY, key, 0x00, value], (m) => isMsg(m, TYPE_RESPONSE, CMD_WRITE_KEY) && hasId(m, 6, key),
      { attempts: 1 });
  }

  async _readParameter(h, p) {
    const reply = await this._request(h, [...CMD_READ_PARAMETER, p, 0x00], (m) => isMsg(m, TYPE_RESPONSE, CMD_READ_PARAMETER) && hasId(m, 7, p));
    if (reply.length <= 9 || reply[6] !== 0) throw protocolError(`Maxwell rejected reading parameter 0x${p.toString(16)}`);
    return reply[9];
  }

  async _writeParameter(h, p, value) {
    const reply = await this._request(h, [...CMD_WRITE_PARAMETER, p, 0x00, ...value],
      (m) => isMsg(m, TYPE_RESPONSE, CMD_WRITE_PARAMETER) && hasId(m, 7, p), { attempts: 1 });
    if (reply[6] !== 0) throw protocolError(`Maxwell rejected writing parameter 0x${p.toString(16)}`);
  }

  async _sidetoneMax(h) {
    try {
      return (await this._readKey(h, KEY_SIDETONE_MAX_LEVEL)) || DEFAULT_SIDETONE_MAX;
    } catch (e) {
      if (!(e instanceof DriverError)) throw e;
      return DEFAULT_SIDETONE_MAX;
    }
  }

  async _switchSidetone(h, on) {
    if (((await this._readParameter(h, PARAM_SIDETONE_ENABLED)) !== 0) === on) return;
    await this._writeParameter(h, PARAM_SIDETONE_ENABLED, [on ? 1 : 0]);
  }

  async battery(h) {
    const accept = (m) => (isMsg(m, TYPE_RESULT, CMD_BATTERY) && m.length > 8)
      || (isMsg(m, TYPE_RESPONSE, CMD_BATTERY) && m.length > 6 && m[6] !== 0);
    let reply;
    try {
      reply = await this._request(h, [...CMD_BATTERY, 0x00], accept);
    } catch (e) {
      if (e instanceof DriverError && e.code === ErrorCode.TIMEOUT) return battery(-1, BS.UNAVAILABLE);
      throw e;
    }
    if (reply[1] !== TYPE_RESULT) return battery(-1, BS.UNAVAILABLE);
    try { // microphone boom state, informational only
      await this._readKey(h, KEY_MIC_MUTE);
    } catch (e) {
      if (!(e instanceof DriverError)) throw e;
    }
    return battery(Math.min(reply[8], 100), BS.AVAILABLE);
  }

  async setSidetone(h, level) {
    const deviceMax = await this._sidetoneMax(h);
    const enabled = level > 0;
    const mapped = enabled ? Math.max(1, u8(mapInt(level, 0, 128, 0, deviceMax))) : 0;
    await this._switchSidetone(h, enabled);
    if (enabled) await this._writeKey(h, KEY_SIDETONE_LEVEL, mapped);
    return sidetone(level, mapped);
  }

  async getSidetone(h) {
    const deviceLevel = await this._readKey(h, KEY_SIDETONE_LEVEL);
    const enabled = await this._readParameter(h, PARAM_SIDETONE_ENABLED);
    const deviceMax = Math.max(await this._sidetoneMax(h), deviceLevel);
    const muted = enabled === 0 || deviceLevel === 0;
    return sidetone(muted ? 0 : u8(mapInt(deviceLevel, 0, deviceMax, 0, 128)), deviceLevel);
  }

  async setInactiveTime(h, minutes) {
    let seconds = 0;
    if (minutes > 0) seconds = ([5, 10, 15, 30, 45, 60, 90, 120, 240].find((m) => m >= minutes) ?? 360) * 60;
    const en = seconds ? 1 : 0;
    const [lsb, msb] = [seconds & 0xff, (seconds >> 8) & 0xff];
    await this._writeParameter(h, PARAM_AUTO_POWER_OFF, [en, 0, lsb, msb, en, 0, lsb, msb]);
    return minutes;
  }

  setVoicePrompts(h, on) { return this._writeKey(h, KEY_VOICE_PROMPT_LEVEL, on ? 0x07 : 0x00); }

  async chatmix(h) {
    const raw = await this._readParameter(h, PARAM_CHATMIX);
    const level = mapInt(Math.min(raw, CHATMIX_MAX), 0, CHATMIX_MAX, 0, 128);
    const game = level >= 64 ? 100 : mapInt(level, 0, 64, 0, 100);
    const chat = level <= 64 ? 100 : mapInt(level, 64, 128, 100, 0);
    return chatmix(level, game, chat);
  }

  async setEqPreset(h, preset) {
    if (preset >= 10) throw invalid("Device only supports presets 0-9");
    await this._writeKey(h, KEY_EQ_PRESET, preset + 1);
  }

  async setNoiseFilter(h, level) {
    if (level > 2) throw invalid("Noise filter level must be 0, 1, or 2");
    await this._writeKey(h, KEY_NOISE_FILTER, level);
  }
}

// -- Maxwell custom EQ (DSP biquad coefficients) ------------------------------------------------
const EQ_FREQUENCIES = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];
const EQ_HEADER_SIZE = 5, EQ_RECORD_SIZE = 18, EQ_SETTINGS_SIZE = EQ_HEADER_SIZE + EQ_RECORD_SIZE * 10;
const EQ_SAMPLE_RATES = [[44100, 1], [48000, 2], [88200, 5], [96000, 6]];
const Q18 = 262144.0, Q22 = 4194304.0, Q23 = 8388608.0, Q30 = 1073741824.0, Q31 = 2147483648.0;
const STAGE_PEAK_LIMIT = 4.0, OUTPUT_PEAK_LIMIT = 8.0, FIRST_STAGE_MARGIN = 1.19;

const supportedGain = (g) => Number.isFinite(g) && g >= -12 && g <= 12 && Math.round(g) === g;
const supportedBand = ([f, g, bw], rate) => Number.isFinite(f) && Number.isFinite(bw) && f > 0
  && f < Math.trunc(rate / 2) && bw > 0 && bw <= 48000 && supportedGain(g);

function peaking(freq, gain, bw, rate) {
  const omega = (freq * (2 * Math.PI)) / rate;
  const amplitude = Math.sqrt(Math.pow(10.0, gain / 20.0));
  const alpha = Math.sin(omega) / (2 * (freq / bw));
  const a0 = 1 + alpha / amplitude;
  const b1 = (-2 * Math.cos(omega)) / a0;
  return [(1 + alpha * amplitude) / a0, b1, (1 - alpha * amplitude) / a0, b1, (1 - alpha / amplitude) / a0];
}

// complex arithmetic exactly as CPython / libgcc do it (Smith's division)
function cdivide(ar, ai, br, bi) {
  if (Math.abs(br) >= Math.abs(bi)) {
    const ratio = bi / br;
    const denom = br + bi * ratio;
    return [(ar + ai * ratio) / denom, (ai - ar * ratio) / denom];
  }
  const ratio = br / bi;
  const denom = br * ratio + bi;
  return [(ar * ratio + ai) / denom, (ai * ratio - ar) / denom];
}

function cascadePeaks(filters, rate) {
  const peaks = new Array(filters.length).fill(0);
  for (let frequency = 0; frequency < Math.trunc(rate / 2); frequency++) {
    const theta = (-2 * Math.PI * frequency) / rate;
    const zr = Math.cos(theta), zi = Math.sin(theta);
    let cr = 1, ci = 0;
    filters.forEach(([b0, b1, b2, a1, a2], i) => {
      // b2 * z * z == (b2*z)*z
      const b2r = b2 * zr, b2i = b2 * zi;
      const nzr = b2r * zr - b2i * zi, nzi = b2r * zi + b2i * zr;
      const numR = b0 + b1 * zr + nzr, numI = b1 * zi + nzi;
      const a2r = a2 * zr, a2i = a2 * zi;
      const dzr = a2r * zr - a2i * zi, dzi = a2r * zi + a2i * zr;
      const denR = 1.0 + a1 * zr + dzr, denI = a1 * zi + dzi;
      const [rr, ri] = cdivide(numR, numI, denR, denI);
      [cr, ci] = [cr * rr - ci * ri, cr * ri + ci * rr];
      const m = Math.hypot(cr, ci);
      if (m > peaks[i]) peaks[i] = m;
    });
  }
  return peaks;
}

const dsp32 = (v) => {
  const bits = v >>> 0;
  return [...le16(bits >>> 16), ...le16(bits & 0xffff)];
};
const stable = (f) => Math.abs(f[4]) < 1 && 1 + f[3] + f[4] > 0 && 1 - f[3] + f[4] > 0;

export function eqCoefficientsForRate(bands, rate) {
  if (!bands.length || bands.length > 10 || !EQ_SAMPLE_RATES.some(([r]) => r === rate)) throw invalid("Invalid Maxwell EQ band count or sample rate");
  if (!bands.every((b) => supportedBand(b, rate))) throw invalid("Maxwell EQ requires finite whole-dB gains from -12 to 12");
  const ordered = bands.map((b, i) => [b, i]).sort((x, y) => y[0][1] - x[0][1] || x[1] - y[1]).map(([b]) => b);
  const filters = ordered.map(([f, g, bw]) => peaking(f, g, bw, rate));
  const peaks = cascadePeaks(filters, rate);
  const peak = peaks[peaks.length - 1];
  const prescale = Math.max(1.0, peak / STAGE_PEAK_LIMIT);
  let scaleProduct = 1.0;
  const out = [...le16(bands.length)];
  filters.forEach((orig, i) => {
    const filt = [...orig];
    let scale = Math.max(Math.abs(filt[0]), Math.abs(filt[1] * 0.5), Math.abs(filt[2]));
    const firstScale = i === 0 ? prescale * FIRST_STAGE_MARGIN : 1.0;
    if (peaks[i] / (scaleProduct * scale * firstScale) > STAGE_PEAK_LIMIT) scale = Math.max(1.0, scale);
    scale *= firstScale;
    scaleProduct *= scale;
    for (let j = 0; j < 5; j++) {
      if (j < 3) filt[j] /= scale;
      const quantum = j === 1 || j === 3 ? Q22 : Q23;
      filt[j] = Math.floor(Math.min(Math.max(filt[j] * quantum + 0.5, -Q23), Q23 - 1)) / quantum;
    }
    if (!stable(filt)) throw invalid("Maxwell EQ filter becomes unstable after quantization");
    for (let j = 0; j < 5; j++) out.push(...dsp32(Math.trunc(filt[j] * (j === 1 || j === 3 ? Q30 : Q31))));
  });
  const gain = scaleProduct * Math.min(1.0, OUTPUT_PEAK_LIMIT / peak);
  if (!Number.isFinite(gain) || gain <= 0 || gain >= 32) throw invalid("Maxwell EQ gain exceeds the DSP range");
  const fixedGain = Math.floor(gain * Q18 + 0.5);
  if (fixedGain > Q23 - 1) throw invalid("Maxwell EQ rounded gain exceeds the DSP range");
  out.push(...dsp32(fixedGain * 256));
  return out;
}

export function eqCoefficients(bands) {
  const out = [EQ_SAMPLE_RATES.length, 0, 0, 0];
  for (const [rate, id] of EQ_SAMPLE_RATES) {
    const block = eqCoefficientsForRate(bands, rate);
    out.push(...le16(id), ...le16(block.length / 2), ...block);
  }
  return out;
}

const int32 = (v) => (v | 0);
const readValue = (s, off) => int32(s[off] | (s[off + 1] << 8) | (s[off + 2] << 16) | (s[off + 3] << 24)) * 0.01;

function readEqBands(settings) {
  if (settings.length !== EQ_SETTINGS_SIZE) throw protocolError("Unexpected Maxwell custom EQ size (expected ten bands)");
  return EQ_FREQUENCIES.map((nominal, i) => {
    const rec = EQ_HEADER_SIZE + EQ_RECORD_SIZE * i;
    const [freq, gain, bw] = [readValue(settings, rec + 2), readValue(settings, rec + 6), readValue(settings, rec + 10)];
    if (freq !== nominal || bw <= 0 || bw > 48000) throw protocolError("Unsupported Maxwell EQ band layout or bandwidth");
    return [freq, gain, bw];
  });
}

const same = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);

class AudezeMaxwell extends Maxwell {
  static EQ_FREQUENCIES = EQ_FREQUENCIES;
  static EQ_TIMEOUT_MS = 1500;
  static EQ_FRAGMENT_INTERVAL_MS = 20;

  setVolumeLimiter(h, on) { return this._writeKey(h, KEY_VOLUME_LIMITER, on ? 0x88 : 0x8e); }

  _eqRequest(h, body, accept) {
    return this._request(h, body, accept, { attempts: 1, timeoutMs: AudezeMaxwell.EQ_TIMEOUT_MS,
      fragmentIntervalMs: AudezeMaxwell.EQ_FRAGMENT_INTERVAL_MS });
  }

  async _readStored(h, key, expectedSize) {
    const cmd = [0x0c, 0x0a];
    const reply = await this._eqRequest(h, [...cmd, ...le16(key), ...le16(1000)],
      (m) => isMsg(m, TYPE_RESPONSE, cmd) && (m.length < 9 || hasId(m, 7, key)));
    if (reply.length !== 11 + expectedSize || reply[6] !== 0 || rd16(reply, 9) !== expectedSize) {
      throw protocolError("Maxwell returned an invalid custom EQ slot");
    }
    return reply.slice(11);
  }

  async _verify(h, key, expected, mismatch) {
    if (!same(await this._readStored(h, key, expected.length), expected)) throw protocolError(mismatch);
  }

  async _eqCommand(h, body, operation, key = null, checked = true) {
    const cmd = [body[0], body[1]];
    const replySize = key !== null ? 9 : 7;
    let reply;
    try {
      reply = await this._eqRequest(h, body, (m) => isMsg(m, TYPE_RESPONSE, cmd) && (key === null || m.length < 9 || hasId(m, 7, key)));
    } catch (e) {
      if (!(e instanceof DriverError)) throw e;
      throw new DriverError(e.code, `Maxwell ${operation} failed: ${e.message}; EQ update may be partial. Close Audeze before retrying.`);
    }
    if (reply.length !== replySize || (checked && reply[6] !== 0)) throw protocolError(`Maxwell rejected ${operation}; EQ update may be partial`);
  }

  async _store(h, key, data) {
    await this._eqCommand(h, [0x03, 0x0a, ...le16(data.length)], "prepare EQ storage", null, false);
    await this._eqCommand(h, [0x0d, 0x0a, ...le16(key), ...data], "save EQ", key);
  }

  async setEqualizer(h, gains) {
    gains = gains.map(f32);
    if (gains.length !== 10 || !gains.every(supportedGain)) throw invalid("Maxwell EQ needs ten whole-dB gains from -12 to 12");
    const preset = await this._readKey(h, KEY_EQ_PRESET);
    if (preset < 7 || preset > 10) throw invalid("Select a Maxwell custom preset with -p 6..9 before editing EQ");
    const slot = preset - 7;
    const [settingsKey, coefficientsKey] = [0xef00 + slot, 0xe42c + slot];
    const settings = await this._readStored(h, settingsKey, EQ_SETTINGS_SIZE);
    const bands = readEqBands(settings);
    gains.forEach((gain, i) => {
      bands[i][1] = gain;
      const off = EQ_HEADER_SIZE + EQ_RECORD_SIZE * i + 6;
      const v = Math.trunc(gain * 100) >>> 0;
      settings.splice(off, 4, v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff);
    });
    const coefficients = eqCoefficients(bands);
    await this._eqCommand(h, [0x03, 0x0e, 0x00, ...coefficients], "apply EQ");
    await this._store(h, settingsKey, settings);
    await this._store(h, coefficientsKey, coefficients);
    await this._verify(h, settingsKey, settings, "Maxwell EQ readback differs from the saved settings; update may be partial");
    await this._verify(h, coefficientsKey, coefficients, "Maxwell coefficient readback differs; EQ update may be partial");
  }
}
driver("Audeze Maxwell", AudezeMaxwell, { web: false });

class AudezeMaxwell2 extends Maxwell {}
driver("Audeze Maxwell 2", AudezeMaxwell2, { web: false });
