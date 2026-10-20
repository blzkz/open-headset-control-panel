// Logitech headsets with fixed HID++ commands, G930, Zone and ASTRO A50 (port of openheadset/drivers/logitech.py).
import {
  BatteryStatus as BS, Driver, DriverError, FilterType, battery, chatmix, driver, f32, fadd, fdiv, fmul, invalid, lround,
  mapInt, offline, pad, protocolError, sidetone, sleepMs, timeout, u8, zeros,
} from "./base.js";

const DEVICE_TIMEOUT = 5000;
const HIDPP_LONG = 0x11;
const HIDPP_LONG_LEN = 20;
const HIDPP_RECEIVER = 0xff;

/** HeadsetControl's piecewise-linear interpolation, float32 like the original. */
export function splineBatteryLevel(percentages, voltages, voltage) {
  const size = Math.min(percentages.length, voltages.length);
  for (let i = 0; i < size; i++) {
    if (voltage >= voltages[i]) return percentages[i];
    if (i < size - 1 && voltage >= voltages[i + 1]) {
      const diff = voltages[i] - voltages[i + 1];
      if (diff === 0) return percentages[i + 1];
      const fraction = fdiv(f32(voltage - voltages[i + 1]), f32(diff));
      return percentages[i + 1] + Math.trunc(fmul(fraction, f32(percentages[i] - percentages[i + 1])));
    }
  }
  return 0;
}

const CAL_G533 = [[100, 50, 30, 20, 5, 0], [4200, 3850, 3790, 3750, 3680, 3330]];
const CAL_G535 = [[100, 50, 30, 20, 5, 0], [4175, 3817, 3766, 3730, 3664, 3310]];
const CAL_G633 = [[100, 80, 60, 40, 20, 10, 5, 0], [4100, 3950, 3850, 3750, 3650, 3500, 3300, 3150]];
const CAL_GPRO = [[100, 50, 30, 20, 5, 0], [4150, 3830, 3780, 3740, 3670, 3320]];

class HidppFixed extends Driver {
  static CALIBRATION = CAL_GPRO;

  static _long(...command) {
    return pad([HIDPP_LONG, HIDPP_RECEIVER, ...command.slice(0, HIDPP_LONG_LEN - 2)], HIDPP_LONG_LEN);
  }

  async _command(h, command, responseLength = 7) {
    await h.write(HidppFixed._long(...command));
    const resp = await h.read(responseLength, DEVICE_TIMEOUT);
    if (resp.length > 2 && resp[2] === 0xff) throw offline("Headset not connected or turned off");
    return resp;
  }

  _feature(h, ...command) {
    return h.write(HidppFixed._long(...command));
  }

  async _battery(h, ...command) {
    const resp = await this._command(h, command);
    const status = resp[6] === 0x03 ? BS.CHARGING : BS.AVAILABLE;
    const voltage = (resp[4] << 8) | resp[5];
    const [percentages, voltages] = this.constructor.CALIBRATION;
    if (voltage < voltages[voltages.length - 1]) return battery(-1, BS.UNAVAILABLE, voltage);
    return battery(splineBatteryLevel(percentages, voltages, voltage), status, voltage);
  }

  async _inactiveTime(h, minutes) {
    await this._command(h, [0x07, 0x21, minutes]);
    return minutes;
  }
}

class LogitechG533 extends HidppFixed {
  static CALIBRATION = CAL_G533;
  battery(h) { return this._battery(h, 0x07, 0x01); }

  async setSidetone(h, level) {
    await this._feature(h, 0x04, 0x0e, 0xff, 0x05, 0x01, 0x04, 0x00, u8(mapInt(level, 0, 128, 200, 255)));
    return sidetone(level);
  }

  setInactiveTime(h, minutes) { return this._inactiveTime(h, minutes); }
}
driver("Logitech G533", LogitechG533, { image: "headset-g533.png" });

