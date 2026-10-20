"""Logitech "Centurion" headsets: G522 LIGHTSPEED and PRO X 2 LIGHTSPEED (ported from HeadsetControl).

Centurion wraps HID++-style feature calls in its own frames; the headset's features are
reached through a bridge feature on the receiver, with large messages split in fragments.
"""

from __future__ import annotations

import math

from .base import (
    Battery,
    BatteryStatus,
    Driver,
    DriverError,
    EqInfo,
    ErrorCode,
    FilterType,
    Handle,
    ParametricBand,
    Sidetone,
    driver,
    f32,
    fdiv,
    fmul,
    invalid,
    lround,
    map_int,
    offline,
    protocol_error,
    timeout,
    u8,
)

DEVICE_TIMEOUT = 5000
FRAME_SIZE = 64
SOFTWARE_ID = 0x01
BRIDGE_FN = 0x10
POLL_ATTEMPTS = 8
MAX_SINGLE_BRIDGE_PAYLOAD = 56
MAX_CONTINUATION_PAYLOAD = 60

F_ROOT, F_FEATURE_SET, F_BRIDGE = 0x0000, 0x0001, 0x0003
F_BATTERY_SOC, F_AUTO_SLEEP = 0x0104, 0x0108
F_ADVANCED_PARA_EQ, F_MIC_MUTE_LED, F_VOICE_PROMPTS = 0x020D, 0x0601, 0x060B
F_LIGHTING, F_ONBOARD_EQ, F_SIDETONE = 0x0621, 0x0636, 0x0604


def _not_supported(msg: str) -> DriverError:
    return DriverError(ErrorCode.NOT_SUPPORTED, msg)


