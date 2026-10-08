"""Dark theme: colour tokens, fonts and the application stylesheet."""
from PySide6.QtGui import QColor, QFont

C = {
    "bg": "#0b0e13",
    "panel": "#131822",
    "panel_alt": "#1a2130",
    "panel_hi": "#222b3d",
    "border": "#273145",
    "video_bg": "#05070a",
    "text": "#e8ecf3",
    "muted": "#8b96a9",
    "dim": "#5b6578",
    "accent": "#4f8cff",
    "accent_dim": "#2a4a85",
    "ok": "#22c55e",
    "warn": "#f5a524",
    "err": "#ef4444",
}

SANS = ["Segoe UI", "Inter", "Helvetica Neue", "Arial", "sans-serif"]
MONO = ["Consolas", "DejaVu Sans Mono", "Menlo", "monospace"]

_SANS_CSS = ", ".join(f'"{f}"' if " " in f else f for f in SANS)


def qcolor(name: str, alpha: int = 255) -> QColor:
    color = QColor(C[name])
    color.setAlpha(alpha)
    return color


_WEIGHTS = {400: QFont.Normal, 500: QFont.Medium, 600: QFont.DemiBold, 700: QFont.Bold,
            800: QFont.ExtraBold, 900: QFont.Black}


def sans(size: float = 10, weight: int = 400) -> QFont:
    """weight is CSS-style (400 normal, 700 bold, 800 extra bold)."""
    font = QFont()
    font.setFamilies(SANS)
    font.setPointSizeF(size)
    font.setWeight(_WEIGHTS[weight])
    return font


def mono(size: float = 10, weight: int = 400) -> QFont:
    font = QFont()
    font.setFamilies(MONO)
    font.setStyleHint(QFont.Monospace)
    font.setPointSizeF(size)
    font.setWeight(_WEIGHTS[weight])
    return font


def pill_style(color_key: str) -> str:
    """Stylesheet for a rounded status chip in one of the palette colours."""
    color = C[color_key]
    return (
        f"QLabel {{ color: {color}; background: {C['panel']}; border: 1px solid {C['border']};"
        f" border-radius: 17px; padding: 7px 16px; font-weight: 700; letter-spacing: 1px; }}"
    )


STYLESHEET = f"""
* {{ font-family: {_SANS_CSS}; }}
QMainWindow, QDialog {{ background: {C['bg']}; }}
QWidget {{ color: {C['text']}; }}
QLabel {{ background: transparent; }}

#card {{ background: {C['panel']}; border: 1px solid {C['border']}; border-radius: 14px; }}
#caption {{ color: {C['muted']}; font-size: 8.5pt; font-weight: 700; letter-spacing: 1.5px; }}
#title {{ font-size: 17pt; font-weight: 800; letter-spacing: 1px; }}
#subtitle {{ color: {C['dim']}; font-size: 9pt; }}
#hint {{ color: {C['dim']}; font-size: 9pt; }}
#bigcount {{ font-size: 34pt; font-weight: 800; }}

QPushButton {{
    background: {C['panel_alt']}; color: {C['text']}; border: 1px solid {C['border']};
    border-radius: 9px; padding: 9px 18px; font-weight: 700;
}}
QPushButton:hover {{ background: {C['panel_hi']}; }}
QPushButton:pressed {{ background: {C['border']}; }}
QPushButton:disabled {{ color: {C['dim']}; background: {C['panel']}; border-color: {C['panel_alt']}; }}
QPushButton[variant="primary"] {{ background: {C['accent']}; border-color: {C['accent']}; color: white; }}
QPushButton[variant="primary"]:hover {{ background: #6a9fff; }}
QPushButton[variant="primary"]:disabled {{ background: {C['panel_alt']}; border-color: {C['panel_alt']}; color: {C['dim']}; }}
QPushButton[variant="ghost"] {{ background: transparent; color: {C['muted']}; }}
QPushButton[variant="ghost"]:hover {{ background: {C['panel_alt']}; color: {C['text']}; }}
QPushButton[variant="start"] {{ background: {C['ok']}; border-color: {C['ok']}; color: #05200d; font-size: 12pt; padding: 11px 30px; }}
QPushButton[variant="start"]:hover {{ background: #3ddc79; }}
QPushButton[variant="stop"] {{ background: {C['err']}; border-color: {C['err']}; color: white; font-size: 12pt; padding: 11px 30px; }}
QPushButton[variant="stop"]:hover {{ background: #f26a6a; }}
QPushButton[variant="stop"]:disabled, QPushButton[variant="start"]:disabled {{ background: {C['panel_alt']}; border-color: {C['panel_alt']}; color: {C['dim']}; }}
QPushButton[variant="editing"] {{ background: {C['warn']}; border-color: {C['warn']}; color: #2a1c00; }}
QPushButton[variant="editing"]:hover {{ background: #ffbb4d; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {C['panel_alt']}; border: 1px solid {C['border']}; border-radius: 8px;
    padding: 7px 10px; selection-background-color: {C['accent']};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {C['accent']}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {C['dim']}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {C['panel_alt']}; border: 1px solid {C['border']}; selection-background-color: {C['accent_dim']}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {C['border']}; background: {C['panel_alt']}; }}
QCheckBox::indicator:checked {{ background: {C['accent']}; border-color: {C['accent']}; }}

QProgressBar {{ background: {C['panel_alt']}; border: none; border-radius: 5px; max-height: 10px; min-height: 10px; }}
QProgressBar::chunk {{ background: {C['warn']}; border-radius: 5px; }}

QToolTip {{ background: {C['panel_hi']}; color: {C['text']}; border: 1px solid {C['border']}; padding: 5px 8px; }}
QMessageBox {{ background: {C['panel']}; }}
"""