class LogitechG535 extends HidppFixed {
  static CALIBRATION = CAL_G535;
  battery(h) { return this._battery(h, 0x05, 0x0d); }

  async setSidetone(h, level) {
    const mapped = u8(mapInt(level, 0, 128, 0, 100));
    const resp = await this._command(h, [0x04, 0x1d, mapped], HIDPP_LONG_LEN);
    if (resp[4] !== mapped) throw protocolError(`Sidetone validation failed: expected ${mapped}, got ${resp[4]}`);
    return sidetone(level);
  }

  async setInactiveTime(h, minutes) {
    const validated = [0, 1, 2, 5, 10, 15].find((v) => minutes <= v) ?? 30;
    const resp = await this._command(h, [0x05, 0x2d, validated], HIDPP_LONG_LEN);
    if (resp[4] !== validated) throw protocolError("Inactive time validation failed");
    return validated;
  }
}
driver("Logitech G535", LogitechG535, { image: "headset-g535.png" });

class LogitechG633Family extends HidppFixed {
  static CALIBRATION = CAL_G633;
  battery(h) { return this._battery(h, 0x08, 0x0a); }

  async setSidetone(h, level) {
    const clamped = Math.min(level, 100);
    await this._feature(h, 0x07, 0x1a, clamped);
    return sidetone(clamped);
  }

  async setLights(h, on) {
    const mode = on ? 0x02 : 0x00; // breathing / off
    const tail = [0x00, 0xb6, 0xff, 0x0f, 0xa0, 0x00, 0x64, 0x00, 0x00, 0x00];
    await this._feature(h, 0x04, 0x3c, 0x01, mode, ...tail); // strips
    await sleepMs(1);
    await this._feature(h, 0x04, 0x3c, 0x00, mode, ...tail); // logo
    return on;
  }
}
driver("Logitech G633/G635/G733/G933/G935", LogitechG633Family, { image: "headset-g935.png" });

class LogitechG432 extends HidppFixed {
  async setSidetone(h, level) {
    await this._feature(h, 0x05, 0x1e, u8(mapInt(level, 0, 128, 0, 100)));
    return sidetone(level);
  }
}
driver("Logitech G431/G432/G433", LogitechG432, { image: "headset-g432.png" });

class LogitechGPro extends HidppFixed {
  static CALIBRATION = CAL_GPRO;
  battery(h) { return this._battery(h, 0x06, 0x0d); }

  async setSidetone(h, level) {
    await this._feature(h, 0x05, 0x1a, u8(mapInt(level, 0, 128, 0, 100)));
    return sidetone(level);
  }

  setInactiveTime(h, minutes) { return this._inactiveTime(h, minutes); }
}
driver("Logitech G PRO Series", LogitechGPro, { image: "headset-front.png" });

class LogitechG930 extends Driver {
  async battery(h) {
    await h.sendFeature(pad([0xff, 0x09, 0x00, 0xfd, 0xf4, 0x10, 0x05, 0xb1, 0xbf, 0xa0, 0x04], 64));
    let buf = [];
    for (let i = 0; i < 3; i++) {
      buf = await h.getFeature(0xff, 64);
      if (i < 2) await sleepMs(100);
    }
    return battery(mapInt(buf[13], 44, 91, 0, 100), BS.AVAILABLE);
  }

  async setSidetone(h, level) {
    await h.sendFeature(pad([0xff, 0x0a, 0x00, 0xff, 0xf4, 0x10, 0x05, 0xda, 0x8f, 0xf2, 0x01, level], 64));
    return sidetone(level);
  }
}
driver("Logitech G930", LogitechG930);

class LogitechZoneWired extends HidppFixed {
  _set(h, a, b, value) {
    return h.sendFeature([0x22, 0xf1, 0x04, 0x00, a, b, value], 20);
  }

  async setSidetone(h, level) {
    await this._set(h, 0x04, 0x3d, u8(mapInt(level, 0, 128, 0, 10)));
    return sidetone(level);
  }

