"""Application settings + about."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QScrollArea, QSpinBox, QVBoxLayout, QWidget

from ... import __version__
from ...config import CONFIG_FILE
from ..controller import Controller
from ..i18n import tr
from ..widgets.common import Card, SwitchRow, hline, label

AUTOSTART = Path.home() / ".config" / "autostart" / "openlogi-headset.desktop"


def autostart_enabled() -> bool:
    return AUTOSTART.exists()


def set_autostart(on: bool) -> None:
    if not on:
        AUTOSTART.unlink(missing_ok=True)
        return
    AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
    launcher = os.environ.get("APPIMAGE") or shutil.which("openlogi-headset")
    if launcher:
        exec_line = f"{launcher} --minimized"
    else:  # running from a source checkout
        project = Path(__file__).resolve().parents[3]
        exec_line = f"env PYTHONPATH={project} {sys.executable} -m openlogi --minimized"
    AUTOSTART.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=OpenLogi Headset\n"
        "Comment=Logitech PRO X Wireless control panel\n"
        f"Exec={exec_line}\n"
        "Icon=openlogi-headset\n"
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
        grid.setContentsMargins(48, 32, 48, 32)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(20)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        title = label(tr("Settings").upper(), "H1")
        grid.addWidget(title, 0, 0, 1, 2)

        # -- application ---------------------------------------------------------------------
        general = Card(tr("Application"))
        self.autostart = SwitchRow(tr("Start with the system"), checked=autostart_enabled())
        self.autostart.toggled.connect(set_autostart)
        general.body.addWidget(self.autostart)
        rows = (
            ("start_minimized", tr("Start minimized to tray")),
            ("close_to_tray", tr("Close button minimizes to tray")),
            ("apply_on_connect", tr("Re-apply profile when the headset connects")),
        )
        for key, text in rows:
            row = SwitchRow(text, checked=getattr(app, key))
            row.toggled.connect(lambda on, k=key: ctl.update_settings(**{k: on}))
            general.body.addWidget(row)
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
        grid.addWidget(general, 1, 0)

        # -- notifications / battery ---------------------------------------------------------------
        notif = Card(tr("Notifications"))
        conn = SwitchRow(tr("Connection notifications"), checked=app.notify_connection)
        conn.toggled.connect(lambda on: ctl.update_settings(notify_connection=on))
        notif.body.addWidget(conn)
        low = SwitchRow(tr("Low battery warning"), checked=app.notify_low_battery)
        low.toggled.connect(lambda on: ctl.update_settings(notify_low_battery=on))
        notif.body.addWidget(low)
        thr = QHBoxLayout()
        thr.addWidget(label(tr("Warn at"), "Muted"))
        thr.addStretch(1)
        self.threshold = QSpinBox()
        self.threshold.setRange(5, 50)
        self.threshold.setSuffix(" %")
        self.threshold.setValue(app.low_battery_percent)
        self.threshold.valueChanged.connect(lambda v: ctl.update_settings(low_battery_percent=v))
        thr.addWidget(self.threshold)
        notif.body.addLayout(thr)
        notif.body.addWidget(hline())
        poll = QHBoxLayout()
        poll.addWidget(label(tr("Battery refresh interval"), "Muted"))
        poll.addStretch(1)
        self.poll = QSpinBox()
        self.poll.setRange(5, 600)
        self.poll.setSuffix(" s")
        self.poll.setValue(app.battery_poll_seconds)
        self.poll.valueChanged.connect(lambda v: ctl.update_settings(battery_poll_seconds=v))
        poll.addWidget(self.poll)
        notif.body.addLayout(poll)
        notif.body.addStretch(1)
        grid.addWidget(notif, 1, 1)

        # -- about -----------------------------------------------------------------------------------
        about = Card(tr("About"))
        name = label(f"OPENLOGI HEADSET  ·  v{__version__}", "H2")
        about.body.addWidget(name)
        about.body.addWidget(label(tr("Open-source control panel for Logitech headsets on Linux."), "Muted", wrap=True))
        about.body.addWidget(label(tr("Not affiliated with Logitech. Product images © Logitech."), "Faint", wrap=True))
        cfg = label(str(CONFIG_FILE), "Faint")
        cfg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        about.body.addWidget(cfg)
        grid.addWidget(about, 2, 0, 1, 2)
        grid.setRowStretch(3, 1)
