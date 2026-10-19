"""G HUB-inspired dark theme: palette constants + application stylesheet."""

from __future__ import annotations

from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PyQt6.QtWidgets import QApplication

BG0 = "#0a0a0b"      # window
BG1 = "#111113"      # rails / bars
BG2 = "#18181b"      # cards
BG3 = "#222226"      # hover / inputs
BG4 = "#2c2c31"      # pressed / borders
LINE = "#26262b"
TEXT = "#f1f1f3"
TEXT2 = "#a3a3ad"
TEXT3 = "#6c6c76"
ACCENT = "#00b4f5"
ACCENT_HI = "#3fd0ff"
ACCENT_LO = "#0086bd"
GOOD = "#3ddc84"
WARN = "#ffb020"
BAD = "#ff4d5a"

FONT_FAMILIES = ("Inter", "Noto Sans", "Cantarell", "DejaVu Sans")


def pick_font() -> str:
    families = set(QFontDatabase.families())
    for fam in FONT_FAMILIES:
        if fam in families:
            return fam
    return QApplication.font().family()


STYLESHEET = f"""
* {{
    outline: none;
}}
QWidget {{
    color: {TEXT};
    font-size: 13px;
    selection-background-color: {ACCENT};
    selection-color: #000;
}}
QMainWindow, #Root {{
    background: {BG0};
}}
QToolTip {{
    background: {BG3};
    color: {TEXT};
    border: 1px solid {BG4};
    padding: 6px 8px;
    border-radius: 4px;
}}

/* ---- title bar ---- */
#TitleBar {{
    background: {BG1};
    border-bottom: 1px solid {LINE};
}}
#Brand {{
    font-size: 15px;
    font-weight: 800;
    letter-spacing: 3px;
}}
#BrandAccent {{
    font-size: 15px;
    font-weight: 300;
    letter-spacing: 3px;
    color: {ACCENT};
}}
#WinButton {{
    background: transparent;
    padding: 0;
    border: none;
    border-radius: 0;
    min-width: 46px;
    min-height: 48px;
}}
#WinButton:hover {{ background: {BG3}; }}
#ProfileButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
}}
#ProfileButton:hover {{ background: {BG2}; border-color: {BG4}; }}
#ProfileButton:checked {{ background: {BG2}; border-color: {ACCENT}; }}
#ProfileCaption {{
    color: {TEXT3};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
}}
#ProfileValue {{
    color: {TEXT};
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 1.5px;
}}

/* ---- popup panel (profile switcher) ---- */
#PopupPanel {{
    background: {BG2};
    border: 1px solid {BG4};
    border-radius: 10px;
}}
QPushButton#PopupItem, QPushButton#PopupAction {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 0;
    text-align: left;
}}
QPushButton#PopupItem:hover, QPushButton#PopupAction:hover {{ background: {BG3}; }}
QPushButton#PopupItem[active="true"] {{ background: rgba(0, 180, 245, 0.10); }}

/* ---- nav rail ---- */
#NavRail {{
    background: {BG1};
    border-right: 1px solid {LINE};
}}

/* ---- typography ---- */
#H1 {{
    font-size: 26px;
    font-weight: 300;
    letter-spacing: 4px;
}}
#H2 {{
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 2.5px;
    color: {TEXT};
}}
#H3 {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
    color: {TEXT3};
}}
#Muted {{ color: {TEXT2}; }}
#Faint {{ color: {TEXT3}; font-size: 12px; }}
#Value {{
    color: {ACCENT};
    font-weight: 700;
    font-size: 13px;
}}
#BigValue {{
    font-size: 38px;
    font-weight: 200;
}}

/* ---- cards ---- */
#Card {{
    background: {BG2};
    border: 1px solid {LINE};
    border-radius: 6px;
}}
#Panel {{
    background: {BG1};
    border-right: 1px solid {LINE};
}}

/* ---- buttons ---- */
QPushButton {{
    background: {BG3};
    border: 1px solid {BG4};
    border-radius: 4px;
    padding: 8px 16px;
    font-weight: 600;
    letter-spacing: 1px;
}}
QPushButton:hover {{ background: {BG4}; border-color: #3a3a40; }}
QPushButton:pressed {{ background: {BG2}; }}
QPushButton:disabled {{ color: {TEXT3}; }}
QPushButton#Primary {{
    background: {ACCENT};
    color: #00131c;
    border: none;
}}
QPushButton#Primary:hover {{ background: {ACCENT_HI}; }}
QPushButton#Ghost {{
    background: transparent;
    border: 1px solid {BG4};
    color: {TEXT2};
}}
QPushButton#Ghost:hover {{ color: {TEXT}; border-color: {TEXT3}; }}

/* preset list */
QPushButton#Preset {{
    background: transparent;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 0;
    text-align: left;
    padding: 11px 18px;
    font-weight: 500;
    letter-spacing: 0.5px;
    color: {TEXT2};
}}
QPushButton#Preset:hover {{ background: {BG2}; color: {TEXT}; }}
QPushButton#Preset:checked {{
    background: {BG2};
    color: {TEXT};
    border-left: 3px solid {ACCENT};
}}

/* ---- sliders ---- */
QSlider::groove:horizontal {{
    height: 4px;
    background: {BG4};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {TEXT};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: {ACCENT_HI}; }}
QSlider::groove:horizontal:disabled, QSlider::sub-page:horizontal:disabled {{ background: {BG3}; }}
QSlider::handle:horizontal:disabled {{ background: {TEXT3}; }}

/* ---- inputs ---- */
QComboBox, QSpinBox, QLineEdit {{
    background: {BG3};
    border: 1px solid {BG4};
    border-radius: 4px;
    padding: 6px 10px;
    min-height: 20px;
}}
QComboBox:hover, QSpinBox:hover, QLineEdit:hover {{ border-color: #3a3a40; }}
QComboBox:focus, QSpinBox:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {BG2};
    border: 1px solid {BG4};
    border-radius: 6px;
    selection-background-color: {BG3};
    selection-color: {TEXT};
    padding: 6px;
    outline: 0;
}}
QComboBox QAbstractItemView::item {{
    min-height: 30px;
    padding: 2px 10px;
    border-radius: 4px;
}}
QComboBox QAbstractItemView::item:hover {{ background: {BG3}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}

QCheckBox {{ spacing: 10px; }}

/* ---- menus ---- */
QMenu {{
    background: {BG2};
    border: 1px solid {BG4};
    padding: 6px 0;
}}
QMenu::item {{ padding: 8px 28px 8px 20px; }}
QMenu::item:selected {{ background: {BG4}; }}
QMenu::item:disabled {{ color: {TEXT3}; }}
QMenu::separator {{ height: 1px; background: {BG4}; margin: 6px 0; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 4px; }}

/* ---- scroll ---- */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {BG4};
    border-radius: 3px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #3a3a40; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {BG4};
    border-radius: 3px;
    min-width: 30px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QDialog {{ background: {BG1}; }}
QMessageBox {{ background: {BG1}; }}

#Banner {{
    background: rgba(255, 176, 32, 0.10);
    border: 1px solid rgba(255, 176, 32, 0.35);
    border-radius: 4px;
    color: {WARN};
    padding: 8px 12px;
}}
#Chip {{
    background: {BG2};
    border: 1px solid {LINE};
    border-radius: 12px;
    padding: 4px 12px;
    color: {TEXT2};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1px;
}}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    font = QFont(pick_font())
    font.setPointSizeF(10)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(font)
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: BG0,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: BG3,
        QPalette.ColorRole.AlternateBase: BG2,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: BG3,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.Highlight: ACCENT,
        QPalette.ColorRole.HighlightedText: "#000000",
        QPalette.ColorRole.ToolTipBase: BG3,
        QPalette.ColorRole.ToolTipText: TEXT,
        QPalette.ColorRole.PlaceholderText: TEXT3,
        QPalette.ColorRole.Link: ACCENT,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(TEXT3))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(TEXT3))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(TEXT3))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET)
