// Office / VoIP headsets: Lenovo, Plantronics and Jabra (port of openheadset/drivers/office.py).
import {
  BatteryStatus as BS, Driver, DriverError, ErrorCode, battery, driver, hidError, invalid, mapInt, offline, pad,
  protocolError, sidetone, sidetoneToDiscrete, u8, zeros,
} from "./base.js";

const DEVICE_TIMEOUT = 5000;

class LenovoWirelessVoip extends Driver {
  async _request(h, cmd, payload = []) {
    await h.sendFeature(pad([0x24, cmd, ...payload], 61));
    const resp = await h.read(61, DEVICE_TIMEOUT);
    if (resp.length !== 61) throw hidError("Failed to get input report");
    if (resp[0] !== 0x27) throw hidError("Wrong response report ID");
    if (resp[1] !== cmd) throw hidError("Wrong command response");
    if (resp[2] !== 0) throw offline("Headset not connected to wireless receiver");
    return resp;
  }

  async setSidetone(h, level) {
    return sidetone((await this._request(h, 0x0e, [0x01, u8(mapInt(level, 0, 128, 0, 5))]))[4]);
  }

  async battery(h) {
    return battery((await this._request(h, 0x01))[7], BS.AVAILABLE);
  }

  async setInactiveTime(h, minutes) {
    const code = minutes <= 0 ? 0 : minutes <= 60 ? 1 : minutes <= 120 ? 2 : minutes <= 240 ? 3 : 4;
    const resp = (await this._request(h, 0x08, [0x01, code]))[4];
    return resp ? u8(Math.min(60 * (1 << (resp - 1)), 255)) : 0;
  }

  setVoicePrompts(h, on) { return this._request(h, 0x05, [0x01, on ? 1 : 0]); }
  setRotateToMute(h, on) { return this._request(h, 0x04, [0x01, on ? 1 : 0]); }

  async setEqPreset(h, preset) {
    if (preset >= 4) throw invalid("Device only supports presets 0-3");
    await this._request(h, 0x02, [0x01, preset]);
  }

  setMicMuteLed(h, brightness) { return this._request(h, 0x15, [0x01, brightness > 0 ? 0x00 : 0x01]); }
  setVolumeLimiter(h, on) { return this._request(h, 0x07, [0x01, on ? 0x02 : 0x00]); }
}
driver("Lenovo Wireless VoIP Headset", LenovoWirelessVoip);

const BT600 = { REPORT_ID: 0x07, HEADSET: 0x20, HEADSET_REPLY: 0x02, DONGLE: 0x00, HELLO: 0x01, GET: 0x02, SET: 0x05,
  HELLO_ACK: 0x08, GET_REPLY: 0x03 };

class PlantronicsBT600 extends Driver {
  static _frame(op, grp, item, value = 0, addr = BT600.HEADSET) {
    const f = zeros(64);
    f.splice(0, 6, BT600.REPORT_ID, 0x01, 0x01, 0x10, op === BT600.GET ? 0x06 : 0x07, addr);
    f.splice(8, 3, op, grp, item);
    if (op === BT600.SET) f[11] = value;
    return f;
  }

  async _await(h, op, grp, item, addr = BT600.HEADSET_REPLY) {
    for (let i = 0; i < 16; i++) {
      let r;
      try {
        r = pad(await h.read(64, DEVICE_TIMEOUT), 64);
      } catch (e) {
        if (e instanceof DriverError && e.code === ErrorCode.TIMEOUT) {
          throw offline("Headset not connected to BT600 adapter (powered off or out of range)");
        }
        throw e;
      }
      if (r[0] === BT600.REPORT_ID && r[1] === 0x01 && r[2] === 0x01 && r[3] === 0x10 && r[5] === addr && r[9] === grp
        && r[10] === item && (op === 0 || r[8] === op)) return r;
    }
    throw protocolError("No reply for the requested setting");
  }

  async _openSession(h) {
    await h.write([0x06, 0x01]);
    await h.write(PlantronicsBT600._frame(BT600.HELLO, 0x01, 0x02, 0, BT600.DONGLE));
    await this._await(h, BT600.HELLO_ACK, 0x01, 0x02, BT600.DONGLE);
    await h.write(PlantronicsBT600._frame(BT600.HELLO, 0x01, 0x02, 0, BT600.HEADSET));
    await this._await(h, BT600.HELLO_ACK, 0x01, 0x02, BT600.HEADSET_REPLY);
  }

  async _apply(h, grp, item, value) {
    await this._openSession(h);
    await h.write(PlantronicsBT600._frame(BT600.SET, grp, item, value));
    await this._await(h, 0, grp, item);
  }

  async setSidetone(h, level) {
    const dev = sidetoneToDiscrete(level, 3);
    await this._apply(h, 0x04, 0x10, dev);
    return sidetone(dev);
  }

  async setLights(h, on) {
    await this._apply(h, 0x04, 0x09, on ? 1 : 0);
    return on;
  }

  setVoicePrompts(h, on) { return this._apply(h, 0x04, 0x11, on ? 1 : 0); }
  setVolumeLimiter(h, on) { return this._apply(h, 0x0f, 0x0e, on ? 1 : 0); }

