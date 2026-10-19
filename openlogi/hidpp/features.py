"""Typed wrappers around the HID++ 2.0 features exposed by Logitech headsets."""

from __future__ import annotations

from dataclasses import dataclass, field

from .protocol import FeatureId, Hidpp20Device

DEVICE_TYPES = {
    0: "Keyboard",
    1: "Remote control",
    2: "Numpad",
    3: "Mouse",
    4: "Trackpad",
    5: "Trackball",
    6: "Presenter",
    7: "Receiver",
    8: "Headset",
    9: "Webcam",
    10: "Steering wheel",
    11: "Joystick",
    12: "Gamepad",
    13: "Dock",
    14: "Speaker",
    15: "Microphone",
}

FW_TYPES = {0: "Application", 1: "Bootloader", 2: "Hardware", 3: "Touchpad", 4: "Optical sensor",
            5: "Softdevice", 6: "RF companion MCU", 7: "Factory application"}


def _bcd(byte: int) -> int:
    return (byte >> 4) * 10 + (byte & 0x0F)


# -- 0x0003 Device firmware version -----------------------------------------------------
@dataclass(slots=True)
class FirmwareEntity:
    kind: int
    prefix: str
    number: int
    revision: int
    build: int
    active: bool
    transport_pid: int

    @property
    def version(self) -> str:
        return f"{self.prefix} {self.number:02d}.{self.revision:02d}.B{self.build:04d}"

    @property
    def kind_name(self) -> str:
        return FW_TYPES.get(self.kind, f"Type {self.kind}")


@dataclass(slots=True)
class DeviceIdentity:
    name: str = ""
    kind: int = -1
    unit_id: str = ""
    model_id: str = ""
    firmware: list[FirmwareEntity] = field(default_factory=list)
    protocol: tuple[int, int] = (0, 0)

    @property
    def kind_name(self) -> str:
        return DEVICE_TYPES.get(self.kind, "Unknown")


def read_firmware(dev: Hidpp20Device, ident: DeviceIdentity) -> None:
    reply = dev.call(FeatureId.DEVICE_FW_VERSION, 0)
    count = reply[0]
    ident.unit_id = reply[1:5].hex().upper()
    ident.model_id = reply[7:13].hex().upper()
    ident.firmware.clear()
    for entity in range(count):
        r = dev.call(FeatureId.DEVICE_FW_VERSION, 1, bytes((entity,)))
        prefix = bytes(r[1:4]).decode("ascii", "replace").strip("\0 ")
        ident.firmware.append(
            FirmwareEntity(
                kind=r[0] & 0x0F,
                prefix=prefix,
                number=_bcd(r[4]),
                revision=_bcd(r[5]),
                build=_bcd(r[6]) * 100 + _bcd(r[7]),
                active=bool(r[8] & 0x01),
                transport_pid=int.from_bytes(r[9:11], "big"),
            )
        )


# -- 0x0005 Device name ------------------------------------------------------------------
def read_name(dev: Hidpp20Device) -> tuple[str, int]:
    length = dev.call(FeatureId.DEVICE_NAME, 0)[0]
    chars = bytearray()
    while len(chars) < length:
        chunk = dev.call(FeatureId.DEVICE_NAME, 1, bytes((len(chars),)))
        chars += chunk[: length - len(chars)]
    kind = dev.call(FeatureId.DEVICE_NAME, 2)[0]
    return chars.decode("utf-8", "replace").rstrip("\0"), kind


# -- 0x8300 Sidetone ---------------------------------------------------------------------
class Sidetone:
    MIN, MAX = 0, 100

    def __init__(self, dev: Hidpp20Device):
        self.dev = dev

    def get(self) -> int:
        return self.dev.call(FeatureId.SIDETONE, 0)[0]

    def set(self, level: int) -> int:
        level = max(self.MIN, min(self.MAX, int(level)))
        return self.dev.call(FeatureId.SIDETONE, 1, bytes((level,)))[0]


# -- 0x8310 Onboard equalizer ------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class EqualizerInfo:
    band_count: int
    db_range: int
    capabilities: int
    frequencies: tuple[int, ...]

    @property
    def db_min(self) -> int:
        return -self.db_range

    @property
    def db_max(self) -> int:
        return self.db_range


class Equalizer:
    # setEQ "mode" byte; empirically 0x02 is the only value the PRO X accepts
    # (0x00/0x01/0x03 return INVALID_ARGUMENT).
    SET_MODE = 0x02

    def __init__(self, dev: Hidpp20Device):
        self.dev = dev
        self._info: EqualizerInfo | None = None

    def info(self) -> EqualizerInfo:
        if self._info is None:
            r = self.dev.call(FeatureId.EQUALIZER, 0)
            bands, db_range, caps = r[0], r[1], r[2]
            freqs: list[int] = []
            while len(freqs) < bands:
                chunk = self.dev.call(FeatureId.EQUALIZER, 1, bytes((len(freqs),)))
                start = chunk[0]
                values = [int.from_bytes(chunk[1 + 2 * i: 3 + 2 * i], "big") for i in range(7)]
                needed = bands - start
                freqs = freqs[:start] + values[: min(7, needed)]
            self._info = EqualizerInfo(bands, db_range, caps, tuple(freqs))
        return self._info

    def get(self) -> list[int]:
        info = self.info()
        r = self.dev.call(FeatureId.EQUALIZER, 2, b"\0")
        return [int.from_bytes(r[i: i + 1], "big", signed=True) for i in range(info.band_count)]

    def set(self, gains: list[int]) -> list[int]:
        info = self.info()
        if len(gains) != info.band_count:
            raise ValueError(f"expected {info.band_count} gains, got {len(gains)}")
        clamped = [max(info.db_min, min(info.db_max, round(g))) for g in gains]
        payload = bytes((self.SET_MODE,)) + b"".join(g.to_bytes(1, "big", signed=True) for g in clamped)
        self.dev.call(FeatureId.EQUALIZER, 3, payload)
        return clamped


# -- 0x1F20 ADC measurement (battery voltage) ---------------------------------------------
@dataclass(frozen=True, slots=True)
class AdcReading:
    millivolts: int
    flags: int

    @property
    def charging(self) -> bool:
        return bool(self.flags & 0x02)

    @property
    def charge_complete(self) -> bool:
        return bool(self.flags & 0x04)


class AdcBattery:
    def __init__(self, dev: Hidpp20Device):
        self.dev = dev

    def read(self) -> AdcReading:
        r = self.dev.call(FeatureId.ADC_MEASUREMENT, 0)
        return AdcReading(int.from_bytes(r[0:2], "big"), r[2])

    @staticmethod
    def parse_event(payload: bytes) -> AdcReading:
        return AdcReading(int.from_bytes(payload[0:2], "big"), payload[2])