class _Centurion(Driver):
    PREFIX: tuple[int, ...] = (0x51,)
    EXACT_DIRECT_LENGTH = False
    DETECT_CONNECTION_EVENTS = False
    PROBE_OFFLINE_ON_TIMEOUT = False
    LOOKUP_FEATURES_BY_ID = False

    def __init__(self, product_id: int = 0) -> None:
        super().__init__(product_id)
        self._discovered = False
        self._bridge: int | None = None
        self._features: dict[int, int] = {}  # feature id -> sub-device index

    # -- framing ------------------------------------------------------------------------------
    def _frame(self, payload: bytes, flags: int = 0) -> bytes:
        hdr = len(self.PREFIX)
        frame = bytearray(FRAME_SIZE)
        frame[:hdr] = bytes(self.PREFIX)
        frame[hdr] = u8(len(payload) + 1)
        frame[hdr + 1] = flags
        n = min(len(payload), FRAME_SIZE - hdr - 2)
        frame[hdr + 2:hdr + 2 + n] = payload[:n]
        return bytes(frame)

    def _extract(self, frame: bytes) -> bytes:
        frame = frame.ljust(FRAME_SIZE, b"\0")
        hdr = len(self.PREFIX)
        if len(frame) < hdr + 3 or frame[:hdr] != bytes(self.PREFIX):
            raise protocol_error("Unexpected Centurion frame prefix")
        cpl = frame[hdr]
        if cpl <= 1 or cpl + hdr + 1 > len(frame):
            raise protocol_error("Invalid Centurion frame length")
        return frame[hdr + 2:hdr + 1 + cpl]

    @staticmethod
    def _is_connection_event(reply: bytes, bridge: int) -> bool:
        return len(reply) >= 4 and reply[0] == bridge and reply[1] == 0x00

    # -- direct requests ----------------------------------------------------------------------
    def _request(self, h: Handle, feature_index: int, function: int, params: bytes = b"",
                 match_function: bool = False) -> bytes:
        fn_sw = (function & 0xF0) | SOFTWARE_ID
        if self.EXACT_DIRECT_LENGTH:
            payload = bytes((feature_index, fn_sw)) + bytes(params)
        else:
            payload = bytes((feature_index, fn_sw, *params[:62])).ljust(64, b"\0")
        h.write(self._frame(payload), FRAME_SIZE)
        for _ in range(POLL_ATTEMPTS):
            reply = self._extract(h.read(FRAME_SIZE, DEVICE_TIMEOUT))
            if len(reply) < 2:
                continue
            if (self.DETECT_CONNECTION_EVENTS and self._bridge is not None
                    and self._is_connection_event(reply, self._bridge)):
                if reply[3] == 0x00:
                    raise offline("Headset is powered off or not connected")
                continue
            if reply[0] != feature_index:
                continue
            if match_function and reply[1] != fn_sw:
                continue
            return reply[2:]
        raise timeout("Timed out waiting for Centurion feature response")

    # -- bridge requests ----------------------------------------------------------------------
    def _bridge_request(self, h: Handle, sub_index: int, function: int, params: bytes = b"") -> bytes:
        if self._bridge is None:
            raise protocol_error("Centurion bridge index not initialized")
        bridge = self._bridge
        sub = bytes((0x00, sub_index, (function & 0xF0) | SOFTWARE_ID)) + bytes(params)
        size = len(sub)
        if size > 0x0FFF:
            raise invalid("Centurion bridge message exceeds the 12-bit size limit")
        prefix = bytes((bridge, BRIDGE_FN | SOFTWARE_ID, (size >> 8) & 0x0F, size & 0xFF))
        if size <= MAX_SINGLE_BRIDGE_PAYLOAD:
            h.write(self._frame(prefix + sub), FRAME_SIZE)
        else:
            offset = frag = 0
            while offset < size:
                chunk = min(MAX_SINGLE_BRIDGE_PAYLOAD if frag == 0 else MAX_CONTINUATION_PAYLOAD, size - offset)
                more = offset + chunk < size
                body = (prefix if frag == 0 else b"") + sub[offset:offset + chunk]
                h.write(self._frame(body, u8((frag << 1) | (1 if more else 0))), FRAME_SIZE)
                offset += chunk
                frag += 1
        ack = False
        for _ in range(POLL_ATTEMPTS):
            try:
                resp = h.read(FRAME_SIZE, DEVICE_TIMEOUT)
            except DriverError as exc:
                if ack and self.PROBE_OFFLINE_ON_TIMEOUT and exc.code is ErrorCode.TIMEOUT:
                    self._probe_offline(h, exc)
                raise
            reply = self._extract(resp)
            if (self.PROBE_OFFLINE_ON_TIMEOUT and len(reply) >= 4 and reply[0] == 0xFF and reply[1] == bridge
                    and reply[2] == BRIDGE_FN | SOFTWARE_ID):
                self._probe_offline(h, protocol_error("Centurion bridge rejected the request"))
            if len(reply) < 2 or reply[0] != bridge:
                continue
            if self.DETECT_CONNECTION_EVENTS and self._is_connection_event(reply, bridge):
                if reply[3] == 0x00:
                    raise offline("Headset is powered off or not connected")
                continue
            fn_sw = reply[1]
            if (fn_sw >> 4) != (BRIDGE_FN >> 4):
                continue
            if fn_sw & 0x0F == SOFTWARE_ID:
                ack = True
                continue
            if fn_sw & 0x0F != 0:
                continue
            if not self._is_bridge_response_for(reply, sub_index):
                continue
            if len(reply) < 7:
                raise protocol_error("Malformed Centurion bridge response")
            if reply[5] == 0xFF:
                raise protocol_error("Centurion sub-device rejected the request")
            return reply[7:]
        if not ack:
            raise timeout("Timed out waiting for Centurion bridge acknowledgment")
        failure = timeout("Timed out waiting for Centurion bridge response")
        if self.PROBE_OFFLINE_ON_TIMEOUT:
            self._probe_offline(h, failure)
        raise failure

    @staticmethod
    def _is_bridge_response_for(reply: bytes, sub_index: int) -> bool:
        if len(reply) < 6 or reply[4] != 0x00:
            return False
        if reply[5] == sub_index:
            return True
        return reply[5] == 0xFF and len(reply) >= 7 and reply[6] == sub_index

    def _probe_offline(self, h: Handle, failure: DriverError) -> None:
        """Always raises: offline when the bridge reports the headset gone, else ``failure``."""
        try:
            status = self._request(h, self._bridge, 0x00, b"", match_function=True)
        except DriverError as exc:
            if exc.code is ErrorCode.OFFLINE:
                raise
            raise failure from exc
        if len(status) >= 4 and status[2] == 0x00 and status[3] == 0x00:
            raise offline("Headset is powered off or not connected")
        raise failure

    # -- feature lookup -----------------------------------------------------------------------
    def _lookup(self, h: Handle, feature_id: int) -> int:
        if feature_id in self._features:
            return self._features[feature_id]
        if self._bridge is None:
            r = self._request(h, F_ROOT, 0x00, F_BRIDGE.to_bytes(2, "big"))
            if not r or r[0] == 0:
                raise protocol_error("Centurion bridge feature not found")
            self._bridge = r[0]
        r = self._bridge_request(h, F_ROOT, 0x00, feature_id.to_bytes(2, "big"))
        if not r or (r[0] == 0 and feature_id != F_ROOT):
            raise _not_supported("Centurion feature not available on this device")
        self._features[feature_id] = r[0]
        return r[0]

    def _discover(self, h: Handle) -> None:
        if self._discovered:
            return
        r = self._request(h, F_ROOT, 0x00, F_FEATURE_SET.to_bytes(2, "big"))
        if not r or r[0] == 0:
            raise protocol_error("Centurion FeatureSet not found")
        feature_set = r[0]
        count = self._request(h, feature_set, 0x00)
        if not count:
            raise protocol_error("Centurion FeatureSet count response was empty")
        bridge = 0xFF
        for index in range(count[0]):
            fr = self._request(h, feature_set, 0x10, bytes((index,)))
            if len(fr) >= 3 and ((fr[1] << 8) | fr[2]) == F_BRIDGE:
                bridge = index
                break
        if bridge == 0xFF:
            raise protocol_error("Centurion bridge feature not found")
        self._bridge = bridge
        r = self._bridge_request(h, F_ROOT, 0x00, F_FEATURE_SET.to_bytes(2, "big"))
        if not r or r[0] == 0:
            raise protocol_error("Centurion sub-device FeatureSet not found")
        sub_set = r[0]
        count = self._bridge_request(h, sub_set, 0x00)
        if not count:
            raise protocol_error("Centurion sub-device FeatureSet count response was empty")
        self._features.clear()
        for index in range(count[0]):
            fr = self._bridge_request(h, sub_set, 0x10, bytes((index,)))
            if len(fr) >= 3:
                self._features[(fr[1] << 8) | fr[2]] = index
        self._discovered = True

    def _feature(self, h: Handle, feature_id: int, function: int, params: bytes = b"") -> bytes:
        if self.LOOKUP_FEATURES_BY_ID:
            index = self._lookup(h, feature_id)
        else:
            self._discover(h)
            if feature_id not in self._features:
                raise _not_supported("Centurion feature not available on this device")
            index = self._features[feature_id]
        return self._bridge_request(h, index, function, params)

    @staticmethod
    def _parse_battery(packet: bytes) -> Battery:
        if not packet:
            raise protocol_error("Empty Centurion battery response")
        if packet[0] > 100:
            raise protocol_error("Centurion battery percentage out of range")
        charging = packet[2] if len(packet) >= 3 else 0
        return Battery(packet[0], BatteryStatus.CHARGING if charging in (1, 2) else BatteryStatus.AVAILABLE)


