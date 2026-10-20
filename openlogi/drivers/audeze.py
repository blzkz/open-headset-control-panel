"""Audeze Maxwell / Maxwell 2 (ported from HeadsetControl).

Requests are framed messages split over output reports 0x06; replies are polled from
input reports 0x07 and reassembled from a byte stream.
"""

from __future__ import annotations

import math

from .base import (
    Battery,
    BatteryStatus,
    Chatmix,
    Driver,
    DriverError,
    ErrorCode,
    Handle,
    Sidetone,
    driver,
    f32,
    invalid,
    map_int,
    monotonic_ms,
    protocol_error,
    sleep_ms,
    timeout,
    u8,
)

MSG_SIZE = 62
REPORT_OUT, REPORT_IN = 0x06, 0x07
FRAGMENT_OFFSET = 3
MAX_FRAGMENT = MSG_SIZE - FRAGMENT_OFFSET
ROUTE_HEADSET = 0x80
MARKER, TYPE_REQUEST, TYPE_RESPONSE, TYPE_RESULT = 0x05, 0x5A, 0x5B, 0x5D
HEADER_SIZE, MAX_MESSAGE_SIZE = 4, 1024

CMD_WRITE_KEY, CMD_READ_KEY = (0x00, 0x09), (0x01, 0x09)
CMD_WRITE_PARAMETER, CMD_READ_PARAMETER = (0x82, 0x2C), (0x83, 0x2C)
CMD_BATTERY = (0xD6, 0x0C)
KEY_EQ_PRESET, KEY_VOICE_PROMPT_LEVEL, KEY_MIC_MUTE, KEY_NOISE_FILTER = 0x00, 0x20, 0x22, 0x24
KEY_VOLUME_LIMITER, KEY_SIDETONE_LEVEL, KEY_SIDETONE_MAX_LEVEL = 0x28, 0x2C, 0x2D
PARAM_AUTO_POWER_OFF, PARAM_SIDETONE_ENABLED, PARAM_CHATMIX = 0x01, 0x07, 0x0B
DEFAULT_SIDETONE_MAX, CHATMIX_MAX = 31, 20


def _le16(v: int) -> bytes:
    return bytes((v & 0xFF, (v >> 8) & 0xFF))


def _rd16(b: bytes, off: int) -> int:
    return b[off] | (b[off + 1] << 8)


def build_reports(message: bytes) -> list[bytes]:
    reports = []
    for off in range(0, len(message), MAX_FRAGMENT):
        chunk = message[off:off + MAX_FRAGMENT]
        reports.append(bytes((REPORT_OUT, len(chunk), ROUTE_HEADSET)) + chunk.ljust(MAX_FRAGMENT, b"\0"))
    return reports


def _fragment(report: bytes) -> bytes:
    if len(report) < FRAGMENT_OFFSET:
        return b""
    n = report[1]
    if n > MAX_FRAGMENT or FRAGMENT_OFFSET + n > len(report):
        return b""
    return report[FRAGMENT_OFFSET:FRAGMENT_OFFSET + n]


def _extract(stream: bytearray) -> bytes:
    while stream:
        if stream[0] != MARKER:
            del stream[0]
            continue
        if len(stream) < HEADER_SIZE:
            break
        size = HEADER_SIZE + _rd16(stream, 2)
        if size > MAX_MESSAGE_SIZE:
            del stream[0]
            continue
        if len(stream) < size:
            break
        message = bytes(stream[:size])
        del stream[:size]
        return message
    return b""


def _is(message: bytes, typ: int, command: tuple[int, int]) -> bool:
    return (len(message) >= HEADER_SIZE + 2 and message[1] == typ
            and message[HEADER_SIZE] == command[0] and message[HEADER_SIZE + 1] == command[1])


def _has_id(message: bytes, offset: int, ident: int) -> bool:
    return len(message) >= offset + 2 and _rd16(message, offset) == ident


