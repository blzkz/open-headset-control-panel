"""Office / VoIP headsets: Lenovo, Plantronics and Jabra (ported from HeadsetControl)."""

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
    hid_error,
    invalid,
    map_int,
    offline,
    protocol_error,
    sidetone_to_discrete,
    u8,
)

DEVICE_TIMEOUT = 5000


@driver("Lenovo Wireless VoIP Headset")
class LenovoWirelessVoip(Driver):
    MSG_SIZE = 61
    CMD_ID, RSP_ID = 0x24, 0x27

    def _request(self, h: Handle, cmd: int, payload: tuple[int, ...] = ()) -> bytes:
        h.send_feature(bytes((self.CMD_ID, cmd, *payload)).ljust(self.MSG_SIZE, b"\0"))
        resp = h.read(self.MSG_SIZE, DEVICE_TIMEOUT)
        if len(resp) != self.MSG_SIZE:
            raise hid_error("Failed to get input report")
        if resp[0] != self.RSP_ID:
            raise hid_error("Wrong response report ID")
        if resp[1] != cmd:
            raise hid_error("Wrong command response")
        if resp[2] != 0:
            raise offline("Headset not connected to wireless receiver")
        return resp

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        resp = self._request(h, 0x0E, (0x01, u8(map_int(level, 0, 128, 0, 5))))
        return Sidetone(resp[4])

    def battery(self, h: Handle) -> Battery:
        return Battery(self._request(h, 0x01)[7], BatteryStatus.AVAILABLE)

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        code = 0
        if minutes > 0:
            code = 1 if minutes <= 60 else 2 if minutes <= 120 else 3 if minutes <= 240 else 4
        resp = self._request(h, 0x08, (0x01, code))[4]
        return u8(min(60 * (1 << (resp - 1)), 255)) if resp else 0

    def set_voice_prompts(self, h: Handle, on: bool) -> None:
        self._request(h, 0x05, (0x01, 1 if on else 0))

    def set_rotate_to_mute(self, h: Handle, on: bool) -> None:
        self._request(h, 0x04, (0x01, 1 if on else 0))

    def set_eq_preset(self, h: Handle, preset: int) -> None:
        if preset >= 4:
            raise invalid("Device only supports presets 0-3")
        self._request(h, 0x02, (0x01, preset))

    def set_mic_mute_led(self, h: Handle, brightness: int) -> None:
        self._request(h, 0x15, (0x01, 0x00 if brightness > 0 else 0x01))

    def set_volume_limiter(self, h: Handle, on: bool) -> None:
        self._request(h, 0x07, (0x01, 0x02 if on else 0x00))


