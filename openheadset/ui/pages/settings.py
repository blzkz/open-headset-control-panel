"""Application settings + about."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ... import __version__
from ...config import CONFIG_FILE
from ..controller import Controller
from ..i18n import tr
from .. import theme
from ..icons import icon
from ..widgets.common import Card, SwitchRow, ValueSlider, hline, label

AUTOSTART = Path.home() / ".config" / "autostart" / "open-headset.desktop"


def migrate_autostart() -> None:
    """Replace the login entry of the app's old name, whose command no longer exists."""
    legacy = AUTOSTART.parent / "openlogi-headset.desktop"
    if legacy.exists():
        legacy.unlink(missing_ok=True)
        set_autostart(True)


def autostart_enabled() -> bool:
    return AUTOSTART.exists()


def set_autostart(on: bool) -> None:
    if not on:
        AUTOSTART.unlink(missing_ok=True)
        return
    AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
    launcher = os.environ.get("APPIMAGE") or shutil.which("open-headset")
    if launcher:
        exec_line = f"{launcher} --minimized"
    else:  # running from a source checkout
        project = Path(__file__).resolve().parents[3]
        exec_line = f"env PYTHONPATH={project} {sys.executable} -m openheadset --minimized"
    AUTOSTART.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Open Headset Control Panel\n"
        "Comment=Control panel for gaming headsets\n"
        f"Exec={exec_line}\n"
        "Icon=open-headset\n"
        "X-GNOME-Autostart-enabled=true\n"
        "X-KDE-autostart-after=panel\n"
    )


