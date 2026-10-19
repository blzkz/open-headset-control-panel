"""Decorative / visual widgets: hero headset image, 7.1 speaker map, level meter."""

from __future__ import annotations

import math
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from .. import theme

ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"


def asset_image(name: str) -> QPixmap:
    return QPixmap(str(ASSETS / "images" / name))


def _grayscale(pm: QPixmap) -> QPixmap:
    img = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    gray = img.convertToFormat(QImage.Format.Format_Grayscale8).convertToFormat(QImage.Format.Format_ARGB32)
    gray.setAlphaChannel(img.convertToFormat(QImage.Format.Format_Alpha8))
    return QPixmap.fromImage(gray)


class HeadsetView(QWidget):
    """Product shot on a soft spotlight with a floor reflection (G HUB device stage)."""

    clicked = pyqtSignal()

    def __init__(self, image: str = "headset-34-left.png", parent: QWidget | None = None, clickable: bool = True):
        super().__init__(parent)
        self._pix = asset_image(image)
        self._gray = _grayscale(self._pix) if not self._pix.isNull() else self._pix
        self._online = 0.0
        self._hover = 0.0
        self._clickable = clickable
        self._scaled_cache: dict[tuple, QPixmap] = {}
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(450)
        self._fade.valueChanged.connect(self._set_online_t)
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(200)
        self._hover_anim.valueChanged.connect(self._set_hover_t)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(240, 240)
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:
        return QSize(520, 560)

    def set_image(self, image: str) -> None:
        pm = asset_image(image)
        if not pm.isNull():
            self._pix, self._gray = pm, _grayscale(pm)
            self._scaled_cache.clear()
            self.update()

    def set_online(self, online: bool) -> None:
        target = 1.0 if online else 0.0
        if self._fade.endValue() == target and self._fade.state() == QVariantAnimation.State.Running:
            return
        self._fade.stop()
        self._fade.setStartValue(self._online)
        self._fade.setEndValue(target)
        self._fade.start()

    def _set_online_t(self, v: float) -> None:
        self._online = float(v)
        self.update()

    def _set_hover_t(self, v: float) -> None:
        self._hover = float(v)
        self.update()

    def _animate_hover(self, on: bool) -> None:
        if not self._clickable:
            return
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(1.0 if on else 0.0)
        self._hover_anim.start()

    def enterEvent(self, ev) -> None:
        self._animate_hover(True)
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._animate_hover(False)
        super().leaveEvent(ev)

    def mouseReleaseEvent(self, ev: QMouseEvent) -> None:
        if self._clickable and ev.button() == Qt.MouseButton.LeftButton and self.rect().contains(ev.pos()):
            self.clicked.emit()

    def _scaled(self, src: QPixmap, key: str, w: int, h: int) -> QPixmap:
        dpr = self.devicePixelRatioF()
        ck = (key, w, h, dpr)
        if ck not in self._scaled_cache:
            pm = src.scaled(int(w * dpr), int(h * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            if len(self._scaled_cache) > 8:
                self._scaled_cache.clear()
            self._scaled_cache[ck] = pm
        return self._scaled_cache[ck]

    def paintEvent(self, _ev) -> None:
        if self._pix.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        scale = 0.80 + 0.03 * self._hover
        box_w, box_h = int(w * scale), int(h * 0.80 * scale)
        on = self._scaled(self._pix, "c", box_w, box_h)
        off = self._scaled(self._gray, "g", box_w, box_h)
        lw, lh = on.width() / on.devicePixelRatio(), on.height() / on.devicePixelRatio()
        x = (w - lw) / 2
        y = (h * 0.86 - lh) / 2

        # spotlight
        center = QPointF(w / 2, y + lh * 0.48)
        spot = QRadialGradient(center, max(lw, lh) * 0.75)
        a = int(46 + 40 * self._online + 25 * self._hover)
        c1 = QColor(theme.ACCENT)
        c1.setAlpha(int(a * self._online))
        neutral = QColor(255, 255, 255, int(16 + 10 * self._hover))
        spot.setColorAt(0.0, c1 if self._online > 0.05 else neutral)
        spot.setColorAt(0.45, QColor(c1.red(), c1.green(), c1.blue(), int(a * 0.25 * self._online)))
        spot.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(spot)
        p.drawEllipse(center, max(lw, lh) * 0.75, max(lw, lh) * 0.75)

        # floor shadow
        floor_y = y + lh + 6
        shadow = QRadialGradient(QPointF(w / 2, floor_y), lw * 0.45)
        shadow.setColorAt(0.0, QColor(0, 0, 0, 200))
        shadow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.save()
        p.translate(w / 2, floor_y)
        p.scale(1.0, 0.12)
        p.translate(-w / 2, -floor_y)
        p.setBrush(shadow)
        p.drawEllipse(QPointF(w / 2, floor_y), lw * 0.45, lw * 0.45)
        p.restore()

        # product: cross-fade gray → colour
        p.setOpacity(0.55 + 0.45 * (1 - self._online))
        p.drawPixmap(QPointF(x, y), off)
        p.setOpacity(self._online)
        p.drawPixmap(QPointF(x, y), on)
        p.setOpacity(1.0)

        # reflection: mirrored copy faded out with an alpha mask (no background assumptions)
        refl = self._reflection(on if self._online > 0.5 else off)
        p.setOpacity(0.16 * (0.4 + 0.6 * self._online))
        p.drawPixmap(QPointF(x, y + lh + 2), refl)
        p.setOpacity(1.0)

    def _reflection(self, src: QPixmap) -> QPixmap:
        key = ("r", src.cacheKey(), src.width(), src.height())
        if key not in self._scaled_cache:
            img = src.toImage()
            img = img.flipped(Qt.Orientation.Vertical) if hasattr(img, "flipped") else img.mirrored(False, True)
            img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            img = img.copy(0, 0, img.width(), int(img.height() * 0.3))
            painter = QPainter(img)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            mask = QLinearGradient(0, 0, 0, img.height())
            mask.setColorAt(0.0, QColor(0, 0, 0, 255))
            mask.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.fillRect(img.rect(), mask)
            painter.end()
            pm = QPixmap.fromImage(img)
            pm.setDevicePixelRatio(src.devicePixelRatio())
            self._scaled_cache[key] = pm
        return self._scaled_cache[key]


class SpeakerMap(QWidget):
    """Top-down 7.1 layout around a listener; click a speaker to hear a test sound."""

    speakerClicked = pyqtSignal(str)

    LAYOUT = (
        ("FL", 30), ("FC", 0), ("FR", -30),
        ("SL", 90), ("SR", -90),
        ("RL", 150), ("RR", -150),
    )

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._hover = ""
        self._active = ""
        self._enabled_map = True
        self._pulse = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(420, 380)

    def flash(self, ch: str, ms: int = 900) -> None:
        self._active = ch
        self._pulse = 0.0
        self._timer.start()
        QTimer.singleShot(ms, self._stop_flash)

    def _stop_flash(self) -> None:
        self._active = ""
        self._timer.stop()
        self.update()

    def _tick(self) -> None:
        self._pulse = (self._pulse + 0.035) % 1.0
        self.update()

    def _geometry(self) -> tuple[QPointF, float]:
        c = QPointF(self.width() / 2, self.height() / 2 + 6)
        rad = min(self.width(), self.height()) * 0.36
        return c, rad

    def _pos(self, angle: float) -> QPointF:
        c, rad = self._geometry()
        a = math.radians(angle)
        return QPointF(c.x() - rad * math.sin(a), c.y() - rad * math.cos(a))

    def _speaker_at(self, pt: QPointF) -> str:
        for ch, ang in (*self.LAYOUT, ("LFE", None)):
            pos = self._lfe_pos() if ang is None else self._pos(ang)
            if (pos - pt).manhattanLength() < 34:
                return ch
        return ""

    def _lfe_pos(self) -> QPointF:
        c, rad = self._geometry()
        return QPointF(c.x(), c.y() + rad)  # behind the listener, between RL and RR

    def mouseMoveEvent(self, ev: QMouseEvent) -> None:
        ch = self._speaker_at(ev.position())
        if ch != self._hover:
            self._hover = ch
            self.setCursor(Qt.CursorShape.PointingHandCursor if ch else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, _ev) -> None:
        self._hover = ""
        self.update()

    def mouseReleaseEvent(self, ev: QMouseEvent) -> None:
        ch = self._speaker_at(ev.position())
        if ch and self.isEnabled():
            self.speakerClicked.emit(ch)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c, rad = self._geometry()
        enabled = self.isEnabled()
        accent = QColor(theme.ACCENT if enabled else theme.TEXT3)

        # rings
        for k, alpha in ((1.0, 40), (0.66, 22), (0.33, 14)):
            p.setPen(QPen(QColor(255, 255, 255, alpha), 1, Qt.PenStyle.DashLine if k < 1 else Qt.PenStyle.SolidLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, rad * k, rad * k)

        # listener (head seen from above)
        head = QRadialGradient(c, rad * 0.22)
        head.setColorAt(0, QColor(theme.BG4))
        head.setColorAt(1, QColor(theme.BG2))
        p.setBrush(head)
        p.setPen(QPen(QColor(theme.TEXT3), 1.5))
        p.drawEllipse(c, rad * 0.16, rad * 0.19)
        p.setBrush(QColor(theme.TEXT3))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(c.x(), c.y() - rad * 0.2), rad * 0.035, rad * 0.035)  # nose
        p.setBrush(accent if enabled else QColor(theme.TEXT3))
        for side in (-1, 1):
            p.drawRoundedRect(QRectF(c.x() + side * rad * 0.17 - rad * 0.035, c.y() - rad * 0.06,
                                     rad * 0.07, rad * 0.12), 3, 3)

        f = QFont(self.font())
        f.setBold(True)
        f.setPointSizeF(8.5)
        p.setFont(f)
        items = [(ch, self._pos(a)) for ch, a in self.LAYOUT] + [("LFE", self._lfe_pos())]
        for ch, pos in items:
            hot = ch == self._hover and enabled
            act = ch == self._active
            if act:
                for k in range(2):
                    t = (self._pulse + k * 0.5) % 1.0
                    ring = QColor(accent)
                    ring.setAlpha(int(180 * (1 - t)))
                    p.setPen(QPen(ring, 2))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawEllipse(pos, 18 + 26 * t, 18 + 26 * t)
            p.setPen(QPen(accent if (hot or act) else QColor(theme.BG4), 2))
            p.setBrush(QColor(theme.ACCENT if act else (theme.BG3 if hot else theme.BG2)))
            p.drawEllipse(pos, 21, 21)
            p.setPen(QColor("#00131c" if act else (theme.TEXT if enabled else theme.TEXT3)))
            p.drawText(QRectF(pos.x() - 22, pos.y() - 10, 44, 20), Qt.AlignmentFlag.AlignCenter, ch)


class LevelMeter(QWidget):
    """Horizontal segmented level meter with peak hold (input in dBFS)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(14)
        self.setMinimumWidth(160)
        self._level = -90.0
        self._peak = -90.0
        self._peak_age = 0
        self._decay = QTimer(self)
        self._decay.setInterval(40)
        self._decay.timeout.connect(self._fall)

    def set_level(self, dbfs: float) -> None:
        self._level = max(self._level - 1.5, dbfs) if dbfs < self._level else dbfs
        if dbfs >= self._peak:
            self._peak, self._peak_age = dbfs, 0
        if not self._decay.isActive():
            self._decay.start()
        self.update()

    def reset(self) -> None:
        self._level = self._peak = -90.0
        self._decay.stop()
        self.update()

    def _fall(self) -> None:
        self._level = max(-90.0, self._level - 1.2)
        self._peak_age += 1
        if self._peak_age > 25:
            self._peak = max(-90.0, self._peak - 1.5)
        if self._level <= -90 and self._peak <= -90:
            self._decay.stop()
        self.update()

    @staticmethod
    def _frac(db: float) -> float:
        return max(0.0, min(1.0, (db + 60.0) / 60.0))

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        w, h = self.width(), self.height()
        segs = max(10, w // 7)
        lit = int(self._frac(self._level) * segs)
        peak = int(self._frac(self._peak) * segs)
        sw = w / segs
        for i in range(segs):
            frac = i / segs
            if frac < 0.7:
                col = QColor(theme.GOOD)
            elif frac < 0.9:
                col = QColor(theme.WARN)
            else:
                col = QColor(theme.BAD)
            if i >= lit and i != peak - 1:
                col = QColor(theme.BG4)
            p.fillRect(QRectF(i * sw + 1, 0, sw - 2, h), col)