# -- G522 LIGHTSPEED --------------------------------------------------------------------------

_G522_Q = 22.0 / 32.0
_G522_DEFAULT = ((20, 0.0, _G522_Q), (50, 0.0, _G522_Q), (125, 0.0, _G522_Q), (250, 0.0, _G522_Q),
                 (500, 0.0, _G522_Q), (1000, 0.0, _G522_Q), (2500, 0.0, _G522_Q), (5000, 0.0, _G522_Q),
                 (10000, 0.0, _G522_Q), (20000, 0.0, _G522_Q))
_G522_PRESET_BANDS = (
    _G522_DEFAULT,
    ((20, 3.0, _G522_Q), (50, 2.5, _G522_Q), (125, 1.75, _G522_Q), (250, 1.0, 1.0), (500, 0.0, _G522_Q),
     (1000, 0.0, _G522_Q), (2500, 0.0, _G522_Q), (5000, 0.0, _G522_Q), (10000, 0.0, _G522_Q),
     (20000, 0.0, _G522_Q)),
    ((20, 0.0, _G522_Q), (50, 0.0, _G522_Q), (250, 0.5, _G522_Q), (400, 0.5, _G522_Q), (800, 0.75, _G522_Q),
     (1500, 1.0, _G522_Q), (2500, 1.25, _G522_Q), (5000, 1.5, _G522_Q), (10000, 1.75, _G522_Q),
     (19000, 2.75, _G522_Q)),
    ((20, 0.0, _G522_Q), (50, 0.0, _G522_Q), (125, 0.0, _G522_Q), (400, -1.0, _G522_Q), (800, -1.0, _G522_Q),
     (1250, 3.75, 2.0), (2500, 4.5, 1.0), (5000, 1.75, _G522_Q), (10000, 1.5, _G522_Q), (19000, 1.5, _G522_Q)),
    ((20, 4.0, _G522_Q), (50, 3.0, _G522_Q), (125, 2.0, _G522_Q), (250, -0.5, _G522_Q), (500, -0.5, _G522_Q),
     (1000, 1.0, _G522_Q), (2500, 2.0, _G522_Q), (5000, 1.5, _G522_Q), (10000, 0.5, _G522_Q),
     (20000, -0.5, _G522_Q)),
)


