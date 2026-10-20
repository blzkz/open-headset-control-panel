"""Linux hidraw I/O for the ported drivers: enumeration like hidapi, and a ``Handle``.

Node selection follows HeadsetControl's ``get_hid_path``: hidapi lists one entry per
collection usage pair of every hidraw node, in udev (sysfs path) order; a capability
picks the first entry matching its usage page + usage when both are set, else the first
entry (interface 0) or the first on the requested USB interface.
"""

from __future__ import annotations

import errno
import fcntl
import os
import select
from dataclasses import dataclass
from pathlib import Path

from ..hidpp.transport import TransportError
from .base import CapDetail, DriverError, ErrorCode

SYSFS_HIDRAW = Path("/sys/class/hidraw")


@dataclass(frozen=True, slots=True)
class HidEntry:
    path: str
    vendor_id: int
    product_id: int
    interface: int  # USB bInterfaceNumber, -1 when not USB
    usage_page: int
    usage: int
    name: str
    syspath: str


def _item_size(desc: bytes, pos: int) -> tuple[int, int] | None:
    key = desc[pos]
    if key & 0xF0 == 0xF0:  # long item
        if pos + 1 >= len(desc):
            return None
        return desc[pos + 1], 3
    return (0, 1, 2, 4)[key & 0x03], 1


def _value(desc: bytes, pos: int, size: int) -> int:
    return int.from_bytes(desc[pos:pos + size], "little") if size else 0


def collection_usages(desc: bytes) -> list[tuple[int, int]]:
    """Usage page / usage pairs as hidapi's linux backend reports them (get_next_hid_usage)."""
    pairs: list[tuple[int, int]] = []
    pos = 0
    usage_page = usage = 0
    usage_found = False
    while pos < len(desc):
        size = _item_size(desc, pos)
        if size is None:
            break
        data_len, key_size = size
        cmd = desc[pos] & 0xFC
        if cmd == 0x04:  # Usage Page (global)
            usage_page = _value(desc, pos + 1, data_len)
        elif cmd == 0x08:  # Usage (local)
            if data_len == 4:  # extended usage: page in the high half
                usage_page = _value(desc, pos + 3, 2)
                usage = _value(desc, pos + 1, 2)
            else:
                usage = _value(desc, pos + 1, data_len)
            usage_found = True
        elif cmd == 0xA0:  # Collection (main)
            if usage_found:
                pairs.append((usage_page, usage))
            usage_found = False
        pos += data_len + key_size
    if not pairs and usage_found:  # no collection at all: the lone usage still counts
        pairs.append((usage_page, usage))
    return pairs or [(0, 0)]


