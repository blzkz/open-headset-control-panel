"""SteelSeries Arctis headsets (ported from HeadsetControl)."""

from __future__ import annotations

import math

from .base import (
    Battery,
    BatteryStatus,
    Cap,
    Chatmix,
    Driver,
    DriverError,
    ErrorCode,
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
    map_int,
    offline,
    protocol_error,
    sidetone_to_discrete,
    timeout,
    u8,
)

DEVICE_TIMEOUT = 5000  # hsc_device_timeout
PACKET_31 = 31


def _pad(data, size: int) -> bytes:
    return bytes(data)[:size].ljust(size, b"\0")


class Legacy(Driver):
    """SteelSeriesLegacyDevice: 31-byte commands followed by a "save state" packet."""

    SAVE = (0x06, 0x09)

    def save_state(self, h: Handle) -> None:
        h.write(_pad(self.SAVE, PACKET_31))

    def send_with_save(self, h: Handle, command, save_after: bool = True) -> None:
        h.write(_pad(command, PACKET_31))
        if save_after:
            self.save_state(h)

    def battery_direct(self, h: Handle, request, response_size: int, battery_byte: int, status_byte: int,
                       offline_value: int, charging_value: int, bmin: int, bmax: int) -> Battery:
        h.write(bytes(request))
        resp = h.read(response_size, DEVICE_TIMEOUT)
        if 0 <= status_byte < len(resp) and resp[status_byte] == offline_value:
            raise offline("Headset not connected or turned off")
        status = BatteryStatus.AVAILABLE
        if 0 <= status_byte < len(resp) and resp[status_byte] == charging_value:
            status = BatteryStatus.CHARGING
        if not 0 <= battery_byte < len(resp):
            raise protocol_error("Battery byte index out of bounds")
        level = max(0, min(100, map_int(resp[battery_byte], bmin, bmax, 0, 100)))
        return Battery(level, status)


@driver("SteelSeries Arctis (1/7X/7P) Wireless")
class Arctis1(Legacy):
    def battery(self, h: Handle) -> Battery:
        return self.battery_direct(h, (0x06, 0x12), 8, 3, 2, 0x01, 0xFF, 0, 100)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        mapped = u8(map_int(level, 0, 128, 0x00, 0x12))
        self.send_with_save(h, (0x06, 0x35, 0x01, 0x00, mapped) if level > 0 else (0x06, 0x35))
        return Sidetone(level)

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        minutes = min(minutes, 90)
        self.send_with_save(h, (0x06, 0x53, minutes))
        return minutes


@driver("SteelSeries Arctis (7/Pro)")
class Arctis7(Legacy):
    def battery(self, h: Handle) -> Battery:
        return self.battery_direct(h, (0x06, 0x18), 8, 2, -1, 0, 0, 0, 100)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        mapped = u8(map_int(level, 0, 128, 0x00, 0x12))
        self.send_with_save(h, (0x06, 0x35, 0x01, 0x00, mapped) if level > 0 else (0x06, 0x35))
        return Sidetone(level)

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        minutes = min(minutes, 90)
        self.send_with_save(h, (0x06, 0x51, minutes))
        return minutes

    def set_lights(self, h: Handle, on: bool) -> bool:
        self.send_with_save(h, _pad((0x06, 0x55, 0x01, 0x02 if on else 0x00), 8))
        return on

    def chatmix(self, h: Handle) -> Chatmix:
        h.write(bytes((0x06, 0x24)))
        resp = h.read(8, DEVICE_TIMEOUT)
        game, chat = resp[2], resp[3]
        if game == 0 and chat == 0:
            level = 64
        elif game == 0:
            level = 64 - (255 - chat)
        else:
            level = 64 + (255 - game)
        game_pct = 100 if game == 0 else map_int(game, 191, 255, 0, 100)
        chat_pct = 100 if chat == 0 else map_int(chat, 191, 255, 0, 100)
        return Chatmix(level, game_pct, chat_pct)


