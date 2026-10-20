"""Raw Linux hidraw transport.

Talks to ``/dev/hidrawN`` directly with ``os.read``/``os.write`` + ``select``,
so the project needs neither hidapi nor any compiled extension.
"""

from __future__ import annotations

import errno
import os
import select
from dataclasses import dataclass
from pathlib import Path

SYSFS_HIDRAW = Path("/sys/class/hidraw")

# Usage page 0xFF43 (Logitech HID++ for headsets) and report ID 0x11 (HID++ long).
_HIDPP_USAGE_PAGES = (b"\x06\x43\xff", b"\x06\x00\xff")
_HIDPP_LONG_REPORT = b"\x85\x11"


class TransportError(OSError):
    """The hidraw node vanished or refused I/O."""


@dataclass(frozen=True, slots=True)
class HidrawInfo:
    path: str
    vendor_id: int
    product_id: int
    name: str
    phys: str
    hidpp: bool  # declares HID++ long reports (report ID 0x11)
    vendor_page: bool = False  # …inside a Logitech vendor collection (0xFF43 / 0xFF00)

    @property
    def usb_id(self) -> str:
        return f"{self.vendor_id:04x}:{self.product_id:04x}"


def _parse_uevent(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, _, value = line.partition("=")
        out[key] = value
    return out


def enumerate_hidraw(vendor_id: int | None = None) -> list[HidrawInfo]:
    """List hidraw nodes, optionally filtered by USB vendor id."""
    found: list[HidrawInfo] = []
    if not SYSFS_HIDRAW.is_dir():
        return found
    for node in sorted(SYSFS_HIDRAW.iterdir()):
        try:
            uevent = _parse_uevent((node / "device" / "uevent").read_text())
            _bus, vid, pid = uevent["HID_ID"].split(":")
            vid_i, pid_i = int(vid, 16), int(pid, 16)
        except (OSError, KeyError, ValueError):
            continue
        if vendor_id is not None and vid_i != vendor_id:
            continue
        try:
            desc = (node / "device" / "report_descriptor").read_bytes()
        except OSError:
            desc = b""
        hidpp = _HIDPP_LONG_REPORT in desc
        vendor_page = any(p in desc for p in _HIDPP_USAGE_PAGES)
        found.append(
            HidrawInfo(
                path=f"/dev/{node.name}",
                vendor_id=vid_i,
                product_id=pid_i,
                name=uevent.get("HID_NAME", ""),
                phys=uevent.get("HID_PHYS", ""),
                hidpp=hidpp,
                vendor_page=vendor_page,
            )
        )
    return found


class HidrawTransport:
    """Non-blocking reader/writer for a single hidraw node."""

    def __init__(self, path: str):
        self.path = path
        try:
            self._fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError as exc:
            raise TransportError(exc.errno, f"{path}: {exc.strerror}") from exc
        self._poll = select.poll()
        self._poll.register(self._fd, select.POLLIN | select.POLLERR | select.POLLHUP)

    def fileno(self) -> int:
        return self._fd

    @property
    def closed(self) -> bool:
        return self._fd < 0

    def close(self) -> None:
        if self._fd >= 0:
            try:
                os.close(self._fd)
            finally:
                self._fd = -1

    def write(self, data: bytes) -> None:
        if self._fd < 0:
            raise TransportError(errno.EBADF, "transport closed")
        try:
            os.write(self._fd, data)
        except OSError as exc:
            raise TransportError(exc.errno, f"write {self.path}: {exc.strerror}") from exc

    def read(self, timeout: float) -> bytes | None:
        """Return one input report, or ``None`` on timeout."""
        if self._fd < 0:
            raise TransportError(errno.EBADF, "transport closed")
        events = self._poll.poll(max(0, int(timeout * 1000)))
        if not events:
            return None
        if any(ev & (select.POLLERR | select.POLLHUP) for _, ev in events):
            raise TransportError(errno.ENODEV, f"{self.path}: device disconnected")
        try:
            return os.read(self._fd, 64)
        except BlockingIOError:
            return None
        except OSError as exc:
            raise TransportError(exc.errno, f"read {self.path}: {exc.strerror}") from exc

    def __enter__(self) -> HidrawTransport:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
