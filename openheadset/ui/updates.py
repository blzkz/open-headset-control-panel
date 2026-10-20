"""Qt wrapper around :mod:`openheadset.updater`: background checks, download progress, restart."""

from __future__ import annotations

import logging
import subprocess
import threading
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .. import __version__, updater

log = logging.getLogger(__name__)


class UpdateManager(QObject):
    changed = pyqtSignal()          # any of the public attributes changed
    available = pyqtSignal(object)  # updater.Release, once per new version
    progress = pyqtSignal(int)
    installed = pyqtSignal()        # files replaced: the app should restart

    FIRST_CHECK_MS = 20_000
    INTERVAL_MS = 6 * 3600 * 1000

    def __init__(self, auto_check: bool, parent: QObject | None = None):
        super().__init__(parent)
        self.current = __version__
        self.kind: updater.InstallKind | None = None
        self.release: updater.Release | None = None
        self.busy = False
        self.error = ""
        self.last_check: float | None = None
        self._announced: str | None = None
        self._timer = QTimer(self, interval=self.INTERVAL_MS)
        self._timer.timeout.connect(self.check)
        self.set_auto(auto_check)

    # -- state ----------------------------------------------------------------------------
    @property
    def update_ready(self) -> bool:
        return self.release is not None and updater.is_newer(self.release.version, self.current)

    @property
    def can_install(self) -> bool:
        return self.update_ready and self.kind is not None and self.kind.can_self_update

    def set_auto(self, on: bool) -> None:
        if on:
            self._timer.start()
            QTimer.singleShot(self.FIRST_CHECK_MS, self.check)
        else:
            self._timer.stop()

    def _run(self, target) -> None:
        if self.busy:
            return
        self.busy = True
        self.error = ""
        self.changed.emit()
        threading.Thread(target=target, name="open-headset-update", daemon=True).start()

    # -- check ------------------------------------------------------------------------------
    def check(self) -> None:
        self._run(self._check)

    def _check(self) -> None:
        try:
            if self.kind is None:
                self.kind = updater.detect_install()
            self.release = updater.fetch_latest(updater.github_token())
            self.last_check = time.time()
        except (updater.UpdateError, OSError) as exc:
            log.info("update check failed: %s", exc)
            self.error = str(exc)
        finally:
            self.busy = False
            self.changed.emit()
        if self.update_ready and self.release and self.release.version != self._announced:
            self._announced = self.release.version
            self.available.emit(self.release)

    # -- install ----------------------------------------------------------------------------
    def install(self) -> None:
        if self.can_install:
            self._run(self._install)

    def _install(self) -> None:
        ok = False
        try:
            token = updater.github_token()
            path = updater.download_verified(self.release, self.kind, token, self.progress.emit)
            updater.install(self.kind, path)
            path.unlink(missing_ok=True)
            ok = True
        except (updater.UpdateError, OSError, subprocess.SubprocessError) as exc:
            log.warning("update failed: %s", exc)
            self.error = str(exc)
        finally:
            self.busy = False
            self.changed.emit()
        if ok:
            self.installed.emit()

    def relaunch_detached(self) -> None:
        """Start the freshly installed version once this process has exited."""
        cmd = updater.relaunch_command(self.kind) if self.kind else []
        if not cmd:
            return
        # wait for our single-instance socket to disappear before starting again
        subprocess.Popen(["sh", "-c", 'sleep 2; exec "$@"', "relaunch", *cmd],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
