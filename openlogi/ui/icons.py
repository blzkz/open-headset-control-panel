"""Inline stroke icons (24×24) rendered to any colour at any size."""

from __future__ import annotations

from functools import lru_cache

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QIcon, QImage, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

_PATHS: dict[str, str] = {
    "equalizer": '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3"/>'
                 '<path d="M1 14h6M9 8h6M17 16h6"/>',
    "mic": '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v1a7 7 0 0 0 14 0v-1M12 18v4M8 22h8"/>',
    "mic-off": '<path d="M2 2l20 20M9 9v2a3 3 0 0 0 5.1 2.1M15 9.3V5a3 3 0 0 0-5.9-.8"/>'
               '<path d="M17 16.9A7 7 0 0 1 5 11v-1M19 10v1a7 7 0 0 1-.1 1.2M12 18v4M8 22h8"/>',
    "surround": '<circle cx="12" cy="12" r="3"/><path d="M6.3 6.3a8 8 0 0 0 0 11.4M17.7 6.3a8 8 0 0 1 0 11.4"/>'
                '<path d="M3.5 3.5a12 12 0 0 0 0 17M20.5 3.5a12 12 0 0 1 0 17"/>',
    "volume": '<path d="M11 5L6 9H2v6h4l5 4V5z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14"/>',
    "volume-off": '<path d="M11 5L6 9H2v6h4l5 4V5z"/><path d="M22 9l-6 6M16 9l6 6"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-5M12 8h.01"/>',
    "settings": '<circle cx="12" cy="12" r="3"/>'
                '<path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3'
                ' 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1'
                'a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1'
                'a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3'
                'H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1'
                'a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1'
                'a1.7 1.7 0 0 0-1.5 1z"/>',
    "back": '<path d="M15 18l-6-6 6-6"/>',
    "chevron-down": '<path d="M6 9l6 6 6-6"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "minimize": '<path d="M6 12h12"/>',
    "maximize": '<rect x="6" y="6" width="12" height="12" rx="1"/>',
    "home": '<path d="M3 10l9-7 9 7v10a2 2 0 0 1-2 2h-4v-7H9v7H5a2 2 0 0 1-2-2z"/>',
    "headset": '<path d="M3 14v-2a9 9 0 0 1 18 0v2"/><path d="M21 15a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3z'
               'M3 15a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/><path d="M19 17v1a4 4 0 0 1-4 4h-3"/>',
    "bolt": '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    "plug": '<path d="M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0zM12 18v4"/>',
    "wireless": '<path d="M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M2 9a15 15 0 0 1 20 0"/><circle cx="12" cy="19.5" r=".8"/>',
    "light": '<path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.2 1 2V17h6v-.3c0-.8.4-1.5 1-2A7 7 0 0 0 12 2z"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "trash": '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6"/>',
    "save": '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/>',
    "reset": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
    "play": '<path d="M6 4l14 8-14 8z"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "edit": '<path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    "copy": '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    "check": '<path d="M20 6L9 17l-5-5"/>',
    "power": '<path d="M18.4 6.6a9 9 0 1 1-12.8 0M12 2v10"/>',
    "usb": '<circle cx="12" cy="20" r="2"/><path d="M12 18V3M12 3l-3 3M12 3l3 3M12 14l-5-3V8M12 12l5-3V7"/>'
           '<rect x="15.5" y="5" width="3" height="2.5"/><circle cx="7" cy="7" r="1.2"/>',
}


def _svg(name: str, color: str, stroke: float) -> bytes:
    body = _PATHS[name]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    ).encode()


@lru_cache(maxsize=512)
def pixmap(name: str, color: str = "#f1f1f3", size: int = 20, stroke: float = 1.8, dpr: float = 2.0) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(_svg(name, color, stroke)))
    px = int(size * dpr)
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, px, px))
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str = "#f1f1f3", active: str | None = None, size: int = 20) -> QIcon:
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size), QIcon.Mode.Normal, QIcon.State.Off)
    if active:
        ic.addPixmap(pixmap(name, active, size), QIcon.Mode.Normal, QIcon.State.On)
        ic.addPixmap(pixmap(name, active, size), QIcon.Mode.Active, QIcon.State.Off)
    return ic


def size(n: int) -> QSize:
    return QSize(n, n)
