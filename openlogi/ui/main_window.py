"""Frameless main window in the style of Logitech G HUB."""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QPropertyAnimation, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QCursor, QMouseEvent
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QApplication,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..service import LinkState
from . import theme
from .controller import Controller
from .i18n import tr
from .icons import icon, size
from .pages.equalizer import EqualizerPage
from .pages.home import HomePage, battery_detail
from .pages.info import InfoPage
from .pages.lighting import LightingPage
from .pages.microphone import MicrophonePage
from .pages.settings import SettingsPage
from .pages.surround import SurroundPage
from .widgets.common import BatteryIcon, Toast, label
from .widgets.navrail import NavRail
from .widgets.popup import PopupPanel, ProfileButton


class FadeStack(QStackedWidget):
    """QStackedWidget with a short fade-in on page change."""

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802 (Qt API)
        if index == self.currentIndex():
            return
        super().setCurrentIndex(index)
        w = self.currentWidget()
        eff = QGraphicsOpacityEffect(w)
        w.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", w)
        anim.setDuration(180)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: w.setGraphicsEffect(None))
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


class TitleBar(QFrame):
    backClicked = pyqtSignal()
    settingsClicked = pyqtSignal()

    def __init__(self, window: QMainWindow):
        super().__init__(window)
        self.window_ = window
        self.setObjectName("TitleBar")
        self.setFixedHeight(48)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 0, 0)
        lay.setSpacing(0)

        self.back = QToolButton()
        self.back.setObjectName("WinButton")
        self.back.setIcon(icon("back", theme.TEXT2, active=theme.TEXT))
        self.back.setIconSize(size(20))
        self.back.setToolTip(tr("Back"))
        self.back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back.clicked.connect(self.backClicked)
        keep = self.back.sizePolicy()
        keep.setRetainSizeWhenHidden(True)  # the brand must not shift when "back" appears
        self.back.setSizePolicy(keep)
        lay.addWidget(self.back)
        lay.addSpacing(8)

        brand = QHBoxLayout()
        brand.setSpacing(6)
        b1 = QLabel("OPENLOGI")
        b1.setObjectName("Brand")
        b2 = QLabel("HUB")
        b2.setObjectName("BrandAccent")
        brand.addWidget(b1)
        brand.addWidget(b2)
        lay.addLayout(brand)
        lay.addStretch(1)

        # not in the layout: kept exactly centred on the window in resizeEvent
        self.profile = ProfileButton(tr("Profile"), self)

        self.settings = self._win_button("settings", tr("Settings"))
        self.settings.clicked.connect(self.settingsClicked)
        lay.addWidget(self.settings)
        lay.addSpacing(6)
        mini = self._win_button("minimize", tr("Minimize"))
        mini.clicked.connect(window.showMinimized)
        self.maxi = self._win_button("maximize", tr("Maximize"))
        self.maxi.clicked.connect(self._toggle_max)
        close = self._win_button("close", tr("Close"))
        close.clicked.connect(window.close)
        for b in (mini, self.maxi, close):
            lay.addWidget(b)

    def _win_button(self, name: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setObjectName("WinButton")
        b.setIcon(icon(name, theme.TEXT2, active=theme.TEXT, size=16))
        b.setIconSize(size(16))
        b.setToolTip(tip)
        return b

    def place_profile(self) -> None:
        self.profile.move((self.width() - self.profile.width()) // 2, (self.height() - self.profile.height()) // 2)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self.place_profile()

    def _toggle_max(self) -> None:
        w = self.window_
        w.showNormal() if w.isMaximized() else w.showMaximized()

    def mousePressEvent(self, ev: QMouseEvent) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            handle = self.window_.windowHandle()
            if handle is not None:
                handle.startSystemMove()

    def mouseDoubleClickEvent(self, ev: QMouseEvent) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._toggle_max()


class DevicePage(QWidget):
    """Left navigation rail + header + sub-pages for the selected device."""

    PAGES = (
        ("equalizer", "Equalizer"),
        ("mic", "Microphone"),
        ("surround", "Surround"),
        ("info", "Device"),
        ("settings", "Settings"),  # pinned to the bottom of the rail
        ("light", "Lighting"),  # only for RGB models; shown right after Microphone
    )
    SETTINGS = 4
    LIGHTING = 5

    def __init__(self, ctl: Controller):
        super().__init__()
        self.ctl = ctl
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.nav = NavRail()
        for i, (ic, tip) in enumerate(self.PAGES):
            self.nav.add_item(ic, tr(tip), bottom=i == self.SETTINGS, position=2 if i == self.LIGHTING else -1)
        self.nav.set_item_visible(self.LIGHTING, False)
        root.addWidget(self.nav)

        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        header = QFrame()
        header.setFixedHeight(76)
        header.setStyleSheet(f"QFrame {{ border-bottom: 1px solid {theme.LINE}; }} QLabel {{ border: none; }}")
        hv = QHBoxLayout(header)
        hv.setContentsMargins(36, 0, 36, 0)
        hv.setSpacing(14)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addStretch(1)
        self.name = label("PRO X WIRELESS", "H1")
        self.name.setStyleSheet("font-size: 21px;")
        self.section = label("", "H3")
        titles.addWidget(self.name)
        titles.addWidget(self.section)
        titles.addStretch(1)
        hv.addLayout(titles)
        hv.addStretch(1)
        self.status = label("", "Muted")
        hv.addWidget(self.status)
        self.batt_icon = BatteryIcon()
        self.batt_pct = label("—")
        self.batt_pct.setStyleSheet("font-size: 15px; font-weight: 600; border: none;")
        hv.addWidget(self.batt_icon)
        hv.addWidget(self.batt_pct)
        main.addWidget(header)

        self.stack = FadeStack()
        self.stack.addWidget(EqualizerPage(ctl))
        self.stack.addWidget(MicrophonePage(ctl))
        self.stack.addWidget(SurroundPage(ctl))
        self.stack.addWidget(InfoPage(ctl))
        self.stack.addWidget(SettingsPage(ctl))
        self.stack.addWidget(LightingPage(ctl))
        main.addWidget(self.stack, 1)
        root.addLayout(main, 1)

        self.nav.currentChanged.connect(self.show_page)
        self.show_page(0)

        b = ctl.bridge
        b.deviceChanged.connect(self._on_device)
        b.batteryChanged.connect(self._on_battery)
        b.stateChanged.connect(self._on_state)
        self._on_state(b.state, "")

    def _on_device(self, d) -> None:
        self.name.setText(d.model.upper())
        has_lights = bool(d.light_zones)
        self.nav.set_item_visible(self.LIGHTING, has_lights)
        if not has_lights and self.nav.current() == self.LIGHTING:
            self.show_page(0)

    def show_page(self, index: int) -> None:
        self.nav.set_current(index)
        self.stack.setCurrentIndex(index)
        self.section.setText(tr(self.PAGES[index][1]).upper())

    def _on_battery(self, b) -> None:
        self.batt_icon.set_state(b.percent, b.charging)
        self.batt_pct.setText(f"{b.percent}%")
        self.batt_icon.setToolTip(battery_detail(b))
        self.status.setText(tr("Charging") if b.charging else "")
        self.status.setStyleSheet(f"color: {theme.ACCENT}; border: none;")

    def _on_state(self, state: LinkState, _d: str) -> None:
        if state is not LinkState.CONNECTED:
            self.batt_icon.set_state(None, False)
            self.batt_pct.setText("—")
            self.status.setText(tr("Headset off") if state is LinkState.HEADSET_OFF else tr("Receiver not found"))
            self.status.setStyleSheet(f"color: {theme.WARN}; border: none;")


class EdgeResizer(QObject):
    """Resize a frameless window from its outer pixels without drawing any border.

    Filters mouse events on the native QWindow (they arrive before any child widget),
    so the whole client area stays edge-to-edge; works on Wayland via startSystemResize.
    """

    BORDER = 6

    def __init__(self, window: QMainWindow):
        super().__init__(window)
        self._window = window
        self._override = False

    def _edges(self, pos: QPointF) -> Qt.Edge:
        w = self._window
        if w.isMaximized() or w.isFullScreen():
            return Qt.Edge(0)
        b, width, height = self.BORDER, w.width(), w.height()
        edges = Qt.Edge(0)
        if pos.x() <= b:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= width - b:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= b:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= height - b:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _set_cursor(self, edges: Qt.Edge) -> None:
        horiz = edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge)
        vert = edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge)
        if horiz and vert:
            fdiag = edges in (Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.Edge.RightEdge | Qt.Edge.BottomEdge)
            shape = Qt.CursorShape.SizeFDiagCursor if fdiag else Qt.CursorShape.SizeBDiagCursor
        elif horiz:
            shape = Qt.CursorShape.SizeHorCursor
        elif vert:
            shape = Qt.CursorShape.SizeVerCursor
        else:
            shape = None
        if shape is None:
            if self._override:
                QApplication.restoreOverrideCursor()
                self._override = False
            return
        if self._override:
            QApplication.changeOverrideCursor(QCursor(shape))
        else:
            QApplication.setOverrideCursor(QCursor(shape))
            self._override = True

    def eventFilter(self, obj, ev: QEvent) -> bool:
        t = ev.type()
        if t == QEvent.Type.MouseMove and not ev.buttons():
            self._set_cursor(self._edges(ev.position()))
        elif t == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
            edges = self._edges(ev.position())
            if edges:
                self._set_cursor(Qt.Edge(0))
                self._window.windowHandle().startSystemResize(edges)
                return True
        elif t == QEvent.Type.Leave:
            self._set_cursor(Qt.Edge(0))
        return False


