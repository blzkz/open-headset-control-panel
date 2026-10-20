// Corsair Void / Virtuoso headsets (port of openheadset/drivers/corsair.py).
import { BatteryStatus as BS, Driver, DriverError, ErrorCode, battery, driver, mapInt, offline, pad, protocolError, sidetone, u8 } from "./base.js";

const DEVICE_TIMEOUT = 5000;

class CorsairVoid extends Driver {
  async battery(h) {
    await h.write([0xc9, 0x64]);
    const resp = await h.read(5, DEVICE_TIMEOUT);
    const statusByte = resp[4];
    if (statusByte === 0) throw offline("Headset not connected to wireless receiver");
    let status;
    if (statusByte === 4 || statusByte === 5) status = BS.CHARGING;
    else if (statusByte === 1 || statusByte === 2) status = BS.AVAILABLE;
    else throw protocolError(`Unknown status byte: ${statusByte}`);
    return battery(resp[2] & 0x7f, status); // bit 7: microphone boom up
  }

  async setSidetone(h, level) {
    const mapped = u8(mapInt(level, 0, 128, 200, 255));
    await h.sendFeature(pad([0xff, 0x0b, 0x00, 0xff, 0x04, 0x0e, 0xff, 0x05, 0x01, 0x04, 0x00, mapped], 64));
    return sidetone(level);
  }

  async setLights(h, on) {
    await h.write([0xc8, on ? 0x00 : 0x01, 0x00]);
    return on;
  }

  async notificationSound(h, sound) {
    await h.write(pad([0xca, 0x02, sound], 5), 3);
  }
}
driver("Corsair Headset Device", CorsairVoid);

const RECEIVER = 0x08;
const HEADSET = 0x09;
const READ_SIZE = 64;
const WRITE_SIZE = 65;

class CorsairVoidV2W extends Driver {
  _w(h, ...head) {
    return h.write(pad(head, WRITE_SIZE), WRITE_SIZE);
  }

  async _flush(h) {
    for (;;) {
      try {
        await h.read(READ_SIZE, 5);
      } catch (e) {
        if (e instanceof DriverError && e.code === ErrorCode.TIMEOUT) return;
        throw e;
      }
    }
  }

  async _readLen(h) {
    try {
      return (await h.read(READ_SIZE, DEVICE_TIMEOUT)).length;
    } catch (e) {
      if (e instanceof DriverError) return 0; // valueOr(0)
      throw e;
    }
  }

  async _wake(h) {
    await this._w(h, 0x00, 0x02, RECEIVER, 0x02, 0x13);
    await this._w(h, 0x00, 0x02, RECEIVER, 0x02, 0x12);
    await this._flush(h);
    await this._w(h, 0x00, 0x02, HEADSET, 0x02, 0x12);
    return this._readLen(h);
  }

  async _initialize(h) {
    await this._w(h, 0x00, 0x02, RECEIVER, 0x02, 0x13);
    await this._w(h, 0x00, 0x02, RECEIVER, 0x01, 0x03, 0x00, 0x02);
    await this._w(h, 0x00, 0x02, RECEIVER, 0x02, 0x12);
    await this._w(h, 0x00, 0x02, HEADSET, 0x01, 0x03, 0x00, 0x02);
    await this._flush(h);
    await this._w(h, 0x00, 0x02, HEADSET, 0x02, 0x12);
    return this._readLen(h);
  }

  async battery(h) {
    if ((await this._wake(h)) === 0) throw offline("Headset not connected to wireless receiver");
    await this._flush(h);
    const request = [0x00, 0x02, HEADSET, 0x02, 0x0f];
    await this._w(h, ...request);
    let resp = await h.read(READ_SIZE, DEVICE_TIMEOUT);
    let level = (resp[4] | (resp[5] << 8)) & 0xffff;
    for (let attempts = 0; attempts < 3 && (level === 0 || level > 1000); attempts++) {
      await this._w(h, ...request);
      resp = await h.read(READ_SIZE, DEVICE_TIMEOUT);
      level = (resp[4] | (resp[5] << 8)) & 0xffff;
    }
    if (level === 0 || level > 1000) return battery(-1, BS.UNAVAILABLE);
    try { // microphone boom state: best effort, as in the original
      await this._w(h, 0x00, 0x02, HEADSET, 0x02, 0xa6);
      await h.read(READ_SIZE, DEVICE_TIMEOUT);
    } catch (e) {
      if (!(e instanceof DriverError)) throw e;
    }
    return battery(Math.floor(level / 10), BS.AVAILABLE);
  }

  async setSidetone(h, level) {
    const mapped = mapInt(level, 0, 128, 0, 1000) & 0xffff;
    const value = Math.floor((mapped + 5) / 10) * 10; // round_to_multiples(mapped, 10)
    if ((await this._initialize(h)) === 0) throw offline("Headset not connected to wireless receiver");
    const anc = pad([0x00, 0x02, HEADSET, 0x01, 0xd1], WRITE_SIZE);
    const side = pad([0x00, 0x02, HEADSET, 0x01, 0x46], WRITE_SIZE);
    if (level === 0) [side[6], anc[6]] = [0x01, 0x01];
    await h.write(anc, WRITE_SIZE);
    await h.write(side, WRITE_SIZE);
    await this._w(h, 0x00, 0x02, HEADSET, 0x01, 0x47, 0, value & 0xff, (value >> 8) & 0xff);
    return sidetone(level);
  }

  async setInactiveTime(h, minutes) {
    minutes = Math.min(minutes, 90);
    if ((await this._initialize(h)) === 0) throw offline("Headset not connected to wireless receiver");
    await this._w(h, 0x00, 0x02, HEADSET, 0x01, 0x0d, 0x00, minutes === 0 ? 0x00 : 0x01);
    if (minutes > 0) {
      const ms = minutes * 60 * 1000;
      await this._w(h, 0x00, 0x02, HEADSET, 0x01, 0x0e, 0x00, ms & 0xff, (ms >> 8) & 0xff, (ms >> 16) & 0xff, (ms >>> 24) & 0xff);
    }
    return minutes;
  }
}
driver("Corsair Wireless V2 Headset Device", CorsairVoidV2W);

class CorsairVirtuosoXT extends Driver {
  async battery(h) {
    await h.write([0x02, 0x00]);
    let resp;
    for (let attempt = 0; ; ) {
      resp = await h.read(64, DEVICE_TIMEOUT);
      if (resp[0] === 0x01) break;
      if (++attempt >= 8) throw protocolError(`Unexpected report ID: 0x${resp[0].toString(16).padStart(2, "0")}`);
    }
    if (resp[1] === 0x00) throw offline("Headset not connected to receiver");
    if (resp[2] > 100) throw protocolError(`Battery percentage out of range: ${resp[2]}`);
    return battery(resp[2], BS.AVAILABLE);
  }
}
driver("Corsair Virtuoso XT/SE", CorsairVirtuosoXT);
