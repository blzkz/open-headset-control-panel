#!/usr/bin/env python3
"""Draw openheadset/assets/images/headset-generic.png: a neutral headset for models without a photo."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "openheadset" / "assets" / "images" / "headset-generic.png"
W, H = 680, 860


def shade(top: str, bottom: str, rect: QRectF) -> QLinearGradient:
    g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    g.setColorAt(0, QColor(top))
    g.setColorAt(1, QColor(bottom))
    return g


def ear_cup(p: QPainter, cx: float, mirror: bool) -> None:
    # slider between headband and cup
    p.setPen(Qt.PenStyle.NoPen)
    slider = QRectF(cx - 16, 330, 32, 60)
    p.setBrush(shade("#5a5a64", "#2a2a30", slider))
    p.drawRoundedRect(slider, 8, 8)
    # shell
    shell = QRectF(cx - 125, 360, 250, 330)
    p.setPen(QPen(QColor("#6a6a74"), 2))
    p.setBrush(shade("#5a5a66", "#202026", shell))
    p.drawRoundedRect(shell, 120, 150)
    # cushion (towards the centre of the picture)
    off = 38 if not mirror else -38
    cushion = QRectF(cx - 105 + off, 380, 210, 290)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(shade("#2c2c33", "#0f0f12", cushion))
    p.drawRoundedRect(cushion, 100, 135)
    # plate with a soft highlight
    plate = QRectF(cx - 80, 420, 160, 210)
    g = QRadialGradient(QPointF(plate.center().x() - 25, plate.top() + 55), 150)
    g.setColorAt(0, QColor("#6a6a76"))
    g.setColorAt(1, QColor("#26262c"))
    p.setBrush(g)
    p.setPen(QPen(QColor("#55555f"), 2))
    p.drawRoundedRect(plate, 75, 100)


def main() -> None:
    QGuiApplication(sys.argv)
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # headband: outer steel band + padded inner band
    band = QPainterPath()
    band.moveTo(150, 360)
    band.cubicTo(120, 30, 560, 30, 530, 360)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor("#33333b"), 40, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawPath(band)
    p.setPen(QPen(QColor("#7a7a86"), 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawPath(band)
    pad = QPainterPath()
    pad.moveTo(200, 250)
    pad.cubicTo(215, 105, 465, 105, 480, 250)
    p.setPen(QPen(QColor("#45454e"), 34, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawPath(pad)

    ear_cup(p, 150, mirror=False)
    ear_cup(p, 530, mirror=True)

    # microphone boom on the left cup
    p.setBrush(Qt.BrushStyle.NoBrush)
    boom = QPainterPath()
    boom.moveTo(120, 640)
    boom.cubicTo(110, 760, 230, 800, 310, 770)
    p.setPen(QPen(QColor("#3a3a42"), 16, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawPath(boom)
    p.setPen(QPen(QColor("#5c5c66"), 24, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawPoint(QPointF(318, 766))
    p.end()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(OUT))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