@driver("SteelSeries Arctis 9")
class Arctis9(Legacy):
    SAVE = (0x90, 0x00)

    def battery(self, h: Handle) -> Battery:
        return self.battery_direct(h, (0x00, 0x20), 12, 3, 4, 0xFF, 0x01, 0x64, 0x9A)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        if level > 0:
            num = u8(map_int(int(math.log2(level) * 100), 0, 700, 0, 128))
            num = u8(map_int(num, 0, 128, 0xC0, 0xFD))
        else:
            num = 0xC0
        self.send_with_save(h, _pad((0x06, 0x00, num), 5))
        return Sidetone(level)

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        t = minutes * 60
        self.send_with_save(h, (0x04, 0x00, u8(t >> 8), u8(t)))
        return minutes

    def chatmix(self, h: Handle) -> Chatmix:
        h.write(bytes((0x00, 0x20)))
        resp = h.read(12, DEVICE_TIMEOUT)
        game = map_int(resp[9], 0, 19, 0, 64)
        chat = map_int(resp[10], 0, 19, 0, -64)
        return Chatmix(64 - (chat + game), map_int(resp[9], 0, 19, 0, 100), map_int(resp[10], 0, 19, 0, 100))


@driver("SteelSeries Arctis Pro Wireless")
class ArctisProWireless(Legacy):
    SAVE = (0x90, 0xAA)

    def battery(self, h: Handle) -> Battery:
        h.write(_pad((0x41, 0xAA), PACKET_31))
        if h.read(2, DEVICE_TIMEOUT)[0] == 0x02:
            raise offline("Headset not connected")
        h.write(_pad((0x40, 0xAA), PACKET_31))
        raw = h.read(1, DEVICE_TIMEOUT)[0]
        return Battery(map_int(raw, 0x00, 0x04, 0, 100), BatteryStatus.AVAILABLE)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        self.send_with_save(h, (0x39, 0xAA, u8(map_int(level, 0, 128, 0x00, 0x09))))
        return Sidetone(level)

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        self.send_with_save(h, (0x3C, 0xAA, u8(minutes // 10)))
        return minutes


# -- Nova family (64-byte reports) -----------------------------------------------------------------
MSG_SIZE = 64


class Nova(Driver):
    def read_status(self, h: Handle) -> bytes:
        h.write(bytes((0x00, 0xB0)))
        return h.read(128, DEVICE_TIMEOUT)

    def send_command(self, h: Handle, command) -> None:
        h.write(_pad(command, MSG_SIZE))


def _sidetone_4(level: int) -> int:
    return 0 if level < 26 else 1 if level < 51 else 2 if level < 76 else 3


@driver("SteelSeries Arctis Nova 3")
class ArctisNova3(Nova):
    PRESETS = (
        (0x14, 0x14, 0x14, 0x14, 0x14, 0x14),
        (0x1C, 0x19, 0x11, 0x14, 0x14, 0x14),
        (0x1A, 0x17, 0x0F, 0x12, 0x17, 0x1A),
        (0x0C, 0x0D, 0x11, 0x18, 0x1C, 0x14),
    )

    def _feature_and_save(self, h: Handle, cmd) -> None:
        h.send_feature(bytes(cmd), MSG_SIZE)
        h.send_feature(bytes((0x06, 0x09)), MSG_SIZE)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        self._feature_and_save(h, (0x06, 0x39, _sidetone_4(level)))
        return Sidetone(level)

    def set_mic_volume(self, h: Handle, volume: int) -> None:
        self._feature_and_save(h, (0x06, 0x37, sidetone_to_discrete(volume, 11)))

    def set_mic_mute_led(self, h: Handle, brightness: int) -> None:
        if brightness > 3:
            raise invalid("Brightness must be 0-3")
        self._feature_and_save(h, (0x06, 0xAE, brightness))

    def set_eq_preset(self, h: Handle, preset: int) -> None:
        if not 0 <= preset < len(self.PRESETS):
            raise invalid("Device only supports presets 0-3")
        self._feature_and_save(h, _pad((0x06, 0x33, *self.PRESETS[preset]), MSG_SIZE))

    def set_equalizer(self, h: Handle, bands: list[float]) -> None:
        if len(bands) != 6:
            raise invalid("Device requires exactly 6 equalizer bands")
        cmd = bytearray(MSG_SIZE)
        cmd[0], cmd[1] = 0x06, 0x33
        for i, v in enumerate(bands):
            if v < -6.0 or v > 6.0:
                raise invalid("Band values must be between -6 and +6")
            cmd[i + 2] = u8(fadd(0x14, fmul(2, v)))
        h.send_feature(bytes(cmd), MSG_SIZE)


@driver("SteelSeries Arctis 7+")
class Arctis7Plus(Nova):
    PRESETS = (
        (0x0, 0x33, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x0),
        (0x0, 0x33, 0x1F, 0x20, 0x1A, 0x15, 0x15, 0x16, 0x16, 0x16, 0x16, 0x23, 0x0),
        (0x0, 0x33, 0x1E, 0x1B, 0x15, 0x10, 0x10, 0x13, 0x1B, 0x1E, 0x20, 0x1F, 0x0),
        (0x0, 0x33, 0x0E, 0x16, 0x11, 0x13, 0x20, 0x24, 0x1F, 0x11, 0x18, 0x11, 0x0),
    )

    def _save(self, h: Handle) -> None:
        self.send_command(h, (0x00, 0x09))

    def battery(self, h: Handle) -> Battery:
        data = self.read_status(h)
        if len(data) < 4:
            raise protocol_error("Response too short")
        if data[1] == 0x01:
            raise offline("Headset not connected")
        status = BatteryStatus.CHARGING if data[3] == 0x01 else BatteryStatus.AVAILABLE
        return Battery(min(100, map_int(data[2], 0, 4, 0, 100)), status)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        self.send_command(h, (0x00, 0x39, _sidetone_4(level)))
        self._save(h)
        return Sidetone(level)

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        minutes = min(minutes, 90)
        self.send_command(h, (0x00, 0xA3, minutes))
        self._save(h)
        return minutes

    def chatmix(self, h: Handle) -> Chatmix:
        data = self.read_status(h)
        if len(data) < 6:
            raise protocol_error("Response too short for chatmix")
        game_raw, chat_raw = data[4], data[5]
        level = 64 - (map_int(chat_raw, 0, 0x64, 0, -64) + map_int(game_raw, 0, 0x64, 0, 64))
        return Chatmix(level, map_int(game_raw, 0, 0x64, 0, 100), map_int(chat_raw, 0, 0x64, 0, 100))

    def set_eq_preset(self, h: Handle, preset: int) -> None:
        if not 0 <= preset < len(self.PRESETS):
            raise invalid("Device only supports presets 0-3")
        self.send_command(h, self.PRESETS[preset])
        self._save(h)

    def set_equalizer(self, h: Handle, bands: list[float]) -> None:
        if len(bands) != 10:
            raise invalid("Device requires exactly 10 equalizer bands")
        cmd = bytearray(MSG_SIZE)
        cmd[1] = 0x33
        for i, v in enumerate(bands):
            if v < -12.0 or v > 12.0:
                raise invalid("Band values must be between -12 and +12")
            cmd[i + 2] = u8(fadd(0x18, fmul(2, v)))
        cmd[len(bands) + 2] = 0x0
        self.send_command(h, cmd)
        self._save(h)


# Nova 5/5X filter codes on the wire
_NOVA5_FILTER = {FilterType.PEAKING: 1, FilterType.LOW_PASS: 2, FilterType.HIGH_PASS: 3,
                 FilterType.LOW_SHELF: 4, FilterType.HIGH_SHELF: 5}


@driver("SteelSeries Arctis Nova (5/5X)")
class ArctisNova5(Nova):
    FREQS = (32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
    PRESET_VALUES = (
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        (3.5, 5.5, 4, 1, -1.5, -1.5, -1, -1, -1, -1),
        (-5, -3.5, -1, -3.5, -2.5, 4, 6, -3.5, 0, 0),
        (3, 3.5, 1.5, -1.5, -4, -4, -2.5, 1.5, 3, 4),
    )
    GAIN_BASE = 20
    FREQ_DISABLED = 20001

    def _save(self, h: Handle) -> None:
        h.write(bytes((0x0, 0x09)), MSG_SIZE)
        h.write(bytes((0x0, 0x35, 0x01)), MSG_SIZE)

    def battery(self, h: Handle) -> Battery:
        data = self.read_status(h)
        if len(data) < 16:
            raise protocol_error("Response too short")
        if data[1] == 0x02:
            raise offline("Headset not connected")
        status = BatteryStatus.CHARGING if data[4] == 0x01 else BatteryStatus.AVAILABLE
        return Battery(data[3], status)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        h.write(bytes((0x0, 0x39, sidetone_to_discrete(level, 11))), MSG_SIZE)
        self._save(h)
        return Sidetone(level)

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        h.write(bytes((0x0, 0xA3, minutes)), MSG_SIZE)
        return minutes

    def set_mic_volume(self, h: Handle, volume: int) -> None:
        mapped = volume // 8
        h.write(bytes((0x0, 0x37, 15 if mapped == 16 else mapped)), MSG_SIZE)
        self._save(h)

    def set_mic_mute_led(self, h: Handle, brightness: int) -> None:
        mapped = {2: 0x04, 3: 0x0A}.get(brightness, brightness)
        h.write(bytes((0x0, 0xAE, mapped)), MSG_SIZE)
        self._save(h)

    def set_volume_limiter(self, h: Handle, on: bool) -> None:
        h.write(bytes((0x0, 0x27, 1 if on else 0)), MSG_SIZE)
        self._save(h)

    def chatmix(self, h: Handle) -> Chatmix:
        data = self.read_status(h)
        if len(data) < 7:
            raise protocol_error("Response too short for chatmix")
        game_raw, chat_raw = data[5], data[6]
        level = 64 - (map_int(chat_raw, 0, 100, 0, -64) + map_int(game_raw, 0, 100, 0, 64))
        return Chatmix(level, game_raw, chat_raw)

    def set_eq_preset(self, h: Handle, preset: int) -> None:
        if not 0 <= preset < len(self.PRESET_VALUES):
            raise invalid("Device only supports presets 0-3")
        try:
            self.set_equalizer(h, list(self.PRESET_VALUES[preset]))
        except DriverError:
            pass  # the original ignores this result

    def set_equalizer(self, h: Handle, bands: list[float]) -> None:
        if len(bands) != 10:
            raise invalid("Device requires exactly 10 equalizer bands")
        data = bytearray(MSG_SIZE)
        data[1] = 0x33
        for i, gain in enumerate(bands):
            if gain < -10.0 or gain > 10.0:
                raise invalid("Gain values must be between -10 and +10")
            raw = u8(fadd(self.GAIN_BASE, fmul(gain, 2)))
            flag = 0x01
            if raw != self.GAIN_BASE:
                flag = 0x04 if i == 0 else 0x05 if i == 9 else 0x01
            f = self.FREQS[i]
            data[2 + 6 * i: 8 + 6 * i] = bytes((f & 0xFF, (f >> 8) & 0xFF, flag, raw, 0x86, 0x05))
        h.write(bytes(data), MSG_SIZE)
        self._save(h)

    def set_parametric_eq(self, h: Handle, bands: list[ParametricBand]) -> None:
        if len(bands) > 10:
            raise invalid("Device only supports up to 10 equalizer bands")
        data = bytearray(MSG_SIZE)
        data[1] = 0x33
        for i, b in enumerate(bands):
            if f32(b.frequency) != self.FREQ_DISABLED and not 20 <= f32(b.frequency) <= 20000:
                raise invalid("Frequency must be between 20 and 20000 Hz")
            if f32(b.q) < f32(0.2) or f32(b.q) > f32(10.0):
                raise invalid("Q-factor must be between 0.2 and 10.0")
            if f32(b.gain) < -10.0 or f32(b.gain) > 10.0:
                raise invalid("Gain must be between -10 and +10")
            freq = int(f32(b.frequency)) & 0xFFFF
            q = int(fmul(b.q, 1000)) & 0xFFFF
            data[2 + 6 * i: 8 + 6 * i] = bytes((freq & 0xFF, freq >> 8, _NOVA5_FILTER.get(b.type, 1),
                                                u8(fmul(fadd(b.gain, 10.0), 2.0)), q & 0xFF, q >> 8))
        for i in range(len(bands), 10):
            f = self.FREQ_DISABLED
            data[2 + 6 * i: 8 + 6 * i] = bytes((f & 0xFF, f >> 8, 1, self.GAIN_BASE, 0x86, 0x05))
        h.write(bytes(data), MSG_SIZE)
        self._save(h)


@driver("SteelSeries Arctis Nova 7")
class ArctisNova7(Nova):
    PRESET_VALUES = ArctisNova5.PRESET_VALUES
    GEN2_SIDETONE_PID = 0x227E
    DISCRETE_BATTERY_PIDS = (0x2202, 0x2206, 0x220A, 0x223A, 0x227A, 0x22A4)
    SIDETONE_LEVELS = (0, 43, 85, 128)

    def capabilities(self):
        caps = super().capabilities()
        if self.product_id == self.GEN2_SIDETONE_PID:
            caps[Cap.SIDETONE_STATUS] = caps[Cap.SIDETONE]
        return caps

    def battery(self, h: Handle) -> Battery:
        data = self.read_status(h)
        if len(data) < 4:
            raise protocol_error("Response too short")
        if data[3] == 0x00:
            raise offline("Headset not connected")
        status = BatteryStatus.CHARGING if data[3] in (0x01, 0x02) else BatteryStatus.AVAILABLE
        if self.product_id in self.DISCRETE_BATTERY_PIDS:
            level = map_int(data[2], 0, 4, 0, 100)
        else:
            level = data[2]
        return Battery(min(level, 100), status)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        mapped = sidetone_to_discrete(level, 4)
        self.send_command(h, (0x00, 0x39, mapped))
        if self.product_id == self.GEN2_SIDETONE_PID:
            self.send_command(h, (0x00, 0x09))
        return Sidetone(level, mapped)

    def get_sidetone(self, h: Handle) -> Sidetone:
        if self.product_id != self.GEN2_SIDETONE_PID:
            raise DriverError(ErrorCode.NOT_SUPPORTED, "Reading sidetone is only verified for the Nova 7 Gen 2")
        self.send_command(h, (0x00, 0x20))
        for _ in range(8):
            resp = h.read(MSG_SIZE, DEVICE_TIMEOUT)
            if len(resp) == 0:
                raise timeout("No SteelSeries settings response received")
            if resp[0] == 0xB0:
                continue
            if len(resp) < 4:
                raise protocol_error("SteelSeries settings response too short")
            if resp[0] != 0x20:
                raise protocol_error("Unexpected SteelSeries settings response")
            raw = resp[2]
            if raw >= len(self.SIDETONE_LEVELS):
                raise protocol_error("Invalid SteelSeries sidetone value")
            return Sidetone(self.SIDETONE_LEVELS[raw], raw)
        raise protocol_error("No SteelSeries settings response received")

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        self.send_command(h, (0x00, 0xA3, minutes))
        return minutes

    def chatmix(self, h: Handle) -> Chatmix:
        data = self.read_status(h)
        if len(data) < 6:
            raise protocol_error("Response too short for chatmix")
        game_raw, chat_raw = data[4], data[5]
        level = 64 - (map_int(chat_raw, 0, 100, 0, -64) + map_int(game_raw, 0, 100, 0, 64))
        return Chatmix(level, game_raw, chat_raw)

    def set_eq_preset(self, h: Handle, preset: int) -> None:
        if not 0 <= preset < len(self.PRESET_VALUES):
            raise invalid("Device only supports presets 0-3")
        try:
            self.set_equalizer(h, list(self.PRESET_VALUES[preset]))
        except DriverError:
            pass

    def set_equalizer(self, h: Handle, bands: list[float]) -> None:
        if len(bands) != 10:
            raise invalid("Device requires exactly 10 equalizer bands")
        data = bytearray(MSG_SIZE)
        data[1] = 0x33
        for i, v in enumerate(bands):
            if v < -10.0 or v > 10.0:
                raise invalid("Gain values must be between -10 and +10")
            data[i + 2] = u8(fadd(0x14, v))
        data[12] = 0x00
        self.send_command(h, data)

    def set_mic_mute_led(self, h: Handle, brightness: int) -> None:
        self.send_command(h, (0x00, 0xAE, min(brightness, 3)))

    def set_mic_volume(self, h: Handle, volume: int) -> None:
        mapped = volume // 16
        self.send_command(h, (0x00, 0x37, 7 if mapped == 8 else mapped))

    def set_volume_limiter(self, h: Handle, on: bool) -> None:
        self.send_command(h, (0x00, 0x3A, 1 if on else 0))

    def set_bt_when_powered_on(self, h: Handle, on: bool) -> None:
        self.send_command(h, (0x00, 0xB2, 1 if on else 0))
        self.send_command(h, (0x06, 0x09))

    def set_bt_call_volume(self, h: Handle, volume: int) -> None:
        if volume > 2:
            raise invalid("Device only supports values 0-2")
        self.send_command(h, (0x00, 0xB3, volume))


@driver("SteelSeries Arctis Nova 7P")
class ArctisNova7P(ArctisNova7):
    pass  # same protocol; HeadsetControl lists fewer capabilities (from the generated spec)


@driver("SteelSeries Arctis Nova Pro Wireless")
class ArctisNovaProWireless(Legacy):
    def battery(self, h: Handle) -> Battery:
        h.write(_pad((0x06, 0xB0), PACKET_31))
        resp = h.read(128, DEVICE_TIMEOUT)
        if len(resp) < 16:
            raise protocol_error("Response too short")
        if resp[15] == 0x01:
            raise offline("Headset not connected")
        status = BatteryStatus.CHARGING if resp[15] == 0x02 else BatteryStatus.AVAILABLE
        return Battery(map_int(resp[6], 0, 8, 0, 100), status)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        dev = 0 if level == 0 else 1 if level <= 42 else 2 if level <= 85 else 3
        self.send_with_save(h, (0x06, 0x39, dev))
        return Sidetone(level)

    def set_lights(self, h: Handle, on: bool) -> bool:
        self.send_with_save(h, (0x06, 0xBF, u8(map_int(1 if on else 0, 0, 1, 1, 10))))
        return on

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        level = (6 if minutes >= 45 else 5 if minutes >= 23 else 4 if minutes >= 13 else
                 3 if minutes >= 8 else 2 if minutes >= 3 else 1 if minutes > 0 else 0)
        self.send_with_save(h, (0x06, 0xC1, level))
        return minutes

    def set_eq_preset(self, h: Handle, preset: int) -> None:
        if preset >= 4:
            raise invalid("Device only supports presets 0-3")
        self.send_with_save(h, (0x06, 0x2E, preset))

    def set_equalizer(self, h: Handle, bands: list[float]) -> None:
        if len(bands) != 10:
            raise invalid("Device requires exactly 10 equalizer bands")
        h.write(bytes((0x06, 0x2E, 4)))  # switch to the custom preset
        cmd = bytearray(PACKET_31)
        cmd[0], cmd[1] = 0x06, 0x33
        for i, v in enumerate(bands):
            if v < -10.0 or v > 10.0:
                raise invalid("Band values must be between -10 and +10")
            cmd[i + 2] = u8(fadd(0x14, fmul(2, v)))
        h.write(bytes(cmd))


_NOVA3P_FILTER = {**_NOVA5_FILTER, FilterType.NOTCH: 6}


def _nova3p_gain(gain: float) -> int:
    if gain < 0:
        raw = int(fdiv(-f32(gain), 0.1))
        return u8((0xFF - raw) + 1)
    return u8(int(fdiv(gain, 0.1)))


@driver("SteelSeries Arctis Nova 3P Wireless")
class ArctisNova3PWireless(Nova):
    FREQS = ArctisNova5.FREQS
    PRESET_VALUES = ArctisNova5.PRESET_VALUES
    FREQ_DISABLED = 20001

    def _save(self, h: Handle) -> None:
        h.write(bytes((0x00, 0x09)), MSG_SIZE)

    def battery(self, h: Handle) -> Battery:
        data = self.read_status(h)
        if len(data) < 5:
            raise protocol_error("Response too short")
        if data[1] == 0x02:
            raise offline("Headset not connected")
        return Battery(data[3], BatteryStatus.CHARGING if data[4] == 0x01 else BatteryStatus.AVAILABLE)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        h.write(bytes((0x00, 0x39, u8(map_int(level, 0, 128, 0, 0x0A)))), MSG_SIZE)
        self._save(h)
        return Sidetone(level)

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        for step in (90, 75, 60, 45, 30, 15, 10, 5, 1):
            if minutes >= step:
                minutes = step
                break
        else:
            minutes = 0
        h.write(bytes((0x00, 0xA3, minutes)), MSG_SIZE)
        self._save(h)
        return minutes

    def set_mic_volume(self, h: Handle, volume: int) -> None:
        h.write(bytes((0x00, 0x37, u8(map_int(volume, 0, 128, 0, 0x0F)))), MSG_SIZE)
        self._save(h)

    def set_eq_preset(self, h: Handle, preset: int) -> None:
        if not 0 <= preset < len(self.PRESET_VALUES):
            raise invalid("Device only supports presets 0-3")
        try:
            self.set_equalizer(h, list(self.PRESET_VALUES[preset]))
        except DriverError:
            pass

    def set_equalizer(self, h: Handle, bands: list[float]) -> None:
        if len(bands) != 10:
            raise invalid("Device requires exactly 10 equalizer bands")
        data = bytearray(MSG_SIZE)
        data[1] = 0x33
        for i, gain in enumerate(bands):
            if gain < -12.0 or gain > 12.0:
                raise invalid("Gain values must be between -12 and +12")
            f = self.FREQS[i]
            data[2 + 6 * i: 8 + 6 * i] = bytes((f & 0xFF, f >> 8, 0x01, _nova3p_gain(gain), 0x86, 0x05))
        h.write(bytes(data), MSG_SIZE)
        self._save(h)

    def set_parametric_eq(self, h: Handle, bands: list[ParametricBand]) -> None:
        if len(bands) > 10:
            raise invalid("Device only supports up to 10 equalizer bands")
        data = bytearray(MSG_SIZE)
        data[1] = 0x33
        for i, b in enumerate(bands):
            if f32(b.frequency) != self.FREQ_DISABLED and not 20 <= f32(b.frequency) <= 20000:
                raise invalid("Frequency must be between 20 and 20000 Hz")
            if f32(b.q) < f32(0.2) or f32(b.q) > f32(10.0):
                raise invalid("Q-factor must be between 0.2 and 10.0")
            if f32(b.gain) < -12.0 or f32(b.gain) > 12.0:
                raise invalid("Gain must be between -12 and +12")
            freq = int(f32(b.frequency)) & 0xFFFF
            q = int(fmul(b.q, 1000)) & 0xFFFF
            data[2 + 6 * i: 8 + 6 * i] = bytes((freq & 0xFF, freq >> 8, _NOVA3P_FILTER.get(b.type, 1),
                                                _nova3p_gain(b.gain), q & 0xFF, q >> 8))
        for i in range(len(bands), 10):
            f = self.FREQ_DISABLED
            data[2 + 6 * i: 8 + 6 * i] = bytes((f & 0xFF, f >> 8, 1, 0, 0x86, 0x05))
        h.write(bytes(data), MSG_SIZE)
        self._save(h)


@driver("SteelSeries Arctis GameBuds")
class ArctisGameBuds(Nova):
    def battery(self, h: Handle) -> Battery:
        data = self.read_status(h)
        if len(data) <= 6:
            raise protocol_error("Battery status response too short")
        level = None
        if data[3] == 0x03:
            level = data[5]
        if data[4] == 0x03:
            level = data[6] if level is None else min(level, data[6])
        if level is None:
            raise offline("Earbuds are docked in the charging case")
        return Battery(min(level, 100), BatteryStatus.AVAILABLE)
