"""Lighting page for RGB models (G633/G635/G733/G933/G935): effect, colour, brightness."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..controller import Controller
from ..i18n import tr
from ..widgets.common import Card, ValueSlider, label
from ..widgets.visuals import HeadsetView

SWATCHES = ("#00b4f5", "#ffffff", "#ff2d55", "#ff6a00", "#ffd60a", "#32d74b",
            "#00e5c0", "#5e5ce6", "#bf5af2", "#ff375f")


class Swatch(QPushButton):
    picked = pyqtSignal(str)

    def __init__(self, color: str):
        super().__init__()
        self.color = color
        self.setCheckable(True)
        self.setFixedSize(34, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(color.upper())
        self.clicked.connect(lambda: self.picked.emit(self.color))

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        if self.isChecked():
            p.setPen(QPen(QColor(theme.TEXT), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(QColor(self.color))
        p.drawEllipse(r.adjusted(2, 2, -2, -2))


class LightingPage(QWidget):
    MODES = (("off", "Off"), ("static", "Static"), ("breathing", "Breathing"))

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
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 3)

        card = Card(tr("Lighting"))
        card.body.addWidget(label(tr("Applies to every lighting zone of the headset"), "Faint", wrap=True))
        card.body.addWidget(label(tr("Effect"), "Muted"))
        modes = QHBoxLayout()
        modes.setSpacing(8)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons: dict[str, QPushButton] = {}
        for key, text in self.MODES:
            b = QPushButton(tr(text))
            b.setObjectName("Segment")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: ctl.update_lighting(mode=k))
            self.mode_group.addButton(b)
            self.mode_buttons[key] = b
            modes.addWidget(b)
        card.body.addLayout(modes)

        card.body.addWidget(label(tr("Colour"), "Muted"))
        swatches = QHBoxLayout()
        swatches.setSpacing(6)
        self.swatches: list[Swatch] = []
        for c in SWATCHES:
            sw = Swatch(c)
            sw.picked.connect(lambda col: ctl.update_lighting(color=col))
            self.swatches.append(sw)
            swatches.addWidget(sw)
        swatches.addStretch(1)
        card.body.addLayout(swatches)
        custom = QPushButton(tr("Custom colour…"))
        custom.setObjectName("Ghost")
        custom.clicked.connect(self._pick_custom)
        row = QHBoxLayout()
        row.addWidget(custom)
        row.addStretch(1)
        card.body.addLayout(row)

        self.brightness = ValueSlider(tr("Brightness"), 1, 100, ctl.profile.lighting.brightness,
                                      fmt=lambda v: f"{v}%")
        self.brightness.released.connect(lambda v: ctl.update_lighting(brightness=v))
        card.body.addWidget(self.brightness)
        card.body.addStretch(1)
        grid.addWidget(card, 0, 0)

        preview = Card()
        self.view = HeadsetView("headset-g935.png", clickable=False)
        self.view.setMinimumHeight(380)
        preview.body.addWidget(self.view)
        self.zones = label("", "Faint")
        self.zones.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.body.addWidget(self.zones)
        grid.addWidget(preview, 0, 1)
        grid.setRowStretch(1, 1)

        ctl.lightingChanged.connect(self._sync)
        ctl.bridge.deviceChanged.connect(self._on_device)
        ctl.bridge.stateChanged.connect(lambda s, _d: self.view.set_online(ctl.bridge.connected))
        self._sync()

    def _on_device(self, snap) -> None:
        self.view.set_image(snap.image)
        self.view.set_online(True)
        self.zones.setText(tr("Zones") + ": " + ", ".join(tr(z) for _, z in snap.light_zones))

    def _pick_custom(self) -> None:
        current = QColor(self.ctl.profile.lighting.color)
        color = QColorDialog.getColor(current, self, tr("Custom colour…"))
        if color.isValid():
            self.ctl.update_lighting(color=color.name())

    def _sync(self) -> None:
        light = self.ctl.profile.lighting
        btn = self.mode_buttons.get(light.mode)
        if btn:
            btn.setChecked(True)
        for sw in self.swatches:
            sw.setChecked(sw.color.lower() == light.color.lower())
            sw.update()
        self.brightness.setValueSilently(light.brightness)
        self.brightness.setEnabled(light.mode != "off")
        self.view.set_tint(None if light.mode == "off" else light.color)
