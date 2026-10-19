"""Styled popup panel (profile switcher) and the title-bar profile button."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..icons import pixmap


def _icon_label(name: str, color: str, px: int = 16) -> QLabel:
    lb = QLabel()
    lb.setPixmap(pixmap(name, color, px))
    lb.setFixedSize(px, px)
    lb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    return lb


class ProfileButton(QPushButton):
    """Centered title-bar control: [user] PERFIL  NAME [chevron]."""

    def __init__(self, caption: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ProfileButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 12, 0)
        lay.setSpacing(8)
        lay.addWidget(_icon_label("user", theme.TEXT2, 15))
        self.caption = QLabel(caption.upper())
        self.caption.setObjectName("ProfileCaption")
        self.value = QLabel()
        self.value.setObjectName("ProfileValue")
        for lb in (self.caption, self.value):
            lb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            lay.addWidget(lb)
        lay.addWidget(_icon_label("chevron-down", theme.TEXT2, 14))
        self.setFixedHeight(34)

    def set_profile(self, name: str) -> None:
        self.value.setText(name.upper())
        self.setFixedWidth(self.layout().sizeHint().width())

    def sizeHint(self) -> QSize:
        return self.layout().sizeHint().expandedTo(QSize(0, 34))


class _Row(QPushButton):
    """Popup row; text/icon colours follow hover in code (Qt QSS can't key on a parent's :hover)."""

    def __init__(self, text: str, icon_name: str | None, obj: str, active: bool = False, danger: bool = False):
        super().__init__()
        self.setObjectName(obj)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("active", active)
        self._active, self._danger, self._icon_name = active, danger, icon_name
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(12)
        self._icon = _icon_label(icon_name, theme.TEXT2, 16) if icon_name else None
        if self._icon:
            lay.addWidget(self._icon)
        self._label = QLabel(text)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self._label, 1)
        if active:
            lay.addWidget(_icon_label("check", theme.ACCENT, 16))
        self.setFixedHeight(40)
        self._paint_state(False)

    def _paint_state(self, hover: bool) -> None:
        if not self.isEnabled():
            text = icon_color = theme.TEXT3
        elif self._active:
            text, icon_color = theme.TEXT, theme.ACCENT
        elif hover:
            text = icon_color = theme.BAD if self._danger else theme.TEXT
        else:
            text = icon_color = theme.TEXT2
        weight = 600 if self._active else 400
        self._label.setStyleSheet(f"color: {text}; font-size: 13px; font-weight: {weight}; letter-spacing: 0.3px;")
        if self._icon:
            self._icon.setPixmap(pixmap(self._icon_name, icon_color, 16))

    def enterEvent(self, ev) -> None:
        self._paint_state(True)
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._paint_state(False)
        super().leaveEvent(ev)

    def changeEvent(self, ev) -> None:
        super().changeEvent(ev)
        if ev.type() == QEvent.Type.EnabledChange:
            self._paint_state(False)


class PopupPanel(QWidget):
    """Frameless popup with rounded corners, shadow and a fade/slide-in animation."""

    closed = pyqtSignal()
    SHADOW = 16

    def __init__(self, parent: QWidget, width: int = 280):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        outer = QVBoxLayout(self)
        m = self.SHADOW
        outer.setContentsMargins(m, m - 6, m, m + 6)
        self.panel = QFrame()
        self.panel.setObjectName("PopupPanel")
        self.panel.setFixedWidth(width)
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 170))
        self.panel.setGraphicsEffect(shadow)
        outer.addWidget(self.panel)
        self.body = QVBoxLayout(self.panel)
        self.body.setContentsMargins(8, 10, 8, 8)
        self.body.setSpacing(2)
        self._anim: QPropertyAnimation | None = None

    def add_header(self, text: str) -> None:
        h = QLabel(text.upper())
        h.setObjectName("H3")
        h.setContentsMargins(12, 4, 12, 8)
        self.body.addWidget(h)

    def add_item(self, text: str, on_click: Callable[[], None], *, icon_name: str | None = None,
                 active: bool = False) -> None:
        row = _Row(text, icon_name, "PopupItem", active=active)
        row.clicked.connect(lambda: (self.close(), on_click()))
        self.body.addWidget(row)

    def add_action(self, text: str, icon_name: str, on_click: Callable[[], None], *,
                   enabled: bool = True, danger: bool = False) -> None:
        row = _Row(text, icon_name, "PopupAction", danger=danger)
        row.setEnabled(enabled)
        row.clicked.connect(lambda: (self.close(), on_click()))
        self.body.addWidget(row)

    def add_separator(self) -> None:
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {theme.BG4}; margin: 6px 6px;")
        self.body.addSpacing(4)
        self.body.addWidget(line)
        self.body.addSpacing(4)

    def show_below(self, anchor: QWidget) -> None:
        self.adjustSize()
        x = anchor.width() // 2 - self.width() // 2
        pos = anchor.mapToGlobal(QPoint(x, anchor.height() + 4 - (self.SHADOW - 6)))
        self.move(pos)
        self.setWindowOpacity(0.0)
        self.show()
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(140)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._anim = anim

    def closeEvent(self, ev) -> None:
        self.closed.emit()
        super().closeEvent(ev)
