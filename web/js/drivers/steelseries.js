// SteelSeries Arctis headsets (port of openheadset/drivers/steelseries.py).
import {
  BatteryStatus as BS, Cap, Driver, DriverError, ErrorCode, FilterType, battery, chatmix, driver, f32, fadd, fdiv,
  fmul, invalid, mapInt, offline, pad, protocolError, sidetone, sidetoneToDiscrete, timeout, u8, zeros,
} from "./base.js";

const DEVICE_TIMEOUT = 5000;
const PACKET_31 = 31;
const MSG_SIZE = 64;

class Legacy extends Driver {
  static SAVE = [0x06, 0x09];

  async saveState(h) {
    await h.write(pad(this.constructor.SAVE, PACKET_31));
  }

  async sendWithSave(h, command, saveAfter = true) {
    await h.write(pad(command, PACKET_31));
    if (saveAfter) await this.saveState(h);
  }

  async batteryDirect(h, request, responseSize, batteryByte, statusByte, offlineValue, chargingValue, bmin, bmax) {
    await h.write(request);
    const resp = await h.read(responseSize, DEVICE_TIMEOUT);
    const status_ = statusByte >= 0 && statusByte < resp.length ? resp[statusByte] : null;
    if (status_ === offlineValue) throw offline("Headset not connected or turned off");
    const status = status_ === chargingValue ? BS.CHARGING : BS.AVAILABLE;
    if (!(batteryByte >= 0 && batteryByte < resp.length)) throw protocolError("Battery byte index out of bounds");
    return battery(Math.max(0, Math.min(100, mapInt(resp[batteryByte], bmin, bmax, 0, 100))), status);
  }
}

class Arctis1 extends Legacy {
  battery(h) {
    return this.batteryDirect(h, [0x06, 0x12], 8, 3, 2, 0x01, 0xff, 0, 100);
  }

  async setSidetone(h, level) {
    const mapped = u8(mapInt(level, 0, 128, 0x00, 0x12));
    await this.sendWithSave(h, level > 0 ? [0x06, 0x35, 0x01, 0x00, mapped] : [0x06, 0x35]);
    return sidetone(level);
  }

  async setInactiveTime(h, minutes) {
    minutes = Math.min(minutes, 90);
    await this.sendWithSave(h, [0x06, 0x53, minutes]);
    return minutes;
  }
}
driver("SteelSeries Arctis (1/7X/7P) Wireless", Arctis1);

class Arctis7 extends Legacy {
  battery(h) {
    return this.batteryDirect(h, [0x06, 0x18], 8, 2, -1, 0, 0, 0, 100);
  }

  async setSidetone(h, level) {
    const mapped = u8(mapInt(level, 0, 128, 0x00, 0x12));
    await this.sendWithSave(h, level > 0 ? [0x06, 0x35, 0x01, 0x00, mapped] : [0x06, 0x35]);
    return sidetone(level);
  }

  async setInactiveTime(h, minutes) {
    minutes = Math.min(minutes, 90);
    await this.sendWithSave(h, [0x06, 0x51, minutes]);
    return minutes;
  }

  async setLights(h, on) {
    await this.sendWithSave(h, pad([0x06, 0x55, 0x01, on ? 0x02 : 0x00], 8));
    return on;
  }

  async chatmix(h) {
    await h.write([0x06, 0x24]);
    const resp = await h.read(8, DEVICE_TIMEOUT);
    const [game, chat] = [resp[2], resp[3]];
    const level = game === 0 && chat === 0 ? 64 : game === 0 ? 64 - (255 - chat) : 64 + (255 - game);
    const gamePct = game === 0 ? 100 : mapInt(game, 191, 255, 0, 100);
    const chatPct = chat === 0 ? 100 : mapInt(chat, 191, 255, 0, 100);
    return chatmix(level, gamePct, chatPct);
  }
}
driver("SteelSeries Arctis (7/Pro)", Arctis7);

class Arctis9 extends Legacy {
  static SAVE = [0x90, 0x00];

  battery(h) {
    return this.batteryDirect(h, [0x00, 0x20], 12, 3, 4, 0xff, 0x01, 0x64, 0x9a);
  }