class MainWindow(QMainWindow):
    HOME, DEVICE = range(2)
    quitRequested = pyqtSignal()

    def __init__(self, ctl: Controller):
        super().__init__()
        self.ctl = ctl
        self.allow_close = False
        self.setWindowTitle("OpenLogi Headset")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.resize(1280, 820)
        self.setMinimumSize(1000, 660)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.title = TitleBar(self)
        lay.addWidget(self.title)
        self.pages = FadeStack()
        self.home = HomePage(ctl)
        self.device = DevicePage(ctl)
        for w in (self.home, self.device):
            self.pages.addWidget(w)
        lay.addWidget(self.pages, 1)

        self.winId()  # create the native window so its events can be filtered
        self._resizer = EdgeResizer(self)
        self.windowHandle().installEventFilter(self._resizer)

        self.home.openDevice.connect(lambda: self.go(self.DEVICE))
        self.title.backClicked.connect(lambda: self.go(self.HOME))
        self.title.settingsClicked.connect(lambda: self.open_device_page(DevicePage.SETTINGS))
        ctl.profileChanged.connect(self._rebuild_profile_menu)
        self.title.profile.clicked.connect(self._show_profile_popup)
        self._rebuild_profile_menu()
        self.toast = Toast(root)
        ctl.bridge.errorOccurred.connect(lambda msg: self.toast.show_message(msg, error=True))
        ctl.audio.errorOccurred.connect(lambda msg: self.toast.show_message(msg, error=True))
        self.go(self.HOME)

    def go(self, page: int) -> None:
        self.pages.setCurrentIndex(page)
        self.title.back.setVisible(page != self.HOME)

    def open_device_page(self, sub: int = 0) -> None:
        self.go(self.DEVICE)
        self.device.show_page(sub)

    # -- profiles menu --------------------------------------------------------------------
    def _rebuild_profile_menu(self) -> None:
        self.title.profile.set_profile(self.ctl.config.active_profile)
        self.title.place_profile()

    def _show_profile_popup(self) -> None:
        cfg = self.ctl.config
        btn = self.title.profile
        popup = PopupPanel(self, width=max(280, btn.width()))
        popup.add_header(tr("Profiles"))
        for name in cfg.profiles:
            popup.add_item(name, lambda n=name: self.ctl.select_profile(n),
                           icon_name="user", active=name == cfg.active_profile)
        popup.add_separator()
        popup.add_action(tr("New profile…"), "plus", lambda: self._new_profile(False))
        popup.add_action(tr("Duplicate profile…"), "copy", lambda: self._new_profile(True))
        popup.add_action(tr("Rename profile…"), "edit", self._rename_profile)
        popup.add_action(tr("Delete profile"), "trash", self._delete_profile,
                         enabled=len(cfg.profiles) > 1, danger=True)
        popup.closed.connect(lambda: btn.setChecked(False))
        btn.setChecked(True)
        popup.show_below(btn)

    def _ask_name(self, title: str, initial: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, title, tr("Profile name"), text=initial)
        name = name.strip()
        if not ok or not name:
            return None
        if name in self.ctl.config.profiles and name != initial:
            return None
        return name

    def _new_profile(self, copy: bool) -> None:
        name = self._ask_name(tr("Duplicate profile…") if copy else tr("New profile…"))
        if name:
            self.ctl.new_profile(name, copy)

    def _rename_profile(self) -> None:
        old = self.ctl.config.active_profile
        name = self._ask_name(tr("Rename profile…"), old)
        if name and name != old:
            self.ctl.rename_profile(old, name)

    def _delete_profile(self) -> None:
        name = self.ctl.config.active_profile
        if QMessageBox.question(self, tr("Delete profile"), tr("Delete profile “{name}”?", name=name)) \
                == QMessageBox.StandardButton.Yes:
            self.ctl.delete_profile(name)

    # -- window behaviour ------------------------------------------------------------------
    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        if hasattr(self, "toast") and self.toast.isVisible():
            self.toast.hide()

    def closeEvent(self, ev: QCloseEvent) -> None:
        if self.ctl.config.app.close_to_tray and not self.allow_close:
            ev.ignore()
            self.hide()
            return
        ev.accept()
        self.quitRequested.emit()

    def bring_to_front(self) -> None:
        self.showNormal() if self.isMinimized() else self.show()
        self.raise_()
        self.activateWindow()