@driver("Logitech G522 LIGHTSPEED")
class LogitechG522Lightspeed(_Centurion):
    PREFIX = (0x50, 0x23)
    EXACT_DIRECT_LENGTH = DETECT_CONNECTION_EVENTS = PROBE_OFFLINE_ON_TIMEOUT = LOOKUP_FEATURES_BY_ID = True
    SIDETONE_MAX = 9
    LIGHTS_ON = bytes((0x00, 0x00, 0x04, 0x00, 0xB8, 0xFC, 0xFF, 0x00, 0xAB, 0x64))

    def battery(self, h: Handle) -> Battery:
        return self._parse_battery(self._feature(h, F_BATTERY_SOC, 0x00))

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        mapped = u8(map_int(level, 0, 128, 0, self.SIDETONE_MAX))
        self._feature(h, F_SIDETONE, 0x10, bytes((0x01, 0xFF, mapped)))
        return Sidetone(level)

    def get_sidetone(self, h: Handle) -> Sidetone:
        params = self._feature(h, F_SIDETONE, 0x00)
        if len(params) < 4 or params[3] > self.SIDETONE_MAX:
            raise protocol_error("Unexpected G522 sidetone reply")
        return Sidetone(u8(map_int(params[3], 0, self.SIDETONE_MAX, 0, 128)), params[3])

    def set_lights(self, h: Handle, on: bool) -> bool:
        self._feature(h, F_LIGHTING, 0x30, self.LIGHTS_ON if on else bytes(10))
        return on

    def set_voice_prompts(self, h: Handle, on: bool) -> None:
        self._feature(h, F_VOICE_PROMPTS, 0x50, bytes((0x00, 1 if on else 0)))

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        current = self._feature(h, F_AUTO_SLEEP, 0x00)
        if len(current) < 3:
            raise protocol_error("Unexpected G522 auto-sleep reply")
        self._feature(h, F_AUTO_SLEEP, 0x10, bytes((minutes, current[1], current[2])))
        return minutes

    def set_mic_mute_led(self, h: Handle, brightness: int) -> None:
        self._feature(h, F_MIC_MUTE_LED, 0x20, bytes((1 if brightness else 0,)))

    @staticmethod
    def _validate(gain: float, q: float) -> None:
        if not math.isfinite(gain) or gain < -6.0 or gain > 6.0:
            raise invalid("Gain must be between -6 and +6 dB")
        if not math.isfinite(q) or not 1 <= lround(fmul(q, 32.0)) <= 255:
            raise invalid("Q factor must be between 0.031 and 7.969")

    def _write_eq(self, h: Handle, bands) -> None:
        params = bytearray(3)
        for freq, gain, q in bands:
            q_raw = min(max(lround(fmul(q, 32.0)), 1), 255)
            gain_raw = min(max(lround(fdiv(gain, 0.05)) + 120, 0), 240)
            params += bytes((freq >> 8, freq & 0xFF, q_raw, gain_raw >> 8, gain_raw & 0xFF))
        self._feature(h, F_ADVANCED_PARA_EQ, 0x20, bytes(params))

    def set_eq_preset(self, h: Handle, preset: int) -> None:
        if preset >= len(_G522_PRESET_BANDS):
            raise invalid("Device only supports presets 0-4")
        self._write_eq(h, [(f, f32(g), f32(q)) for f, g, q in _G522_PRESET_BANDS[preset]])

    def set_equalizer(self, h: Handle, gains: list[float]) -> None:
        if len(gains) != 10:
            raise invalid("Equalizer requires 10 gain values")
        bands = []
        for (freq, _g, q), gain in zip(_G522_DEFAULT, gains, strict=True):
            gain, q = f32(gain), f32(q)
            self._validate(gain, q)
            bands.append((freq, gain, q))
        self._write_eq(h, bands)

    def set_parametric_eq(self, h: Handle, bands: list[ParametricBand]) -> None:
        if len(bands) > 10:
            raise invalid("Device only supports up to 10 equalizer bands")
        out = [(f, f32(g), f32(q)) for f, g, q in _G522_DEFAULT]
        for i, band in enumerate(bands):
            freq = f32(band.frequency)
            if band.type != FilterType.PEAKING:
                raise invalid("This headset only supports peaking EQ bands")
            if not math.isfinite(freq) or freq < 20 or freq > 20000:
                raise invalid("Frequency must be between 20 Hz and 20000 Hz")
            out[i] = (lround(freq) & 0xFFFF, f32(band.gain), f32(band.q))
            self._validate(out[i][1], out[i][2])
        self._write_eq(h, out)


