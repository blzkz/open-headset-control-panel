"""Left navigation rail with an animated selection indicator (G HUB style)."""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter
from PyQt6.QtWidgets import QFrame, QToolButton, QVBoxLayout, QWidget

from .. import theme
from ..icons import icon


class _Indicator(QWidget):
    """Highlighted slot + accent bar; slides between items."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.BG2))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT))
        p.drawRoundedRect(QRectF(0, 10, 3, self.height() - 20), 1.5, 1.5)


class RailButton(QToolButton):
    """Icon-only rail entry; selection visuals are drawn by the shared indicator."""

    def __init__(self, icon_name: str, tooltip: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setToolTip(tooltip)
        self.setIcon(icon(icon_name, theme.TEXT3, active=theme.TEXT, size=24))
        self.setIconSize(QSize(24, 24))
        self.setFixedSize(72, 64)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False

    def enterEvent(self, ev) -> None:
        self._hover = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(ev)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        if self._hover and not self.isChecked():
            p.fillRect(self.rect(), QColor("#17171a"))
        lit = self.isChecked() or self._hover
        pm = self.icon().pixmap(self.iconSize(), QIcon.Mode.Normal, QIcon.State.On if lit else QIcon.State.Off)
        p.drawPixmap((self.width() - self.iconSize().width()) // 2,
                     (self.height() - self.iconSize().height()) // 2, pm)


class NavRail(QFrame):
    currentChanged = pyqtSignal(int)

    DURATION_MS = 280

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("NavRail")
        self.setFixedWidth(72)
        self._indicator = _Indicator(self)  # created first → painted below the buttons
        self._indicator.hide()
        self._anim = QPropertyAnimation(self._indicator, b"geometry", self)
        self._anim.setDuration(self.DURATION_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._top = QVBoxLayout()
        self._top.setSpacing(2)
        self._bottom = QVBoxLayout()
        self._bottom.setSpacing(2)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 12)
        lay.setSpacing(0)
        lay.addLayout(self._top)
        lay.addStretch(1)
        lay.addLayout(self._bottom)
        self._buttons: list[RailButton] = []
        self._current = -1

    def add_item(self, icon_name: str, tooltip: str, bottom: bool = False, position: int = -1) -> int:
        index = len(self._buttons)
        btn = RailButton(icon_name, tooltip, self)
        btn.clicked.connect(lambda _=False, i=index: self._clicked(i))
        (self._bottom if bottom else self._top).insertWidget(position, btn)
        self._buttons.append(btn)
        return index

    def set_item_visible(self, index: int, visible: bool) -> None:
        self._buttons[index].setVisible(visible)
        QTimer.singleShot(0, self._snap)  # neighbours moved

    def current(self) -> int:
        return self._current

    def _clicked(self, index: int) -> None:
        self.set_current(index)
        self.currentChanged.emit(index)

    def set_current(self, index: int, animate: bool = True) -> None:
        for i, b in enumerate(self._buttons):
            b.setChecked(i == index)
        previous, self._current = self._current, index
        target = self._target()
        if target is None:
            return
        if not animate or previous < 0 or not self._indicator.isVisible():
            self._anim.stop()
            self._indicator.setGeometry(target)
            self._indicator.show()
            return
        self._anim.stop()
        self._anim.setStartValue(self._indicator.geometry())
        self._anim.setEndValue(target)
        self._anim.start()

    def _target(self) -> QRect | None:
        if not 0 <= self._current < len(self._buttons):
            return None
        return self._buttons[self._current].geometry()

    def _snap(self) -> None:
        target = self._target()
        if target is not None and self._anim.state() != QPropertyAnimation.State.Running:
            self._indicator.setGeometry(target)
            self._indicator.show()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._snap()  # bottom items move when the window height changes

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self._snap()
        QTimer.singleShot(0, self._snap)  # after the first layout pass
