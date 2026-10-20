"""High-level headset model: discovery + capability-aware access to features."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .hidpp import (
    FeatureId,
    FeatureNotSupported,
    Hidpp20Device,
    HidppError,
    HidrawInfo,
    HidrawTransport,
    enumerate_hidraw,
)
from .hidpp.features import (
    AdcBattery,
    AdcReading,
    AutoOff,
    ColorLeds,
    DeviceIdentity,
    Equalizer,
    EqualizerInfo,
    LevelReading,
    Sidetone,
    read_battery_status,
    read_firmware,
    read_name,
    read_unified_battery,
)
from .hidpp.protocol import SOFTWARE_ID_GUI, NotificationHandler
from .models import BY_PID, ModelSpec

LOGITECH_VID = 0x046D

# Back-compat alias: product id → model.
SUPPORTED_MODELS = BY_PID


def _usb_device_key(info: HidrawInfo) -> str:
    """Identify the physical USB device (all its interfaces share this prefix)."""
    return info.phys.rsplit("/input", 1)[0]


def find_receivers() -> list[HidrawInfo]:
    """One HID++ hidraw node per connected supported headset/receiver.

    A device may expose HID++ long reports on several interfaces; the one inside the
    Logitech vendor collection is preferred.
    """
    best: dict[str, HidrawInfo] = {}
    for info in enumerate_hidraw(LOGITECH_VID):
        if not info.hidpp or info.product_id not in BY_PID:
            continue
        key = _usb_device_key(info)
        if key not in best or (info.vendor_page and not best[key].vendor_page):
            best[key] = info
    return sorted(best.values(), key=lambda i: (not BY_PID[i.product_id].verified, i.path))


def charge_cable_connected(spec: ModelSpec) -> bool:
    if spec.charger_pid is None:
        return False
    return any(info.product_id == spec.charger_pid for info in enumerate_hidraw(LOGITECH_VID))


class BatterySource(Enum):
    NONE = "none"
    ADC = "adc"  # cell voltage, percentage estimated by us
    UNIFIED = "unified"  # 0x1004, percentage from the device
    STATUS = "status"  # 0x1000, percentage from the device


@dataclass(slots=True)
class Capabilities:
    sidetone: bool = False
    equalizer: bool = False
    battery: BatterySource = BatterySource.NONE
    auto_off: bool = False
    lights: bool = False
    features: list[tuple[int, int, int, int]] = field(default_factory=list)


class Headset:
    """An opened, online headset. Construct via :meth:`open`."""

    def __init__(self, info: HidrawInfo, dev: Hidpp20Device):
        self.info = info
        self.spec = BY_PID[info.product_id]
        self.dev = dev
        self.identity = DeviceIdentity()
        self.caps = Capabilities()
        self.sidetone = Sidetone(dev)
        self.equalizer = Equalizer(dev)
        self.battery = AdcBattery(dev)
        self.auto_off = AutoOff(dev)
        self.leds = ColorLeds(dev)
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
        self.caps.lights = FeatureId.COLOR_LED_EFFECTS in ids and bool(self.spec.light_zones)
        if FeatureId.ADC_MEASUREMENT in ids:
            self.caps.battery = BatterySource.ADC
        elif FeatureId.UNIFIED_BATTERY in ids:
            self.caps.battery = BatterySource.UNIFIED
        elif FeatureId.BATTERY_STATUS in ids:
            self.caps.battery = BatterySource.STATUS
        if FeatureId.ADC_MEASUREMENT in ids:
            try:  # only advertise the timer if the getter answers
                self.auto_off.get()
                self.caps.auto_off = True
            except HidppError:
                self.caps.auto_off = False
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

    def read_battery(self) -> AdcReading | LevelReading | None:
        src = self.caps.battery
        if src is BatterySource.ADC:
            return self.battery.read()
        if src is BatterySource.UNIFIED:
            return read_unified_battery(self.dev)
        if src is BatterySource.STATUS:
            return read_battery_status(self.dev)
        return None

    def adc_feature_index(self) -> int:
        try:
            return self.dev.feature_index(FeatureId.ADC_MEASUREMENT)
        except FeatureNotSupported:
            return -1
