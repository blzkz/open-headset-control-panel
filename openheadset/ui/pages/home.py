"""Home: the device "stage" with the product shot, battery and link status."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QRadialGradient
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from ...battery import BatteryState
from ...permissions import install_rule
from ...service import LinkState
from .. import theme
from ..controller import Controller
from ..i18n import tr
from ..icons import pixmap
from ..widgets.common import BatteryIcon, label
from ..widgets.visuals import HeadsetView


def battery_text(b: BatteryState | None) -> str:
    if b is None or b.percent < 0:
        return "—"
    return f"{b.percent}%"


def battery_detail(b: BatteryState | None) -> str:
    if b is None:
        return ""
    if b.full:
        return tr("Fully charged")
    if b.charging:
        hrs = b.hours_to_full if b.percent >= 0 else None
        return tr("Charging") + (f" · {tr('≈ {h} h to full', h=f'{hrs:.1f}')}" if hrs else "")
    hrs = b.remaining_hours
    return tr("≈ {h} h remaining", h=f"{hrs:.0f}") if hrs is not None and hrs >= 0 else ""


class StatusPill(QWidget):
    """Icon + text chip (LIGHTSPEED / USB)."""

    def __init__(self, icon_name: str, text: str):
        super().__init__()
        self.setObjectName("Chip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 12, 4)
        lay.setSpacing(6)
        self.icon = QLabel()
        self.icon_name = icon_name
        self.text = QLabel(text)
        self.text.setStyleSheet(f"color: {theme.TEXT2}; font-size: 11px; font-weight: 600; letter-spacing: 1px;")
        lay.addWidget(self.icon)
        lay.addWidget(self.text)
        self.set_active(False)

    def set_active(self, on: bool, text: str | None = None) -> None:
        self.icon.setPixmap(pixmap(self.icon_name, theme.ACCENT if on else theme.TEXT3, 14))
        if text is not None:
            self.text.setText(text)


class HomePage(QWidget):
    openDevice = pyqtSignal()

    def __init__(self, ctl: Controller):
        super().__init__()
        self.ctl = ctl
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 18, 40, 28)
        root.setSpacing(0)

        self.view = HeadsetView("headset-34-left.png")
        self.view.clicked.connect(self.openDevice)
        root.addWidget(self.view, 1)

        self.name = label("PRO X WIRELESS", "H1")
        self.name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.name)
        root.addSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        self.batt_icon = BatteryIcon()
        self.batt_pct = label("—")
        self.batt_pct.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.batt_detail = label("", "Muted")
        row.addWidget(self.batt_icon)
        row.addWidget(self.batt_pct)
        row.addSpacing(6)
        row.addWidget(self.batt_detail)
        row.addStretch(1)
        root.addLayout(row)
        root.addSpacing(16)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        chips.addStretch(1)
        self.link_chip = StatusPill("wireless", "LIGHTSPEED")
        self.charge_chip = StatusPill("bolt", tr("Charging").upper())
        self.exp_chip = StatusPill("info", tr("Experimental support").upper())
        self.exp_chip.setToolTip(tr("This model has not been tested on real hardware yet. If something misbehaves, "
                                    "open an issue with the output of “open-headsetctl --diagnose”."))
        self.exp_chip.hide()
        chips.addWidget(self.link_chip)
        chips.addWidget(self.charge_chip)
        chips.addWidget(self.exp_chip)
        chips.addStretch(1)
        root.addLayout(chips)
        root.addSpacing(14)

        self.hint = label(tr("Click the headset to configure it"), "Faint")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.hint)
        self.grant = QPushButton(tr("Grant access"))
        self.grant.setObjectName("Primary")
        self.grant.setCursor(Qt.CursorShape.PointingHandCursor)
        self.grant.clicked.connect(self._grant_access)
        self.grant.hide()
        grow = QHBoxLayout()
        grow.addStretch(1)
        grow.addWidget(self.grant)
        grow.addStretch(1)
        root.addSpacing(8)
        root.addLayout(grow)

        self._link = "LIGHTSPEED"
        b = ctl.bridge
        b.stateChanged.connect(self._on_state)
        b.batteryChanged.connect(self._on_battery)
        b.deviceChanged.connect(self._on_device)
        self._on_state(b.state, "")
        self.charge_chip.hide()

    def _on_state(self, state: LinkState, detail: str) -> None:
        online = state is LinkState.CONNECTED
        self.view.set_online(online)
        texts = {
            LinkState.CONNECTED: tr("Click the headset to configure it"),
            LinkState.HEADSET_OFF: tr("Turn on your headset"),
            LinkState.NO_RECEIVER: tr("Plug in your headset or its wireless receiver"),
            LinkState.NO_PERMISSION: tr("Grant access to the hidraw device (see README: udev rule)"),
        }
        self.hint.setText(texts[state])
        self.grant.setVisible(state is LinkState.NO_PERMISSION)
        self.hint.setStyleSheet("" if online else f"color: {theme.WARN};")
        self.link_chip.set_active(
            online,
            {
                LinkState.CONNECTED: f"{self._link} · " + tr("Connected").upper(),
                LinkState.HEADSET_OFF: f"{self._link} · " + tr("Headset off").upper(),
                LinkState.NO_RECEIVER: tr("Receiver not found").upper(),
                LinkState.NO_PERMISSION: tr("No permission").upper(),
            }[state],
        )
        if not online:
            self.batt_icon.set_state(None, False)
            self.batt_pct.setText("—")
            self.batt_detail.setText("")
            self.charge_chip.hide()

    def _on_device(self, d) -> None:
        self.name.setText(d.model.upper())
        self.view.set_image(d.image)
        if d.backend == "hidpp":
            self._link = "LIGHTSPEED" if d.wireless else "USB"
            self.exp_chip.setToolTip(tr("This model has not been tested on real hardware yet. If something "
                                        "misbehaves, open an issue with the output of “open-headsetctl --diagnose”."))
        else:
            self._link = tr("Wireless").upper() if d.wireless else "USB"
            self.exp_chip.setToolTip(tr("Driven by a port of HeadsetControl's driver for this model, checked "
                                        "byte by byte against it, but not yet tested with this app on real hardware."))
        self.exp_chip.setVisible(not d.verified)
        self.exp_chip.set_active(True)
        self._on_state(self.ctl.bridge.state, "")

    def _grant_access(self) -> None:
        ok, msg = install_rule()
        if not ok:
            QMessageBox.warning(self, tr("Grant access"), f"{tr('Could not install the udev rule')}\n\n{msg}")

    def _on_battery(self, b: BatteryState) -> None:
        self.batt_icon.set_state(b.percent, b.charging)
        self.batt_pct.setText(battery_text(b))
        self.batt_detail.setText(battery_detail(b))
        self.charge_chip.set_active(b.charging, tr("Fully charged").upper() if b.full else tr("Charging").upper())
        self.charge_chip.setVisible(b.charging)

    def paintEvent(self, ev) -> None:
        # subtle vignette like the G HUB home background
        p = QPainter(self)
        g = QRadialGradient(self.width() / 2, self.height() * 0.42, max(self.width(), self.height()) * 0.7)
        g.setColorAt(0, QColor("#17171a"))
        g.setColorAt(1, QColor(theme.BG0))
        p.fillRect(self.rect(), g)
