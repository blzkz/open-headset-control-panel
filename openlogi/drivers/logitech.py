"""Logitech headsets handled by fixed HID++ commands (ported from HeadsetControl).

The app talks to most Logitech wireless headsets through runtime HID++ feature
discovery (openlogi.hidpp); these ports keep exact parity with HeadsetControl for
models and protocols the discovery path does not cover.
"""

from __future__ import annotations

from .base import (
    Battery,
    BatteryStatus,
    Driver,
    Handle,
    Sidetone,
    driver,
    f32,
    fdiv,
    fmul,
    map_int,
    offline,
    protocol_error,
    sleep_ms,
    u8,
)

DEVICE_TIMEOUT = 5000
HIDPP_LONG, HIDPP_LONG_LEN, HIDPP_RECEIVER = 0x11, 20, 0xFF


def spline_battery_level(percentages: tuple[int, ...], voltages: tuple[int, ...], voltage: int) -> int:
    """HeadsetControl's piecewise-linear interpolation, float32 like the original."""
    size = min(len(percentages), len(voltages))
    for i in range(size):
        if voltage >= voltages[i]:
            return percentages[i]
        if i < size - 1 and voltage >= voltages[i + 1]:
            diff = voltages[i] - voltages[i + 1]
            if diff == 0:
                return percentages[i + 1]
            fraction = fdiv(f32(voltage - voltages[i + 1]), f32(diff))
            return percentages[i + 1] + int(fmul(fraction, f32(percentages[i] - percentages[i + 1])))
    return 0


CAL_G533 = ((100, 50, 30, 20, 5, 0), (4200, 3850, 3790, 3750, 3680, 3330))
CAL_G535 = ((100, 50, 30, 20, 5, 0), (4175, 3817, 3766, 3730, 3664, 3310))
CAL_G633 = ((100, 80, 60, 40, 20, 10, 5, 0), (4100, 3950, 3850, 3750, 3650, 3500, 3300, 3150))
CAL_GPRO = ((100, 50, 30, 20, 5, 0), (4150, 3830, 3780, 3740, 3670, 3320))


class _HidppFixed(Driver):
    """Fixed-index HID++ long messages to the receiver (HIDPPDevice in HeadsetControl)."""

    CALIBRATION = CAL_GPRO

    @staticmethod
    def _long(*command: int) -> bytes:
        return bytes((HIDPP_LONG, HIDPP_RECEIVER, *command[:HIDPP_LONG_LEN - 2])).ljust(HIDPP_LONG_LEN, b"\0")

    def _command(self, h: Handle, *command: int, response_length: int = 7) -> bytes:
        h.write(self._long(*command))
        resp = h.read(response_length, DEVICE_TIMEOUT)
        if len(resp) > 2 and resp[2] == 0xFF:
            raise offline("Headset not connected or turned off")
        return resp

    def _feature(self, h: Handle, *command: int) -> None:
        h.write(self._long(*command))

    def _battery(self, h: Handle, *command: int) -> Battery:
        resp = self._command(h, *command)
        status = BatteryStatus.CHARGING if resp[6] == 0x03 else BatteryStatus.AVAILABLE
        voltage = (resp[4] << 8) | resp[5]
        percentages, voltages = self.CALIBRATION
        if voltage < voltages[-1]:
            return Battery(-1, BatteryStatus.UNAVAILABLE, voltage)
        return Battery(spline_battery_level(percentages, voltages, voltage), status, voltage)

    def _inactive_time(self, h: Handle, minutes: int) -> int:
        self._command(h, 0x07, 0x21, minutes)
        return minutes


@driver("Logitech G533")
class LogitechG533(_HidppFixed):
    CALIBRATION = CAL_G533

    def battery(self, h: Handle) -> Battery:
        return self._battery(h, 0x07, 0x01)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        self._feature(h, 0x04, 0x0E, 0xFF, 0x05, 0x01, 0x04, 0x00, u8(map_int(level, 0, 128, 200, 255)))
        return Sidetone(level)

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        return self._inactive_time(h, minutes)


