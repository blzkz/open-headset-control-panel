"""Reusable G HUB-style building blocks."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .. import theme


def label(text: str, obj: str | None = None, wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if obj:
        lb.setObjectName(obj)
    lb.setWordWrap(wrap)
    return lb


def hline() -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f"background: {theme.LINE};")
    return f


class ToggleSwitch(QAbstractButton):
    """Animated on/off switch."""

    def __init__(self, parent: QWidget | None = None, checked: bool = False):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(44, 24)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setCheckedSilently(self, on: bool) -> None:
        self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(False)
        self._pos = 1.0 if on else 0.0
        self.update()

    @pyqtProperty(float)
    def knob(self) -> float:
        return self._pos

    @knob.setter
    def knob(self, value: float) -> None:
        self._pos = value
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(44, 24)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        off, on = QColor(theme.BG4), QColor(theme.ACCENT)
        t = self._pos
        track = QColor(
            int(off.red() + (on.red() - off.red()) * t),
            int(off.green() + (on.green() - off.green()) * t),
            int(off.blue() + (on.blue() - off.blue()) * t),
        )
        if not self.isEnabled():
            track = QColor(theme.BG3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.left() + 3 + (r.width() - d - 6) * t
        p.setBrush(QColor("#ffffff") if self.isEnabled() else QColor(theme.TEXT3))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))


class Card(QFrame):
    """Rounded panel with optional uppercase title and trailing widget."""

    def __init__(self, title: str = "", trailing: QWidget | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(22, 18, 22, 20)
        self.body.setSpacing(14)
        if title or trailing:
            head = QHBoxLayout()
            head.setSpacing(10)
            if title:
                self.title = label(title.upper(), "H2")
                head.addWidget(self.title)
            head.addStretch(1)
            if trailing:
                head.addWidget(trailing)
            self.body.addLayout(head)


class ValueSlider(QWidget):
    """Title row with live value + horizontal slider. Emits ``valueChanged`` and ``released``."""

    valueChanged = pyqtSignal(int)
    released = pyqtSignal(int)

    def __init__(
        self,
        title: str,
        minimum: int,
        maximum: int,
        value: int = 0,
        fmt: Callable[[int], str] = str,
        step: int = 1,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.fmt = fmt
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        row = QHBoxLayout()
        self.title = label(title, "Muted")
        self.value_label = label("", "Value")
        row.addWidget(self.title)
        row.addStretch(1)
        row.addWidget(self.value_label)
        lay.addLayout(row)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(minimum, maximum)
        self.slider.setSingleStep(step)
        self.slider.setPageStep(max(step, (maximum - minimum) // 10))
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.setValue(value)
        lay.addWidget(self.slider)
        self.slider.valueChanged.connect(self._changed)
        self.slider.sliderReleased.connect(lambda: self.released.emit(self.slider.value()))
        # keyboard / wheel changes count as "released" too
        self.slider.actionTriggered.connect(self._action)
        self._update_label(value)

    def _action(self, action: int) -> None:
        if not self.slider.isSliderDown():
            # the value isn't updated yet at this point → defer
            QTimer.singleShot(0, lambda: self.released.emit(self.slider.value()))

    def _changed(self, v: int) -> None:
        self._update_label(v)
        self.valueChanged.emit(v)

    def _update_label(self, v: int) -> None:
        self.value_label.setText(self.fmt(v))

    def value(self) -> int:
        return self.slider.value()

    def setValueSilently(self, v: int) -> None:
        if self.slider.isSliderDown():
            return  # never fight the user while dragging
        self.slider.blockSignals(True)
        self.slider.setValue(v)
        self.slider.blockSignals(False)
        self._update_label(self.slider.value())


class SwitchRow(QWidget):
    """Label (+ optional description) with a toggle on the right."""

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, description: str = "", checked: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(3)
        self.title = label(title)
        text.addWidget(self.title)
        if description:
            self.description = label(description, "Faint", wrap=True)
            text.addWidget(self.description)
        lay.addLayout(text, 1)
        self.switch = ToggleSwitch(checked=checked)
        lay.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)
        self.switch.toggled.connect(self.toggled)

    def isChecked(self) -> bool:
        return self.switch.isChecked()

    def setCheckedSilently(self, on: bool) -> None:
        self.switch.setCheckedSilently(on)


class BatteryIcon(QWidget):
    """Horizontal battery glyph with fill level and charging bolt."""

    def __init__(self, parent: QWidget | None = None, width: int = 34, height: int = 16):
        super().__init__(parent)
        self.setFixedSize(width, height)
        self.percent: int | None = None
        self.charging = False

    def set_state(self, percent: int | None, charging: bool) -> None:
        if percent is not None and percent < 0:
            percent = None  # level unknown (some headsets only say "charging")
        self.percent, self.charging = percent, charging
        self.update()

    @staticmethod
    def color_for(percent: int | None, charging: bool) -> QColor:
        if percent is None:
            return QColor(theme.TEXT3)
        if charging:
            return QColor(theme.ACCENT)
        if percent <= 15:
            return QColor(theme.BAD)
        if percent <= 30:
            return QColor(theme.WARN)
        return QColor(theme.GOOD)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        body = QRectF(1, 1, w - 5, h - 2)
        p.setPen(QPen(QColor(theme.TEXT2), 1.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(body, 3, 3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.TEXT2))
        p.drawRoundedRect(QRectF(w - 3.5, h * 0.3, 2.5, h * 0.4), 1, 1)
        if self.percent is not None:
            inner = body.adjusted(2.5, 2.5, -2.5, -2.5)
            inner.setWidth(max(1.5, inner.width() * self.percent / 100))
            p.setBrush(self.color_for(self.percent, self.charging))
            p.drawRoundedRect(inner, 1.5, 1.5)
        if self.charging:
            c = body.center()
            s = h * 0.42
            path = QPainterPath(QPointF(c.x() + s * 0.15, c.y() - s))
            path.lineTo(c.x() - s * 0.55, c.y() + s * 0.12)
            path.lineTo(c.x() - s * 0.02, c.y() + s * 0.12)
            path.lineTo(c.x() - s * 0.15, c.y() + s)
            path.lineTo(c.x() + s * 0.55, c.y() - s * 0.12)
            path.lineTo(c.x() + s * 0.02, c.y() - s * 0.12)
            path.closeSubpath()
            p.setPen(QPen(QColor(theme.BG0), 1.2))
            p.setBrush(QColor("#ffffff"))
            p.drawPath(path)


class Chip(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("Chip")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)


class Toast(QLabel):
    """Transient message bubble anchored to the bottom of its parent."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setStyleSheet(
            f"background: {theme.BG4}; color: {theme.TEXT}; border: 1px solid #3a3a40;"
            "border-radius: 6px; padding: 10px 16px;"
        )
        self.setWordWrap(True)
        self.setMaximumWidth(520)
        self._timer = QTimer(self, singleShot=True, interval=4500)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def show_message(self, text: str, error: bool = False) -> None:
        color = theme.BAD if error else theme.ACCENT
        self.setStyleSheet(
            f"background: {theme.BG3}; color: {theme.TEXT}; border: 1px solid {color};"
            "border-radius: 6px; padding: 10px 16px;"
        )
        self.setText(text)
        self.adjustSize()
        par = self.parentWidget()
        self.move((par.width() - self.width()) // 2, par.height() - self.height() - 28)
        self.raise_()
        self.show()
        self._timer.start()