  setVoicePrompts(h, on) { return this._set(h, 0x05, 0x3d, on ? 1 : 0); }
  setRotateToMute(h, on) { return this._set(h, 0x05, 0x6d, on ? 1 : 0); }
}
driver("Logitech Zone Wired/Zone 750", LogitechZoneWired);

class LogitechAstroA50 extends Driver {
  static NOISE_LEVELS = [0x00, 0x01, 0x04];

  async _request(h, cmd, handleByte, payload = [], readReply = false) {
    const frame = zeros(64);
    frame.splice(0, 6, 0x02, 0x0c, (3 + payload.length) & 0xff, 0x00, cmd, handleByte);
    frame.splice(6, Math.min(payload.length, 58), ...payload.slice(0, 58));
    await h.write(frame, 64);
    if (!readReply) return frame;
    for (let i = 0; i < 8; i++) {
      let resp = await h.read(64, DEVICE_TIMEOUT);
      if (!resp.length) continue;
      resp = pad(resp, 64);
      if (resp[0] === 0x02 && resp[1] === 0x0c && resp[4] === cmd) return resp;
    }
    throw timeout("ASTRO A50: no response for command");
  }

  async battery(h) {
    const f = await this._request(h, 0x06, 0x0c, [], true);
    return battery(f[6], f[8] ? BS.CHARGING : BS.AVAILABLE);
  }

  async chatmix(h) {
    const level = mapInt((await this._request(h, 0x0a, 0x0c, [], true))[6], 0, 12, 128, 0);
    const game = level <= 64 ? 100 : mapInt(level, 64, 128, 100, 0);
    const chat = level >= 64 ? 100 : mapInt(level, 0, 64, 0, 100);
    return chatmix(level, game, chat);
  }

  async setSidetone(h, level) {
    await this._request(h, 0x09, 0x1c, [0x01, 0xff, u8(mapInt(level, 0, 128, 0, 6))]);
    return sidetone(level);
  }

  async setLights(h, on) {
    await this._request(h, 0x0f, 0x1c, [on ? 100 : 0]);
    return on;
  }

  async setNoiseFilter(h, level) {
    if (level > 2) throw invalid("Noise filter level must be 0, 1, or 2");
    await this._request(h, 0x14, 0x2d, [LogitechAstroA50.NOISE_LEVELS[level]]);
  }

  async setParametricEq(h, bands) {
    if (bands.length !== 10) throw invalid("ASTRO A50 requires exactly 10 parametric EQ bands");
    const payload = [0x01, 0x03, 0x00];
    for (const band of bands) {
      const [freq, gain, q] = [f32(band.frequency), f32(band.gain), f32(band.q)];
      if (band.type !== FilterType.PEAKING) throw invalid("ASTRO A50 only supports peaking EQ bands");
      if (freq < 20 || freq > 20000) throw invalid("Frequency must be between 20 Hz and 20000 Hz");
      if (gain < -6.0 || gain > 6.0) throw invalid("Gain must be between -6 dB and +6 dB");
      if (q < f32(0.031) || q > f32(7.969)) throw invalid("Q factor must be between 0.031 and 7.969");
      const f = lround(freq) & 0xffff;
      const qv = Math.min(Math.max(lround(fmul(q, 32.0)), 1), 0xffff);
      const g = Math.min(Math.max(lround(fadd(120.0, fmul(gain, 20.0))), 0), 240);
      payload.push(f >> 8, f & 0xff, qv & 0xff, qv >> 8, g);
    }
    await this._request(h, 0x0d, 0x2c, payload);
  }
}
driver("Logitech ASTRO A50 Gen 5", LogitechAstroA50);

class LogitechAstroA50Gen4 extends Driver {
  static PRESET_COUNT = 3;
  static NOISE_LEVELS = [0x00, 0x01, 0x03];
  static Q_MIN = fdiv(4096.0, 12288.0);
  static Q_MAX = fdiv(4096.0, 409.0);