class _Maxwell(Driver):
    RESPONSE_TIMEOUT_MS = 1250
    POLL_INTERVAL_MS = 50

    def _await(self, h: Handle, accept, timeout_ms: int) -> bytes:
        deadline = monotonic_ms() + timeout_ms
        stream = bytearray()
        while True:
            message = _extract(stream)
            while message:
                if accept(message):
                    return message
                message = _extract(stream)
            if monotonic_ms() >= deadline:
                raise timeout("No matching reply from the Maxwell")
            sleep_ms(self.POLL_INTERVAL_MS)
            report = h.get_input(REPORT_IN, MSG_SIZE)
            stream += _fragment(report[:MSG_SIZE])

    def _request(self, h: Handle, body: bytes, accept, attempts: int = 3, timeout_ms: int | None = None,
                 fragment_interval_ms: int = 0) -> bytes:
        reports = build_reports(bytes((MARKER, TYPE_REQUEST)) + _le16(len(body)) + body)
        wait = self.RESPONSE_TIMEOUT_MS if timeout_ms is None else timeout_ms
        error: DriverError = timeout("No matching reply from the Maxwell")
        for _ in range(attempts):
            for i, report in enumerate(reports):
                if i > 0 and fragment_interval_ms > 0:
                    sleep_ms(fragment_interval_ms)
                h.write(report, MSG_SIZE)
            try:
                return self._await(h, accept, wait)
            except DriverError as exc:
                error = exc
                if exc.code is not ErrorCode.TIMEOUT:
                    break
        raise error

    def _read_key(self, h: Handle, key: int) -> int:
        reply = self._request(h, bytes((*CMD_READ_KEY, key, 0x00)),
                              lambda m: _is(m, TYPE_RESPONSE, CMD_READ_KEY) and _has_id(m, 6, key))
        if len(reply) <= 9 or reply[8] != 0:
            raise protocol_error(f"Maxwell rejected reading key 0x{key:02x}")
        return reply[9]

    def _write_key(self, h: Handle, key: int, value: int) -> None:
        self._request(h, bytes((*CMD_WRITE_KEY, key, 0x00, value)),
                      lambda m: _is(m, TYPE_RESPONSE, CMD_WRITE_KEY) and _has_id(m, 6, key), attempts=1)

    def _read_parameter(self, h: Handle, parameter: int) -> int:
        reply = self._request(h, bytes((*CMD_READ_PARAMETER, parameter, 0x00)),
                              lambda m: _is(m, TYPE_RESPONSE, CMD_READ_PARAMETER) and _has_id(m, 7, parameter))
        if len(reply) <= 9 or reply[6] != 0:
            raise protocol_error(f"Maxwell rejected reading parameter 0x{parameter:02x}")
        return reply[9]

    def _write_parameter(self, h: Handle, parameter: int, value: bytes) -> None:
        reply = self._request(h, bytes((*CMD_WRITE_PARAMETER, parameter, 0x00)) + value,
                              lambda m: _is(m, TYPE_RESPONSE, CMD_WRITE_PARAMETER) and _has_id(m, 7, parameter),
                              attempts=1)
        if reply[6] != 0:
            raise protocol_error(f"Maxwell rejected writing parameter 0x{parameter:02x}")

    def _sidetone_max(self, h: Handle) -> int:
        try:
            value = self._read_key(h, KEY_SIDETONE_MAX_LEVEL)
        except DriverError:
            return DEFAULT_SIDETONE_MAX
        return value or DEFAULT_SIDETONE_MAX

    def _switch_sidetone(self, h: Handle, on: bool) -> None:
        if (self._read_parameter(h, PARAM_SIDETONE_ENABLED) != 0) == on:
            return
        self._write_parameter(h, PARAM_SIDETONE_ENABLED, bytes((1 if on else 0,)))

    def battery(self, h: Handle) -> Battery:
        def accept(m: bytes) -> bool:
            has_level = _is(m, TYPE_RESULT, CMD_BATTERY) and len(m) > 8
            refused = _is(m, TYPE_RESPONSE, CMD_BATTERY) and len(m) > 6 and m[6] != 0
            return has_level or refused

        try:
            reply = self._request(h, bytes((*CMD_BATTERY, 0x00)), accept)
        except DriverError as exc:
            if exc.code is ErrorCode.TIMEOUT:
                return Battery(-1, BatteryStatus.UNAVAILABLE)
            raise
        if reply[1] != TYPE_RESULT:
            return Battery(-1, BatteryStatus.UNAVAILABLE)
        try:  # microphone boom state, informational only
            self._read_key(h, KEY_MIC_MUTE)
        except DriverError:
            pass
        return Battery(min(reply[8], 100), BatteryStatus.AVAILABLE)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        device_max = self._sidetone_max(h)
        enabled = level > 0
        mapped = max(1, u8(map_int(level, 0, 128, 0, device_max))) if enabled else 0
        self._switch_sidetone(h, enabled)
        if enabled:
            self._write_key(h, KEY_SIDETONE_LEVEL, mapped)
        return Sidetone(level, mapped)

    def get_sidetone(self, h: Handle) -> Sidetone:
        device_level = self._read_key(h, KEY_SIDETONE_LEVEL)
        enabled = self._read_parameter(h, PARAM_SIDETONE_ENABLED)
        device_max = max(self._sidetone_max(h), device_level)
        muted = enabled == 0 or device_level == 0
        return Sidetone(0 if muted else u8(map_int(device_level, 0, device_max, 0, 128)), device_level)

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        seconds = 0
        if minutes > 0:
            seconds = next((m for m in (5, 10, 15, 30, 45, 60, 90, 120, 240) if m >= minutes), 360) * 60
        en = 1 if seconds else 0
        lsb, msb = seconds & 0xFF, (seconds >> 8) & 0xFF
        self._write_parameter(h, PARAM_AUTO_POWER_OFF, bytes((en, 0, lsb, msb, en, 0, lsb, msb)))
        return minutes

    def set_voice_prompts(self, h: Handle, on: bool) -> None:
        self._write_key(h, KEY_VOICE_PROMPT_LEVEL, 0x07 if on else 0x00)

    def chatmix(self, h: Handle) -> Chatmix:
        raw = self._read_parameter(h, PARAM_CHATMIX)
        level = map_int(min(raw, CHATMIX_MAX), 0, CHATMIX_MAX, 0, 128)
        game = 100 if level >= 64 else map_int(level, 0, 64, 0, 100)
        chat = 100 if level <= 64 else map_int(level, 64, 128, 100, 0)
        return Chatmix(level, game, chat)

    def set_eq_preset(self, h: Handle, preset: int) -> None:
        if preset >= 10:
            raise invalid("Device only supports presets 0-9")
        self._write_key(h, KEY_EQ_PRESET, preset + 1)

    def set_noise_filter(self, h: Handle, level: int) -> None:
        if level > 2:
            raise invalid("Noise filter level must be 0, 1, or 2")
        self._write_key(h, KEY_NOISE_FILTER, level)