  async setSidetone(h, level) {
    let num = 0xc0;
    if (level > 0) {
      num = u8(mapInt(Math.trunc(Math.log2(level) * 100), 0, 700, 0, 128));
      num = u8(mapInt(num, 0, 128, 0xc0, 0xfd));
    }
    await this.sendWithSave(h, pad([0x06, 0x00, num], 5));
    return sidetone(level);
  }

  async setInactiveTime(h, minutes) {
    const t = minutes * 60;
    await this.sendWithSave(h, [0x04, 0x00, u8(t >> 8), u8(t)]);
    return minutes;
  }

  async chatmix(h) {
    await h.write([0x00, 0x20]);
    const resp = await h.read(12, DEVICE_TIMEOUT);
    const game = mapInt(resp[9], 0, 19, 0, 64);
    const chat = mapInt(resp[10], 0, 19, 0, -64);
    return chatmix(64 - (chat + game), mapInt(resp[9], 0, 19, 0, 100), mapInt(resp[10], 0, 19, 0, 100));
  }
}
driver("SteelSeries Arctis 9", Arctis9);

class ArctisProWireless extends Legacy {
  static SAVE = [0x90, 0xaa];

  async battery(h) {
    await h.write(pad([0x41, 0xaa], PACKET_31));
    if ((await h.read(2, DEVICE_TIMEOUT))[0] === 0x02) throw offline("Headset not connected");
    await h.write(pad([0x40, 0xaa], PACKET_31));
    const raw = (await h.read(1, DEVICE_TIMEOUT))[0];
    return battery(mapInt(raw, 0x00, 0x04, 0, 100), BS.AVAILABLE);
  }

  async setSidetone(h, level) {
    await this.sendWithSave(h, [0x39, 0xaa, u8(mapInt(level, 0, 128, 0x00, 0x09))]);
    return sidetone(level);
  }

  async setInactiveTime(h, minutes) {
    await this.sendWithSave(h, [0x3c, 0xaa, u8(Math.floor(minutes / 10))]);
    return minutes;
  }
}
driver("SteelSeries Arctis Pro Wireless", ArctisProWireless);

// -- Nova family (64-byte reports) -----------------------------------------------------------------
class Nova extends Driver {
  async readStatus(h) {
    await h.write([0x00, 0xb0]);
    return h.read(128, DEVICE_TIMEOUT);
  }

  async sendCommand(h, command) {
    await h.write(pad(command, MSG_SIZE));
  }
}

const sidetone4 = (level) => (level < 26 ? 0 : level < 51 ? 1 : level < 76 ? 2 : 3);

class ArctisNova3 extends Nova {
  static PRESETS = [
    [0x14, 0x14, 0x14, 0x14, 0x14, 0x14],
    [0x1c, 0x19, 0x11, 0x14, 0x14, 0x14],
    [0x1a, 0x17, 0x0f, 0x12, 0x17, 0x1a],
    [0x0c, 0x0d, 0x11, 0x18, 0x1c, 0x14],
  ];

  async _featureAndSave(h, cmd) {
    await h.sendFeature(cmd, MSG_SIZE);
    await h.sendFeature([0x06, 0x09], MSG_SIZE);
  }

  async setSidetone(h, level) {
    await this._featureAndSave(h, [0x06, 0x39, sidetone4(level)]);
    return sidetone(level);
  }

  async setMicVolume(h, volume) {
    await this._featureAndSave(h, [0x06, 0x37, sidetoneToDiscrete(volume, 11)]);
  }

  async setMicMuteLed(h, brightness) {
    if (brightness > 3) throw invalid("Brightness must be 0-3");
    await this._featureAndSave(h, [0x06, 0xae, brightness]);
  }

  async setEqPreset(h, preset) {
    if (!(preset >= 0 && preset < ArctisNova3.PRESETS.length)) throw invalid("Device only supports presets 0-3");
    await this._featureAndSave(h, pad([0x06, 0x33, ...ArctisNova3.PRESETS[preset]], MSG_SIZE));
  }

