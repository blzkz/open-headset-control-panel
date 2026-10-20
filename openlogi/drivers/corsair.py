"""Corsair Void / Virtuoso headsets (ported from HeadsetControl)."""

from __future__ import annotations

from .base import (
    Battery,
    BatteryStatus,
    Driver,
    DriverError,
    ErrorCode,
    Handle,
    Sidetone,
    driver,
    map_int,
    offline,
    protocol_error,
    u8,
)

DEVICE_TIMEOUT = 5000


def _pad(data, size: int) -> bytes:
    return bytes(data)[:size].ljust(size, b"\0")


@driver("Corsair Headset Device")
class CorsairVoid(Driver):
    """Void / Void RGB / Void Pro / HS70 family."""

    def battery(self, h: Handle) -> Battery:
        h.write(bytes((0xC9, 0x64)))
        resp = h.read(5, DEVICE_TIMEOUT)
        status_byte = resp[4]
        if status_byte == 0:
            raise offline("Headset not connected to wireless receiver")
        if status_byte in (4, 5):
            status = BatteryStatus.CHARGING
        elif status_byte in (1, 2):
            status = BatteryStatus.AVAILABLE
        else:
            raise protocol_error(f"Unknown status byte: {status_byte}")
        return Battery(resp[2] & 0x7F, status)  # bit 7: microphone boom up

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        mapped = u8(map_int(level, 0, 128, 200, 255))
        h.send_feature(_pad((0xFF, 0x0B, 0x00, 0xFF, 0x04, 0x0E, 0xFF, 0x05, 0x01, 0x04, 0x00, mapped), 64))
        return Sidetone(level)

    def set_lights(self, h: Handle, on: bool) -> bool:
        h.write(bytes((0xC8, 0x00 if on else 0x01, 0x00)))
        return on

    def notification_sound(self, h: Handle, sound: int) -> None:
        h.write(_pad((0xCA, 0x02, sound), 5), 3)


RECEIVER, HEADSET = 0x08, 0x09
READ_SIZE, WRITE_SIZE = 64, 65


@driver("Corsair Wireless V2 Headset Device")
class CorsairVoidV2W(Driver):
    """Void v2 Wireless / Void Elite v2 family (endpoint protocol)."""

    def _w(self, h: Handle, *head: int) -> None:
        h.write(_pad(head, WRITE_SIZE), WRITE_SIZE)

    def _flush(self, h: Handle) -> None:
        while True:
            try:
                h.read(READ_SIZE, 5)
            except DriverError as exc:
                if exc.code is ErrorCode.TIMEOUT:
                    return
                raise

    def _read_len(self, h: Handle) -> int:
        try:
            return len(h.read(READ_SIZE, DEVICE_TIMEOUT))
        except DriverError:
            return 0  # valueOr(0)

    def _wake(self, h: Handle) -> int:
        self._w(h, 0x00, 0x02, RECEIVER, 0x02, 0x13)
        self._w(h, 0x00, 0x02, RECEIVER, 0x02, 0x12)
        self._flush(h)
        self._w(h, 0x00, 0x02, HEADSET, 0x02, 0x12)
        return self._read_len(h)

    def _initialize(self, h: Handle) -> int:
        self._w(h, 0x00, 0x02, RECEIVER, 0x02, 0x13)
        self._w(h, 0x00, 0x02, RECEIVER, 0x01, 0x03, 0x00, 0x02)
        self._w(h, 0x00, 0x02, RECEIVER, 0x02, 0x12)
        self._w(h, 0x00, 0x02, HEADSET, 0x01, 0x03, 0x00, 0x02)
        self._flush(h)
        self._w(h, 0x00, 0x02, HEADSET, 0x02, 0x12)
        return self._read_len(h)

    def battery(self, h: Handle) -> Battery:
        if self._wake(h) == 0:
            raise offline("Headset not connected to wireless receiver")
        self._flush(h)
        request = (0x00, 0x02, HEADSET, 0x02, 0x0F)
        self._w(h, *request)
        resp = h.read(READ_SIZE, DEVICE_TIMEOUT)
        vendor_level = (resp[4] | (resp[5] << 8)) & 0xFFFF
        attempts = 0
        while attempts < 3 and (vendor_level == 0 or vendor_level > 1000):
            self._w(h, *request)
            resp = h.read(READ_SIZE, DEVICE_TIMEOUT)
            vendor_level = (resp[4] | (resp[5] << 8)) & 0xFFFF
            attempts += 1
        if vendor_level == 0 or vendor_level > 1000:
            return Battery(-1, BatteryStatus.UNAVAILABLE)
        try:  # microphone boom state: best effort, as in the original
            self._w(h, 0x00, 0x02, HEADSET, 0x02, 0xA6)
            h.read(READ_SIZE, DEVICE_TIMEOUT)
        except DriverError:
            pass
        return Battery(vendor_level // 10, BatteryStatus.AVAILABLE)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        mapped = map_int(level, 0, 128, 0, 1000) & 0xFFFF
        value = ((mapped + 5) // 10) * 10  # round_to_multiples(mapped, 10)
        if self._initialize(h) == 0:
            raise offline("Headset not connected to wireless receiver")
        anc = bytearray(_pad((0x00, 0x02, HEADSET, 0x01, 0xD1), WRITE_SIZE))
        side = bytearray(_pad((0x00, 0x02, HEADSET, 0x01, 0x46), WRITE_SIZE))
        if level == 0:
            side[6] = 0x01
            anc[6] = 0x01
        h.write(bytes(anc), WRITE_SIZE)
        h.write(bytes(side), WRITE_SIZE)
        self._w(h, 0x00, 0x02, HEADSET, 0x01, 0x47, 0, value & 0xFF, (value >> 8) & 0xFF)
        return Sidetone(level)

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        minutes = min(minutes, 90)
        if self._initialize(h) == 0:
            raise offline("Headset not connected to wireless receiver")
        self._w(h, 0x00, 0x02, HEADSET, 0x01, 0x0D, 0x00, 0x00 if minutes == 0 else 0x01)
        if minutes > 0:
            ms = minutes * 60 * 1000
            self._w(h, 0x00, 0x02, HEADSET, 0x01, 0x0E, 0x00, ms & 0xFF, (ms >> 8) & 0xFF, (ms >> 16) & 0xFF,
                    (ms >> 24) & 0xFF)
        return minutes


@driver("Corsair Virtuoso XT/SE")
class CorsairVirtuosoXT(Driver):
    def battery(self, h: Handle) -> Battery:
        h.write(bytes((0x02, 0x00)))
        attempt = 0
        while True:
            resp = h.read(64, DEVICE_TIMEOUT)
            if resp[0] == 0x01:
                break
            attempt += 1
            if attempt >= 8:
                raise protocol_error(f"Unexpected report ID: 0x{resp[0]:02x}")
        if resp[1] == 0x00:
            raise offline("Headset not connected to receiver")
        if resp[2] > 100:
            raise protocol_error(f"Battery percentage out of range: {resp[2]}")
        return Battery(resp[2], BatteryStatus.AVAILABLE)
