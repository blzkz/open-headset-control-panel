// HyperX / HP headsets (port of openheadset/drivers/hyperx.py).
import { BatteryStatus as BS, Driver, battery, driver, f32, offline, pad, protocolError, roundf, sidetone, sleepMs } from "./base.js";

const TIMEOUT_MS = 2000;
const req = (head, size) => pad(head, size);

class CloudAlphaWireless extends Driver {
  async battery(h) {
    await h.write(req([0x21, 0xbb, 0x03], 31));
    if ((await h.read(31, TIMEOUT_MS))[3] === 0x01) throw offline("Headset not connected");
    await h.write(req([0x21, 0xbb, 0x0c], 31));
    if ((await h.read(31, TIMEOUT_MS))[3] === 0x01) return battery(-1, BS.CHARGING);
    await h.write(req([0x21, 0xbb, 0x0b], 31));
    return battery((await h.read(31, TIMEOUT_MS))[3], BS.AVAILABLE);
  }

  async setSidetone(h, level) {
    if (level === 0) await h.write(req([0x21, 0xbb, 0x10, 0x00], 31));
    else {
      await h.write(req([0x21, 0xbb, 0x10, 0x01], 31));
      await h.write(req([0x21, 0xbb, 0x11, level], 31));
    }
    return sidetone(level);
  }

  async setInactiveTime(h, minutes) {
    await h.write(req([0x21, 0xbb, 0x12, minutes], 31));
    return minutes;
  }

  async setVoicePrompts(h, on) {
    await h.write(req([0x21, 0xbb, 0x13, on ? 1 : 0], 31));
  }
}
driver("HyperX Cloud Alpha Wireless", CloudAlphaWireless);

class CloudFlight extends Driver {
  static CHARGING_THRESHOLD = 0x100b;

  static estimate(voltage) {
    if (voltage <= 3648) return f32(f32(0.00125) * voltage); // float arithmetic in the original
    if (voltage > 3975) return 100.0;
    const v = voltage;
    return 0.00000002547505 * v ** 4 - 0.0003900299 * v ** 3 + 2.238321 * v ** 2 - 5706.256 * v + 5452299.0;
  }

  async battery(h) {
    await h.write(req([0x21, 0xff, 0x05], 20));
    const resp = await h.read(20, TIMEOUT_MS);
    if (resp.length !== 0x0f && resp.length !== 0x14) throw protocolError("Unexpected response length");
    const voltage = (resp[3] << 8) | resp[4];
    if (voltage > CloudFlight.CHARGING_THRESHOLD) return battery(-1, BS.CHARGING);
    return battery(Math.trunc(roundf(f32(CloudFlight.estimate(voltage)))), BS.AVAILABLE);
  }
}
driver("HyperX Cloud Flight Wireless", CloudFlight);

class Cloud3 extends Driver {
  async setSidetone(h, level) {
    await h.sendFeature(req([0x20, 0x86, level > 0 ? 1 : 0], 62));
    return sidetone(level);
  }
}
driver("HyperX Cloud 3", Cloud3, { wireless: false });

class Cloud2Wireless extends Driver {
  async _command(h, command, value) {
    const head = value !== null ? [0x06, 0xff, 0xbb, command, value, 0x00] : [0x06, 0xff, 0xbb, command, 0x00];
    await h.write(req(head, 52));
    await sleepMs(100);
    const resp = await h.read(20, 1000);
    if (resp.length !== 20) throw protocolError("Invalid response size");
    if (resp[0] !== 0x06 || resp[1] !== 0xff || resp[2] !== 0xbb || resp[3] !== command) throw protocolError("Invalid response header");
    if (value !== null && resp[4] !== value) throw protocolError("Response value does not match request");
    return resp;
  }

  async battery(h) {
    const level = await this._command(h, 0x02, null);
    const charging = await this._command(h, 0x03, null);
    return battery(level[7], charging[4] === 1 ? BS.CHARGING : BS.AVAILABLE, (level[5] << 8) | level[6]);
  }

  async setInactiveTime(h, minutes) {
    return (await this._command(h, 0x22, minutes))[4];
  }
}
driver("HyperX Cloud II Wireless", Cloud2Wireless);

class Cloud2WirelessKingston extends Driver {
  static _request(command, payload = 0) {
    return req([0x06, 0x00, 0x02, 0x00, 0x9a, 0x00, 0x00, 0x68, 0x4a, 0x8e, 0x0a, 0x00, 0x00, 0x00, 0xbb, command, payload], 62);
  }

  async _command(h, command, payload = 0) {
    await h.getInput(0x06, 64); // prepareDevice: result deliberately ignored
    await h.write(Cloud2WirelessKingston._request(command, payload));
    await sleepMs(100);
    const resp = await h.read(64, 1000);
    if (resp[0] !== 0x0b || resp[2] !== 0xbb || resp[3] !== command) throw protocolError("Invalid response header");
    return resp;
  }

  async battery(h) {
    const level = await this._command(h, 0x02);
    const charging = await this._command(h, 0x03);
    return battery(level[7], charging[4] === 1 ? BS.CHARGING : BS.AVAILABLE);
  }

  async setSidetone(h, level) {
    const hw = level > 0 ? 1 : 0;
    await h.getInput(0x06, 64);
    await h.write(Cloud2WirelessKingston._request(0x19, hw));
    return sidetone(hw);
  }

  async setInactiveTime(h, minutes) {
    const mins = minutes > 30 ? 30 : minutes;
    await this._command(h, 0x18, mins);
    return mins;
  }
}
driver("HyperX Cloud II Wireless (Kingston)", Cloud2WirelessKingston);