  async battery(h) {
    await this._openSession(h);
    await h.write(PlantronicsBT600._frame(BT600.GET, 0x0a, 0x1a));
    const r = await this._await(h, BT600.GET_REPLY, 0x0a, 0x1a);
    const [level, count] = [r[11], r[12]];
    if (count < 2) throw protocolError("Unexpected battery reply format");
    return battery(Math.min(100, Math.floor((level * 100) / (count - 1))), r[13] ? BS.CHARGING : BS.AVAILABLE);
  }
}
driver("Plantronics Voyager 8200 UC (BT600)", PlantronicsBT600);

const SIDETONE_DB = [-9, -6, -3, 0, 3, 6];

class JabraGNP extends Driver {
  static ADDRESS = 0x04;

  constructor(productId = 0) {
    super(productId);
    this._seq = 0;
  }

  static _nack(reason) {
    if ([0xf3, 0xf4, 0xf5].includes(reason)) return offline("Headset not connected to the Jabra dongle (powered off or out of range)");
    if ([0xf6, 0xff].includes(reason)) return new DriverError(ErrorCode.NOT_SUPPORTED, "The paired Jabra headset does not implement this setting");
    return protocolError("Jabra device rejected the command (NACK)");
  }

  async _request(h, typ, cmd, sub, payload = []) {
    const seq = this._seq;
    this._seq = (this._seq + 1) & 0xff;
    const f = zeros(64);
    f.splice(0, 7, 0x05, this.constructor.ADDRESS, 0x00, seq, ((typ << 6) | (6 + payload.length)) & 0xff, cmd, sub);
    f.splice(7, payload.length, ...payload);
    await h.write(f);
    for (let i = 0; i < 16; i++) {
      let r;
      try {
        r = pad(await h.read(64, DEVICE_TIMEOUT), 64);
      } catch (e) {
        if (e instanceof DriverError && e.code === ErrorCode.TIMEOUT) throw offline("No reply from the Jabra dongle");
        throw e;
      }
      if (r[0] !== 0x05 || r[4] >> 6 !== 0x03 || r[3] !== seq) continue;
      if (r[5] === 0xfe) throw JabraGNP._nack(r[6]);
      return r;
    }
    throw protocolError("No reply for the requested command");
  }

  async _set(h, cmd, sub, ...payload) {
    if ((await this._request(h, 0x02, cmd, sub, payload))[5] !== 0xff) {
      throw protocolError("Jabra device did not acknowledge the setting");
    }
  }

  static _payloadLen(r) {
    const n = r[4] & 0x3f;
    return n > 6 ? n - 6 : 0;
  }

  async _readSidetone(h) {
    const r = await this._request(h, 0x01, 0x13, 0x7c);
    if (JabraGNP._payloadLen(r) < 2) throw protocolError("Unexpected Jabra sidetone reply format");
    const db = r[8] > 127 ? r[8] - 256 : r[8];
    if (!SIDETONE_DB.includes(db)) throw protocolError("Invalid Jabra sidetone value");
    return [r[7] === 0x01, SIDETONE_DB.indexOf(db)];
  }

  static _sidetoneResult(muted, index) {
    const n = SIDETONE_DB.length;
    const step = Math.floor(128 / n);
    return sidetone(muted ? 0 : step * Math.min(index, n - 1) + Math.floor(step / 2), index);
  }

  async battery(h) {
    const r = await this._request(h, 0x01, 0x12, 0x02);
    if (JabraGNP._payloadLen(r) < 2) throw protocolError("Unexpected battery reply format");
    return battery(Math.min(100, r[8]), r[7] & 0x01 ? BS.CHARGING : BS.AVAILABLE);
  }

  async setSidetone(h, level) {
    let [, index] = await this._readSidetone(h);
    const muted = level === 0;
    if (!muted) index = sidetoneToDiscrete(level, SIDETONE_DB.length);
    await this._set(h, 0x13, 0x7c, muted ? 0x01 : 0x00, SIDETONE_DB[index] & 0xff);
    return JabraGNP._sidetoneResult(muted, index);
  }

  async getSidetone(h) {
    return JabraGNP._sidetoneResult(...(await this._readSidetone(h)));
  }

  async setLights(h, on) {
    await this._set(h, 0x12, 0x08, on ? 1 : 0, 0x00);
    return on;
  }

  setVoicePrompts(h, on) { return this._set(h, 0x13, 0x3a, on ? 0x01 : 0x00); }

  async setInactiveTime(h, minutes) {
    const units = u8(Math.floor((minutes + 2) / 5));
    await this._set(h, 0x13, 0x90, units);
    return u8(units * 5);
  }

  setVolumeLimiter(h, on) { return this._set(h, 0x13, 0x01, on ? 0x03 : 0x00); }
}

class JabraLink380 extends JabraGNP {}
driver("Jabra Link 380 (paired headset)", JabraLink380);
class JabraLink390 extends JabraGNP {}
driver("Jabra Link 390 (paired headset)", JabraLink390);
class JabraEvolve265Flex extends JabraGNP {
  static ADDRESS = 0x08;
}
driver("Jabra Evolve2 65 Flex (USB)", JabraEvolve265Flex);