  async setEqualizer(h, bands) {
    if (bands.length !== 6) throw invalid("Device requires exactly 6 equalizer bands");
    const cmd = zeros(MSG_SIZE);
    [cmd[0], cmd[1]] = [0x06, 0x33];
    bands.forEach((v, i) => {
      if (v < -6.0 || v > 6.0) throw invalid("Band values must be between -6 and +6");
      cmd[i + 2] = u8(fadd(0x14, fmul(2, v)));
    });
    await h.sendFeature(cmd, MSG_SIZE);
  }
}
driver("SteelSeries Arctis Nova 3", ArctisNova3);

class Arctis7Plus extends Nova {
  static PRESETS = [
    [0x0, 0x33, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x0],
    [0x0, 0x33, 0x1f, 0x20, 0x1a, 0x15, 0x15, 0x16, 0x16, 0x16, 0x16, 0x23, 0x0],
    [0x0, 0x33, 0x1e, 0x1b, 0x15, 0x10, 0x10, 0x13, 0x1b, 0x1e, 0x20, 0x1f, 0x0],
    [0x0, 0x33, 0x0e, 0x16, 0x11, 0x13, 0x20, 0x24, 0x1f, 0x11, 0x18, 0x11, 0x0],
  ];

  _save(h) {
    return this.sendCommand(h, [0x00, 0x09]);
  }

  async battery(h) {
    const data = await this.readStatus(h);
    if (data.length < 4) throw protocolError("Response too short");
    if (data[1] === 0x01) throw offline("Headset not connected");
    return battery(Math.min(100, mapInt(data[2], 0, 4, 0, 100)), data[3] === 0x01 ? BS.CHARGING : BS.AVAILABLE);
  }

  async setSidetone(h, level) {
    await this.sendCommand(h, [0x00, 0x39, sidetone4(level)]);
    await this._save(h);
    return sidetone(level);
  }

  async setInactiveTime(h, minutes) {
    minutes = Math.min(minutes, 90);
    await this.sendCommand(h, [0x00, 0xa3, minutes]);
    await this._save(h);
    return minutes;
  }

  async chatmix(h) {
    const data = await this.readStatus(h);
    if (data.length < 6) throw protocolError("Response too short for chatmix");
    const [gameRaw, chatRaw] = [data[4], data[5]];
    const level = 64 - (mapInt(chatRaw, 0, 0x64, 0, -64) + mapInt(gameRaw, 0, 0x64, 0, 64));
    return chatmix(level, mapInt(gameRaw, 0, 0x64, 0, 100), mapInt(chatRaw, 0, 0x64, 0, 100));
  }

  async setEqPreset(h, preset) {
    if (!(preset >= 0 && preset < Arctis7Plus.PRESETS.length)) throw invalid("Device only supports presets 0-3");
    await this.sendCommand(h, Arctis7Plus.PRESETS[preset]);
    await this._save(h);
  }

  async setEqualizer(h, bands) {
    if (bands.length !== 10) throw invalid("Device requires exactly 10 equalizer bands");
    const cmd = zeros(MSG_SIZE);
    cmd[1] = 0x33;
    bands.forEach((v, i) => {
      if (v < -12.0 || v > 12.0) throw invalid("Band values must be between -12 and +12");
      cmd[i + 2] = u8(fadd(0x18, fmul(2, v)));
    });
    cmd[bands.length + 2] = 0x0;
    await this.sendCommand(h, cmd);
    await this._save(h);
  }
}
driver("SteelSeries Arctis 7+", Arctis7Plus);

// Nova 5/5X filter codes on the wire
const NOVA5_FILTER = { [FilterType.PEAKING]: 1, [FilterType.LOW_PASS]: 2, [FilterType.HIGH_PASS]: 3,
  [FilterType.LOW_SHELF]: 4, [FilterType.HIGH_SHELF]: 5 };
