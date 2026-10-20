"""System tray icon with live battery gauge and quick controls."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QRectF, Qt
from PyQt6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from ..battery import BatteryState
from ..presets import BUILTIN_PRESETS
from ..service import LinkState
from . import theme
from .controller import Controller
from .i18n import tr
from .icons import pixmap
from .widgets.common import BatteryIcon


def tray_icon(battery: BatteryState | None, online: bool) -> QIcon:
    ic = QIcon()
    for px in (16, 22, 24, 32, 48, 64):
        pm = QPixmap(px, px)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        glyph = pixmap("headset", "#ffffff" if online else "#8a8a92", px, stroke=2.2, dpr=1.0)
        p.drawPixmap(0, 0, glyph)
        if online and battery is not None:
            # battery arc around the glyph
            pen = QPen(BatteryIcon.color_for(battery.percent, battery.charging), max(1.5, px / 12))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            r = QRectF(pen.widthF() / 2, pen.widthF() / 2, px - pen.widthF(), px - pen.widthF())
            p.drawArc(r, -90 * 16, int(-360 * 16 * battery.percent / 100))
        elif not online:
            p.setPen(QPen(QColor(theme.BAD), max(1.5, px / 10)))
            p.drawLine(int(px * 0.15), int(px * 0.85), int(px * 0.85), int(px * 0.15))
        p.end()
        ic.addPixmap(pm)
    return ic


class Tray(QObject):
    def __init__(self, ctl: Controller, window, parent: QObject | None = None):
        super().__init__(parent)
        self.ctl = ctl
        self.window = window
        self.tray = QSystemTrayIcon(tray_icon(None, False), self)
        self.tray.setToolTip("OpenLogi Headset")
        self.tray.activated.connect(self._activated)
        self._warned_low = False
        self._was_full: bool | None = None
        self._last_state: LinkState | None = None

        self.menu = QMenu()
        self.status_action = QAction("—", self.menu)
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()
        self.menu.addAction(tr("Show"), self.window.bring_to_front)
        self.menu.addSeparator()

        self.eq_menu = self.menu.addMenu(tr("Equalizer"))
        self.eq_group = QActionGroup(self.eq_menu)
        self.eq_actions: dict[str, QAction] = {}
        for preset in BUILTIN_PRESETS:
            act = QAction(tr(preset.name), self.eq_menu, checkable=True)
            act.triggered.connect(lambda _=False, k=preset.key: ctl.apply_eq_preset(k))
            self.eq_group.addAction(act)
            self.eq_menu.addAction(act)
            self.eq_actions[preset.key] = act

        self.st_menu = self.menu.addMenu(tr("Sidetone"))
        self.st_group = QActionGroup(self.st_menu)
        self.st_actions: dict[int, QAction] = {}
        for level in (0, 25, 50, 75, 100):
            act = QAction(tr("Off") if level == 0 else f"{level}%", self.st_menu, checkable=True)
            act.triggered.connect(lambda _=False, v=level: ctl.set_sidetone(v))
            self.st_group.addAction(act)
            self.st_menu.addAction(act)
            self.st_actions[level] = act

        self.voice_action = QAction(tr("Voice enhancement"), self.menu, checkable=True)
        self.voice_action.triggered.connect(lambda on: ctl.update_voice(enabled=on))
        self.menu.addAction(self.voice_action)
        self.surround_action = QAction(tr("Virtual 7.1 surround"), self.menu, checkable=True)
        self.surround_action.triggered.connect(lambda on: ctl.update_surround(enabled=on))
        self.menu.addAction(self.surround_action)

        self.profile_menu = self.menu.addMenu(tr("Profile"))
        self.menu.addSeparator()
        self.menu.addAction(tr("Quit"), self.window.quitRequested.emit)
        self.tray.setContextMenu(self.menu)

        b = ctl.bridge
        b.stateChanged.connect(self._on_state)
        b.batteryChanged.connect(self._on_battery)
        ctl.eqChanged.connect(self._on_eq)
        ctl.sidetoneChanged.connect(self._on_sidetone)
        ctl.voiceChanged.connect(lambda: self.voice_action.setChecked(ctl.profile.voice.enabled))
        ctl.surroundChanged.connect(lambda: self.surround_action.setChecked(ctl.profile.surround.enabled))
        ctl.profileChanged.connect(self._rebuild_profiles)
        ctl.updates.available.connect(
            lambda r: self._notify(tr("Update available"), tr("OpenLogi Headset {v} is ready to install.", v=r.version)))
        self._rebuild_profiles()
        self._on_eq(ctl.profile.eq_gains, ctl.profile.eq_preset)
        self._on_sidetone(ctl.profile.sidetone)
        self.voice_action.setChecked(ctl.profile.voice.enabled)
        self.surround_action.setChecked(ctl.profile.surround.enabled)
        self._on_state(b.state, "")

    def show(self) -> None:
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    @property
    def available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.window.isVisible() and not self.window.isMinimized():
                self.window.hide()
            else:
                self.window.bring_to_front()

    def _rebuild_profiles(self) -> None:
        self.profile_menu.clear()
        group = QActionGroup(self.profile_menu)
        for name in self.ctl.config.profiles:
            act = QAction(name, self.profile_menu, checkable=True)
            act.setChecked(name == self.ctl.config.active_profile)
            act.triggered.connect(lambda _=False, n=name: self.ctl.select_profile(n))
            group.addAction(act)
            self.profile_menu.addAction(act)

    def _notify(self, title: str, text: str) -> None:
        if self.tray.isVisible():
            self.tray.showMessage(title, text, tray_icon(self.ctl.bridge.battery, self.ctl.bridge.connected), 4000)

    def _on_state(self, state: LinkState, _d: str) -> None:
        online = state is LinkState.CONNECTED
        self.tray.setIcon(tray_icon(self.ctl.bridge.battery if online else None, online))
        if not online:
            self.status_action.setText(tr("Headset off") if state is LinkState.HEADSET_OFF else tr("Receiver not found"))
            self.tray.setToolTip("OpenLogi Headset — " + self.status_action.text())
        if self._last_state is not None and self.ctl.config.app.notify_connection:
            if online and self._last_state is not LinkState.CONNECTED:
                self._notify("PRO X Wireless", tr("Headset connected"))
            elif not online and self._last_state is LinkState.CONNECTED:
                self._notify("PRO X Wireless", tr("Headset disconnected"))
        self._last_state = state

    def _on_battery(self, b: BatteryState) -> None:
        self.tray.setIcon(tray_icon(b, True))
        text = tr("Battery: {p}%", p=b.percent) + (f" · {tr('Charging')}" if b.charging else "")
        self.status_action.setText(text)
        self.tray.setToolTip(f"PRO X Wireless — {text}")
        app = self.ctl.config.app
        if self._was_full is False and b.full and app.notify_full_charge:
            self._notify(tr("Fully charged"), tr("Your headset is fully charged."))
        self._was_full = b.full
        if b.charging or b.percent > app.low_battery_percent + 3:
            self._warned_low = False
        elif app.notify_low_battery and not self._warned_low and b.percent <= app.low_battery_percent:
            self._warned_low = True
            self._notify(tr("Low battery"), tr("Battery at {p}%. Charge your headset soon.", p=b.percent))

    def _on_eq(self, _gains: list[int], key: str) -> None:
        for k, act in self.eq_actions.items():
            act.setChecked(k == key)

    def _on_sidetone(self, level: int) -> None:
        for v, act in self.st_actions.items():
            act.setChecked(v == level)
