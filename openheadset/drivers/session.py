"""Discovery of headsets handled by the ported drivers, and per-call device access.

Each capability is served by the hidraw node HeadsetControl would pick for it. Nodes are
opened per call, like HeadsetControl does: a fresh descriptor starts with an empty input
queue, so a reply is never confused with a report that arrived between two calls.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from ..hidpp.transport import TransportError
from . import REGISTRY
from .base import Cap, Driver, DriverError, ErrorCode
from .hidio import HidEntry, HidHandle, enumerate_entries, select_path

T = TypeVar("T")

VENDOR_NAMES = {
    0x046D: "Logitech", 0x9886: "Logitech (ASTRO)", 0x1038: "SteelSeries", 0x0951: "HyperX (Kingston)",
    0x03F0: "HyperX (HP)", 0x1B1C: "Corsair", 0x1E7D: "ROCCAT", 0x3329: "Audeze", 0x054C: "Sony", 0x0B0E: "Jabra",
    0x17EF: "Lenovo", 0x047F: "Plantronics", 0x3837: "MCHOSE",
}


def _usb_device(entry: HidEntry) -> str:
    # …/<usb device>/<usb interface>/<hid device>/hidraw/hidrawN
    parents = Path(entry.syspath).parents
    return str(parents[3]) if len(parents) > 3 else entry.syspath


@dataclass(slots=True)
class DriverDevice:
    cls: type[Driver]
    vendor_id: int
    product_id: int
    entries: list[HidEntry] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.cls.NAME

    @property
    def vendor(self) -> str:
        return VENDOR_NAMES.get(self.vendor_id, f"{self.vendor_id:04x}")

    @property
    def usb_id(self) -> str:
        return f"{self.vendor_id:04x}:{self.product_id:04x}"


def find_driver_devices(skip: Iterable[tuple[int, int]] = ()) -> list[DriverDevice]:
    """Connected devices with a ported driver, one per physical device, in udev order."""
    skipped = set(skip)
    found: dict[str, DriverDevice] = {}
    for entry in enumerate_entries():
        key = (entry.vendor_id, entry.product_id)
        if key in skipped:
            continue
        cls = REGISTRY.find(*key)
        if cls is None:
            continue
        dev = found.setdefault(_usb_device(entry), DriverDevice(cls, entry.vendor_id, entry.product_id))
        dev.entries.append(entry)
    return list(found.values())


class DriverSession:
    """A driver instance bound to one physical device."""

    def __init__(self, device: DriverDevice):
        self.device = device
        self.driver = device.cls(device.product_id)
        self.caps: frozenset[Cap] = frozenset(self.driver.capabilities())

    def path_for(self, cap: Cap) -> str:
        path = select_path(self.device.entries, self.driver.detail(cap))
        if path is None:
            raise DriverError(ErrorCode.NOT_SUPPORTED, f"no HID interface for {cap.value}")
        return path

    def check_access(self) -> None:
        """Open every node the capabilities need once; raises TransportError (e.g. EACCES)."""
        for path in sorted({self.path_for(cap) for cap in self.caps}):
            HidHandle(path).close()

    def call(self, cap: Cap, fn: Callable[..., T], *args) -> T:
        if cap not in self.caps:
            raise DriverError(ErrorCode.NOT_SUPPORTED, f"{self.device.name}: {cap.value} not supported")
        handle = HidHandle(self.path_for(cap))
        try:
            return fn(handle, *args)
        finally:
            handle.close()


__all__ = ["DriverDevice", "DriverSession", "TransportError", "VENDOR_NAMES", "find_driver_devices"]
