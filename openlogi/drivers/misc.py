"""Roccat, MCHOSE and Sony INZONE headsets (ported from HeadsetControl)."""

from __future__ import annotations

from .base import (
    Battery,
    BatteryStatus,
    Driver,
    DriverError,
    ErrorCode,
    Handle,
    driver,
    invalid,
    monotonic_ms,
    sleep_ms,
    timeout,
)

DEVICE_TIMEOUT = 5000


def _pkt(size: int, *head: int) -> bytes:
    return bytes(head).ljust(size, b"\0")


class _Roccat(Driver):
    PACKET = 64
    DELAY_MS = 75

    def _send(self, h: Handle, data: bytes, read_len: int = 0) -> None:
        h.write(data)
        if read_len:
            h.read(read_len, DEVICE_TIMEOUT)
        sleep_ms(self.DELAY_MS)

    _try = _send  # every step propagates its error, as in the original


@driver("ROCCAT Elo 7.1 Air")
class RoccatElo71Air(_Roccat):
    def set_lights(self, h: Handle, on: bool) -> bool:
        v = 0xFF if on else 0
        for pkt in (_pkt(64, 0xFF, 0x02), _pkt(64, 0xFF, 0x03, 0x00, 0x01, 0x00, 0x01),
                    _pkt(64, 0xFF, 0x04, 0x00, 0x00, v, v, v), _pkt(64, 0xFF, 0x01)):
            self._try(h, pkt)
        return on

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        if minutes > 60:
            raise invalid("Device only supports up to 60 minutes")
        seconds = (minutes * 60) & 0xFFFF
        self._try(h, _pkt(64, 0xA1, 0x04, 0x06, 0x54, (seconds >> 8) & 0xFF, seconds & 0xFF), read_len=64)
        return minutes


@driver("ROCCAT Elo 7.1 USB")
class RoccatElo71USB(_Roccat):
    PACKET = 16
    DELAY_MS = 40

    def set_lights(self, h: Handle, on: bool) -> bool:
        self._try(h, _pkt(16, 0xFF, 0x02))
        self._try(h, _pkt(16, 0xFF, 0x03, 0x00, 0x01))
        self._try(h, _pkt(16, 0xFF, 0x04, 0x00, 0x00, 0xF4) if on else _pkt(16, 0xFF, 0x04))
        self._try(h, _pkt(16, 0xFF, 0x01))
        return on


@driver("MCHOSE X9 Wireless")
class MchoseX9(Driver):
    TIMING_EXACT = False  # read timeouts count down from a wall-clock deadline

    def battery(self, h: Handle) -> Battery:
        h.write(_pkt(64, 0x55, 0x65, 0x01))
        deadline = monotonic_ms() + DEVICE_TIMEOUT
        while True:
            remaining = deadline - monotonic_ms()
            if remaining <= 0:
                raise timeout("No battery reply from the dongle")
            resp = h.read(64, remaining)
            if len(resp) >= 4 and resp[0] == 0x55 and resp[1] == 0x65 and resp[3] == 0x02:
                return Battery(resp[2], BatteryStatus.AVAILABLE)


@driver("Sony INZONE Buds")
class SonyInzoneBuds(Driver):
    def battery(self, h: Handle) -> Battery:
        for _ in range(45):
            try:
                resp = h.read(64, DEVICE_TIMEOUT)
            except DriverError as exc:
                if exc.code is ErrorCode.TIMEOUT:
                    continue
                raise
            if resp[1] == 0x12 and resp[2] == 0x04:
                return Battery(min(resp[16], resp[14]), BatteryStatus.AVAILABLE)
        raise timeout("No battery report received within attempt limit")
