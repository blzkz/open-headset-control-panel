"""Roccat, MCHOSE and Sony INZONE headsets (ported from HeadsetControl)."""

from __future__ import annotations

from .base import (
    Battery,
    BatteryStatus,
    Chatmix,
    Driver,
    DriverError,
    ErrorCode,
    Handle,
    driver,
    invalid,
    map_int,
    monotonic_ms,
    offline,
    protocol_error,
    Sidetone,
    sleep_ms,
    timeout,
    u8,
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


@driver("Sony INZONE H5")
class SonyInzoneH5(Driver):
    """Sony HCI-over-HID vendor events (report 0x02)."""

    ADDR_PC_TO_RX = 0x41
    GET, SET, RET, NTFY, NTFY_ACTIVE = 0x01, 0x02, 0x10, 0x20, 0xA0
    EID_BATTERY, EID_BALANCE, EID_SIDETONE, EID_MIC_VOLUME = 0x04, 0x22, 0x23, 0x24
    BALANCE_MAX, SIDETONE_MAX, MIC_VOL_MAX = 90, 50, 50

    def __init__(self, product_id: int = 0) -> None:
        super().__init__(product_id)
        self._tid = 0

    @staticmethod
    def _build(address: int, event_id: int, event_type: int, tid: int, payload: bytes) -> bytes:
        n = len(payload)
        buf = bytearray(64)
        buf[0:13] = (0x02, (12 + n) & 0xFF, 0x01, 0x00, 0xFC, (8 + n) & 0xFF, 0x96, 0xC3, address, event_id,
                     event_type, tid & 0xFF, (tid >> 8) & 0xFF)
        buf[13:13 + n] = payload
        buf[13 + n] = sum(buf[6:13 + n]) & 0xFF
        return bytes(buf)

    @staticmethod
    def _parse(buf: bytes):
        if buf[0] != 0x02:
            return None
        hid_length = buf[1]
        if hid_length < 12 or hid_length > 62:
            return None
        if buf[2] != 0x04 or buf[3] != 0xFF or buf[5] != 0x00 or buf[6] != 0x96 or buf[7] != 0xC3:
            return None
        if (buf[8] >> 4) != 0x1:
            return None
        if sum(buf[5:hid_length + 1]) & 0xFF != buf[hid_length + 1]:
            return None
        return buf[9], buf[10], buf[11] | (buf[12] << 8), bytes(buf[13:hid_length + 1])

    def _exchange(self, h: Handle, event_id: int, event_type: int, payload: bytes = b"") -> bytes:
        self._tid = (self._tid + 1) & 0xFFFF
        if self._tid <= 1:
            self._tid = 2
        tid = self._tid
        h.write(self._build(self.ADDR_PC_TO_RX, event_id, event_type, tid, payload))
        want = self.NTFY if event_type == self.SET else self.RET
        for _ in range(10):
            try:
                resp = h.read(64, 500)
            except DriverError as exc:
                if exc.code is ErrorCode.TIMEOUT:
                    continue
                raise
            parsed = self._parse(resp.ljust(64, b"\0"))
            if parsed and parsed[0] == event_id and parsed[2] == tid and parsed[1] in (want, self.NTFY_ACTIVE):
                return parsed[3]
        raise timeout(f"No response for event_id 0x{event_id:02x} (TID {tid})")

    def battery(self, h: Handle) -> Battery:
        payload = self._exchange(h, self.EID_BATTERY, self.GET)
        if len(payload) < 2:
            raise protocol_error("BATTERY_INFO payload too short")
        charger, percent = payload[0], payload[1]
        if percent == 0xFF:
            raise offline("Headset reports battery=0xFF (offline)")
        if percent > 100:
            raise protocol_error(f"Invalid battery percent: {percent}")
        return Battery(percent, BatteryStatus.CHARGING if charger else BatteryStatus.AVAILABLE)

    def chatmix(self, h: Handle) -> Chatmix:
        payload = self._exchange(h, self.EID_BALANCE, self.GET)
        if not payload:
            raise protocol_error("GAME_CHAT_MIX_BALANCE payload empty")
        balance = payload[0]
        if balance == 0xFF:
            raise offline("Headset offline")
        if balance > self.BALANCE_MAX:
            raise protocol_error(f"Invalid balance: {balance}")
        chat = balance * 100 // self.BALANCE_MAX
        return Chatmix(map_int(balance, 0, self.BALANCE_MAX, 0, 128), 100 - chat, chat)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        dev = u8(map_int(level, 0, 128, 0, self.SIDETONE_MAX))
        self._exchange(h, self.EID_SIDETONE, self.SET, bytes((dev, 0xFF)))
        return Sidetone(level)

    def set_mic_volume(self, h: Handle, volume: int) -> None:
        dev = u8(map_int(volume, 0, 128, 0, self.MIC_VOL_MAX))
        self._exchange(h, self.EID_MIC_VOLUME, self.SET, bytes((0x00, dev, 0xFF)))