@driver("Plantronics Voyager 8200 UC (BT600)")
class PlantronicsBT600(Driver):
    REPORT_ID = 0x07
    ADDR_HEADSET, ADDR_HEADSET_REPLY, ADDR_DONGLE = 0x20, 0x02, 0x00
    OP_HELLO, OP_GET, OP_SET, OP_HELLO_ACK, OP_GET_REPLY = 0x01, 0x02, 0x05, 0x08, 0x03
    GRP_SETTINGS, ITEM_ONLINE_LED, ITEM_SIDETONE, ITEM_NOTIF_TONES = 0x04, 0x09, 0x10, 0x11
    GRP_BATTERY, ITEM_BATTERY = 0x0A, 0x1A
    GRP_CALL_AUDIO, ITEM_TWA_LIMIT = 0x0F, 0x0E

    @classmethod
    def _frame(cls, op: int, grp: int, item: int, value: int = 0, addr: int = ADDR_HEADSET) -> bytes:
        f = bytearray(64)
        f[0:6] = (cls.REPORT_ID, 0x01, 0x01, 0x10, 0x06 if op == cls.OP_GET else 0x07, addr)
        f[8:11] = (op, grp, item)
        if op == cls.OP_SET:
            f[11] = value
        return bytes(f)

    def _await(self, h: Handle, op: int, grp: int, item: int, addr: int = ADDR_HEADSET_REPLY) -> bytes:
        for _ in range(16):
            try:
                r = h.read(64, DEVICE_TIMEOUT).ljust(64, b"\0")
            except DriverError as exc:
                if exc.code is ErrorCode.TIMEOUT:
                    raise offline("Headset not connected to BT600 adapter (powered off or out of range)") from exc
                raise
            if (r[0] == self.REPORT_ID and r[1] == 0x01 and r[2] == 0x01 and r[3] == 0x10 and r[5] == addr
                    and r[9] == grp and r[10] == item and (op == 0 or r[8] == op)):
                return r
        raise protocol_error("No reply for the requested setting")

    def _open_session(self, h: Handle) -> None:
        h.write(bytes((0x06, 0x01)))
        h.write(self._frame(self.OP_HELLO, 0x01, 0x02, 0, self.ADDR_DONGLE))
        self._await(h, self.OP_HELLO_ACK, 0x01, 0x02, self.ADDR_DONGLE)
        h.write(self._frame(self.OP_HELLO, 0x01, 0x02, 0, self.ADDR_HEADSET))
        self._await(h, self.OP_HELLO_ACK, 0x01, 0x02, self.ADDR_HEADSET_REPLY)

    def _apply(self, h: Handle, grp: int, item: int, value: int) -> None:
        self._open_session(h)
        h.write(self._frame(self.OP_SET, grp, item, value))
        self._await(h, 0, grp, item)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        dev = sidetone_to_discrete(level, 3)
        self._apply(h, self.GRP_SETTINGS, self.ITEM_SIDETONE, dev)
        return Sidetone(dev)

    def set_lights(self, h: Handle, on: bool) -> bool:
        self._apply(h, self.GRP_SETTINGS, self.ITEM_ONLINE_LED, 1 if on else 0)
        return on

    def set_voice_prompts(self, h: Handle, on: bool) -> None:
        self._apply(h, self.GRP_SETTINGS, self.ITEM_NOTIF_TONES, 1 if on else 0)

    def set_volume_limiter(self, h: Handle, on: bool) -> None:
        self._apply(h, self.GRP_CALL_AUDIO, self.ITEM_TWA_LIMIT, 1 if on else 0)

    def battery(self, h: Handle) -> Battery:
        self._open_session(h)
        h.write(self._frame(self.OP_GET, self.GRP_BATTERY, self.ITEM_BATTERY))
        r = self._await(h, self.OP_GET_REPLY, self.GRP_BATTERY, self.ITEM_BATTERY)
        level, count = r[11], r[12]
        if count < 2:
            raise protocol_error("Unexpected battery reply format")
        return Battery(min(100, level * 100 // (count - 1)), BatteryStatus.CHARGING if r[13] else BatteryStatus.AVAILABLE)


class _JabraGNP(Driver):
    """Jabra GN Protocol over HID report 0x05 (Link dongles, Evolve2 USB)."""

    ADDRESS = 0x04
    TYPE_GET, TYPE_SET, TYPE_RESPONSE = 0x01, 0x02, 0x03
    CMD_ACK, CMD_NACK = 0xFF, 0xFE
    CMD_STATUS, CMD_CONFIG = 0x12, 0x13
    SIDETONE_DB = (-9, -6, -3, 0, 3, 6)

    def __init__(self, product_id: int = 0) -> None:
        super().__init__(product_id)
        self._seq = 0

    @staticmethod
    def _nack(reason: int) -> DriverError:
        if reason in (0xF3, 0xF4, 0xF5):
            return offline("Headset not connected to the Jabra dongle (powered off or out of range)")
        if reason in (0xF6, 0xFF):
            return DriverError(ErrorCode.NOT_SUPPORTED, "The paired Jabra headset does not implement this setting")
        return protocol_error("Jabra device rejected the command (NACK)")

    def _request(self, h: Handle, typ: int, cmd: int, sub: int, payload: tuple[int, ...] = ()) -> bytes:
        seq = self._seq
        self._seq = (self._seq + 1) & 0xFF
        f = bytearray(64)
        f[0:7] = (0x05, self.ADDRESS, 0x00, seq, ((typ << 6) | (6 + len(payload))) & 0xFF, cmd, sub)
        f[7:7 + len(payload)] = payload
        h.write(bytes(f))
        for _ in range(16):
            try:
                r = h.read(64, DEVICE_TIMEOUT).ljust(64, b"\0")
            except DriverError as exc:
                if exc.code is ErrorCode.TIMEOUT:
                    raise offline("No reply from the Jabra dongle") from exc
                raise
            if r[0] != 0x05 or (r[4] >> 6) != self.TYPE_RESPONSE or r[3] != seq:
                continue
            if r[5] == self.CMD_NACK:
                raise self._nack(r[6])
            return r
        raise protocol_error("No reply for the requested command")

    def _set(self, h: Handle, cmd: int, sub: int, *payload: int) -> None:
        if self._request(h, self.TYPE_SET, cmd, sub, payload)[5] != self.CMD_ACK:
            raise protocol_error("Jabra device did not acknowledge the setting")

    @staticmethod
    def _payload_len(r: bytes) -> int:
        n = r[4] & 0x3F
        return n - 6 if n > 6 else 0

    def _read_sidetone(self, h: Handle) -> tuple[bool, int]:
        r = self._request(h, self.TYPE_GET, self.CMD_CONFIG, 0x7C)
        if self._payload_len(r) < 2:
            raise protocol_error("Unexpected Jabra sidetone reply format")
        db = r[8] - 256 if r[8] > 127 else r[8]
        if db not in self.SIDETONE_DB:
            raise protocol_error("Invalid Jabra sidetone value")
        return r[7] == 0x01, self.SIDETONE_DB.index(db)

    def _sidetone_result(self, muted: bool, index: int) -> Sidetone:
        n = len(self.SIDETONE_DB)
        step = 128 // n
        return Sidetone(0 if muted else step * min(index, n - 1) + step // 2, index)

    def battery(self, h: Handle) -> Battery:
        r = self._request(h, self.TYPE_GET, self.CMD_STATUS, 0x02)
        if self._payload_len(r) < 2:
            raise protocol_error("Unexpected battery reply format")
        return Battery(min(100, r[8]), BatteryStatus.CHARGING if r[7] & 0x01 else BatteryStatus.AVAILABLE)

    def set_sidetone(self, h: Handle, level: int) -> Sidetone:
        _muted, index = self._read_sidetone(h)
        muted = level == 0
        if not muted:
            index = sidetone_to_discrete(level, len(self.SIDETONE_DB))
        self._set(h, self.CMD_CONFIG, 0x7C, 0x01 if muted else 0x00, self.SIDETONE_DB[index] & 0xFF)
        return self._sidetone_result(muted, index)

    def get_sidetone(self, h: Handle) -> Sidetone:
        return self._sidetone_result(*self._read_sidetone(h))

    def set_lights(self, h: Handle, on: bool) -> bool:
        self._set(h, self.CMD_STATUS, 0x08, 1 if on else 0, 0x00)
        return on

    def set_voice_prompts(self, h: Handle, on: bool) -> None:
        self._set(h, self.CMD_CONFIG, 0x3A, 0x01 if on else 0x00)

    def set_inactive_time(self, h: Handle, minutes: int) -> int:
        units = u8((minutes + 2) // 5)
        self._set(h, self.CMD_CONFIG, 0x90, units)
        return u8(units * 5)

    def set_volume_limiter(self, h: Handle, on: bool) -> None:
        self._set(h, self.CMD_CONFIG, 0x01, 0x03 if on else 0x00)


@driver("Jabra Link 380 (paired headset)")
class JabraLink380(_JabraGNP):
    ADDRESS = 0x04


@driver("Jabra Link 390 (paired headset)")
class JabraLink390(_JabraGNP):
    ADDRESS = 0x04


@driver("Jabra Evolve2 65 Flex (USB)")
class JabraEvolve265Flex(_JabraGNP):
    ADDRESS = 0x08
