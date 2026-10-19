"""Minimal, correct HID++ 2.0 implementation.

Unlike HeadsetControl, feature indexes are never hard-coded: they are resolved
at runtime through the IRoot feature (0x0000), so firmware revisions that move
features around keep working.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum

from .transport import HidrawTransport

REPORT_SHORT = 0x10
REPORT_LONG = 0x11
REPORT_LEN = {REPORT_SHORT: 7, REPORT_LONG: 20}

DEVICE_INDEX_SELF = 0xFF  # corded / dongle-attached device addressed directly
SOFTWARE_ID_GUI = 0x0A  # non-zero so replies never get confused with notifications
SOFTWARE_ID_CLI = 0x0C  # distinct id lets a running GUI recognise (and mirror) CLI changes

DEFAULT_TIMEOUT = 0.8


class FeatureId(IntEnum):
    ROOT = 0x0000
    FEATURE_SET = 0x0001
    DEVICE_FW_VERSION = 0x0003
    DEVICE_NAME = 0x0005
    DEVICE_FRIENDLY_NAME = 0x0007
    BATTERY_STATUS = 0x1000
    UNIFIED_BATTERY = 0x1004
    ADC_MEASUREMENT = 0x1F20
    SIDETONE = 0x8300
    EQUALIZER = 0x8310
    HEADSET_OUT = 0x8320

    @classmethod
    def label(cls, value: int) -> str:
        try:
            return cls(value).name.replace("_", " ").title()
        except ValueError:
            return f"Unknown 0x{value:04X}"


class Hidpp20ErrorCode(IntEnum):
    NO_ERROR = 0
    UNKNOWN = 1
    INVALID_ARGUMENT = 2
    OUT_OF_RANGE = 3
    HARDWARE_ERROR = 4
    LOGITECH_INTERNAL = 5
    INVALID_FEATURE_INDEX = 6
    INVALID_FUNCTION_ID = 7
    BUSY = 8
    UNSUPPORTED = 9


class HidppError(Exception):
    """Base error for HID++ failures."""


class HidppTimeout(HidppError):
    """No reply: typically the headset is switched off or out of range."""


class HidppDeviceError(HidppError):
    def __init__(self, code: int, feature_index: int, function: int, hidpp10: bool = False):
        self.code = code
        self.feature_index = feature_index
        self.function = function
        self.hidpp10 = hidpp10
        if hidpp10:
            name = f"HID++1.0 error 0x{code:02X}"
        else:
            try:
                name = Hidpp20ErrorCode(code).name
            except ValueError:
                name = f"0x{code:02X}"
        super().__init__(f"feature[{feature_index}] fn{function}: {name}")

    @property
    def device_unreachable(self) -> bool:
        # HID++ 1.0 errors from a receiver mean the paired device is not there.
        return self.hidpp10


class FeatureNotSupported(HidppError):
    def __init__(self, feature_id: int):
        super().__init__(f"feature 0x{feature_id:04X} not supported")
        self.feature_id = feature_id


@dataclass(frozen=True, slots=True)
class Message:
    report_id: int
    device_index: int
    feature_index: int
    function: int
    sw_id: int
    payload: bytes

    @classmethod
    def parse(cls, raw: bytes) -> Message | None:
        if len(raw) < 4 or raw[0] not in REPORT_LEN:
            return None
        return cls(raw[0], raw[1], raw[2], raw[3] >> 4, raw[3] & 0x0F, bytes(raw[4:]))

    @property
    def is_notification(self) -> bool:
        return self.sw_id == 0


NotificationHandler = Callable[[Message], None]


class Hidpp20Device:
    """A HID++ 2.0 device reachable through a hidraw node."""

    def __init__(
        self,
        transport: HidrawTransport,
        device_index: int = DEVICE_INDEX_SELF,
        on_notification: NotificationHandler | None = None,
        sw_id: int = SOFTWARE_ID_GUI,
    ):
        self.transport = transport
        self.device_index = device_index
        self.sw_id = sw_id & 0x0F
        self.on_notification = on_notification
        self._lock = threading.RLock()
        self._feature_cache: dict[int, int] = {FeatureId.ROOT: 0}

    # -- low level -----------------------------------------------------------------
    def _build(self, feature_index: int, function: int, params: bytes) -> bytes:
        if len(params) > 16:
            raise ValueError("HID++ long message parameters are limited to 16 bytes")
        head = bytes((REPORT_LONG, self.device_index, feature_index, (function << 4) | self.sw_id))
        return (head + params).ljust(REPORT_LEN[REPORT_LONG], b"\0")

    def request(
        self,
        feature_index: int,
        function: int,
        params: bytes | bytearray | list[int] = b"",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> bytes:
        """Send a request and return the 16-byte reply payload."""
        params = bytes(params)
        with self._lock:
            self.transport.write(self._build(feature_index, function, params))
            deadline = time.monotonic() + timeout
            while (remaining := deadline - time.monotonic()) > 0:
                raw = self.transport.read(remaining)
                if raw is None:
                    break
                msg = Message.parse(raw)
                if msg is None:
                    continue  # consumer-control / other reports on the same node
                if msg.device_index != self.device_index:
                    self._dispatch(msg)
                    continue
                # HID++ 2.0 error: [0x11, dev, 0xFF, featIdx, fn|sw, code]
                if msg.feature_index == 0xFF and raw[3] == feature_index and (raw[4] >> 4) == function:
                    raise HidppDeviceError(raw[5], feature_index, function)
                # HID++ 1.0 error (receiver answering for an absent device)
                if msg.feature_index == 0x8F and raw[3] == feature_index:
                    raise HidppDeviceError(raw[5], feature_index, function, hidpp10=True)
                if (
                    msg.feature_index == feature_index
                    and msg.function == function
                    and msg.sw_id == self.sw_id
                ):
                    return msg.payload
                self._dispatch(msg)
        raise HidppTimeout(f"no reply to feature[{feature_index}] fn{function}")

    def _dispatch(self, msg: Message) -> None:
        if self.on_notification is not None:
            try:
                self.on_notification(msg)
            except Exception:  # never let a UI callback break the protocol loop
                pass

    def poll_notifications(self, timeout: float) -> None:
        """Read unsolicited reports for up to ``timeout`` seconds."""
        with self._lock:
            raw = self.transport.read(timeout)
            while raw is not None:
                msg = Message.parse(raw)
                if msg is not None:
                    self._dispatch(msg)
                raw = self.transport.read(0)

    # -- IRoot / IFeatureSet -------------------------------------------------------
    def ping(self, timeout: float = DEFAULT_TIMEOUT) -> tuple[int, int]:
        """Return the HID++ protocol version (major, minor)."""
        reply = self.request(0, 1, b"\0\0\x5A", timeout=timeout)
        return reply[0], reply[1]

    def feature_index(self, feature_id: int) -> int:
        if feature_id in self._feature_cache:
            index = self._feature_cache[feature_id]
        else:
            reply = self.request(0, 0, feature_id.to_bytes(2, "big"))
            index = reply[0]
            if index == 0:
                index = -1
            self._feature_cache[feature_id] = index
        if index < 0:
            raise FeatureNotSupported(feature_id)
        return index

    def has_feature(self, feature_id: int) -> bool:
        try:
            self.feature_index(feature_id)
            return True
        except FeatureNotSupported:
            return False

    def call(
        self, feature_id: int, function: int, params: bytes | list[int] = b"", timeout: float = DEFAULT_TIMEOUT
    ) -> bytes:
        return self.request(self.feature_index(feature_id), function, params, timeout)

    def enumerate_features(self) -> list[tuple[int, int, int, int]]:
        """Return ``[(index, feature_id, type_flags, version), ...]``."""
        fs = self.feature_index(FeatureId.FEATURE_SET)
        count = self.request(fs, 0)[0]
        features = [(0, FeatureId.ROOT, 0, 0)]
        for idx in range(1, count + 1):
            reply = self.request(fs, 1, bytes((idx,)))
            fid = int.from_bytes(reply[0:2], "big")
            features.append((idx, fid, reply[2], reply[3]))
            self._feature_cache.setdefault(fid, idx)
        return features
