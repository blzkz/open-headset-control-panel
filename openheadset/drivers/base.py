"""Common types for vendor drivers ported from HeadsetControl.

The drivers in this package are ports of HeadsetControl's C++ device classes
(https://github.com/Sapd/HeadsetControl, GPL-3.0). Their byte output is checked
against packets recorded from the original code (tests/test_drivers_golden.py),
so a port only counts as done when it is byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Protocol


class Cap(str, Enum):
    SIDETONE = "CAP_SIDETONE"
    BATTERY = "CAP_BATTERY_STATUS"
    NOTIFICATION_SOUND = "CAP_NOTIFICATION_SOUND"
    LIGHTS = "CAP_LIGHTS"
    INACTIVE_TIME = "CAP_INACTIVE_TIME"
    CHATMIX = "CAP_CHATMIX_STATUS"
    VOICE_PROMPTS = "CAP_VOICE_PROMPTS"
    ROTATE_TO_MUTE = "CAP_ROTATE_TO_MUTE"
    EQ_PRESET = "CAP_EQUALIZER_PRESET"
    EQUALIZER = "CAP_EQUALIZER"
    PARAMETRIC_EQ = "CAP_PARAMETRIC_EQUALIZER"
    MIC_MUTE_LED = "CAP_MICROPHONE_MUTE_LED_BRIGHTNESS"
    MIC_VOLUME = "CAP_MICROPHONE_VOLUME"
    VOLUME_LIMITER = "CAP_VOLUME_LIMITER"
    BT_WHEN_POWERED_ON = "CAP_BT_WHEN_POWERED_ON"
    BT_CALL_VOLUME = "CAP_BT_CALL_VOLUME"
    NOISE_FILTER = "CAP_NOISE_FILTER"
    SIDETONE_STATUS = "CAP_SIDETONE_STATUS"


class BatteryStatus(IntEnum):
    UNAVAILABLE = 0
    CHARGING = 1
    AVAILABLE = 2
    HIDERROR = 3
    TIMEOUT = 4


class ErrorCode(Enum):
    TIMEOUT = "timeout"
    OFFLINE = "offline"
    PROTOCOL = "protocol"
    INVALID_PARAMETER = "invalid parameter"
    NOT_SUPPORTED = "not supported"
    HID = "hid"


class DriverError(Exception):
    def __init__(self, code: ErrorCode, message: str = ""):
        super().__init__(message or code.value)
        self.code = code


def timeout(msg: str = "timeout") -> DriverError:
    return DriverError(ErrorCode.TIMEOUT, msg)


def protocol_error(msg: str) -> DriverError:
    return DriverError(ErrorCode.PROTOCOL, msg)


def invalid(msg: str) -> DriverError:
    return DriverError(ErrorCode.INVALID_PARAMETER, msg)


def offline(msg: str = "device offline") -> DriverError:
    return DriverError(ErrorCode.OFFLINE, msg)


def hid_error(msg: str) -> DriverError:
    return DriverError(ErrorCode.HID, msg)


# -- results ------------------------------------------------------------------------------------
@dataclass(slots=True)
class Battery:
    level: int  # 0-100, -1 when unknown
    status: BatteryStatus
    millivolts: int | None = None


@dataclass(slots=True)
class Sidetone:
    current: int  # HeadsetControl scale 0-128
    device_level: int = 0


@dataclass(slots=True)
class Chatmix:
    level: int  # 0-128 (<64 game, >64 chat)
    game: int
    chat: int


@dataclass(slots=True)
class EqInfo:
    bands: int
    baseline: float
    step: float
    min: float
    max: float


@dataclass(slots=True)
class ParametricEqInfo:
    bands: int
    gain_base: float
    gain_step: float
    gain_min: float
    gain_max: float
    q_min: float
    q_max: float
    freq_min: int
    freq_max: int
    filter_types: int


class FilterType(IntEnum):
    LOW_SHELF = 0
    LOW_PASS = 1
    PEAKING = 2
    HIGH_PASS = 3
    HIGH_SHELF = 4
    NOTCH = 5


@dataclass(slots=True)
class ParametricBand:
    frequency: float
    gain: float
    q: float = 1.0
    type: FilterType = FilterType.PEAKING


@dataclass(frozen=True, slots=True)
class CapDetail:
    usage_page: int = 0
    usage: int = 0
    interface: int = 0


# -- I/O ----------------------------------------------------------------------------------------
class Handle(Protocol):
    """The subset of hidapi HeadsetControl drivers use."""

    def write(self, data: bytes, size: int | None = None) -> None: ...
    def read(self, length: int, timeout_ms: int) -> bytes: ...  # raises DriverError(TIMEOUT)
    def send_feature(self, data: bytes, size: int | None = None) -> None: ...
    def get_feature(self, report_id: int, length: int) -> bytes: ...  # data[0] == report_id
    def get_input(self, report_id: int, length: int) -> bytes: ...


# -- timing ---------------------------------------------------------------------------------------
_sleep = None


def sleep_ms(ms: int) -> None:
    """Device-required pauses; tests swap this for a no-op via ``set_sleeper``."""
    import time

    (_sleep or time.sleep)(ms / 1000.0)


def set_sleeper(fn) -> None:
    global _sleep
    _sleep = fn


_clock = None


def monotonic_ms() -> int:
    """Monotonic milliseconds; tests swap in a fake clock via ``set_clock``."""
    import time

    return int((_clock or time.monotonic)() * 1000)


def set_clock(fn) -> None:
    global _clock
    _clock = fn


# -- C++-faithful arithmetic ------------------------------------------------------------------------
def cdiv(a: int, b: int) -> int:
    """Integer division truncating toward zero, as in C/C++."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def u8(v: int | float) -> int:
    """static_cast<uint8_t>: truncate floats, wrap integers modulo 256."""
    return int(v) & 0xFF


