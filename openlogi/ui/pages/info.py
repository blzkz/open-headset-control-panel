"""Device information: identity, firmware, battery telemetry, HID++ feature table."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ...battery import BatteryState
from ...service import DeviceSnapshot, LinkState
from .. import theme
from ..controller import Controller
from ..i18n import tr
from ..widgets.common import BatteryIcon, Card, label
from ..widgets.visuals import HeadsetView
from .home import battery_detail


class KeyValueGrid(QWidget):
    def __init__(self):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(24)
        self.grid.setVerticalSpacing(10)
        self.grid.setColumnStretch(1, 1)
        self._values: dict[str, QLabel] = {}

    def set(self, key: str, value: str) -> None:
        if key not in self._values:
            row = len(self._values)
            k = label(key, "Muted")
            v = label(value)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.grid.addWidget(k, row, 0, Qt.AlignmentFlag.AlignTop)
            self.grid.addWidget(v, row, 1)
            self._values[key] = v
        self._values[key].setText(value)

    def clear(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._values.clear()


class InfoPage(QWidget):
    def __init__(self, ctl: Controller):
        super().__init__()
        self.ctl = ctl
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        grid = QGridLayout(body)
        grid.setContentsMargins(36, 28, 36, 28)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(20)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)

        dev = Card(tr("Device information"))
        self.kv = KeyValueGrid()
        dev.body.addWidget(self.kv)
        grid.addWidget(dev, 0, 0)

        bat = Card(tr("Battery"))
        top = QGridLayout()
        self.batt_icon = BatteryIcon(width=58, height=26)
        self.batt_pct = label("—", "BigValue")
        self.batt_state = label("", "Muted")
        top.addWidget(self.batt_icon, 0, 0, 2, 1, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.batt_pct, 0, 1)
        top.addWidget(self.batt_state, 1, 1)
        top.setColumnStretch(2, 1)
        bat.body.addLayout(top)
        self.bkv = KeyValueGrid()
        bat.body.addWidget(self.bkv)
        grid.addWidget(bat, 1, 0)

        feats = Card(tr("HID++ features"))
        self.fkv = KeyValueGrid()
        feats.body.addWidget(self.fkv)
        grid.addWidget(feats, 2, 0)

        side = Card()
        self.view = HeadsetView("headset-side.png", clickable=False)
        self.view.setMinimumHeight(420)
        side.body.addWidget(self.view)
        grid.addWidget(side, 0, 1, 3, 1)
        grid.setRowStretch(3, 1)

        b = ctl.bridge
        b.deviceChanged.connect(self._on_device)
        b.batteryChanged.connect(self._on_battery)
        b.stateChanged.connect(self._on_state)
        b.cableChanged.connect(lambda c: self.bkv.set(tr("USB cable"), tr("On") if c else tr("Off")))
        if b.device:
            self._on_device(b.device)
        self._on_state(b.state, "")

    def _on_state(self, state: LinkState, detail: str) -> None:
        self.view.set_online(state is LinkState.CONNECTED)
        names = {
            LinkState.CONNECTED: tr("Connected"),
            LinkState.HEADSET_OFF: tr("Headset off"),
            LinkState.NO_RECEIVER: tr("Receiver not found"),
            LinkState.NO_PERMISSION: tr("No permission") + (f" — {detail}" if detail else ""),
        }
        self.kv.set(tr("LIGHTSPEED receiver"), names[state])

    def _on_device(self, d: DeviceSnapshot) -> None:
        self.kv.set(tr("Name"), d.name)
        self.kv.set(tr("Model"), d.model)
        for kind, version in d.firmware:
            self.kv.set(f"{tr('Firmware')} ({kind})", version)
        self.kv.set(tr("HID++ protocol"), d.protocol)
        self.kv.set(tr("USB ID"), d.usb_id)
        self.kv.set(tr("Device node"), d.hidraw)
        if d.eq_info:
            bands = ", ".join(f"{f / 1000:g}k" if f >= 1000 else str(f) for f in d.eq_info.frequencies)
            self.kv.set(tr("Equalizer"), f"{d.eq_info.band_count} × ±{d.eq_info.db_range} dB ({bands} Hz)")
        self.fkv.clear()
        for idx, fid, name in d.features:
            self.fkv.set(f"[{idx}]  0x{fid:04X}", name)

    def _on_battery(self, b: BatteryState) -> None:
        self.batt_icon.set_state(b.percent, b.charging)
        self.batt_pct.setText(f"{b.percent}%")
        self.batt_state.setText(battery_detail(b) or tr("Discharging"))
        self.bkv.set(tr("Battery voltage"), f"{b.millivolts} mV")
        color = theme.ACCENT if b.charging else theme.TEXT2
        self.batt_state.setStyleSheet(f"color: {color};")