# -- PRO X 2 LIGHTSPEED -----------------------------------------------------------------------

_PROX2_PRESETS = ((0.0, 0.0, 0.0, 0.0, 0.0), (4.0, 2.0, 0.0, 0.0, 0.0), (-1.0, 2.0, 1.0, 3.0, 3.0),
                  (-1.0, -1.0, 4.0, 3.0, 2.0), (0.0, 1.0, 1.0, 2.0, 4.0))


def _int8(v: int) -> int:
    return v - 256 if v > 127 else v


def _biquad(frequency: float, gain_db: float, q: float, sample_rate: float) -> tuple[float, ...]:
    amplitude = math.pow(10.0, gain_db / 40.0)
    w0 = 2.0 * 3.14159265358979323846 * frequency / sample_rate
    cos_w0 = math.cos(w0)
    alpha = math.sin(w0) / (2.0 * q)
    a0 = 1.0 + alpha / amplitude
    return ((1.0 + alpha * amplitude) / a0, (-2.0 * cos_w0) / a0, (1.0 - alpha * amplitude) / a0,
            (-2.0 * cos_w0) / a0, (1.0 - alpha / amplitude) / a0)


def _quantize(coeffs) -> list[int]:
    words = []
    for c, scale in zip(coeffs, (2147483648.0, 1073741824.0, 2147483648.0, 1073741824.0, 2147483648.0),
                        strict=True):
        q = min(max(lround(c * scale), -(1 << 31)), (1 << 31) - 1) & ~0xFF
        words += [(q >> 16) & 0xFFFF, q & 0xFFFF]
    return words