@driver("Logitech G535")
class LogitechG535(_HidppFixed):
    CALIBRATION = CAL_G535

    def battery(self, h: Handle) -> Battery:
        return self._battery(h, 0x05, 0x0D)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        mapped = u8(map_int(level, 0, 128, 0, 100))
        resp = self._command(h, 0x04, 0x1D, mapped, response_length=HIDPP_LONG_LEN)
        if resp[4] != mapped:
            raise protocol_error(f"Sidetone validation failed: expected {mapped}, got {resp[4]}")
        return Sidetone(level)

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        validated = next((v for v in (0, 1, 2, 5, 10, 15) if minutes <= v), 30)
        resp = self._command(h, 0x05, 0x2D, validated, response_length=HIDPP_LONG_LEN)
        if resp[4] != validated:
            raise protocol_error("Inactive time validation failed")
        return validated


@driver("Logitech G633/G635/G733/G933/G935")
class LogitechG633Family(_HidppFixed):
    CALIBRATION = CAL_G633

    def battery(self, h: Handle) -> Battery:
        return self._battery(h, 0x08, 0x0A)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        clamped = min(level, 100)
        self._feature(h, 0x07, 0x1A, clamped)
        return Sidetone(clamped)

    def set_lights(self, h: Handle, on: bool) -> bool:
        mode = 0x02 if on else 0x00  # breathing / off
        tail = (0x00, 0xB6, 0xFF, 0x0F, 0xA0, 0x00, 0x64, 0x00, 0x00, 0x00)
        self._feature(h, 0x04, 0x3C, 0x01, mode, *tail)  # strips
        sleep_ms(1)
        self._feature(h, 0x04, 0x3C, 0x00, mode, *tail)  # logo
        return on


@driver("Logitech G431/G432/G433")
class LogitechG432(_HidppFixed):
    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        self._feature(h, 0x05, 0x1E, u8(map_int(level, 0, 128, 0, 100)))
        return Sidetone(level)


@driver("Logitech G PRO Series")
class LogitechGPro(_HidppFixed):
    CALIBRATION = CAL_GPRO

    def battery(self, h: Handle) -> Battery:
        return self._battery(h, 0x06, 0x0D)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        self._feature(h, 0x05, 0x1A, u8(map_int(level, 0, 128, 0, 100)))
        return Sidetone(level)

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        return self._inactive_time(h, minutes)


@driver("Logitech G930")
class LogitechG930(Driver):
    def battery(self, h: Handle) -> Battery:
        h.send_feature(bytes((0xFF, 0x09, 0x00, 0xFD, 0xF4, 0x10, 0x05, 0xB1, 0xBF, 0xA0, 0x04)).ljust(64, b"\0"))
        buf = b""
        for i in range(3):
            buf = h.get_feature(0xFF, 64)
            if i < 2:
                sleep_ms(100)
        return Battery(map_int(buf[13], 44, 91, 0, 100), BatteryStatus.AVAILABLE)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        h.send_feature(bytes((0xFF, 0x0A, 0x00, 0xFF, 0xF4, 0x10, 0x05, 0xDA, 0x8F, 0xF2, 0x01, level)).ljust(64, b"\0"))
        return Sidetone(level)


@driver("Logitech Zone Wired/Zone 750")
class LogitechZoneWired(_HidppFixed):
    def _set(self, h: Handle, a: int, b: int, value: int) -> None:
        h.send_feature(bytes((0x22, 0xF1, 0x04, 0x00, a, b, value)), 20)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        self._set(h, 0x04, 0x3D, u8(map_int(level, 0, 128, 0, 10)))
        return Sidetone(level)

    def set_voice_prompts(self, h: Handle, on: bool) -> None:
        self._set(h, 0x05, 0x3D, 1 if on else 0)

    def set_rotate_to_mute(self, h: Handle, on: bool) -> None:
        self._set(h, 0x05, 0x6D, 1 if on else 0)
