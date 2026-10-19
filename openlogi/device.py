"""High-level headset model: discovery + capability-aware access to features."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .hidpp import (
    FeatureId,
    FeatureNotSupported,
    Hidpp20Device,
    HidrawInfo,
    HidrawTransport,
    enumerate_hidraw,
)
from .hidpp.features import (
    AdcBattery,
    AdcReading,
    DeviceIdentity,
    Equalizer,
    EqualizerInfo,
    Sidetone,
    read_firmware,
    read_name,
)
from .hidpp.protocol import SOFTWARE_ID_GUI, NotificationHandler

LOGITECH_VID = 0x046D


@dataclass(frozen=True, slots=True)
class ModelSpec:
    name: str
    image: str
    # Product id that shows up when the headset itself is plugged in with its
    # USB charge cable (it then enumerates as a "Battery Charger").
    charger_pid: int | None = None


SUPPORTED_MODELS: dict[int, ModelSpec] = {
    0x0ABA: ModelSpec("PRO X Wireless", "headset-34-left.png", charger_pid=0x0ABB),
    0x0AAA: ModelSpec("PRO X Wireless", "headset-34-left.png", charger_pid=0x0ABB),
    0x0AA7: ModelSpec("PRO Wireless", "headset-34-left.png"),
}


def find_receivers() -> list[HidrawInfo]:
    """HID++-capable hidraw nodes of supported Logitech headset receivers."""
    return [
        info
        for info in enumerate_hidraw(LOGITECH_VID)
        if info.hidpp and info.product_id in SUPPORTED_MODELS
    ]


def charge_cable_connected(spec: ModelSpec) -> bool:
    if spec.charger_pid is None:
        return False
    return any(info.product_id == spec.charger_pid for info in enumerate_hidraw(LOGITECH_VID))


def usb_device_path(info: HidrawInfo) -> str:
    """Resolve the sysfs USB device directory for a hidraw node (for udev/power info)."""
    node = Path("/sys/class/hidraw") / Path(info.path).name / "device"
    try:
        return str(node.resolve().parent.parent)
    except OSError:
        return ""


@dataclass(slots=True)
class Capabilities:
    sidetone: bool = False
    equalizer: bool = False
    battery: bool = False
    features: list[tuple[int, int, int, int]] = field(default_factory=list)


class Headset:
    """An opened, online headset. Construct via :meth:`open`."""

    def __init__(self, info: HidrawInfo, dev: Hidpp20Device):
        self.info = info
        self.spec = SUPPORTED_MODELS[info.product_id]
        self.dev = dev
        self.identity = DeviceIdentity()
        self.caps = Capabilities()
        self.sidetone = Sidetone(dev)
        self.equalizer = Equalizer(dev)
        self.battery = AdcBattery(dev)
        self.eq_info: EqualizerInfo | None = None

    @classmethod
    def open(
        cls, info: HidrawInfo, on_notification: NotificationHandler | None = None, sw_id: int = SOFTWARE_ID_GUI
    ) -> Headset:
        transport = HidrawTransport(info.path)
        dev = Hidpp20Device(transport, on_notification=on_notification, sw_id=sw_id)
        return cls(info, dev)

    def close(self) -> None:
        self.dev.transport.close()

    def probe(self) -> None:
        """Ping and discover features. Raises HidppTimeout if the headset is off."""
        self.identity.protocol = self.dev.ping()
        self.caps.features = self.dev.enumerate_features()
        ids = {fid for _, fid, _, _ in self.caps.features}
        self.caps.sidetone = FeatureId.SIDETONE in ids
        self.caps.equalizer = FeatureId.EQUALIZER in ids
        self.caps.battery = FeatureId.ADC_MEASUREMENT in ids
        try:
            self.identity.name, self.identity.kind = read_name(self.dev)
        except FeatureNotSupported:
            self.identity.name = self.spec.name
        try:
            read_firmware(self.dev, self.identity)
        except FeatureNotSupported:
            pass
        if self.caps.equalizer:
            self.eq_info = self.equalizer.info()

    def read_battery(self) -> AdcReading | None:
        return self.battery.read() if self.caps.battery else None

    def adc_feature_index(self) -> int:
        try:
            return self.dev.feature_index(FeatureId.ADC_MEASUREMENT)
        except FeatureNotSupported:
            return -1