def _coefficient_section(bands, sample_rate: float, section_type: int) -> bytes:
    raw = []
    max_b0 = 1.0
    for freq, gain, q in bands:
        coeffs = _biquad(float(freq), float(gain), max(0.1, float(q)), sample_rate)
        max_b0 = max(max_b0, abs(coeffs[0]))
        raw.append(coeffs)
    rescale = max(1.0, max_b0) * 1.19
    words = [len(bands)]
    for c in raw:
        words += _quantize((c[0] / rescale, c[1] / rescale, c[2] / rescale, c[3], c[4]))
    rq = min(max(lround(rescale * 67108864.0), -(1 << 31)), (1 << 31) - 1) & 0xFFFFFF00
    words += [(rq >> 16) & 0xFFFF, rq & 0xFFFF]
    count = (len(bands) * 10 + 3) & 0xFFFF
    out = bytearray((section_type, 0x00, count & 0xFF, count >> 8))
    for w in words:
        out += bytes((w & 0xFF, w >> 8))
    return bytes(out)


def onboard_eq_payload(slot: int, bands) -> bytes:
    """``bands`` are (frequency, int8 gain, uint8 q) triples."""
    out = bytearray((slot, len(bands) & 0xFF))
    for freq, gain, q in bands:
        out += bytes(((freq >> 8) & 0xFF, freq & 0xFF, gain & 0xFF, q))
    out += bytes((0x05, 0x5A, 0xE3, 0x00, 0x03, 0x0E, 0x00, 0x02, 0x00, 0x00, 0x00))
    out += _coefficient_section(bands, 48000.0, 0x01)
    out += _coefficient_section(bands, 16000.0, 0x02)
    return bytes(out)