def map_int(value: int, in_min: int, in_max: int, out_min: int, out_max: int) -> int:
    """HeadsetControl's integer ``map()``: clamp, then ``out_min + norm*out_range/in_range``."""
    if in_max == in_min:
        return out_min
    clamped = max(min(value, max(in_min, in_max)), min(in_min, in_max))
    return out_min + cdiv((clamped - in_min) * (out_max - out_min), in_max - in_min)


def map_float(value: float, in_min: float, in_max: float, out_min: float, out_max: float) -> float:
    if in_max == in_min:
        return out_min
    clamped = max(min(value, max(in_min, in_max)), min(in_min, in_max))
    return out_min + ((clamped - in_min) * (out_max - out_min)) / (in_max - in_min)


def sidetone_to_discrete(level: int, n: int) -> int:
    if level == 0:
        return 0
    step = 128 // n
    for i in range(1, n):
        if level < step * i:
            return i - 1
    return n - 1


def sidetone_with_toggle(level: int, device_min: int, device_max: int) -> tuple[bool, int]:
    if level == 0:
        return False, 0
    return True, u8(device_min + cdiv((level - 1) * (device_max - device_min), 127))


def closest(value: int, allowed: tuple[int, ...]) -> int:
    best, diff = allowed[0], abs(value - allowed[0])
    for a in allowed[1:]:
        if abs(value - a) < diff:
            best, diff = a, abs(value - a)
    return best


def round_to_nearest(value: int, sorted_allowed: tuple[int, ...]) -> int:
    """C++ roundToNearest: lower_bound, ties go to the lower neighbour."""
    import bisect

    i = bisect.bisect_left(sorted_allowed, value)
    if i == 0:
        return sorted_allowed[0]
    if i == len(sorted_allowed):
        return sorted_allowed[-1]
    prev, nxt = sorted_allowed[i - 1], sorted_allowed[i]
    return prev if value - prev <= nxt - value else nxt


def voltage_to_percent(mv: int, points: tuple[tuple[int, int], ...]) -> int:
    """C++ voltageToPercent over ascending (percent, millivolts) calibration points."""
    if mv <= points[0][1]:
        return points[0][0]
    if mv >= points[-1][1]:
        return points[-1][0]
    for i in range(1, len(points)):
        if mv <= points[i][1]:
            (p0, v0), (p1, v1) = points[i - 1], points[i]
            return u8(map_int(mv, v0, v1, p0, p1))
    return points[-1][0]


def chatmix_from_level(level: int) -> Chatmix:
    """HeadsetControl's usual game/chat split for a 0-128 dial value."""
    game = 100 if level < 64 else map_int(level, 64, 128, 100, 0)
    chat = 100 if level > 64 else map_int(level, 0, 64, 0, 100)
    return Chatmix(level, game, chat)


