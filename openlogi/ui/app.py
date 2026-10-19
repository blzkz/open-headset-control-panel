"""QApplication bootstrap: single instance, theme, window, tray."""

from __future__ import annotations

import logging
import signal
import sys

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


def run(minimized: bool = False, page: str | None = None) -> int:
    QGuiApplication.setDesktopFileName(APP_ID)
    app = QApplication(sys.argv)
    app.setApplicationName("OpenLogi Headset")
    app.setOrganizationName("openlogi")
    app.setQuitOnLastWindowClosed(False)

    if _already_running():
        log.info("another instance is running; asked it to show itself")
        return 0

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
