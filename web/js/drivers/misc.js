// ROCCAT, MCHOSE and Sony INZONE headsets (port of openheadset/drivers/misc.py).
import {
  BatteryStatus as BS, Driver, DriverError, ErrorCode, battery, chatmix, driver, invalid, mapInt, monotonicMs, offline,
  pad, protocolError, sidetone, sleepMs, timeout, u8, zeros,
} from "./base.js";

const DEVICE_TIMEOUT = 5000;

class Roccat extends Driver {
  static DELAY_MS = 75;

  async _send(h, data, readLen = 0) {
    await h.write(data);
    if (readLen) await h.read(readLen, DEVICE_TIMEOUT);
    await sleepMs(this.constructor.DELAY_MS);
  }
}

class RoccatElo71Air extends Roccat {
  async setLights(h, on) {
    const v = on ? 0xff : 0;
    for (const pkt of [pad([0xff, 0x02], 64), pad([0xff, 0x03, 0x00, 0x01, 0x00, 0x01], 64),
      pad([0xff, 0x04, 0x00, 0x00, v, v, v], 64), pad([0xff, 0x01], 64)]) await this._send(h, pkt);
    return on;
  }

  async setInactiveTime(h, minutes) {
    if (minutes > 60) throw invalid("Device only supports up to 60 minutes");
    const seconds = (minutes * 60) & 0xffff;
    await this._send(h, pad([0xa1, 0x04, 0x06, 0x54, (seconds >> 8) & 0xff, seconds & 0xff], 64), 64);
    return minutes;
  }
}
driver("ROCCAT Elo 7.1 Air", RoccatElo71Air);

class RoccatElo71USB extends Roccat {
  static DELAY_MS = 40;

  async setLights(h, on) {
    await this._send(h, pad([0xff, 0x02], 16));
    await this._send(h, pad([0xff, 0x03, 0x00, 0x01], 16));
    await this._send(h, on ? pad([0xff, 0x04, 0x00, 0x00, 0xf4], 16) : pad([0xff, 0x04], 16));
    await this._send(h, pad([0xff, 0x01], 16));
    return on;
  }
}
driver("ROCCAT Elo 7.1 USB", RoccatElo71USB);

class MchoseX9 extends Driver {
  static TIMING_EXACT = false; // read timeouts count down from a wall-clock deadline

  async battery(h) {
    await h.write(pad([0x55, 0x65, 0x01], 64));
    const deadline = monotonicMs() + DEVICE_TIMEOUT;
    for (;;) {
      const remaining = deadline - monotonicMs();
      if (remaining <= 0) throw timeout("No battery reply from the dongle");
      const resp = await h.read(64, remaining);
      if (resp.length >= 4 && resp[0] === 0x55 && resp[1] === 0x65 && resp[3] === 0x02) return battery(resp[2], BS.AVAILABLE);
    }
  }
}
driver("MCHOSE X9 Wireless", MchoseX9);

class SonyInzoneBuds extends Driver {
  async battery(h) {
    for (let i = 0; i < 45; i++) {
      let resp;
      try {
        resp = await h.read(64, DEVICE_TIMEOUT);
      } catch (e) {
        if (e instanceof DriverError && e.code === ErrorCode.TIMEOUT) continue;
        throw e;
      }
      if (resp[1] === 0x12 && resp[2] === 0x04) return battery(Math.min(resp[16], resp[14]), BS.AVAILABLE);
    }
    throw timeout("No battery report received within attempt limit");
  }
}
driver("Sony INZONE Buds", SonyInzoneBuds);

const sum = (a) => a.reduce((s, x) => s + x, 0);

class SonyInzoneH5 extends Driver {
  static ADDR_PC_TO_RX = 0x41;

  constructor(productId = 0) {
    super(productId);
    this._tid = 0;
  }

  static _build(address, eventId, eventType, tid, payload) {
    const n = payload.length;
    const buf = zeros(64);
    buf.splice(0, 13, 0x02, (12 + n) & 0xff, 0x01, 0x00, 0xfc, (8 + n) & 0xff, 0x96, 0xc3, address, eventId, eventType,
      tid & 0xff, (tid >> 8) & 0xff);
    buf.splice(13, n, ...payload);
    buf[13 + n] = sum(buf.slice(6, 13 + n)) & 0xff;
    return buf;
  }

  static _parse(buf) {
    if (buf[0] !== 0x02) return null;
    const len = buf[1];
    if (len < 12 || len > 62) return null;
    if (buf[2] !== 0x04 || buf[3] !== 0xff || buf[5] !== 0x00 || buf[6] !== 0x96 || buf[7] !== 0xc3) return null;
    if (buf[8] >> 4 !== 0x1) return null;
    if ((sum(buf.slice(5, len + 1)) & 0xff) !== buf[len + 1]) return null;
    return [buf[9], buf[10], buf[11] | (buf[12] << 8), buf.slice(13, len + 1)];
  }

  async _exchange(h, eventId, eventType, payload = []) {
    this._tid = (this._tid + 1) & 0xffff;
    if (this._tid <= 1) this._tid = 2;
    const tid = this._tid;
    await h.write(SonyInzoneH5._build(SonyInzoneH5.ADDR_PC_TO_RX, eventId, eventType, tid, payload));
    const want = eventType === 0x02 ? 0x20 : 0x10; // SET → NTFY, GET → RET
    for (let i = 0; i < 10; i++) {
      let resp;
      try {
        resp = await h.read(64, 500);
      } catch (e) {
        if (e instanceof DriverError && e.code === ErrorCode.TIMEOUT) continue;
        throw e;
      }
      const parsed = SonyInzoneH5._parse(pad(resp, 64));
      if (parsed && parsed[0] === eventId && parsed[2] === tid && (parsed[1] === want || parsed[1] === 0xa0)) return parsed[3];
    }
    throw timeout(`No response for event_id 0x${eventId.toString(16).padStart(2, "0")} (TID ${tid})`);
  }

  async battery(h) {
    const payload = await this._exchange(h, 0x04, 0x01);
    if (payload.length < 2) throw protocolError("BATTERY_INFO payload too short");
    const [charger, percent] = payload;
    if (percent === 0xff) throw offline("Headset reports battery=0xFF (offline)");
    if (percent > 100) throw protocolError(`Invalid battery percent: ${percent}`);
    return battery(percent, charger ? BS.CHARGING : BS.AVAILABLE);
  }

  async chatmix(h) {
    const payload = await this._exchange(h, 0x22, 0x01);
    if (!payload.length) throw protocolError("GAME_CHAT_MIX_BALANCE payload empty");
    const balance = payload[0];
    if (balance === 0xff) throw offline("Headset offline");
    if (balance > 90) throw protocolError(`Invalid balance: ${balance}`);
    const chat = Math.floor((balance * 100) / 90);
    return chatmix(mapInt(balance, 0, 90, 0, 128), 100 - chat, chat);
  }

  async setSidetone(h, level) {
    await this._exchange(h, 0x23, 0x02, [u8(mapInt(level, 0, 128, 0, 50)), 0xff]);
    return sidetone(level);
  }

  async setMicVolume(h, volume) {
    await this._exchange(h, 0x24, 0x02, [0x00, u8(mapInt(volume, 0, 128, 0, 50)), 0xff]);
  }
}
driver("Sony INZONE H5", SonyInzoneH5);
