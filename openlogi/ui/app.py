"""QApplication bootstrap: single instance, theme, window, tray."""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication

from ..config import APP_ID, Config
from . import i18n
from .theme import apply_theme
from .widgets.visuals import ASSETS

log = logging.getLogger(__name__)


def _already_running() -> bool:
    """Ask a running instance to show its window; True if one answered."""
    sock = QLocalSocket()
    sock.connectToServer(APP_ID)
    if sock.waitForConnected(300):
        sock.write(b"show\n")
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return True
    return False


def _refresh_kde_app_cache() -> None:
    """Make Plasma's task manager find our .desktop file (and so our icon).

    Plasma resolves a Wayland window's icon through its KService cache (ksycoca),
    which is per-user and not always rebuilt after a system package install; then
    the window shows a generic icon. Rebuild it once, in the background, if needed.
    """
    if "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP", "") or shutil.which("kbuildsycoca6") is None:
        return
    data_dirs = [os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")),
                 *os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")]
    if not any((Path(d) / "applications" / f"{APP_ID}.desktop").exists() for d in data_dirs):
        return  # not installed (running from source / AppImage): nothing to find
    cache_dir = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    needle = APP_ID.encode("utf-16-be")
    for cache in cache_dir.glob("ksycoca6_*"):
        try:
            if needle in cache.read_bytes():
                return
        except OSError:
            continue
    subprocess.Popen(["kbuildsycoca6"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def run(minimized: bool = False, page: str | None = None) -> int:
    QGuiApplication.setDesktopFileName(APP_ID)
    app = QApplication(sys.argv)
    app.setApplicationName("OpenLogi Headset")
    app.setOrganizationName("openlogi")
    app.setQuitOnLastWindowClosed(False)

    if _already_running():
        log.info("another instance is running; asked it to show itself")
        return 0

    _refresh_kde_app_cache()
    config = Config.load()
    i18n.set_language(config.app.language)
    apply_theme(app)
    app.setWindowIcon(QIcon(str(ASSETS / "icons" / "openlogi-headset.svg")))

    # imported late so i18n is configured before widgets build their strings
    from .controller import Controller
    from .main_window import MainWindow
    from .tray import Tray

    ctl = Controller(config)
    window = MainWindow(ctl)
    tray = Tray(ctl, window)
    tray.show()

    server = QLocalServer()
    QLocalServer.removeServer(APP_ID)
    server.listen(APP_ID)

    def on_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.readyRead.connect(lambda: (conn.readAll(), window.bring_to_front()))

    server.newConnection.connect(on_connection)

    quitting = False

    def quit_app() -> None:
        # Re-entrant safe: QApplication.quit() itself sends close events to the window.
        nonlocal quitting
        if quitting:
            return
        quitting = True
        window.allow_close = True
        tray.tray.hide()
        ctl.shutdown()
        app.quit()

    window.quitRequested.connect(quit_app)
    if not tray.available:
        config.app.close_to_tray = False  # nowhere to hide to

    # Ctrl+C in a terminal: let Python see signals periodically
    signal.signal(signal.SIGINT, lambda *_: quit_app())
    signal.signal(signal.SIGTERM, lambda *_: quit_app())
    tick = QTimer()
    tick.start(300)
    tick.timeout.connect(lambda: None)

    ctl.start()
    if page:
        pages = {"equalizer": 0, "eq": 0, "mic": 1, "microphone": 1, "surround": 2, "audio": 2, "info": 3}
        window.open_device_page(pages.get(page, 0))
    if not (minimized or config.app.start_minimized) or not tray.available:
        window.show()
    return app.exec()


def selftest() -> int:
    """Build every page offscreen-capable and exit: used by packaging smoke tests."""
    app = QApplication(sys.argv)
    config = Config()
    i18n.set_language("auto")
    apply_theme(app)
    from .controller import Controller
    from .main_window import MainWindow
    from .tray import tray_icon

    ctl = Controller(config)
    window = MainWindow(ctl)
    window.show()
    for page in range(4):
        window.open_device_page(page)
    window.go(window.SETTINGS)
    assert not tray_icon(None, False).isNull()
    assert not window.grab().isNull()
    QTimer.singleShot(800, app.quit)
    app.exec()
    ctl.audio.shutdown()
    print("selftest ok")
    return 0