  constructor(productId = 0) {
    super(productId);
    this._pending = false; // a reply may still be queued after a timeout
  }

  async _request(h, cmd, payload = [], expectPayload = false) {
    if (this._pending) {
      try {
        await h.read(64, 1000);
      } catch (e) {
        if (!(e instanceof DriverError)) throw e;
      }
      this._pending = false;
    }
    const frame = zeros(64);
    [frame[0], frame[1]] = [0x02, cmd];
    if (payload.length) {
      frame[2] = payload.length;
      frame.splice(3, Math.min(payload.length, 61), ...payload.slice(0, 61));
    }
    await h.write(frame, 64);
    let reply;
    try {
      reply = await h.read(64, DEVICE_TIMEOUT);
    } catch (e) {
      this._pending = true;
      throw e;
    }
    if (!reply.length) {
      this._pending = true;
      throw timeout("ASTRO A50 Gen 4: no reply from base station");
    }
    if (reply.length !== 64) throw protocolError("ASTRO A50 Gen 4: incomplete reply from base station");
    if (reply[0] !== 0x02) throw protocolError("ASTRO A50 Gen 4: reply does not start with report ID 0x02");
    if (reply[1] === 0x02) return reply;
    if (reply[1] === 0x00) {
      if (expectPayload) throw protocolError("ASTRO A50 Gen 4: base station returned no data");
      return reply;
    }
    if (reply[1] === 0x01) throw protocolError(`ASTRO A50 Gen 4: error (code 0x${reply[3].toString(16)})`);
    throw protocolError("ASTRO A50 Gen 4: unknown reply status");
  }

  async _linked(h) {
    return !!((await this._request(h, 0x54, [], true))[3] & 0x02);
  }

  async battery(h) {
    if (!(await this._linked(h))) return battery(-1, BS.UNAVAILABLE);
    const raw = (await this._request(h, 0x7c, [], true))[3];
    return battery(raw & 0x7f, raw & 0x80 ? BS.CHARGING : BS.AVAILABLE);
  }

  async chatmix(h) {
    if (!(await this._linked(h))) throw offline("ASTRO A50 Gen 4: headset is not linked to the base station");
    const raw = (await this._request(h, 0x72, [], true))[3];
    const level = raw >= 125 && raw <= 130 ? 64 : mapInt(raw, 0, 255, 128, 0);
    const game = level <= 64 ? 100 : mapInt(level, 64, 128, 100, 0);
    const chat = level >= 64 ? 100 : mapInt(level, 0, 64, 0, 100);
    return chatmix(level, game, chat);
  }

  async _setSlider(h, slider, value) {
    const reply = await this._request(h, 0x62, [slider, value]);
    if (reply[1] === 0x02 && reply[4] !== slider) throw protocolError("ASTRO A50 Gen 4: slider write was not echoed back");
  }

  async setSidetone(h, level) {
    const deviceLevel = u8(mapInt(level, 0, 128, 0, 100));
    await this._setSlider(h, 0x05, deviceLevel);
    return sidetone(level, deviceLevel);
  }

  async getSidetone(h) {
    const reply = await this._request(h, 0x68, [0x05], true);
    if (reply[3] !== 0x68 || reply[4] !== 0x05) throw protocolError("ASTRO A50 Gen 4: unexpected slider reply");
    return sidetone(u8(mapInt(reply[5], 0, 100, 0, 128)), reply[5]);
  }

  setMicVolume(h, volume) {
    return this._setSlider(h, 0x04, u8(mapInt(volume, 0, 128, 0, 100)));
  }

  async setNoiseFilter(h, level) {
    const levels = LogitechAstroA50Gen4.NOISE_LEVELS;
    if (level >= levels.length) throw invalid("Noise filter level must be 0, 1, or 2");
    const reply = await this._request(h, 0x64, [levels[level]]);
    if (reply[1] === 0x02 && reply[3] !== levels[level]) throw protocolError("ASTRO A50 Gen 4: noise gate mode was not echoed back");
  }