# -- Maxwell custom EQ (DSP biquad coefficients) ------------------------------------------------

EQ_FREQUENCIES = (32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
EQ_HEADER_SIZE, EQ_RECORD_SIZE = 5, 18
EQ_SETTINGS_SIZE = EQ_HEADER_SIZE + EQ_RECORD_SIZE * len(EQ_FREQUENCIES)
EQ_SAMPLE_RATES = ((44100, 1), (48000, 2), (88200, 5), (96000, 6))
Q18, Q22, Q23, Q30, Q31 = 262144.0, 4194304.0, 8388608.0, 1073741824.0, 2147483648.0
STAGE_PEAK_LIMIT, OUTPUT_PEAK_LIMIT, FIRST_STAGE_MARGIN = 4.0, 8.0, 1.19
STORAGE_KEY_EQ_SETTINGS, STORAGE_KEY_EQ_COEFFICIENTS = 0xEF00, 0xE42C


def _int32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


def _trunc_int32(x: float) -> int:
    return int(x)  # static_cast<int32_t>(double) truncates toward zero


def _supported_gain(gain: float) -> bool:
    return math.isfinite(gain) and -12 <= gain <= 12 and float(round(gain)) == gain


def _supported_band(freq: float, gain: float, bw: float, rate: int) -> bool:
    return (math.isfinite(freq) and math.isfinite(bw) and 0 < freq < rate // 2 and 0 < bw <= 48000
            and _supported_gain(gain))


def _peaking(freq: float, gain: float, bw: float, rate: float) -> list[float]:
    omega = freq * (2 * math.pi) / rate
    amplitude = math.sqrt(math.pow(10.0, gain / 20.0))
    alpha = math.sin(omega) / (2 * (freq / bw))
    a0 = 1 + alpha / amplitude
    b1 = -2 * math.cos(omega) / a0
    return [(1 + alpha * amplitude) / a0, b1, (1 - alpha * amplitude) / a0, b1, (1 - alpha / amplitude) / a0]


def _cascade_peaks(filters: list[list[float]], rate: int) -> list[float]:
    peaks = [0.0] * len(filters)
    for frequency in range(rate // 2):
        theta = -2 * math.pi * frequency / rate
        z = complex(math.cos(theta), math.sin(theta))
        cascade = complex(1, 0)
        for i, (b0, b1, b2, a1, a2) in enumerate(filters):
            cascade *= (b0 + b1 * z + b2 * z * z) / (1.0 + a1 * z + a2 * z * z)
            m = abs(cascade)
            if m > peaks[i]:
                peaks[i] = m
    return peaks


def _dsp32(value: int) -> bytes:
    bits = value & 0xFFFFFFFF
    return _le16(bits >> 16) + _le16(bits & 0xFFFF)


def _stable(f: list[float]) -> bool:
    return abs(f[4]) < 1 and 1 + f[3] + f[4] > 0 and 1 - f[3] + f[4] > 0


def eq_coefficients_for_rate(bands: list[tuple[float, float, float]], rate: int) -> bytes:
    if not bands or len(bands) > len(EQ_FREQUENCIES) or rate not in dict(EQ_SAMPLE_RATES):
        raise invalid("Invalid Maxwell EQ band count or sample rate")
    if not all(_supported_band(f, g, bw, rate) for f, g, bw in bands):
        raise invalid("Maxwell EQ requires finite whole-dB gains from -12 to 12")
    ordered = sorted(bands, key=lambda b: -b[1])  # stable, like std::stable_sort(a.gain > b.gain)
    filters = [_peaking(f, g, bw, float(rate)) for f, g, bw in ordered]
    peaks = _cascade_peaks(filters, rate)
    peak = peaks[-1]
    prescale = max(1.0, peak / STAGE_PEAK_LIMIT)
    scale_product = 1.0
    out = bytearray(_le16(len(bands)))
    for i, filt in enumerate(filters):
        filt = list(filt)
        scale = max(abs(filt[0]), abs(filt[1] * 0.5), abs(filt[2]))
        first_scale = prescale * FIRST_STAGE_MARGIN if i == 0 else 1.0
        if peaks[i] / (scale_product * scale * first_scale) > STAGE_PEAK_LIMIT:
            scale = max(1.0, scale)
        scale *= first_scale
        scale_product *= scale
        for j in range(5):
            if j < 3:
                filt[j] /= scale
            quantum = Q22 if j in (1, 3) else Q23
            filt[j] = math.floor(min(max(filt[j] * quantum + 0.5, -Q23), Q23 - 1)) / quantum
        if not _stable(filt):
            raise invalid("Maxwell EQ filter becomes unstable after quantization")
        for j in range(5):
            out += _dsp32(_trunc_int32(filt[j] * (Q30 if j in (1, 3) else Q31)))
    gain = scale_product * min(1.0, OUTPUT_PEAK_LIMIT / peak)
    if not math.isfinite(gain) or gain <= 0 or gain >= 32:
        raise invalid("Maxwell EQ gain exceeds the DSP range")
    fixed_gain = int(math.floor(gain * Q18 + 0.5))
    if fixed_gain > Q23 - 1:
        raise invalid("Maxwell EQ rounded gain exceeds the DSP range")
    out += _dsp32(fixed_gain * 256)
    return bytes(out)


def eq_coefficients(bands: list[tuple[float, float, float]]) -> bytes:
    out = bytearray((len(EQ_SAMPLE_RATES), 0, 0, 0))
    for rate, ident in EQ_SAMPLE_RATES:
        block = eq_coefficients_for_rate(bands, rate)
        out += _le16(ident) + _le16(len(block) // 2) + block
    return bytes(out)


def _read_value(settings: bytes, off: int) -> float:
    return _int32(int.from_bytes(settings[off:off + 4], "little")) * 0.01


def read_eq_bands(settings: bytes) -> list[list[float]]:
    if len(settings) != EQ_SETTINGS_SIZE:
        raise protocol_error("Unexpected Maxwell custom EQ size (expected ten bands)")
    bands = []
    for i, nominal in enumerate(EQ_FREQUENCIES):
        rec = EQ_HEADER_SIZE + EQ_RECORD_SIZE * i
        freq, gain, bw = _read_value(settings, rec + 2), _read_value(settings, rec + 6), _read_value(settings, rec + 10)
        if freq != nominal or bw <= 0 or bw > 48000:
            raise protocol_error("Unsupported Maxwell EQ band layout or bandwidth")
        bands.append([freq, gain, bw])
    return bands


@driver("Audeze Maxwell")
class AudezeMaxwell(_Maxwell):
    EQ_FREQUENCIES = EQ_FREQUENCIES
    EQ_TIMEOUT_MS = 1500
    EQ_FRAGMENT_INTERVAL_MS = 20

    def set_volume_limiter(self, h: Handle, on: bool) -> None:
        self._write_key(h, KEY_VOLUME_LIMITER, 0x88 if on else 0x8E)

    def _eq_request(self, h: Handle, body: bytes, accept) -> bytes:
        return self._request(h, body, accept, attempts=1, timeout_ms=self.EQ_TIMEOUT_MS,
                             fragment_interval_ms=self.EQ_FRAGMENT_INTERVAL_MS)

    def _read_stored(self, h: Handle, key: int, expected_size: int) -> bytes:
        cmd = (0x0C, 0x0A)
        reply = self._eq_request(h, bytes(cmd) + _le16(key) + _le16(1000),
                                 lambda m: _is(m, TYPE_RESPONSE, cmd) and (len(m) < 9 or _has_id(m, 7, key)))
        if len(reply) != 11 + expected_size or reply[6] != 0 or _rd16(reply, 9) != expected_size:
            raise protocol_error("Maxwell returned an invalid custom EQ slot")
        return reply[11:]

    def _verify(self, h: Handle, key: int, expected: bytes, mismatch: str) -> None:
        if self._read_stored(h, key, len(expected)) != expected:
            raise protocol_error(mismatch)

    def _eq_command(self, h: Handle, body: bytes, operation: str, key: int | None = None,
                    checked: bool = True) -> None:
        cmd = (body[0], body[1])
        reply_size = 9 if key is not None else 7
        try:
            reply = self._eq_request(
                h, body, lambda m: _is(m, TYPE_RESPONSE, cmd) and (key is None or len(m) < 9 or _has_id(m, 7, key)))
        except DriverError as exc:
            raise DriverError(exc.code, f"Maxwell {operation} failed: {exc}; EQ update may be partial. "
                                        "Close Audeze before retrying.") from exc
        if len(reply) != reply_size or (checked and reply[6] != 0):
            raise protocol_error(f"Maxwell rejected {operation}; EQ update may be partial")

    def _store(self, h: Handle, key: int, data: bytes) -> None:
        self._eq_command(h, bytes((0x03, 0x0A)) + _le16(len(data)), "prepare EQ storage", None, False)
        self._eq_command(h, bytes((0x0D, 0x0A)) + _le16(key) + data, "save EQ", key)

    def set_equalizer(self, h: Handle, gains: list[float]) -> None:
        gains = [float(f32(g)) for g in gains]
        if len(gains) != len(EQ_FREQUENCIES) or not all(_supported_gain(g) for g in gains):
            raise invalid("Maxwell EQ needs ten whole-dB gains from -12 to 12")
        preset = self._read_key(h, KEY_EQ_PRESET)
        if preset < 7 or preset > 10:
            raise invalid("Select a Maxwell custom preset with -p 6..9 before editing EQ")
        slot = preset - 7
        settings_key, coefficients_key = STORAGE_KEY_EQ_SETTINGS + slot, STORAGE_KEY_EQ_COEFFICIENTS + slot
        settings = bytearray(self._read_stored(h, settings_key, EQ_SETTINGS_SIZE))
        bands = read_eq_bands(bytes(settings))
        for i, gain in enumerate(gains):
            bands[i][1] = gain
            off = EQ_HEADER_SIZE + EQ_RECORD_SIZE * i + 6
            settings[off:off + 4] = (_trunc_int32(gain * 100) & 0xFFFFFFFF).to_bytes(4, "little")
        coefficients = eq_coefficients([tuple(b) for b in bands])
        self._eq_command(h, bytes((0x03, 0x0E, 0x00)) + coefficients, "apply EQ")
        self._store(h, settings_key, bytes(settings))
        self._store(h, coefficients_key, coefficients)
        self._verify(h, settings_key, bytes(settings),
                     "Maxwell EQ readback differs from the saved settings; update may be partial")
        self._verify(h, coefficients_key, coefficients,
                     "Maxwell coefficient readback differs; EQ update may be partial")


@driver("Audeze Maxwell 2")
class AudezeMaxwell2(_Maxwell):
    pass