@driver("Logitech G PRO X 2 LIGHTSPEED")
class LogitechGProX2Lightspeed(_Centurion):
    EXACT_DIRECT_LENGTH = DETECT_CONNECTION_EVENTS = PROBE_OFFLINE_ON_TIMEOUT = LOOKUP_FEATURES_BY_ID = True
    EQ_FROM_DEVICE = True
    SIDETONE_MAX = 100

    def equalizer_info(self, h: Handle) -> EqInfo:
        """Playback EQ layout as the headset reports it (band count and gain range)."""
        desc = self._descriptor(h)
        return EqInfo(len(desc["bands"]), 0, 1.0, int(desc["min"]), int(desc["max"]))

    def battery(self, h: Handle) -> Battery:
        try:
            packet = self._feature(h, F_BATTERY_SOC, 0x00)
        except DriverError as exc:
            if exc.code is not ErrorCode.NOT_SUPPORTED:
                raise
            return self._legacy_battery(h)
        return self._parse_battery(packet)

    def _legacy_battery(self, h: Handle) -> Battery:
        request = bytearray(FRAME_SIZE)
        request[0:10] = (0x51, 0x08, 0x00, 0x03, 0x1A, 0x00, 0x03, 0x00, 0x04, 0x0A)
        h.write(bytes(request), FRAME_SIZE)
        for _ in range(4):
            p = h.read(FRAME_SIZE, DEVICE_TIMEOUT).ljust(FRAME_SIZE, b"\0")
            if p[0] == 0x51 and p[1] == 0x05 and p[6] == 0x00:
                raise offline("Headset is powered off or not connected")
            if p[0] == 0x51 and p[1] in (0x05, 0x03):
                continue
            if not (p[0] == 0x51 and p[1] == 0x0B and p[8] == 0x04):
                continue
            if p[10] > 100:
                raise protocol_error("Battery percentage out of range")
            return Battery(p[10], BatteryStatus.CHARGING if p[12] == 0x02 else BatteryStatus.AVAILABLE)
        raise protocol_error("Battery response packet not received")

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        mapped = u8(map_int(level, 0, 128, 0, self.SIDETONE_MAX))
        state = self._feature(h, F_SIDETONE, 0x00)
        payload = bytes((0x01, 0xFF, mapped)) if len(state) >= 4 else bytes((0x01, mapped))
        self._feature(h, F_SIDETONE, 0x10, payload)
        return Sidetone(level)

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        self._feature(h, F_AUTO_SLEEP, 0x10, bytes((minutes,)))
        return minutes

    # -- equalizer ------------------------------------------------------------------------------
    def _advanced_descriptor(self, h: Handle) -> dict:
        info = self._feature(h, F_ADVANCED_PARA_EQ, 0x00)
        if len(info) < 5:
            raise protocol_error("Advanced EQ info response was too short")
        slot = self._feature(h, F_ADVANCED_PARA_EQ, 0x30, bytes((0x00,)))
        if not slot:
            raise protocol_error("Advanced EQ active slot response was empty")
        params = self._feature(h, F_ADVANCED_PARA_EQ, 0x10, bytes((0x00, slot[0])))
        bands = []
        offset = 0
        while offset + 2 < len(params):
            freq = (params[offset] << 8) | params[offset + 1]
            if freq == 0:
                break
            bands.append(freq)
            offset += 3
        if not bands:
            raise protocol_error("Advanced EQ band response was empty")
        return {"onboard": False, "slot": slot[0], "min": float(_int8(info[3])), "max": float(_int8(info[4])),
                "bands": bands}

    def _onboard_descriptor(self, h: Handle) -> dict:
        info = self._feature(h, F_ONBOARD_EQ, 0x00)
        if len(info) < 5:
            raise protocol_error("Onboard EQ info response was too short")
        params = self._feature(h, F_ONBOARD_EQ, 0x10, bytes((0x00,)))
        if len(params) < 2:
            raise protocol_error("Onboard EQ parameter response was too short")
        bands = []
        offset = 2
        for _ in range(params[1]):
            if offset + 3 >= len(params):
                break
            bands.append((params[offset] << 8) | params[offset + 1])
            offset += 4
        if not bands:
            raise protocol_error("Onboard EQ band response was empty")
        return {"onboard": True, "slot": 0, "min": -12.0, "max": 12.0, "bands": bands}

    def _descriptor(self, h: Handle) -> dict:
        try:
            return self._advanced_descriptor(h)
        except DriverError:
            return self._onboard_descriptor(h)

    def _write(self, h: Handle, desc: dict, bands: list[tuple[int, int, int]]) -> None:
        if not desc["onboard"]:
            payload = bytearray((0x00, desc["slot"]))
            for freq, gain, _q in bands:
                payload += bytes(((freq >> 8) & 0xFF, freq & 0xFF, gain & 0xFF))
            self._feature(h, F_ADVANCED_PARA_EQ, 0x20, bytes(payload))
        else:
            self._feature(h, F_ONBOARD_EQ, 0x20, onboard_eq_payload(desc["slot"], bands))

    def set_equalizer(self, h: Handle, gains: list[float]) -> None:
        desc = self._descriptor(h)
        if len(gains) != len(desc["bands"]):
            raise invalid("Equalizer requires one gain value per playback band")
        bands = []
        for freq, gain in zip(desc["bands"], gains, strict=True):
            gain = f32(gain)
            if gain < desc["min"] or gain > desc["max"]:
                raise invalid("Equalizer gain is outside the supported range")
            bands.append((freq, int(gain), 1))  # static_cast<int8_t>(float) truncates
        self._write(h, desc, bands)

    def set_eq_preset(self, h: Handle, preset: int) -> None:
        if preset >= len(_PROX2_PRESETS):
            raise invalid("Device only supports presets 0-4")
        self.set_equalizer(h, list(_PROX2_PRESETS[preset]))

    def set_parametric_eq(self, h: Handle, bands: list[ParametricBand]) -> None:
        desc = self._descriptor(h)
        if len(bands) != len(desc["bands"]):
            raise invalid("Parametric equalizer requires exactly one entry per playback band")
        out = []
        for band in bands:
            freq, gain, q = f32(band.frequency), f32(band.gain), f32(band.q)
            if band.type != FilterType.PEAKING:
                raise invalid("This headset only supports peaking EQ bands")
            if abs(f32(q - 1.0)) > f32(0.001):
                raise invalid("This headset uses a fixed Q factor of 1.0")
            if freq < 20.0 or freq > 20000.0:
                raise invalid("Frequency must be between 20 Hz and 20000 Hz")
            if gain < desc["min"] or gain > desc["max"]:
                raise invalid("Gain is outside the supported range")
            out.append((int(freq) & 0xFFFF, int(gain), min(max(lround(q), 1), 255)))
        self._write(h, desc, out)
