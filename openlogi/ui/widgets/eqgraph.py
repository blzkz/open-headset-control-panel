"""Interactive equalizer curve, modelled on the G HUB EQ editor."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QWheelEvent,
)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from .. import theme


def _fmt_freq(hz: int) -> str:
    return f"{hz / 1000:g}k" if hz >= 1000 else str(hz)


def _fmt_db(db: float) -> str:
    return f"{db:+g} dB" if db else "0 dB"


def _monotone_tangents(ys: list[float]) -> list[float]:
    """Fritsch–Carlson tangents: smooth curve without overshoot between bands."""
    n = len(ys)
    d = [ys[i + 1] - ys[i] for i in range(n - 1)]
    m = [0.0] * n
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, n - 1):
        m[i] = 0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0.0
            continue
        a, b = m[i] / d[i], m[i + 1] / d[i]
        s = a * a + b * b
        if s > 9:
            t = 3 / s ** 0.5
            m[i], m[i + 1] = t * a * d[i], t * b * d[i]
    return m


class EqualizerGraph(QWidget):
    gainsChanged = pyqtSignal(list)    # live while dragging
    gainsCommitted = pyqtSignal(list)  # on release / discrete change

    MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 52, 28, 28, 40

    def __init__(self, frequencies: tuple[int, ...] = (32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000),
                 db_range: int = 12, parent: QWidget | None = None):
        super().__init__(parent)
        self.freqs = tuple(frequencies)
        self.db_range = db_range
        self.step = 1.0  # smallest gain change the headset accepts
        self.gains: list[float] = [0.0] * len(self.freqs)
        self._shown: list[float] = [0.0] * len(self.freqs)
        self._hover = -1
        self._drag = -1
        self._selected = -1
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(260)
        self._anim.valueChanged.connect(self._on_anim)
        self._anim_from: list[float] = []
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(260)

    def sizeHint(self) -> QSize:
        return QSize(760, 360)

    # -- public API ------------------------------------------------------------------
    def set_bands(self, frequencies: tuple[int, ...], db_range: int, step: float = 1.0) -> None:
        if tuple(frequencies) == self.freqs and db_range == self.db_range and step == self.step:
            return
        self.freqs, self.db_range, self.step = tuple(frequencies), db_range, float(step) or 1.0
        self.gains = [0.0] * len(self.freqs)
        self._shown = [0.0] * len(self.freqs)
        self.update()

    def _snap(self, db: float) -> float:
        db = max(-self.db_range, min(self.db_range, float(db)))
        v = round(round(db / self.step) * self.step, 4)
        return float(int(v)) if v == int(v) else v

    @property
    def _nudge(self) -> float:
        """Wheel / arrow-key increment: the device step, but at least half a dB."""
        return self.step if self.step >= 0.5 else round(0.5 / self.step) * self.step

    def set_gains(self, gains: list[float], animate: bool = True) -> None:
        if self._drag >= 0 or len(gains) != len(self.freqs):
            return
        gains = [self._snap(g) for g in gains]
        if gains == self.gains and not self._anim.state() == QVariantAnimation.State.Running:
            return
        self.gains = gains
        if animate and self.isVisible():
            self._anim.stop()
            self._anim_from = list(self._shown)
            self._anim.setStartValue(0.0)
            self._anim.setEndValue(1.0)
            self._anim.start()
        else:
            self._shown = [float(g) for g in gains]
            self.update()

    def _on_anim(self, t: float) -> None:
        e = 1 - (1 - t) ** 3
        self._shown = [a + (b - a) * e for a, b in zip(self._anim_from, self.gains, strict=False)]
        self.update()

    # -- geometry --------------------------------------------------------------------
    def _plot(self) -> QRectF:
        return QRectF(self.MARGIN_L, self.MARGIN_T,
                      self.width() - self.MARGIN_L - self.MARGIN_R,
                      self.height() - self.MARGIN_T - self.MARGIN_B)

    def _x(self, i: int) -> float:
        r = self._plot()
        step = r.width() / len(self.freqs)
        return r.left() + step * (i + 0.5)

    def _y(self, db: float) -> float:
        r = self._plot()
        return r.center().y() - (db / self.db_range) * (r.height() / 2)

    def _db_at(self, y: float) -> float:
        r = self._plot()
        return self._snap((r.center().y() - y) / (r.height() / 2) * self.db_range)

    def _band_at(self, x: float) -> int:
        r = self._plot()
        if not (r.left() - 10 <= x <= r.right() + 10):
            return -1
        i = int((x - r.left()) / (r.width() / len(self.freqs)))
        return max(0, min(len(self.freqs) - 1, i))

    # -- painting ----------------------------------------------------------------------
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._plot()
        enabled = self.isEnabled()
        accent = QColor(theme.ACCENT if enabled else theme.TEXT3)

        small = QFont(self.font())
        small.setPointSizeF(8.5)
        p.setFont(small)

        # horizontal grid + dB labels
        step = 3 if self.db_range <= 12 else 6
        for db in range(-(self.db_range // step) * step, self.db_range + 1, step):
            y = self._y(db)
            if db == 0:
                p.setPen(QPen(QColor(theme.TEXT3), 1, Qt.PenStyle.DashLine))
            else:
                p.setPen(QPen(QColor(255, 255, 255, 14), 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            if db % 6 == 0:
                p.setPen(QColor(theme.TEXT3))
                txt = f"{db:+d}" if db else "0"
                p.drawText(QRectF(0, y - 8, self.MARGIN_L - 12, 16),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, txt)
        p.setPen(QColor(theme.TEXT3))
        p.drawText(QRectF(0, r.top() - 24, self.MARGIN_L - 12, 16),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "dB")

        # band tracks + frequency labels
        for i, f in enumerate(self.freqs):
            x = self._x(i)
            active = i in (self._hover, self._drag, self._selected)
            p.setPen(QPen(QColor(255, 255, 255, 36 if active else 16), 2 if active else 1))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(QColor(theme.TEXT if active else theme.TEXT2))
            p.drawText(QRectF(x - 30, r.bottom() + 10, 60, 18), Qt.AlignmentFlag.AlignCenter, _fmt_freq(f))
        p.setPen(QColor(theme.TEXT3))
        p.drawText(QRectF(r.right() - 40, r.bottom() + 10, 60, 18), Qt.AlignmentFlag.AlignRight, "")

        # curve
        pts = [QPointF(self._x(i), self._y(g)) for i, g in enumerate(self._shown)]
        pts = [QPointF(r.left(), pts[0].y()), *pts, QPointF(r.right(), pts[-1].y())]
        ys = [pt.y() for pt in pts]
        tangents = _monotone_tangents(ys)
        curve = QPainterPath(pts[0])
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            h = b.x() - a.x()
            curve.cubicTo(QPointF(a.x() + h / 3, a.y() + tangents[i] / 3),
                          QPointF(b.x() - h / 3, b.y() - tangents[i + 1] / 3), b)

        zero = self._y(0)
        fill = QPainterPath(curve)
        fill.lineTo(r.right(), zero)
        fill.lineTo(r.left(), zero)
        fill.closeSubpath()
        grad = QLinearGradient(0, r.top(), 0, r.bottom())
        c_hi, c_mid = QColor(accent), QColor(accent)
        c_hi.setAlpha(90)
        c_mid.setAlpha(18)
        grad.setColorAt(0.0, c_hi)
        grad.setColorAt(0.5, c_mid)
        grad.setColorAt(1.0, c_hi)
        p.fillPath(fill, QBrush(grad))

        glow = QColor(accent)
        glow.setAlpha(60)
        p.setPen(QPen(glow, 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPath(curve)
        p.setPen(QPen(accent, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPath(curve)

        # handles
        for i, g in enumerate(self._shown):
            c = QPointF(self._x(i), self._y(g))
            hot = i in (self._hover, self._drag)
            sel = i == self._selected and self.hasFocus()
            rad = 9.0 if hot or sel else 7.0
            if hot and enabled:
                halo = QRadialGradient(c, rad * 2.6)
                hc = QColor(accent)
                hc.setAlpha(110)
                halo.setColorAt(0, hc)
                halo.setColorAt(1, QColor(0, 0, 0, 0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(halo)
                p.drawEllipse(c, rad * 2.6, rad * 2.6)
            p.setPen(QPen(accent, 2.5))
            p.setBrush(QColor(theme.ACCENT) if sel and enabled else QColor(theme.BG0))
            p.drawEllipse(c, rad, rad)
            if not (sel and enabled):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#ffffff") if enabled else QColor(theme.TEXT3))
                p.drawEllipse(c, rad * 0.38, rad * 0.38)

        # value bubble
        idx = self._drag if self._drag >= 0 else self._hover
        if idx >= 0 and enabled:
            g = self.gains[idx]
            c = QPointF(self._x(idx), self._y(self._shown[idx]))
            txt = _fmt_db(g)
            bf = QFont(self.font())
            bf.setBold(True)
            bf.setPointSizeF(9)
            p.setFont(bf)
            w = p.fontMetrics().horizontalAdvance(txt) + 18
            box = QRectF(c.x() - w / 2, c.y() - 42, w, 24)
            if box.top() < 2:
                box.moveTop(c.y() + 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.BG4))
            p.drawRoundedRect(box, 4, 4)
            p.setPen(QColor(theme.TEXT))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, txt)

    # -- interaction -----------------------------------------------------------------------
    def _set_band(self, i: int, db: float, live: bool = True) -> None:
        db = self._snap(db)
        if self.gains[i] == db:
            return
        self._anim.stop()
        self.gains[i] = db
        self._shown = [float(g) for g in self.gains]
        self.update()
        if live:
            self.gainsChanged.emit(list(self.gains))

    def mousePressEvent(self, ev: QMouseEvent) -> None:
        if ev.button() != Qt.MouseButton.LeftButton or not self.isEnabled():
            return
        i = self._band_at(ev.position().x())
        if i < 0:
            return
        self._drag = self._selected = i
        self._set_band(i, self._db_at(ev.position().y()))
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, ev: QMouseEvent) -> None:
        if self._drag >= 0:
            self._set_band(self._drag, self._db_at(ev.position().y()))
            return
        i = self._band_at(ev.position().x())
        if i != self._hover:
            self._hover = i
            self.setCursor(Qt.CursorShape.OpenHandCursor if i >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, ev: QMouseEvent) -> None:
        if self._drag >= 0:
            self._drag = -1
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.gainsCommitted.emit(list(self.gains))
            self.update()

    def mouseDoubleClickEvent(self, ev: QMouseEvent) -> None:
        i = self._band_at(ev.position().x())
        if i >= 0 and self.isEnabled():
            self._set_band(i, 0)
            self.gainsCommitted.emit(list(self.gains))

    def leaveEvent(self, _ev) -> None:
        self._hover = -1
        self.update()

    def wheelEvent(self, ev: QWheelEvent) -> None:
        i = self._band_at(ev.position().x())
        if i < 0 or not self.isEnabled():
            return
        delta = self._nudge if ev.angleDelta().y() > 0 else -self._nudge
        self._selected = i
        self._set_band(i, self.gains[i] + delta)
        self.gainsCommitted.emit(list(self.gains))
        ev.accept()

    def keyPressEvent(self, ev: QKeyEvent) -> None:
        if not self.isEnabled():
            return
        if self._selected < 0:
            self._selected = 0
        k = ev.key()
        if k == Qt.Key.Key_Left:
            self._selected = max(0, self._selected - 1)
        elif k == Qt.Key.Key_Right:
            self._selected = min(len(self.freqs) - 1, self._selected + 1)
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown, Qt.Key.Key_0):
            n = self._nudge
            step = {Qt.Key.Key_Up: n, Qt.Key.Key_Down: -n, Qt.Key.Key_PageUp: 3 * n, Qt.Key.Key_PageDown: -3 * n}
            target = 0 if k == Qt.Key.Key_0 else self.gains[self._selected] + step[k]
            self._set_band(self._selected, target)
            self.gainsCommitted.emit(list(self.gains))
        else:
            super().keyPressEvent(ev)
            return
        self.update()

    def focusInEvent(self, ev) -> None:
        self.update()
        super().focusInEvent(ev)

    def focusOutEvent(self, ev) -> None:
        self.update()
        super().focusOutEvent(ev)