def _uevent(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        key, _, value = line.partition("=")
        out[key] = value
    return out


def _interface_number(hid_dev: Path) -> int:
    try:
        return int((hid_dev.resolve().parent / "bInterfaceNumber").read_text().strip(), 16)
    except (OSError, ValueError):
        return -1


def enumerate_entries(vendor_id: int | None = None, product_id: int | None = None) -> list[HidEntry]:
    entries: list[HidEntry] = []
    if not SYSFS_HIDRAW.is_dir():
        return entries
    nodes = []
    for node in SYSFS_HIDRAW.iterdir():
        try:
            nodes.append((os.path.realpath(node), node))
        except OSError:
            continue
    for syspath, node in sorted(nodes):
        try:
            ue = _uevent(node / "device" / "uevent")
            _bus, vid, pid = ue["HID_ID"].split(":")
            vid_i, pid_i = int(vid, 16), int(pid, 16)
        except (OSError, KeyError, ValueError):
            continue
        if (vendor_id is not None and vid_i != vendor_id) or (product_id is not None and pid_i != product_id):
            continue
        try:
            desc = (node / "device" / "report_descriptor").read_bytes()
        except OSError:
            desc = b""
        iface = _interface_number(node / "device")
        for page, usage in collection_usages(desc):
            entries.append(HidEntry(f"/dev/{node.name}", vid_i, pid_i, iface, page, usage, ue.get("HID_NAME", ""),
                                    syspath))
    return entries


def select_path(entries: list[HidEntry], detail: CapDetail) -> str | None:
    """HeadsetControl's ``get_hid_path`` over already-enumerated entries of one device."""
    if detail.usage_page and detail.usage:
        for e in entries:
            if e.usage_page == detail.usage_page and e.usage == detail.usage:
                return e.path
    for e in entries:
        if not detail.interface or e.interface == detail.interface:
            return e.path
    return None


# -- I/O ----------------------------------------------------------------------------------------

def _ioc(direction: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord("H") << 8) | nr


def HIDIOCSFEATURE(n: int) -> int:  # noqa: N802 - kernel macro names
    return _ioc(3, 0x06, n)


def HIDIOCGFEATURE(n: int) -> int:  # noqa: N802
    return _ioc(3, 0x07, n)


def HIDIOCGINPUT(n: int) -> int:  # noqa: N802
    return _ioc(3, 0x0A, n)


_GONE = (errno.ENODEV, errno.ENOENT, errno.ENXIO, errno.EIO, errno.ESHUTDOWN, errno.EPIPE)


class HidHandle:
    """hidapi-like access to one hidraw node (``Handle`` protocol of the drivers)."""

    def __init__(self, path: str):
        self.path = path
        try:
            self._fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError as exc:
            raise TransportError(exc.errno, f"{path}: {exc.strerror}") from exc
        self._poll = select.poll()
        self._poll.register(self._fd, select.POLLIN | select.POLLERR | select.POLLHUP)

    def close(self) -> None:
        if self._fd >= 0:
            try:
                os.close(self._fd)
            finally:
                self._fd = -1

    def _fail(self, exc: OSError, what: str) -> Exception:
        if exc.errno in _GONE:
            return TransportError(exc.errno, f"{self.path}: device disconnected")
        return DriverError(ErrorCode.HID, f"{what} {self.path}: {exc.strerror}")

    def write(self, data: bytes, size: int | None = None) -> None:
        data = bytes(data)
        if size is not None:
            data = data[:size].ljust(size, b"\0")
        try:
            os.write(self._fd, data)
        except OSError as exc:
            raise self._fail(exc, "write") from exc

    def read(self, length: int, timeout_ms: int) -> bytes:
        try:
            events = self._poll.poll(None if timeout_ms < 0 else timeout_ms)
            if not events:
                raise DriverError(ErrorCode.TIMEOUT, f"HID read timeout after {timeout_ms}ms (no data)")
            if any(ev & (select.POLLERR | select.POLLHUP) for _, ev in events):
                raise TransportError(errno.ENODEV, f"{self.path}: device disconnected")
            data = os.read(self._fd, length)
        except BlockingIOError as exc:
            raise DriverError(ErrorCode.TIMEOUT, "HID read timeout (no data)") from exc
        except OSError as exc:
            if isinstance(exc, TransportError):
                raise
            raise self._fail(exc, "read") from exc
        if not data:
            raise DriverError(ErrorCode.TIMEOUT, "HID read returned no data")
        return data

    def send_feature(self, data: bytes, size: int | None = None) -> None:
        buf = bytearray(data if size is None else bytes(data)[:size].ljust(size, b"\0"))
        try:
            fcntl.ioctl(self._fd, HIDIOCSFEATURE(len(buf)), buf, True)
        except OSError as exc:
            raise self._fail(exc, "send feature report") from exc

    def _get(self, request, report_id: int, length: int, what: str) -> bytes:
        buf = bytearray(length)
        if length:
            buf[0] = report_id
        try:
            n = fcntl.ioctl(self._fd, request(length), buf, True)
        except OSError as exc:
            raise self._fail(exc, what) from exc
        return bytes(buf[:n])

    def get_feature(self, report_id: int, length: int) -> bytes:
        return self._get(HIDIOCGFEATURE, report_id, length, "get feature report")

    def get_input(self, report_id: int, length: int) -> bytes:
        return self._get(HIDIOCGINPUT, report_id, length, "get input report")