class SettingsPage(QWidget):
    def __init__(self, ctl: Controller):
        super().__init__()
        self.ctl = ctl
        app = ctl.config.app
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
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        # -- startup ------------------------------------------------------------------------
        startup = Card(tr("Startup"))
        self.autostart = SwitchRow(tr("Start at login"), checked=autostart_enabled())
        self.autostart.toggled.connect(set_autostart)
        startup.body.addWidget(self.autostart)
        minimized = SwitchRow(tr("Start minimized"), tr("Open hidden in the system tray"),
                              checked=app.start_minimized)
        minimized.toggled.connect(lambda on: ctl.update_settings(start_minimized=on))
        startup.body.addWidget(minimized)
        to_tray = SwitchRow(tr("Close button minimizes to tray"), checked=app.close_to_tray)
        to_tray.toggled.connect(lambda on: ctl.update_settings(close_to_tray=on))
        startup.body.addWidget(to_tray)
        startup.body.addStretch(1)
        grid.addWidget(startup, 0, 0)

        # -- battery alerts --------------------------------------------------------------------
        battery = Card(tr("Battery alerts"))
        low = SwitchRow(tr("Low battery warning"), checked=app.notify_low_battery)
        battery.body.addWidget(low)
        self.threshold = ValueSlider(tr("Warn when the battery drops to this level"), 5, 50,
                                     app.low_battery_percent, fmt=lambda v: f"{v}%")
        self.threshold.released.connect(lambda v: ctl.update_settings(low_battery_percent=v))
        self.threshold.setEnabled(app.notify_low_battery)
        low.toggled.connect(lambda on: (ctl.update_settings(notify_low_battery=on), self.threshold.setEnabled(on)))
        battery.body.addWidget(self.threshold)
        full = SwitchRow(tr("Notify when charging completes"), checked=app.notify_full_charge)
        full.toggled.connect(lambda on: ctl.update_settings(notify_full_charge=on))
        battery.body.addWidget(full)
        battery.body.addWidget(hline())
        self.poll = ValueSlider(tr("Battery refresh interval"), 5, 120, app.battery_poll_seconds,
                                fmt=lambda v: f"{v} s", step=5)
        self.poll.released.connect(lambda v: ctl.update_settings(battery_poll_seconds=v))
        battery.body.addWidget(self.poll)
        battery.body.addStretch(1)
        grid.addWidget(battery, 0, 1)

        # -- updates -------------------------------------------------------------------------------
        upd = ctl.updates
        updates = Card(tr("Updates"))
        auto = SwitchRow(tr("Check for updates automatically"), tr("Checked at startup and every 6 hours"),
                         checked=app.auto_check_updates)
        auto.toggled.connect(lambda on: ctl.update_settings(auto_check_updates=on))
        updates.body.addWidget(auto)
        updates.body.addWidget(hline())
        versions = QGridLayout()
        versions.setHorizontalSpacing(24)
        versions.setVerticalSpacing(8)
        versions.addWidget(label(tr("Installed version"), "Muted"), 0, 0)
        versions.addWidget(label(upd.current), 0, 1)
        versions.addWidget(label(tr("Latest version"), "Muted"), 1, 0)
        self.latest = label("—")
        versions.addWidget(self.latest, 1, 1)
        versions.setColumnStretch(1, 1)
        updates.body.addLayout(versions)
        self.update_status = label("", "Faint", wrap=True)
        updates.body.addWidget(self.update_status)
        self.update_bar = QProgressBar()
        self.update_bar.setRange(0, 100)
        self.update_bar.setTextVisible(False)
        self.update_bar.setFixedHeight(4)
        self.update_bar.hide()
        updates.body.addWidget(self.update_bar)
        buttons = QHBoxLayout()
        self.check_btn = QPushButton(icon("reset", theme.TEXT2), "  " + tr("Check now"))
        self.check_btn.setObjectName("Ghost")
        self.check_btn.clicked.connect(upd.check)
        self.install_btn = QPushButton()
        self.install_btn.setObjectName("Primary")
        self.install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.install_btn.clicked.connect(upd.install)
        self.notes_btn = QPushButton(tr("Release notes"))
        self.notes_btn.setObjectName("Ghost")
        self.notes_btn.clicked.connect(
            lambda: upd.release and QDesktopServices.openUrl(QUrl(upd.release.html_url)))
        buttons.addWidget(self.check_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.notes_btn)
        buttons.addWidget(self.install_btn)
        updates.body.addLayout(buttons)
        updates.body.addStretch(1)
        grid.addWidget(updates, 1, 0)
        upd.changed.connect(self._sync_updates)
        upd.progress.connect(self._on_progress)
        self._sync_updates()

        # -- general ---------------------------------------------------------------------------------
        general = Card(tr("General"))
        reapply = SwitchRow(tr("Re-apply profile when the headset connects"), checked=app.apply_on_connect)
        reapply.toggled.connect(lambda on: ctl.update_settings(apply_on_connect=on))
        general.body.addWidget(reapply)
        conn = SwitchRow(tr("Connection notifications"), checked=app.notify_connection)
        conn.toggled.connect(lambda on: ctl.update_settings(notify_connection=on))
        general.body.addWidget(conn)
        general.body.addWidget(hline())
        lang = QHBoxLayout()
        lang.addWidget(label(tr("Language"), "Muted"))
        lang.addStretch(1)
        self.lang = QComboBox()
        for code, name in (("auto", tr("Automatic")), ("es", "Español"), ("en", "English")):
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(app.language)))
        self.lang.activated.connect(lambda i: ctl.update_settings(language=self.lang.itemData(i)))
        lang.addWidget(self.lang)
        general.body.addLayout(lang)
        general.body.addWidget(label(tr("Restart the app to change the language"), "Faint"))
        general.body.addStretch(1)
        grid.addWidget(general, 1, 1)

        # -- about -----------------------------------------------------------------------------------
        about = Card(tr("About"))
        about.body.addWidget(label(f"OPEN HEADSET CONTROL PANEL  ·  v{__version__}", "H2"))
        about.body.addWidget(label(tr("Open-source control panel for Logitech headsets on Linux."), "Muted", wrap=True))
        about.body.addWidget(label(tr("Not affiliated with Logitech. Product images © Logitech."), "Faint", wrap=True))
        cfg = label(str(CONFIG_FILE), "Faint")
        cfg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        about.body.addWidget(cfg)
        grid.addWidget(about, 2, 0, 1, 2)
        grid.setRowStretch(3, 1)

    # -- updates ---------------------------------------------------------------------------------------
    def _on_progress(self, pct: int) -> None:
        self.update_bar.show()
        self.update_bar.setValue(pct)
        self.update_status.setText(tr("Downloading… {p}%", p=pct) if pct < 100 else tr("Installing…"))

    def _sync_updates(self) -> None:
        upd = self.ctl.updates
        rel = upd.release
        self.latest.setText(rel.version if rel else "—")
        self.check_btn.setEnabled(not upd.busy)
        self.notes_btn.setVisible(upd.update_ready)
        self.install_btn.setVisible(upd.can_install)
        self.install_btn.setEnabled(not upd.busy)
        if rel:
            self.install_btn.setText(tr("Update to {v}", v=rel.version))
        if upd.busy:
            if not self.update_bar.isVisible():
                self.update_status.setText(tr("Checking…"))
            return
        self.update_bar.hide()
        if upd.error:
            title = tr("Update failed") if upd.update_ready else tr("Could not check for updates")
            self.update_status.setText(f"{title}: {upd.error}")
            self.update_status.setStyleSheet(f"color: {theme.BAD};")
            return
        self.update_status.setStyleSheet("")
        if upd.update_ready and upd.kind is not None and not upd.kind.can_self_update:
            self.update_status.setText(tr("Installed from source: update with git pull"))
        elif upd.update_ready:
            self.update_status.setText(tr("The app will restart when the update is installed"))
        elif rel:
            self.update_status.setText(tr("You are up to date"))
        else:
            self.update_status.setText("")
