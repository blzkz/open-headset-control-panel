"""Logitech headsets handled by fixed HID++ commands (ported from HeadsetControl).

The app talks to most Logitech wireless headsets through runtime HID++ feature
discovery (openlogi.hidpp); these ports keep exact parity with HeadsetControl for
models and protocols the discovery path does not cover.
"""

from __future__ import annotations

import math

from .base import (
    Battery,
    BatteryStatus,
    Chatmix,
    Driver,
    DriverError,
    FilterType,
    Handle,
    ParametricBand,
    Sidetone,
    driver,
    f32,
    fadd,
    fdiv,
    fmul,
    invalid,
    lround,
    map_int,
    offline,
    protocol_error,
    sleep_ms,
    timeout,
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


@driver("Logitech ASTRO A50 Gen 5")
class LogitechAstroA50(Driver):
    """ASTRO A50 (2024) base station protocol, report 0x02 / marker 0x0c."""

    CMD_BATTERY, CMD_SIDETONE, CMD_CHATMIX, CMD_EQ, CMD_BRIGHTNESS, CMD_NOISE_GATE = 0x06, 0x09, 0x0A, 0x0D, 0x0F, 0x14
    NOISE_LEVELS = (0x00, 0x01, 0x04)

    def _request(self, h: Handle, cmd: int, handle_byte: int, payload: bytes = b"", read_reply: bool = False) -> bytes:
        frame = bytearray(64)
        frame[0:6] = (0x02, 0x0C, (3 + len(payload)) & 0xFF, 0x00, cmd, handle_byte)
        frame[6:6 + len(payload)] = payload[:58]
        h.write(bytes(frame), 64)
        if not read_reply:
            return bytes(frame)
        for _ in range(8):
            resp = h.read(64, DEVICE_TIMEOUT)
            if not resp:
                continue
            resp = resp.ljust(64, b"\0")
            if resp[0] == 0x02 and resp[1] == 0x0C and resp[4] == cmd:
                return resp
        raise timeout("ASTRO A50: no response for command")

    def battery(self, h: Handle) -> Battery:
        f = self._request(h, self.CMD_BATTERY, 0x0C, read_reply=True)
        return Battery(f[6], BatteryStatus.CHARGING if f[8] else BatteryStatus.AVAILABLE)

    def chatmix(self, h: Handle) -> Chatmix:
        level = map_int(self._request(h, self.CMD_CHATMIX, 0x0C, read_reply=True)[6], 0, 12, 128, 0)
        game = 100 if level <= 64 else map_int(level, 64, 128, 100, 0)
        chat = 100 if level >= 64 else map_int(level, 0, 64, 0, 100)
        return Chatmix(level, game, chat)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        self._request(h, self.CMD_SIDETONE, 0x1C, bytes((0x01, 0xFF, u8(map_int(level, 0, 128, 0, 6)))))
        return Sidetone(level)

    def set_lights(self, h: Handle, on: bool) -> bool:
        self._request(h, self.CMD_BRIGHTNESS, 0x1C, bytes((100 if on else 0,)))
        return on

    def set_noise_filter(self, h: Handle, level: int) -> None:
        if level > 2:
            raise invalid("Noise filter level must be 0, 1, or 2")
        self._request(h, self.CMD_NOISE_GATE, 0x2D, bytes((self.NOISE_LEVELS[level],)))

    def set_parametric_eq(self, h: Handle, bands: list[ParametricBand]) -> None:
        if len(bands) != 10:
            raise invalid("ASTRO A50 requires exactly 10 parametric EQ bands")
        payload = bytearray(3 + 10 * 5)
        payload[0:3] = (0x01, 0x03, 0x00)
        for i, band in enumerate(bands):
            freq, gain, q = f32(band.frequency), f32(band.gain), f32(band.q)
            if band.type != FilterType.PEAKING:
                raise invalid("ASTRO A50 only supports peaking EQ bands")
            if freq < 20 or freq > 20000:
                raise invalid("Frequency must be between 20 Hz and 20000 Hz")
            if gain < -6.0 or gain > 6.0:
                raise invalid("Gain must be between -6 dB and +6 dB")
            if q < f32(0.031) or q > f32(7.969):
                raise invalid("Q factor must be between 0.031 and 7.969")
            f = lround(freq) & 0xFFFF
            qv = min(max(lround(fmul(q, 32.0)), 1), 0xFFFF)
            g = min(max(lround(fadd(120.0, fmul(gain, 20.0))), 0), 240)
            payload[3 + i * 5:8 + i * 5] = (f >> 8, f & 0xFF, qv & 0xFF, qv >> 8, g)
        self._request(h, self.CMD_EQ, 0x2C, bytes(payload))


@driver("Logitech ASTRO A50 Gen 4")
class LogitechAstroA50Gen4(Driver):
    """ASTRO A50 Gen 4 base station (vendor 0x9886), report 0x02."""

    STATUS_ACCEPTED, STATUS_ERROR, STATUS_OK = 0x00, 0x01, 0x02
    CMD_HEADSET_STATUS, CMD_SET_SLIDER, CMD_SET_EQ_GAIN, CMD_SET_NOISE_GATE = 0x54, 0x62, 0x63, 0x64
    CMD_SET_EQ_PRESET, CMD_GET_SLIDER, CMD_GET_EQ_PRESET, CMD_SET_EQ_BAND = 0x67, 0x68, 0x6C, 0x6F
    CMD_GET_BALANCE, CMD_GET_BATTERY = 0x72, 0x7C
    SLIDER_MIC, SLIDER_SIDETONE, SLIDER_MAX = 0x04, 0x05, 100
    NOISE_LEVELS = (0x00, 0x01, 0x03)
    PRESET_COUNT, EQ_BANDS = 3, 5
    Q_MIN, Q_MAX = fdiv(4096.0, 12288.0), fdiv(4096.0, 409.0)

    def __init__(self, product_id: int = 0) -> None:
        super().__init__(product_id)
        self._pending = False  # a reply may still be queued after a timeout

    def _request(self, h: Handle, cmd: int, payload: bytes = b"", expect_payload: bool = False) -> bytes:
        if self._pending:
            try:
                h.read(64, 1000)
            except DriverError:
                pass
            self._pending = False
        frame = bytearray(64)
        frame[0:2] = (0x02, cmd)
        if payload:
            frame[2] = len(payload)
            frame[3:3 + len(payload)] = payload[:61]
        h.write(bytes(frame), 64)
        try:
            reply = h.read(64, DEVICE_TIMEOUT)
        except DriverError:
            self._pending = True
            raise
        if not reply:
            self._pending = True
            raise timeout("ASTRO A50 Gen 4: no reply from base station")
        if len(reply) != 64:
            raise protocol_error("ASTRO A50 Gen 4: incomplete reply from base station")
        if reply[0] != 0x02:
            raise protocol_error("ASTRO A50 Gen 4: reply does not start with report ID 0x02")
        if reply[1] == self.STATUS_OK:
            return reply
        if reply[1] == self.STATUS_ACCEPTED:
            if expect_payload:
                raise protocol_error("ASTRO A50 Gen 4: base station returned no data")
            return reply
        if reply[1] == self.STATUS_ERROR:
            raise protocol_error(f"ASTRO A50 Gen 4: error (code 0x{reply[3]:02x})")
        raise protocol_error("ASTRO A50 Gen 4: unknown reply status")

    def _linked(self, h: Handle) -> bool:
        return bool(self._request(h, self.CMD_HEADSET_STATUS, expect_payload=True)[3] & 0x02)

    def battery(self, h: Handle) -> Battery:
        if not self._linked(h):
            return Battery(-1, BatteryStatus.UNAVAILABLE)
        raw = self._request(h, self.CMD_GET_BATTERY, expect_payload=True)[3]
        return Battery(raw & 0x7F, BatteryStatus.CHARGING if raw & 0x80 else BatteryStatus.AVAILABLE)

    def chatmix(self, h: Handle) -> Chatmix:
        if not self._linked(h):
            raise offline("ASTRO A50 Gen 4: headset is not linked to the base station")
        raw = self._request(h, self.CMD_GET_BALANCE, expect_payload=True)[3]
        level = 64 if 125 <= raw <= 130 else map_int(raw, 0, 255, 128, 0)
        game = 100 if level <= 64 else map_int(level, 64, 128, 100, 0)
        chat = 100 if level >= 64 else map_int(level, 0, 64, 0, 100)
        return Chatmix(level, game, chat)

    def _set_slider(self, h: Handle, slider: int, value: int) -> None:
        reply = self._request(h, self.CMD_SET_SLIDER, bytes((slider, value)))
        if reply[1] == self.STATUS_OK and reply[4] != slider:
            raise protocol_error("ASTRO A50 Gen 4: slider write was not echoed back")

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        device_level = u8(map_int(level, 0, 128, 0, self.SLIDER_MAX))
        self._set_slider(h, self.SLIDER_SIDETONE, device_level)
        return Sidetone(level, device_level)

    def get_sidetone(self, h: Handle) -> Sidetone:
        reply = self._request(h, self.CMD_GET_SLIDER, bytes((self.SLIDER_SIDETONE,)), expect_payload=True)
        if reply[3] != self.CMD_GET_SLIDER or reply[4] != self.SLIDER_SIDETONE:
            raise protocol_error("ASTRO A50 Gen 4: unexpected slider reply")
        return Sidetone(u8(map_int(reply[5], 0, self.SLIDER_MAX, 0, 128)), reply[5])

    def set_mic_volume(self, h: Handle, volume: int) -> None:
        self._set_slider(h, self.SLIDER_MIC, u8(map_int(volume, 0, 128, 0, self.SLIDER_MAX)))

    def set_noise_filter(self, h: Handle, level: int) -> None:
        if level >= len(self.NOISE_LEVELS):
            raise invalid("Noise filter level must be 0, 1, or 2")
        reply = self._request(h, self.CMD_SET_NOISE_GATE, bytes((self.NOISE_LEVELS[level],)))
        if reply[1] == self.STATUS_OK and reply[3] != self.NOISE_LEVELS[level]:
            raise protocol_error("ASTRO A50 Gen 4: noise gate mode was not echoed back")

    def _active_preset(self, h: Handle) -> int:
        preset = self._request(h, self.CMD_GET_EQ_PRESET, expect_payload=True)[3]
        if preset < 1 or preset > self.PRESET_COUNT:
            raise protocol_error("ASTRO A50 Gen 4: active preset out of range")
        return preset

    def set_eq_preset(self, h: Handle, preset: int) -> None:
        if preset >= self.PRESET_COUNT:
            raise invalid("ASTRO A50 Gen 4 has presets 0, 1 and 2")
        device_preset = preset + 1
        reply = self._request(h, self.CMD_SET_EQ_PRESET, bytes((device_preset,)))
        if reply[1] == self.STATUS_OK and reply[4] != device_preset:
            raise protocol_error("ASTRO A50 Gen 4: preset write was not echoed back")
        for _ in range(8):
            sleep_ms(200)
            if self._active_preset(h) == device_preset:
                return
        raise protocol_error("ASTRO A50 Gen 4: preset change was not confirmed by the base station")

    @staticmethod
    def _valid_gain(gain: float) -> bool:
        return math.isfinite(gain) and -7.0 <= gain <= 7.0

    def _set_gains(self, h: Handle, preset: int, gains: list[float]) -> None:
        payload = bytes((preset, *(u8(lround(g) + 12) for g in gains)))
        reply = self._request(h, self.CMD_SET_EQ_GAIN, payload)
        if reply[1] == self.STATUS_OK and (reply[3] != self.CMD_SET_EQ_GAIN or reply[4] != preset):
            raise protocol_error("ASTRO A50 Gen 4: EQ gain write was not echoed back")

    def set_equalizer(self, h: Handle, gains: list[float]) -> None:
        if len(gains) != self.EQ_BANDS:
            raise invalid("ASTRO A50 Gen 4 requires exactly 5 equalizer gains")
        gains = [f32(g) for g in gains]
        if not all(self._valid_gain(g) for g in gains):
            raise invalid("Gain must be finite and between -7 dB and +7 dB")
        self._set_gains(h, self._active_preset(h), gains)

    def set_parametric_eq(self, h: Handle, bands: list[ParametricBand]) -> None:
        if len(bands) != self.EQ_BANDS:
            raise invalid("ASTRO A50 Gen 4 requires exactly 5 parametric EQ bands")
        expected = (FilterType.LOW_SHELF, FilterType.PEAKING, FilterType.PEAKING, FilterType.PEAKING,
                    FilterType.HIGH_SHELF)
        for band, want in zip(bands, expected, strict=True):
            freq, q = f32(band.frequency), f32(band.q)
            if band.type != want:
                raise invalid("ASTRO A50 Gen 4 bands must be lowshelf, peaking, peaking, peaking, highshelf")
            if not math.isfinite(freq) or freq < 80 or freq > 15000:
                raise invalid("Frequency must be between 80 Hz and 15000 Hz")
            if not self._valid_gain(f32(band.gain)):
                raise invalid("Gain must be between -7 dB and +7 dB")
            if band.type == FilterType.PEAKING and (not math.isfinite(q) or q < self.Q_MIN or q > self.Q_MAX):
                raise invalid("Q factor out of range")
        preset = self._active_preset(h)
        for i, band in enumerate(bands):
            bw = min(max(lround(fdiv(4096.0, band.q)), 409), 12288) if band.type == FilterType.PEAKING else 0
            freq = lround(f32(band.frequency)) & 0xFFFF
            reply = self._request(h, self.CMD_SET_EQ_BAND,
                                  bytes((preset, i + 1, bw & 0xFF, bw >> 8, freq & 0xFF, freq >> 8)))
            if reply[1] == self.STATUS_OK and reply[3] != self.CMD_SET_EQ_BAND:
                raise protocol_error("ASTRO A50 Gen 4: EQ band write was not echoed back")
        self._set_gains(h, preset, [f32(b.gain) for b in bands])
