"""HyperX / HP headsets (ported from HeadsetControl)."""

from __future__ import annotations

from .base import Battery, BatteryStatus, Driver, Handle, Sidetone, driver, offline, protocol_error, sleep_ms

TIMEOUT_MS = 2000


def _req(*head: int, size: int) -> bytes:
    return bytes(head).ljust(size, b"\0")


@driver("HyperX Cloud Alpha Wireless")
class CloudAlphaWireless(Driver):
    def battery(self, h: Handle) -> Battery:
        h.write(_req(0x21, 0xBB, 0x03, size=31))
        if h.read(31, TIMEOUT_MS)[3] == 0x01:
            raise offline("Headset not connected")
        h.write(_req(0x21, 0xBB, 0x0C, size=31))
        if h.read(31, TIMEOUT_MS)[3] == 0x01:
            return Battery(-1, BatteryStatus.CHARGING)
        h.write(_req(0x21, 0xBB, 0x0B, size=31))
        return Battery(h.read(31, TIMEOUT_MS)[3], BatteryStatus.AVAILABLE)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        if level == 0:
            h.write(_req(0x21, 0xBB, 0x10, 0x00, size=31))
        else:
            h.write(_req(0x21, 0xBB, 0x10, 0x01, size=31))
            h.write(_req(0x21, 0xBB, 0x11, level, size=31))
        return Sidetone(level)

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        h.write(_req(0x21, 0xBB, 0x12, minutes, size=31))
        return minutes

    def set_voice_prompts(self, h: Handle, on: bool) -> None:
        h.write(_req(0x21, 0xBB, 0x13, 1 if on else 0, size=31))


@driver("HyperX Cloud Flight Wireless")
class CloudFlight(Driver):
    CHARGING_THRESHOLD = 0x100B

    @staticmethod
    def estimate(voltage: int) -> float:
        if voltage <= 3648:
            return _f32(_f32(0.00125) * voltage)  # float arithmetic in the original
        if voltage > 3975:
            return 100.0
        v = float(voltage)
        return (0.00000002547505 * v**4 - 0.0003900299 * v**3 + 2.238321 * v**2 - 5706.256 * v + 5452299.0)

    def battery(self, h: Handle) -> Battery:
        h.write(_req(0x21, 0xFF, 0x05, size=20))
        resp = h.read(20, TIMEOUT_MS)
        if len(resp) not in (0x0F, 0x14):
            raise protocol_error("Unexpected response length")
        voltage = (resp[3] << 8) | resp[4]
        if voltage > self.CHARGING_THRESHOLD:
            return Battery(-1, BatteryStatus.CHARGING)
        return Battery(int(_roundf(_f32(self.estimate(voltage)))), BatteryStatus.AVAILABLE)


def _f32(x: float) -> float:
    import struct

    return struct.unpack("f", struct.pack("f", x))[0]


def _roundf(x: float) -> float:
    """C roundf: half away from zero."""
    import math

    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


@driver("HyperX Cloud 3")
class Cloud3(Driver):
    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        h.send_feature(_req(0x20, 0x86, 1 if level > 0 else 0, size=62))
        return Sidetone(level)


@driver("HyperX Cloud II Wireless")
class Cloud2Wireless(Driver):
    def _command(self, h: Handle, command: int, value: int | None) -> bytes:
        head = (0x06, 0xFF, 0xBB, command, value, 0x00) if value is not None else (0x06, 0xFF, 0xBB, command, 0x00)
        h.write(_req(*head, size=52))
        sleep_ms(100)
        resp = h.read(20, 1000)
        if len(resp) != 20:
            raise protocol_error("Invalid response size")
        if resp[0] != 0x06 or resp[1] != 0xFF or resp[2] != 0xBB or resp[3] != command:
            raise protocol_error("Invalid response header")
        if value is not None and resp[4] != value:
            raise protocol_error("Response value does not match request")
        return resp

    def battery(self, h: Handle) -> Battery:
        level = self._command(h, 0x02, None)
        charging = self._command(h, 0x03, None)
        return Battery(level[7], BatteryStatus.CHARGING if charging[4] == 1 else BatteryStatus.AVAILABLE,
                       (level[5] << 8) | level[6])

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        return self._command(h, 0x22, minutes)[4]


@driver("HyperX Cloud II Wireless (Kingston)")
class Cloud2WirelessKingston(Driver):
    @staticmethod
    def _request(command: int, payload: int = 0) -> bytes:
        head = (0x06, 0x00, 0x02, 0x00, 0x9A, 0x00, 0x00, 0x68, 0x4A, 0x8E, 0x0A, 0x00, 0x00, 0x00, 0xBB,
                command, payload)
        return _req(*head, size=62)

    def _command(self, h: Handle, command: int, payload: int = 0) -> bytes:
        h.get_input(0x06, 64)  # prepareDevice: result deliberately ignored
        h.write(self._request(command, payload))
        sleep_ms(100)
        resp = h.read(64, 1000)
        if resp[0] != 0x0B or resp[2] != 0xBB or resp[3] != command:
            raise protocol_error("Invalid response header")
        return resp

    def battery(self, h: Handle) -> Battery:
        level = self._command(h, 0x02)
        charging = self._command(h, 0x03)
        return Battery(level[7], BatteryStatus.CHARGING if charging[4] == 1 else BatteryStatus.AVAILABLE)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        hw = 1 if level > 0 else 0
        h.get_input(0x06, 64)
        h.write(self._request(0x19, hw))
        return Sidetone(hw)

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        mins = 30 if minutes > 30 else minutes
        self._command(h, 0x18, mins)
        return mins