  async _activePreset(h) {
    const preset = (await this._request(h, 0x6c, [], true))[3];
    if (preset < 1 || preset > 3) throw protocolError("ASTRO A50 Gen 4: active preset out of range");
    return preset;
  }

  async setEqPreset(h, preset) {
    if (preset >= 3) throw invalid("ASTRO A50 Gen 4 has presets 0, 1 and 2");
    const devicePreset = preset + 1;
    const reply = await this._request(h, 0x67, [devicePreset]);
    if (reply[1] === 0x02 && reply[4] !== devicePreset) throw protocolError("ASTRO A50 Gen 4: preset write was not echoed back");
    for (let i = 0; i < 8; i++) {
      await sleepMs(200);
      if ((await this._activePreset(h)) === devicePreset) return;
    }
    throw protocolError("ASTRO A50 Gen 4: preset change was not confirmed by the base station");
  }

  static _validGain(gain) {
    return Number.isFinite(gain) && gain >= -7.0 && gain <= 7.0;
  }

  async _setGains(h, preset, gains) {
    const reply = await this._request(h, 0x63, [preset, ...gains.map((g) => u8(lround(g) + 12))]);
    if (reply[1] === 0x02 && (reply[3] !== 0x63 || reply[4] !== preset)) {
      throw protocolError("ASTRO A50 Gen 4: EQ gain write was not echoed back");
    }
  }

  async setEqualizer(h, gains) {
    if (gains.length !== 5) throw invalid("ASTRO A50 Gen 4 requires exactly 5 equalizer gains");
    gains = gains.map(f32);
    if (!gains.every(LogitechAstroA50Gen4._validGain)) throw invalid("Gain must be finite and between -7 dB and +7 dB");
    await this._setGains(h, await this._activePreset(h), gains);
  }

  async setParametricEq(h, bands) {
    if (bands.length !== 5) throw invalid("ASTRO A50 Gen 4 requires exactly 5 parametric EQ bands");
    const expected = [FilterType.LOW_SHELF, FilterType.PEAKING, FilterType.PEAKING, FilterType.PEAKING, FilterType.HIGH_SHELF];
    bands.forEach((band, i) => {
      const [freq, q] = [f32(band.frequency), f32(band.q)];
      if (band.type !== expected[i]) throw invalid("ASTRO A50 Gen 4 bands must be lowshelf, peaking, peaking, peaking, highshelf");
      if (!Number.isFinite(freq) || freq < 80 || freq > 15000) throw invalid("Frequency must be between 80 Hz and 15000 Hz");
      if (!LogitechAstroA50Gen4._validGain(f32(band.gain))) throw invalid("Gain must be between -7 dB and +7 dB");
      if (band.type === FilterType.PEAKING
        && (!Number.isFinite(q) || q < LogitechAstroA50Gen4.Q_MIN || q > LogitechAstroA50Gen4.Q_MAX)) {
        throw invalid("Q factor out of range");
      }
    });
    const preset = await this._activePreset(h);
    for (let i = 0; i < bands.length; i++) {
      const band = bands[i];
      const bw = band.type === FilterType.PEAKING ? Math.min(Math.max(lround(fdiv(4096.0, band.q)), 409), 12288) : 0;
      const freq = lround(f32(band.frequency)) & 0xffff;
      const reply = await this._request(h, 0x6f, [preset, i + 1, bw & 0xff, bw >> 8, freq & 0xff, freq >> 8]);
      if (reply[1] === 0x02 && reply[3] !== 0x6f) throw protocolError("ASTRO A50 Gen 4: EQ band write was not echoed back");
    }
    await this._setGains(h, preset, bands.map((b) => f32(b.gain)));
  }
}
driver("Logitech ASTRO A50 Gen 4", LogitechAstroA50Gen4);