const FREQS10 = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];
const NOVA5_PRESETS = [
  [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  [3.5, 5.5, 4, 1, -1.5, -1.5, -1, -1, -1, -1],
  [-5, -3.5, -1, -3.5, -2.5, 4, 6, -3.5, 0, 0],
  [3, 3.5, 1.5, -1.5, -4, -4, -2.5, 1.5, 3, 4],
];

class ArctisNova5 extends Nova {
  static GAIN_BASE = 20;
  static FREQ_DISABLED = 20001;

  async _save(h) {
    await h.write([0x0, 0x09], MSG_SIZE);
    await h.write([0x0, 0x35, 0x01], MSG_SIZE);
  }

  async battery(h) {
    const data = await this.readStatus(h);
    if (data.length < 16) throw protocolError("Response too short");
    if (data[1] === 0x02) throw offline("Headset not connected");
    return battery(data[3], data[4] === 0x01 ? BS.CHARGING : BS.AVAILABLE);
  }

  async setSidetone(h, level) {
    await h.write([0x0, 0x39, sidetoneToDiscrete(level, 11)], MSG_SIZE);
    await this._save(h);
    return sidetone(level);
  }

  async setInactiveTime(h, minutes) {
    await h.write([0x0, 0xa3, minutes], MSG_SIZE);
    return minutes;
  }

  async setMicVolume(h, volume) {
    const mapped = Math.floor(volume / 8);
    await h.write([0x0, 0x37, mapped === 16 ? 15 : mapped], MSG_SIZE);
    await this._save(h);
  }

  async setMicMuteLed(h, brightness) {
    const mapped = { 2: 0x04, 3: 0x0a }[brightness] ?? brightness;
    await h.write([0x0, 0xae, mapped], MSG_SIZE);
    await this._save(h);
  }

  async setVolumeLimiter(h, on) {
    await h.write([0x0, 0x27, on ? 1 : 0], MSG_SIZE);
    await this._save(h);
  }

  async chatmix(h) {
    const data = await this.readStatus(h);
    if (data.length < 7) throw protocolError("Response too short for chatmix");
    const [gameRaw, chatRaw] = [data[5], data[6]];
    const level = 64 - (mapInt(chatRaw, 0, 100, 0, -64) + mapInt(gameRaw, 0, 100, 0, 64));
    return chatmix(level, gameRaw, chatRaw);
  }

  async setEqPreset(h, preset) {
    if (!(preset >= 0 && preset < NOVA5_PRESETS.length)) throw invalid("Device only supports presets 0-3");
    await this.setEqualizer(h, [...NOVA5_PRESETS[preset]]);
  }

  async setEqualizer(h, bands) {
    if (bands.length !== 10) throw invalid("Device requires exactly 10 equalizer bands");
    const data = zeros(MSG_SIZE);
    data[1] = 0x33;
    const base = ArctisNova5.GAIN_BASE;
    bands.forEach((gain, i) => {
      if (gain < -10.0 || gain > 10.0) throw invalid("Gain values must be between -10 and +10");
      const raw = u8(fadd(base, fmul(gain, 2)));
      const flag = raw !== base ? (i === 0 ? 0x04 : i === 9 ? 0x05 : 0x01) : 0x01;
      const f = FREQS10[i];
      data.splice(2 + 6 * i, 6, f & 0xff, (f >> 8) & 0xff, flag, raw, 0x86, 0x05);
    });
    await h.write(data, MSG_SIZE);
    await this._save(h);
  }

  async setParametricEq(h, bands) {
    if (bands.length > 10) throw invalid("Device only supports up to 10 equalizer bands");
    const data = zeros(MSG_SIZE);
    data[1] = 0x33;
    const dis = ArctisNova5.FREQ_DISABLED;
    bands.forEach((b, i) => {
      if (f32(b.frequency) !== dis && !(f32(b.frequency) >= 20 && f32(b.frequency) <= 20000)) {
        throw invalid("Frequency must be between 20 and 20000 Hz");
      }
      if (f32(b.q) < f32(0.2) || f32(b.q) > f32(10.0)) throw invalid("Q-factor must be between 0.2 and 10.0");
      if (f32(b.gain) < -10.0 || f32(b.gain) > 10.0) throw invalid("Gain must be between -10 and +10");
      const freq = Math.trunc(f32(b.frequency)) & 0xffff;
      const q = Math.trunc(fmul(b.q, 1000)) & 0xffff;
      data.splice(2 + 6 * i, 6, freq & 0xff, freq >> 8, NOVA5_FILTER[b.type] ?? 1, u8(fmul(fadd(b.gain, 10.0), 2.0)),
        q & 0xff, q >> 8);
    });
    for (let i = bands.length; i < 10; i++) data.splice(2 + 6 * i, 6, dis & 0xff, dis >> 8, 1, ArctisNova5.GAIN_BASE, 0x86, 0x05);
    await h.write(data, MSG_SIZE);
    await this._save(h);
  }
}
driver("SteelSeries Arctis Nova (5/5X)", ArctisNova5);

class ArctisNova7 extends Nova {
  static GEN2_SIDETONE_PID = 0x227e;
  static DISCRETE_BATTERY_PIDS = [0x2202, 0x2206, 0x220a, 0x223a, 0x227a, 0x22a4];
  static SIDETONE_LEVELS = [0, 43, 85, 128];

  capabilities() {
    const caps = super.capabilities();
    if (this.productId === ArctisNova7.GEN2_SIDETONE_PID) caps[Cap.SIDETONE_STATUS] = caps[Cap.SIDETONE];
    return caps;
  }

  async battery(h) {
    const data = await this.readStatus(h);
    if (data.length < 4) throw protocolError("Response too short");
    if (data[3] === 0x00) throw offline("Headset not connected");
    const status = data[3] === 0x01 || data[3] === 0x02 ? BS.CHARGING : BS.AVAILABLE;
    const level = ArctisNova7.DISCRETE_BATTERY_PIDS.includes(this.productId) ? mapInt(data[2], 0, 4, 0, 100) : data[2];
    return battery(Math.min(level, 100), status);
  }

  async setSidetone(h, level) {
    const mapped = sidetoneToDiscrete(level, 4);
    await this.sendCommand(h, [0x00, 0x39, mapped]);
    if (this.productId === ArctisNova7.GEN2_SIDETONE_PID) await this.sendCommand(h, [0x00, 0x09]);
    return sidetone(level, mapped);
  }

  async getSidetone(h) {
    if (this.productId !== ArctisNova7.GEN2_SIDETONE_PID) {
      throw new DriverError(ErrorCode.NOT_SUPPORTED, "Reading sidetone is only verified for the Nova 7 Gen 2");
    }
    await this.sendCommand(h, [0x00, 0x20]);
    for (let i = 0; i < 8; i++) {
      const resp = await h.read(MSG_SIZE, DEVICE_TIMEOUT);
      if (resp.length === 0) throw timeout("No SteelSeries settings response received");
      if (resp[0] === 0xb0) continue;
      if (resp.length < 4) throw protocolError("SteelSeries settings response too short");
      if (resp[0] !== 0x20) throw protocolError("Unexpected SteelSeries settings response");
      const raw = resp[2];
      if (raw >= ArctisNova7.SIDETONE_LEVELS.length) throw protocolError("Invalid SteelSeries sidetone value");
      return sidetone(ArctisNova7.SIDETONE_LEVELS[raw], raw);
    }
    throw protocolError("No SteelSeries settings response received");
  }

  async setInactiveTime(h, minutes) {
    await this.sendCommand(h, [0x00, 0xa3, minutes]);
    return minutes;
  }

  async chatmix(h) {
    const data = await this.readStatus(h);
    if (data.length < 6) throw protocolError("Response too short for chatmix");
    const [gameRaw, chatRaw] = [data[4], data[5]];
    const level = 64 - (mapInt(chatRaw, 0, 100, 0, -64) + mapInt(gameRaw, 0, 100, 0, 64));
    return chatmix(level, gameRaw, chatRaw);
  }

  async setEqPreset(h, preset) {
    if (!(preset >= 0 && preset < NOVA5_PRESETS.length)) throw invalid("Device only supports presets 0-3");
    await this.setEqualizer(h, [...NOVA5_PRESETS[preset]]);
  }

  async setEqualizer(h, bands) {
    if (bands.length !== 10) throw invalid("Device requires exactly 10 equalizer bands");
    const data = zeros(MSG_SIZE);
    data[1] = 0x33;
    bands.forEach((v, i) => {
      if (v < -10.0 || v > 10.0) throw invalid("Gain values must be between -10 and +10");
      data[i + 2] = u8(fadd(0x14, v));
    });
    data[12] = 0x00;
    await this.sendCommand(h, data);
  }

  setMicMuteLed(h, brightness) {
    return this.sendCommand(h, [0x00, 0xae, Math.min(brightness, 3)]);
  }

  setMicVolume(h, volume) {
    const mapped = Math.floor(volume / 16);
    return this.sendCommand(h, [0x00, 0x37, mapped === 8 ? 7 : mapped]);
  }

  setVolumeLimiter(h, on) {
    return this.sendCommand(h, [0x00, 0x3a, on ? 1 : 0]);
  }

  async setBtWhenPoweredOn(h, on) {
    await this.sendCommand(h, [0x00, 0xb2, on ? 1 : 0]);
    await this.sendCommand(h, [0x06, 0x09]);
  }

  async setBtCallVolume(h, volume) {
    if (volume > 2) throw invalid("Device only supports values 0-2");
    await this.sendCommand(h, [0x00, 0xb3, volume]);
  }
}
driver("SteelSeries Arctis Nova 7", ArctisNova7);

class ArctisNova7P extends ArctisNova7 {} // same protocol; HeadsetControl lists fewer capabilities
driver("SteelSeries Arctis Nova 7P", ArctisNova7P);

class ArctisNovaProWireless extends Legacy {
  async battery(h) {
    await h.write(pad([0x06, 0xb0], PACKET_31));
    const resp = await h.read(128, DEVICE_TIMEOUT);
    if (resp.length < 16) throw protocolError("Response too short");
    if (resp[15] === 0x01) throw offline("Headset not connected");
    return battery(mapInt(resp[6], 0, 8, 0, 100), resp[15] === 0x02 ? BS.CHARGING : BS.AVAILABLE);
  }

  async setSidetone(h, level) {
    const dev = level === 0 ? 0 : level <= 42 ? 1 : level <= 85 ? 2 : 3;
    await this.sendWithSave(h, [0x06, 0x39, dev]);
    return sidetone(level);
  }

  async setLights(h, on) {
    await this.sendWithSave(h, [0x06, 0xbf, u8(mapInt(on ? 1 : 0, 0, 1, 1, 10))]);
    return on;
  }

  async setInactiveTime(h, minutes) {
    const level = minutes >= 45 ? 6 : minutes >= 23 ? 5 : minutes >= 13 ? 4 : minutes >= 8 ? 3 : minutes >= 3 ? 2
      : minutes > 0 ? 1 : 0;
    await this.sendWithSave(h, [0x06, 0xc1, level]);
    return minutes;
  }

  async setEqPreset(h, preset) {
    if (preset >= 4) throw invalid("Device only supports presets 0-3");
    await this.sendWithSave(h, [0x06, 0x2e, preset]);
  }

  async setEqualizer(h, bands) {
    if (bands.length !== 10) throw invalid("Device requires exactly 10 equalizer bands");
    await h.write([0x06, 0x2e, 4]); // switch to the custom preset
    const cmd = zeros(PACKET_31);
    [cmd[0], cmd[1]] = [0x06, 0x33];
    bands.forEach((v, i) => {
      if (v < -10.0 || v > 10.0) throw invalid("Band values must be between -10 and +10");
      cmd[i + 2] = u8(fadd(0x14, fmul(2, v)));
    });
    await h.write(cmd);
  }
}
driver("SteelSeries Arctis Nova Pro Wireless", ArctisNovaProWireless);

const NOVA3P_FILTER = { ...NOVA5_FILTER, [FilterType.NOTCH]: 6 };

function nova3pGain(gain) {
  if (gain < 0) return u8(0xff - Math.trunc(fdiv(-f32(gain), 0.1)) + 1);
  return u8(Math.trunc(fdiv(gain, 0.1)));
}

class ArctisNova3PWireless extends Nova {
  static FREQ_DISABLED = 20001;

  _save(h) {
    return h.write([0x00, 0x09], MSG_SIZE);
  }

  async battery(h) {
    const data = await this.readStatus(h);
    if (data.length < 5) throw protocolError("Response too short");
    if (data[1] === 0x02) throw offline("Headset not connected");
    return battery(data[3], data[4] === 0x01 ? BS.CHARGING : BS.AVAILABLE);
  }

  async setSidetone(h, level) {
    await h.write([0x00, 0x39, u8(mapInt(level, 0, 128, 0, 0x0a))], MSG_SIZE);
    await this._save(h);
    return sidetone(level);
  }

  async setInactiveTime(h, minutes) {
    minutes = [90, 75, 60, 45, 30, 15, 10, 5, 1].find((step) => minutes >= step) ?? 0;
    await h.write([0x00, 0xa3, minutes], MSG_SIZE);
    await this._save(h);
    return minutes;
  }

  async setMicVolume(h, volume) {
    await h.write([0x00, 0x37, u8(mapInt(volume, 0, 128, 0, 0x0f))], MSG_SIZE);
    await this._save(h);
  }

  async setEqPreset(h, preset) {
    if (!(preset >= 0 && preset < NOVA5_PRESETS.length)) throw invalid("Device only supports presets 0-3");
    await this.setEqualizer(h, [...NOVA5_PRESETS[preset]]);
  }

  async setEqualizer(h, bands) {
    if (bands.length !== 10) throw invalid("Device requires exactly 10 equalizer bands");
    const data = zeros(MSG_SIZE);
    data[1] = 0x33;
    bands.forEach((gain, i) => {
      if (gain < -12.0 || gain > 12.0) throw invalid("Gain values must be between -12 and +12");
      const f = FREQS10[i];
      data.splice(2 + 6 * i, 6, f & 0xff, f >> 8, 0x01, nova3pGain(gain), 0x86, 0x05);
    });
    await h.write(data, MSG_SIZE);
    await this._save(h);
  }

  async setParametricEq(h, bands) {
    if (bands.length > 10) throw invalid("Device only supports up to 10 equalizer bands");
    const data = zeros(MSG_SIZE);
    data[1] = 0x33;
    const dis = ArctisNova3PWireless.FREQ_DISABLED;
    bands.forEach((b, i) => {
      if (f32(b.frequency) !== dis && !(f32(b.frequency) >= 20 && f32(b.frequency) <= 20000)) {
        throw invalid("Frequency must be between 20 and 20000 Hz");
      }
      if (f32(b.q) < f32(0.2) || f32(b.q) > f32(10.0)) throw invalid("Q-factor must be between 0.2 and 10.0");
      if (f32(b.gain) < -12.0 || f32(b.gain) > 12.0) throw invalid("Gain must be between -12 and +12");
      const freq = Math.trunc(f32(b.frequency)) & 0xffff;
      const q = Math.trunc(fmul(b.q, 1000)) & 0xffff;
      data.splice(2 + 6 * i, 6, freq & 0xff, freq >> 8, NOVA3P_FILTER[b.type] ?? 1, nova3pGain(b.gain), q & 0xff, q >> 8);
    });
    for (let i = bands.length; i < 10; i++) data.splice(2 + 6 * i, 6, dis & 0xff, dis >> 8, 1, 0, 0x86, 0x05);
    await h.write(data, MSG_SIZE);
    await this._save(h);
  }
}
driver("SteelSeries Arctis Nova 3P Wireless", ArctisNova3PWireless);

class ArctisGameBuds extends Nova {
  async battery(h) {
    const data = await this.readStatus(h);
    if (data.length <= 6) throw protocolError("Battery status response too short");
    let level = null;
    if (data[3] === 0x03) level = data[5];
    if (data[4] === 0x03) level = level === null ? data[6] : Math.min(level, data[6]);
    if (level === null) throw offline("Earbuds are docked in the charging case");
    return battery(Math.min(level, 100), BS.AVAILABLE);
  }
}
driver("SteelSeries Arctis GameBuds", ArctisGameBuds);