# -- driver base ------------------------------------------------------------------------------------
class Driver:
    """Base class; subclasses override what their headset supports."""

    NAME: str = ""
    VENDOR: int = 0
    PIDS: tuple[int, ...] = ()
    CAPS: dict[Cap, CapDetail] = {}
    EQ: EqInfo | None = None
    PEQ: ParametricEqInfo | None = None
    EQ_PRESETS: tuple[tuple[str, tuple[float, ...]], ...] = ()
    WIRELESS: bool = True
    IMAGE: str = ""
    # False when the original derives read timeouts from the wall clock (the golden test
    # then compares bytes but not those clock-derived values)
    TIMING_EXACT: bool = True
    PRESET_COUNT: int = 0  # onboard EQ presets (names in EQ_PRESETS when HeadsetControl has them)
    EQ_FREQUENCIES: tuple[int, ...] = ()  # band centres for the UI; HeadsetControl does not define them
    EQ_FROM_DEVICE: bool = False  # EQ layout is read from the headset (see equalizer_info)

    def __init__(self, product_id: int = 0) -> None:
        self.product_id = product_id  # HeadsetControl's getMatchedProductId()
        self.state: dict = {}

    def capabilities(self) -> dict[Cap, CapDetail]:
        """Capabilities of this particular unit (some depend on the product id)."""
        return dict(self.CAPS)

    @classmethod
    def has(cls, cap: Cap) -> bool:
        return cap in cls.CAPS

    @classmethod
    def detail(cls, cap: Cap) -> CapDetail:
        return cls.CAPS.get(cap, CapDetail())

    # every method below mirrors a HIDDevice virtual of the same purpose
    def battery(self, h: Handle) -> Battery:
        raise DriverError(ErrorCode.NOT_SUPPORTED)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        raise DriverError(ErrorCode.NOT_SUPPORTED)

    def get_sidetone(self, h: Handle) -> Sidetone:
        raise DriverError(ErrorCode.NOT_SUPPORTED)

    def notification_sound(self, h: Handle, sound: int) -> None:
        raise DriverError(ErrorCode.NOT_SUPPORTED)

    def set_lights(self, h: Handle, on: bool) -> bool:
        raise DriverError(ErrorCode.NOT_SUPPORTED)

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        raise DriverError(ErrorCode.NOT_SUPPORTED)

    def chatmix(self, h: Handle) -> Chatmix:
        raise DriverError(ErrorCode.NOT_SUPPORTED)

    def set_voice_prompts(self, h: Handle, on: bool) -> None:
        raise DriverError(ErrorCode.NOT_SUPPORTED)

    def set_rotate_to_mute(self, h: Handle, on: bool) -> None:
        raise DriverError(ErrorCode.NOT_SUPPORTED)

    def set_eq_preset(self, h: Handle, preset: int) -> None:
        raise DriverError(ErrorCode.NOT_SUPPORTED)

    def set_equalizer(self, h: Handle, bands: list[float]) -> None:
        raise DriverError(ErrorCode.NOT_SUPPORTED)

    def set_parametric_eq(self, h: Handle, bands: list[ParametricBand]) -> None:
        raise DriverError(ErrorCode.NOT_SUPPORTED)

    def set_mic_mute_led(self, h: Handle, brightness: int) -> None:
        raise DriverError(ErrorCode.NOT_SUPPORTED)

    def set_mic_volume(self, h: Handle, volume: int) -> None:
        raise DriverError(ErrorCode.NOT_SUPPORTED)

    def set_volume_limiter(self, h: Handle, on: bool) -> None:
        raise DriverError(ErrorCode.NOT_SUPPORTED)

    def set_bt_when_powered_on(self, h: Handle, on: bool) -> None:
        raise DriverError(ErrorCode.NOT_SUPPORTED)

    def set_bt_call_volume(self, h: Handle, volume: int) -> None:
        raise DriverError(ErrorCode.NOT_SUPPORTED)

    def set_noise_filter(self, h: Handle, level: int) -> None:
        raise DriverError(ErrorCode.NOT_SUPPORTED)


@dataclass(slots=True)
class Registry:
    drivers: list[type[Driver]] = field(default_factory=list)

    def add(self, cls: type[Driver]) -> type[Driver]:
        self.drivers.append(cls)
        return cls

    def find(self, vendor: int, product: int) -> type[Driver] | None:
        for cls in self.drivers:
            if cls.VENDOR == vendor and product in cls.PIDS:
                return cls
        return None


REGISTRY = Registry()
register = REGISTRY.add


def driver(name: str, *, image: str = "", wireless: bool = True):
    """Class decorator: attach HeadsetControl's static data for ``name`` and register."""

    def wrap(cls: type[Driver]) -> type[Driver]:
        from .specs import SPECS

        spec = SPECS[name]
        cls.NAME = name
        cls.VENDOR = spec["vendor"]
        cls.PIDS = spec["pids"]
        cls.CAPS = spec["caps"]
        cls.EQ = spec["eq"]
        cls.PEQ = spec["peq"]
        cls.EQ_PRESETS = spec["presets"]
        cls.PRESET_COUNT = spec["preset_count"]
        cls.IMAGE = image
        cls.WIRELESS = wireless
        return register(cls)

    return wrap


def f32(x: float) -> float:
    """Round a Python float to IEEE single precision (C++ ``float``)."""
    import struct

    return struct.unpack("f", struct.pack("f", x))[0]


def fmul(a: float, b: float) -> float:
    """float * float evaluated in single precision."""
    return f32(f32(a) * f32(b))


def fadd(a: float, b: float) -> float:
    return f32(f32(a) + f32(b))


def fdiv(a: float, b: float) -> float:
    return f32(f32(a) / f32(b))


def lround(x: float) -> int:
    """C ``lround``/``llround``: nearest integer, halfway cases away from zero."""
    import math

    if x < 0:
        return -lround(-x)
    r = math.floor(x)
    return int(r) + (1 if x - r >= 0.5 else 0)
