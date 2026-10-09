"""Colours, Qt style sheets and vector icons (no external image files needed).

The look follows Windows 11 Fluent: neutral layered surfaces (window → panel → card), 1 px hairline borders,
6–8 px radii, one blue accent with hover / pressed shades and thin overlay-style scroll bars.
"""

from __future__ import annotations

import hashlib
import os
import tempfile

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer

ACCENT = "#2F7DE1"
#: accent shades for hover / pressed (shared by both themes)
ACCENT_HOVER = "#4A8FE8"
ACCENT_PRESSED = "#2468C2"
#: UI font stack (Windows 11 first, then the bundled-everywhere fallbacks)
FONT_STACK = '"Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif'
#: status badge colours (filled pill, white text) – the same in both themes
BADGE = {"error": "#D13438", "warn": "#C27C0E", "ok": "#0F9D58", "info": ACCENT}

PALETTES = {
    "light": {
        # surfaces: window (Mica) → panel (docks, ribbon) → card (group boxes, inputs, popups)
        "bg": "#F1F3F6",
        "panel": "#FAFBFC",
        "card": "#FFFFFF",
        "text": "#1B2430",
        "muted": "#5B6675",
        "border": "#DFE3E9",
        "border_strong": "#C3CAD4",
        "hover": "#EDF0F4",
        "pressed": "#E3E7ED",
        "input": "#FFFFFF",
        "input_hover": "#F8F9FB",
        "gridline": "#ECEFF3",
        "header": "#F5F7F9",
        "sel": "#DCE9FA",
        "sel_hover": "#CFE1F8",
        "accent_text": "#1C62C2",
        "scroll": "#C2C9D2",
        "scroll_hover": "#8F99A6",
        "disabled_bg": "#F1F3F5",
        "disabled_text": "#A1A9B4",
        "tooltip": "#FFFFFF",
        "tooltip_text": "#1B2430",
        "ribbon_top": ACCENT,
        "app_button": "#1E5BB0",
        "canvas": "#FBFCFE",
        "grid_minor": "#EEF1F5",
        "grid_major": "#DCE2EA",
        "slab": "#DCEBFF",
        "slab_cant": "#FFE7C2",
        "slab_grade": "#E5E7EB",
        "slab_edge": "#7FA6D9",
        "beam": "#1F4E99",
        "beam_ext": "#0E2F66",
        "beam_cant": "#C2410C",
        "column": "#1B2430",
        "wall": "#8B5CF6",
        "select": "#F59E0B",
        "error": "#DC2626",
        "ok": "#16A34A",
        "warn": "#D97706",
        "text_canvas": "#334155",
        "chat_user": "#E3EEFF",
        "chat_bot": "#F1F3F6",
        "alt_row": "#F8F9FB",
        "link": "#1D4ED8",
    },
    "dark": {
        "bg": "#15181D",
        "panel": "#1C2026",
        "card": "#22272E",
        "text": "#E6EAF0",
        "muted": "#98A2B3",
        "border": "#2D333C",
        "border_strong": "#444C58",
        "hover": "#2A3039",
        "pressed": "#323943",
        "input": "#262B33",
        "input_hover": "#2B313A",
        "gridline": "#2A3038",
        "header": "#20252C",
        "sel": "#21406B",
        "sel_hover": "#284B7A",
        "accent_text": "#82B6FF",
        "scroll": "#4A525E",
        "scroll_hover": "#707A88",
        "disabled_bg": "#1F2329",
        "disabled_text": "#5F6875",
        "tooltip": "#2B3038",
        "tooltip_text": "#E6EAF0",
        "ribbon_top": "#1F5FB8",
        "app_button": "#174A93",
        "canvas": "#10141A",
        "grid_minor": "#1A2029",
        "grid_major": "#262E3A",
        "slab": "#1E3A5F",
        "slab_cant": "#5B4320",
        "slab_grade": "#2A2F38",
        "slab_edge": "#4C77AE",
        "beam": "#7FB2FF",
        "beam_ext": "#A9CBFF",
        "beam_cant": "#FB923C",
        "column": "#E6EAF0",
        "wall": "#A78BFA",
        "select": "#FBBF24",
        "error": "#F87171",
        "ok": "#4ADE80",
        "warn": "#FBBF24",
        "text_canvas": "#CBD5E1",
        "chat_user": "#1F3B63",
        "chat_bot": "#232A35",
        "alt_row": "#20252C",
        "link": "#93C5FD",
    },
}

# ------------------------------------------------------------- QSS image assets
# Qt style sheets cannot load ``data:`` URLs, so the few glyphs the style sheet needs (chevrons, check mark,
# dock buttons) are written once as tiny SVG files to a per-user temp folder and referenced by path.
_QSS_SVG = {
    "chevron_down": '<path d="M3 6l5 5 5-5" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    "chevron_up": '<path d="M3 10l5-5 5 5" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    "chevron_right": '<path d="M6 3l5 5-5 5" fill="none" stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    "check": '<path d="M3.5 8.5l3 3 6-7" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    "partial": '<path d="M4 8h8" stroke="{c}" stroke-width="1.8" stroke-linecap="round"/>',
    "close": '<path d="M4.5 4.5l7 7M11.5 4.5l-7 7" stroke="{c}" stroke-width="1.3" stroke-linecap="round"/>',
    "float": '<rect x="3.5" y="5.5" width="7" height="7" rx="1" fill="none" stroke="{c}" stroke-width="1.2"/><path d="M6 3.5h5.5a1 1 0 011 1V10" fill="none" stroke="{c}" stroke-width="1.2"/>',
}


def _asset_dir() -> str | None:
    d = os.path.join(tempfile.gettempdir(), "planwin_ai_qss")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return None
    return d


def _asset(name: str, color: str) -> str:
    """Path (forward slashes) of the SVG glyph ``name`` in ``color``; "" when it cannot be written."""
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">' + _QSS_SVG[name].format(c=color) + "</svg>"
    d = _asset_dir()
    if d is None:
        return ""
    digest = hashlib.sha1(svg.encode()).hexdigest()[:10]
    path = os.path.join(d, f"{name}_{digest}.svg")
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(svg)
        except OSError:
            return ""
    return path.replace("\\", "/")


def _img(name: str, color: str) -> str:
    """``image: url(...)`` declaration for a glyph, or ``image: none`` when the asset is unavailable."""
    p = _asset(name, color)
    return f'image: url("{p}")' if p else "image: none"


def qpalette(theme: str) -> QPalette:
    """QPalette matching the style sheet, so widgets the sheet does not reach never fall back to the system
    palette (on Windows in dark mode that showed up as black boxes). Use with ``app.setPalette``."""
    c = PALETTES[theme]
    pal = QPalette()
    roles = {
        QPalette.Window: c["panel"],
        QPalette.WindowText: c["text"],
        QPalette.Base: c["card"],
        QPalette.AlternateBase: c["alt_row"],
        QPalette.Text: c["text"],
        QPalette.Button: c["card"],
        QPalette.ButtonText: c["text"],
        QPalette.BrightText: "#FFFFFF",
        QPalette.Highlight: ACCENT,
        QPalette.HighlightedText: "#FFFFFF",
        QPalette.ToolTipBase: c["tooltip"],
        QPalette.ToolTipText: c["tooltip_text"],
        QPalette.PlaceholderText: c["muted"],
        QPalette.Link: c["link"],
        QPalette.Light: c["card"],
        QPalette.Midlight: c["hover"],
        QPalette.Mid: c["border_strong"],
        QPalette.Dark: c["border_strong"],
        QPalette.Shadow: "#000000",
    }
    for role, col in roles.items():
        pal.setColor(role, QColor(col))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(c["disabled_text"]))
    return pal


def qss(theme: str) -> str:
    c = PALETTES[theme]
    dark = theme == "dark"
    on_accent_soft = "rgba(130, 182, 255, 0.20)" if dark else "rgba(47, 125, 225, 0.13)"
    on_accent_soft_hover = "rgba(130, 182, 255, 0.28)" if dark else "rgba(47, 125, 225, 0.20)"
    chev = _img("chevron_down", c["muted"])
    chev_up = _img("chevron_up", c["muted"])
    chev_right = _img("chevron_right", c["muted"])
    chev_dis = _img("chevron_down", c["disabled_text"])
    check = _img("check", "#FFFFFF")
    partial = _img("partial", "#FFFFFF")
    close = _asset("close", c["muted"])
    flt = _asset("float", c["muted"])
    dock_icons = (f'titlebar-close-icon: url("{close}");' if close else "") + (
        f' titlebar-normal-icon: url("{flt}");' if flt else ""
    )
    return f"""
    /* ------------------------------------------------------------ base */
    QWidget {{ color: {c["text"]}; font-family: {FONT_STACK}; font-size: 9.5pt; }}
    QMainWindow, QDialog {{ background: {c["bg"]}; color: {c["text"]}; }}
    QMainWindow::separator {{ background: {c["bg"]}; width: 6px; height: 6px; }}
    QMainWindow::separator:hover {{ background: {on_accent_soft}; }}
    QLabel {{ background: transparent; }}
    QLabel:disabled {{ color: {c["disabled_text"]}; }}
    QToolTip {{ background: {c["tooltip"]}; color: {c["tooltip_text"]}; border: 1px solid {c["border_strong"]};
                border-radius: 4px; padding: 6px 8px; font-size: 9pt; }}

    /* ------------------------------------------------------------ docks, panels, scroll areas */
    QDockWidget {{ font-weight: 600; {dock_icons} }}
    QDockWidget::title {{ background: {c["panel"]}; padding: 8px 10px 7px 12px; border: none;
                          border-bottom: 1px solid {c["border"]}; text-align: left; }}
    QDockWidget::close-button, QDockWidget::float-button {{ border: none; background: transparent; border-radius: 4px;
                                                            padding: 2px; }}
    QDockWidget::close-button:hover, QDockWidget::float-button:hover {{ background: {c["hover"]}; }}
    QDockWidget::close-button:pressed, QDockWidget::float-button:pressed {{ background: {c["pressed"]}; }}
    QDockWidget > QWidget {{ background: {c["panel"]}; }}
    QScrollArea {{ background: {c["panel"]}; border: none; }}
    QScrollArea > QWidget#qt_scrollarea_viewport, QScrollArea > QWidget#qt_scrollarea_viewport > QWidget {{
        background: {c["panel"]}; }}
    QAbstractScrollArea {{ background: {c["panel"]}; }}
    QStackedWidget {{ background: transparent; }}
    QSplitter::handle {{ background: transparent; }}
    QSplitter::handle:hover {{ background: {on_accent_soft}; }}
    QSplitter::handle:horizontal {{ width: 6px; }}
    QSplitter::handle:vertical {{ height: 6px; }}
    QFrame[frameShape="4"], QFrame[frameShape="5"] {{ color: {c["border"]}; }}

    QGroupBox {{ background: {c["card"]}; border: 1px solid {c["border"]}; border-radius: 8px; margin-top: 14px;
                 padding: 10px 8px 8px 8px; font-weight: 600; }}
    QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 10px; padding: 0 4px;
                        color: {c["text"]}; }}
    QStatusBar {{ background: {c["panel"]}; border-top: 1px solid {c["border"]}; color: {c["muted"]}; }}
    QStatusBar::item {{ border: none; }}
    QStatusBar QLabel {{ color: {c["muted"]}; padding: 0 6px; }}

    /* ------------------------------------------------------------ scroll bars (thin overlay style) */
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px 1px 2px 1px; border: none; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 1px 2px 1px 2px; border: none; }}
    QScrollBar::handle:vertical {{ background: {c["scroll"]}; min-height: 32px; border-radius: 3px; margin: 0 2px; }}
    QScrollBar::handle:horizontal {{ background: {c["scroll"]}; min-width: 32px; border-radius: 3px; margin: 2px 0; }}
    QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {{ background: {c["scroll_hover"]};
                                                                             border-radius: 4px; margin: 0; }}
    QScrollBar::handle:horizontal:hover, QScrollBar::handle:horizontal:pressed {{ background: {c["scroll_hover"]};
                                                                                 border-radius: 4px; margin: 0; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
    QAbstractScrollArea::corner {{ background: transparent; border: none; }}

    /* ------------------------------------------------------------ buttons */
    QPushButton {{ background: {c["card"]}; color: {c["text"]}; border: 1px solid {c["border"]};
                   border-bottom-color: {c["border_strong"]}; border-radius: 6px; padding: 4px 14px; min-height: 18px; }}
    QPushButton:hover {{ background: {c["input_hover"] if not dark else c["hover"]}; border-color: {c["border_strong"]}; }}
    QPushButton:pressed {{ background: {c["pressed"]}; color: {c["muted"]}; border-color: {c["border"]}; }}
    QPushButton:checked {{ background: {c["sel"]}; color: {c["accent_text"]}; border-color: {c["sel"]}; }}
    QPushButton:focus {{ border-color: {ACCENT}; }}
    QPushButton:disabled {{ background: {c["disabled_bg"]}; color: {c["disabled_text"]}; border-color: {c["border"]}; }}
    QPushButton:default {{ border-color: {ACCENT}; }}
    QPushButton#primary {{ background: {ACCENT}; color: #FFFFFF; border: 1px solid {ACCENT};
                           border-bottom-color: {ACCENT_PRESSED}; font-weight: 600; }}
    QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
    QPushButton#primary:pressed {{ background: {ACCENT_PRESSED}; border-color: {ACCENT_PRESSED};
                                   color: rgba(255, 255, 255, 0.85); }}
    QPushButton#primary:focus {{ border: 1px solid {c["accent_text"]}; }}
    QPushButton#primary:disabled {{ background: {c["disabled_bg"]}; color: {c["disabled_text"]};
                                    border-color: {c["border"]}; }}
    QPushButton#chip {{ border-radius: 11px; padding: 2px 10px; min-height: 0; font-size: 8.5pt;
                        background: {c["card"]}; border: 1px solid {c["border"]}; }}
    QPushButton#chip:hover {{ background: {c["sel"]}; border-color: {c["sel"]}; color: {c["accent_text"]}; }}
    QPushButton::menu-indicator {{ {chev}; subcontrol-origin: padding; subcontrol-position: center right;
                                   width: 10px; height: 10px; right: 4px; }}

    QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 4px 6px; }}
    QToolButton:hover {{ background: {c["hover"]}; }}
    QToolButton:pressed {{ background: {c["pressed"]}; }}
    QToolButton:checked {{ background: {on_accent_soft}; color: {c["accent_text"]}; }}
    QToolButton:checked:hover {{ background: {on_accent_soft_hover}; }}
    QToolButton:disabled {{ color: {c["disabled_text"]}; }}
    QToolButton::menu-indicator {{ {chev}; width: 9px; height: 9px; subcontrol-position: bottom right; }}
    QToolButton#aiButton {{ background: {ACCENT}; color: #FFFFFF; font-weight: 600; padding: 4px 10px; }}
    QToolButton#aiButton:hover {{ background: {ACCENT_HOVER}; }}
    QToolButton#aiButton:pressed, QToolButton#aiButton:checked {{ background: {ACCENT_PRESSED}; color: #FFFFFF; }}

    /* ------------------------------------------------------------ inputs */
    QLineEdit, QAbstractSpinBox, QComboBox {{ background: {c["input"]}; color: {c["text"]}; border: 1px solid {c["border"]};
        border-bottom-color: {c["border_strong"]}; border-radius: 6px; padding: 3px 8px; min-height: 20px;
        selection-background-color: {ACCENT}; selection-color: #FFFFFF; }}
    QLineEdit:hover, QAbstractSpinBox:hover, QComboBox:hover {{ background: {c["input_hover"]}; }}
    QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus, QComboBox:on {{ background: {c["input"]};
        border: 1px solid {ACCENT}; }}
    QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled {{ background: {c["disabled_bg"]};
        color: {c["disabled_text"]}; border-color: {c["border"]}; }}
    QLineEdit:read-only {{ background: {c["panel"]}; }}
    QLineEdit {{ placeholder-text-color: {c["muted"]}; }}
    QAbstractSpinBox {{ padding-right: 22px; }}
    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ subcontrol-origin: border; width: 20px;
        border: none; background: transparent; }}
    QAbstractSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: 6px; margin: 2px 2px 0 0; }}
    QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: 6px;
                                     margin: 0 2px 2px 0; }}
    QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {c["hover"]}; }}
    QAbstractSpinBox::up-button:pressed, QAbstractSpinBox::down-button:pressed {{ background: {c["pressed"]}; }}
    QAbstractSpinBox::up-arrow {{ {chev_up}; width: 9px; height: 9px; }}
    QAbstractSpinBox::down-arrow {{ {chev}; width: 9px; height: 9px; }}
    QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off {{ image: none; }}
    QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {{ image: none; }}
    QComboBox {{ padding-right: 26px; }}
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 24px; border: none;
                            background: transparent; }}
    QComboBox::down-arrow {{ {chev}; width: 10px; height: 10px; }}
    QComboBox::down-arrow:disabled {{ {chev_dis}; }}
    QComboBox QAbstractItemView {{ background: {c["card"]}; border: 1px solid {c["border"]}; border-radius: 8px;
        padding: 4px; outline: 0; selection-background-color: {c["sel"]}; selection-color: {c["text"]}; }}
    QComboBox QAbstractItemView::item {{ min-height: 24px; padding: 0 8px; border-radius: 4px; }}
    QComboBox QAbstractItemView::item:hover {{ background: {c["hover"]}; }}
    QComboBox QAbstractItemView::item:selected {{ background: {c["sel"]}; color: {c["text"]}; }}
    QTextEdit, QPlainTextEdit, QTextBrowser {{ background: {c["card"]}; border: 1px solid {c["border"]};
        border-radius: 8px; padding: 2px; selection-background-color: {ACCENT}; selection-color: #FFFFFF; }}
    QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}

    QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
    QCheckBox:disabled, QRadioButton:disabled {{ color: {c["disabled_text"]}; }}
    QCheckBox::indicator, QRadioButton::indicator, QAbstractItemView::indicator {{ width: 16px; height: 16px;
        background: {c["input"]}; border: 1px solid {c["border_strong"]}; }}
    QCheckBox::indicator, QAbstractItemView::indicator {{ border-radius: 4px; }}
    QRadioButton::indicator {{ border-radius: 9px; }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover, QAbstractItemView::indicator:hover {{
        border-color: {ACCENT}; background: {c["input_hover"]}; }}
    QCheckBox::indicator:checked, QAbstractItemView::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT};
        {check}; }}
    QCheckBox::indicator:indeterminate, QAbstractItemView::indicator:indeterminate {{ background: {ACCENT};
        border-color: {ACCENT}; {partial}; }}
    QCheckBox::indicator:checked:hover, QAbstractItemView::indicator:checked:hover {{ background: {ACCENT_HOVER};
        border-color: {ACCENT_HOVER}; }}
    QRadioButton::indicator:checked {{ border: 1px solid {ACCENT}; background: qradialgradient(cx: 0.5, cy: 0.5,
        radius: 0.5, fx: 0.5, fy: 0.5, stop: 0 #FFFFFF, stop: 0.45 #FFFFFF, stop: 0.55 {ACCENT}, stop: 1 {ACCENT}); }}
    QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ background: {c["disabled_bg"]};
        border-color: {c["border"]}; }}

    /* ------------------------------------------------------------ item views */
    QAbstractItemView {{ background: {c["card"]}; alternate-background-color: {c["alt_row"]};
        border: 1px solid {c["border"]}; border-radius: 8px; gridline-color: {c["gridline"]};
        selection-background-color: {c["sel"]}; selection-color: {c["text"]}; outline: 0; }}
    QAbstractItemView:disabled {{ color: {c["disabled_text"]}; }}
    QListView::item, QTreeView::item {{ padding: 3px 4px; border-radius: 4px; }}
    QListView::item:hover, QTreeView::item:hover, QTableView::item:hover {{ background: {c["hover"]}; }}
    QListView::item:selected, QTreeView::item:selected, QTableView::item:selected {{ background: {c["sel"]}; }}
    QListView::item:selected:hover, QTreeView::item:selected:hover, QTableView::item:selected:hover {{
        background: {c["sel_hover"]}; }}
    QTreeView::branch {{ background: transparent; }}
    QTreeView::branch:has-children:closed {{ {chev_right}; }}
    QTreeView::branch:has-children:open {{ {chev}; }}
    QHeaderView {{ background: {c["header"]}; border: none; border-top-left-radius: 8px; border-top-right-radius: 8px; }}
    QHeaderView::section {{ background: {c["header"]}; color: {c["muted"]}; padding: 4px 6px; border: none;
        border-bottom: 1px solid {c["border"]}; border-right: 1px solid {c["gridline"]}; font-weight: 600;
        font-size: 8.5pt; }}
    QHeaderView::section:vertical {{ border-bottom: 1px solid {c["gridline"]}; border-right: 1px solid {c["border"]};
        padding: 2px 6px; }}
    QHeaderView::section:hover {{ background: {c["hover"]}; color: {c["text"]}; }}
    QHeaderView::down-arrow {{ {chev}; width: 9px; height: 9px; }}
    QHeaderView::up-arrow {{ {chev_up}; width: 9px; height: 9px; }}
    QTableCornerButton::section {{ background: {c["header"]}; border: none; border-bottom: 1px solid {c["border"]};
        border-right: 1px solid {c["border"]}; }}
    QAbstractItemView QLineEdit, QAbstractItemView QAbstractSpinBox {{ min-height: 0; padding: 0 4px;
        border-radius: 3px; border: 1px solid {ACCENT}; }}
    QAbstractItemView QComboBox {{ min-height: 0; padding: 0 14px 0 3px; border-radius: 4px;
        border: 1px solid transparent; background: {c["card"]}; }}
    QAbstractItemView QComboBox::drop-down {{ width: 14px; }}
    QAbstractItemView QComboBox:hover {{ border-color: {c["border"]}; background: {c["input_hover"]}; }}
    QAbstractItemView QComboBox:focus, QAbstractItemView QComboBox:on {{ border-color: {ACCENT}; }}

    /* ------------------------------------------------------------ tabs (underline style) */
    QTabWidget::pane {{ border: 1px solid {c["border"]}; border-radius: 8px; background: {c["card"]}; top: -1px; }}
    QTabWidget::tab-bar {{ left: 4px; }}
    QTabBar {{ qproperty-drawBase: 0; background: transparent; }}
    QTabBar::tab {{ background: transparent; color: {c["muted"]}; border: none; border-bottom: 2px solid transparent;
                    padding: 6px 12px; margin: 0 2px 0 0; border-top-left-radius: 6px; border-top-right-radius: 6px; }}
    QTabBar::tab:hover:!selected {{ background: {c["hover"]}; color: {c["text"]}; }}
    QTabBar::tab:selected {{ color: {c["text"]}; border-bottom: 2px solid {ACCENT}; font-weight: 600; }}
    QTabBar::tab:disabled {{ color: {c["disabled_text"]}; }}
    QTabBar::close-button {{ image: url("{close}"); subcontrol-position: right; border-radius: 4px; }}
    QTabBar::close-button:hover {{ background: {c["pressed"]}; }}
    QTabBar::scroller {{ width: 22px; }}
    QTabBar QToolButton {{ background: {c["panel"]}; border: none; border-radius: 4px; padding: 0; }}

    /* ------------------------------------------------------------ menus */
    QMenuBar {{ background: {c["panel"]}; border-bottom: 1px solid {c["border"]}; }}
    QMenuBar::item {{ background: transparent; padding: 4px 10px; border-radius: 4px; }}
    QMenuBar::item:selected {{ background: {c["hover"]}; }}
    QMenu {{ background: {c["card"]}; border: 1px solid {c["border_strong"] if dark else c["border"]}; border-radius: 6px;
             padding: 4px; }}
    QMenu::item {{ background: transparent; padding: 6px 28px 6px 10px; margin: 1px 0; border-radius: 4px; }}
    QMenu::item:selected {{ background: {c["sel"]}; color: {c["text"]}; }}
    QMenu::item:disabled {{ color: {c["disabled_text"]}; background: transparent; }}
    QMenu::icon {{ padding-left: 6px; }}
    QMenu::separator {{ height: 1px; background: {c["border"]}; margin: 4px 6px; }}
    QMenu::right-arrow {{ {chev_right}; width: 10px; height: 10px; }}
    QMenu::indicator {{ width: 14px; height: 14px; left: 4px; }}
    QMenu::indicator:checked {{ {_img("check", c["accent_text"])}; }}

    /* ------------------------------------------------------------ ribbon */
    QWidget#Ribbon {{ background: {c["panel"]}; border-bottom: 1px solid {c["border"]}; }}
    QWidget#RibbonTop {{ background: {c["ribbon_top"]}; }}
    QWidget#RibbonBody, QWidget#Ribbon QScrollArea {{ background: {c["panel"]}; border: none; }}
    QToolButton#AppButton {{ background: {c["app_button"]}; color: #FFFFFF; font-weight: 600; padding: 4px 16px;
                             border: none; border-radius: 0; }}
    QToolButton#AppButton:hover {{ background: {ACCENT_PRESSED}; }}
    QToolButton#AppButton::menu-indicator {{ image: none; width: 0; }}
    QToolButton#QuickButton {{ padding: 2px; border: none; border-radius: 4px; background: transparent; }}
    QToolButton#QuickButton:hover {{ background: rgba(255, 255, 255, 0.22); }}
    QToolButton#QuickButton:pressed {{ background: rgba(255, 255, 255, 0.32); }}
    QLabel#RibbonTitle {{ color: #FFFFFF; font-weight: 600; padding-left: 12px; padding-right: 8px; }}
    QTabBar#RibbonTabs {{ background: transparent; }}
    QTabBar#RibbonTabs::tab {{ background: transparent; border: none; color: rgba(255, 255, 255, 0.92); padding: 6px 14px;
                              margin: 3px 1px 0 1px; border-top-left-radius: 6px; border-top-right-radius: 6px;
                              font-weight: 400; }}
    QTabBar#RibbonTabs::tab:selected {{ background: {c["panel"]}; color: {c["accent_text"]}; font-weight: 600; }}
    QTabBar#RibbonTabs::tab:hover:!selected {{ background: rgba(255, 255, 255, 0.16); color: #FFFFFF; }}
    QFrame#RibbonGroup {{ background: transparent; border: none; border-right: 1px solid {c["border"]}; }}
    QLabel#RibbonGroupTitle {{ color: {c["muted"]}; font-size: 8pt; }}
    QLabel#RibbonCaption {{ color: {c["muted"]}; font-size: 8pt; padding: 0 2px; }}
    QToolButton#RibbonLarge {{ padding: 3px 6px; border: 1px solid transparent; border-radius: 6px; min-width: 52px; }}
    QToolButton#RibbonSmall {{ padding: 1px 6px; border: 1px solid transparent; border-radius: 4px; text-align: left; }}
    QToolButton#RibbonLarge:hover, QToolButton#RibbonSmall:hover {{ background: {c["hover"]}; }}
    QToolButton#RibbonLarge:pressed, QToolButton#RibbonSmall:pressed {{ background: {c["pressed"]}; }}
    QToolButton#RibbonLarge:checked, QToolButton#RibbonSmall:checked {{ background: {on_accent_soft};
        border: 1px solid transparent; color: {c["accent_text"]}; }}
    QToolButton#RibbonLarge:checked:hover, QToolButton#RibbonSmall:checked:hover {{ background: {on_accent_soft_hover}; }}
    QToolButton#RibbonLarge:disabled, QToolButton#RibbonSmall:disabled {{ color: {c["disabled_text"]}; }}
    QLineEdit#CommandSearch {{ background: rgba(255, 255, 255, 0.16); color: #FFFFFF;
        border: 1px solid rgba(255, 255, 255, 0.28); border-radius: 6px; padding: 2px 10px; min-height: 18px;
        min-width: 220px; max-width: 320px; placeholder-text-color: rgba(255, 255, 255, 0.70);
        selection-background-color: rgba(255, 255, 255, 0.35); selection-color: #FFFFFF; }}
    QLineEdit#CommandSearch:hover {{ background: rgba(255, 255, 255, 0.22); }}
    QLineEdit#CommandSearch:focus {{ background: {c["card"]}; color: {c["text"]}; border: 1px solid #FFFFFF;
        placeholder-text-color: {c["muted"]}; selection-background-color: {ACCENT}; }}
    QListWidget#CommandPopup {{ background: {c["card"]}; border: 1px solid {c["border_strong"] if dark else c["border"]};
        border-radius: 8px; padding: 4px; outline: 0; }}
    QListWidget#CommandPopup::item {{ padding: 6px 10px; border-radius: 4px; margin: 1px 0; }}
    QListWidget#CommandPopup::item:hover {{ background: {c["hover"]}; }}
    QListWidget#CommandPopup::item:selected {{ background: {c["sel"]}; }}

    /* ------------------------------------------------------------ start page */
    QWidget#StartPage {{ background: {c["bg"]}; }}
    QWidget#StartHero {{ background: transparent; }}
    QLabel#StartTitle {{ font-size: 22pt; font-weight: 600; color: {c["text"]}; }}
    QLabel#StartSubtitle {{ font-size: 10.5pt; color: {c["muted"]}; }}
    QFrame#StartCard {{ background: {c["card"]}; border: 1px solid {c["border"]}; border-radius: 10px; }}
    QFrame#StartCard:hover {{ border: 1px solid {ACCENT}; background: {c["input_hover"] if not dark else c["hover"]}; }}
    QLabel#StartCardTitle {{ font-size: 10pt; font-weight: 600; color: {c["text"]}; background: transparent; }}
    QLabel#StartCardText {{ font-size: 8.5pt; color: {c["muted"]}; background: transparent; }}
    QWidget#StartBody {{ background: {c["bg"]}; }}
    QLabel#StartSection {{ font-size: 12pt; font-weight: 600; color: {c["text"]}; margin-top: 14px; padding-bottom: 2px; }}
    QLineEdit#StartPrompt {{ background: {c["card"]}; border: 1px solid {c["border_strong"]}; border-radius: 12px;
        padding: 10px 16px; min-height: 24px; font-size: 11pt; }}
    QLineEdit#StartPrompt:focus {{ border: 2px solid {ACCENT}; padding: 9px 15px; }}

    /* ------------------------------------------------------------ results navigator and tables
       ResultsNav: setRootIsDecorated(False) + setIndentation(0) (children are indented by the sheet); with a
       branch area Qt paints the selected pill there too. No `color:` on its items: the per-item foreground
       (group headers, badges) must win. */
    QTreeWidget#ResultsNav {{ background: transparent; border: none; padding: 4px; show-decoration-selected: 0;
        selection-background-color: transparent; }}
    QTreeWidget#ResultsNav::item {{ padding: 5px 8px 5px 22px; margin: 1px 0; border-radius: 6px; }}
    QTreeWidget#ResultsNav::item:has-children {{ padding: 8px 8px 4px 8px; }}
    QTreeWidget#ResultsNav::item:hover {{ background: {c["hover"]}; }}
    QTreeWidget#ResultsNav::item:selected {{ background: {on_accent_soft}; }}
    QTreeWidget#ResultsNav::branch, QTreeWidget#ResultsNav::branch:selected, QTreeWidget#ResultsNav::branch:hover {{
        background: transparent; image: none; border: none; }}
    QLineEdit#ResultsFilter {{ border-radius: 6px; padding-left: 10px; }}
    QToolButton#TableAction {{ padding: 3px 10px; border: 1px solid {c["border"]}; border-radius: 6px;
                               background: {c["card"]}; font-size: 8.5pt; }}
    QToolButton#TableAction:hover {{ background: {c["hover"]}; border-color: {c["border_strong"]}; }}
    QToolButton#TableAction:pressed {{ background: {c["pressed"]}; }}
    QLabel#Badge {{ background: #6B7280; color: #FFFFFF; border-radius: 8px; padding: 1px 7px; font-size: 8pt;
                    font-weight: 600; }}
    QLabel#Badge[kind="error"] {{ background: {BADGE["error"]}; }}
    QLabel#Badge[kind="warn"] {{ background: {BADGE["warn"]}; }}
    QLabel#Badge[kind="ok"] {{ background: {BADGE["ok"]}; }}
    QLabel#Badge[kind="info"] {{ background: {BADGE["info"]}; }}
    QLabel#ResultsCount {{ color: {c["muted"]}; font-size: 8.5pt; padding: 0 4px; }}
    QLabel#ResultsEmpty {{ color: {c["muted"]}; font-size: 10pt; qproperty-alignment: AlignCenter; padding: 24px; }}

    /* ------------------------------------------------------------ collapsible panel sections
       SectionHeader: QToolButton with ToolButtonTextBesideIcon (text is then left-aligned) and either
       setArrowType(Right/DownArrow) or icon("chevron_right" / "chevron_down", muted colour). */
    QWidget#PanelSection {{ background: {c["card"]}; border: 1px solid {c["border"]}; border-radius: 8px; }}
    QToolButton#SectionHeader {{ background: transparent; border: none; border-radius: 6px; padding: 6px 8px;
                                 font-weight: 600; color: {c["text"]}; }}
    QToolButton#SectionHeader:hover {{ background: {c["hover"]}; }}
    QToolButton#SectionHeader:checked {{ background: transparent; color: {c["text"]}; }}
    QToolButton#SectionHeader:checked:hover {{ background: {c["hover"]}; }}
    QWidget#SectionBody {{ background: transparent; border: none; }}
    QLabel#SectionBadge {{ background: {c["hover"]}; color: {c["muted"]}; border-radius: 8px; padding: 0 6px;
                           font-size: 8pt; font-weight: 600; min-width: 8px; }}
    QLabel#PanelTitle {{ font-size: 11pt; font-weight: 600; color: {c["text"]}; padding: 2px 2px 4px 2px; }}
    QLabel#PanelHint {{ color: {c["muted"]}; font-size: 8.5pt; qproperty-wordWrap: true; padding: 2px; }}
    QWidget#PanelButtons {{ background: {c["panel"]}; border-top: 1px solid {c["border"]}; }}
    """


# --------------------------------------------------------------------- icons
_SVG = {
    "select": '<path d="M5 3l14 9-6 1.5L16 21l-3 1-3-7.5L5 18z" fill="{c}"/>',
    "pan": '<path d="M12 2l3 3h-2v6h6V9l3 3-3 3v-2h-6v6h2l-3 3-3-3h2v-6H5v2l-3-3 3-3v2h6V5H9z" fill="{c}"/>',
    "rect": '<rect x="4" y="5" width="16" height="14" rx="1" fill="none" stroke="{c}" stroke-width="2"/><path d="M4 5l16 14M20 5L4 19" stroke="{c}" stroke-width="0.8" opacity="0.6"/>',
    "poly": '<path d="M4 18l3-12 10 2 3 9-8 3z" fill="none" stroke="{c}" stroke-width="2"/>',
    "column": '<rect x="7" y="5" width="10" height="14" fill="{c}"/>',
    "beam": '<rect x="2" y="10" width="20" height="4" fill="{c}"/><rect x="2" y="7" width="3" height="10" fill="{c}"/><rect x="19" y="7" width="3" height="10" fill="{c}"/>',
    "autocol": '<rect x="3" y="3" width="5" height="5" fill="{c}"/><rect x="16" y="3" width="5" height="5" fill="{c}"/><rect x="3" y="16" width="5" height="5" fill="{c}"/><rect x="16" y="16" width="5" height="5" fill="{c}"/><path d="M12 8v8M8 12h8" stroke="{c}" stroke-width="2"/>',
    "autobeam": '<path d="M3 4h18M3 20h18M4 3v18M20 3v18" stroke="{c}" stroke-width="2.5"/><path d="M9 12h6" stroke="{c}" stroke-width="2"/>',
    "analyze": '<path d="M3 20h18M5 17l4-6 4 3 6-9" fill="none" stroke="{c}" stroke-width="2"/>',
    "frame": '<path d="M4 20V6l8-3 8 3v14M4 13h16M12 3v17" fill="none" stroke="{c}" stroke-width="2"/>',
    "design": '<path d="M4 20l4-1 11-11-3-3L5 16zM14 6l3 3" fill="none" stroke="{c}" stroke-width="2"/>',
    "ai": '<path d="M12 2l2.2 5.8L20 10l-5.8 2.2L12 18l-2.2-5.8L4 10l5.8-2.2z" fill="{c}"/><circle cx="19" cy="19" r="2" fill="{c}"/>',
    "export": '<path d="M12 3v12M7 10l5 5 5-5M4 20h16" fill="none" stroke="{c}" stroke-width="2"/>',
    "new": '<path d="M6 2h8l5 5v15H6z" fill="none" stroke="{c}" stroke-width="2"/><path d="M14 2v5h5" fill="none" stroke="{c}" stroke-width="2"/>',
    "open": '<path d="M3 6h6l2 2h10v11H3z" fill="none" stroke="{c}" stroke-width="2"/>',
    "save": '<path d="M4 4h13l3 3v13H4z" fill="none" stroke="{c}" stroke-width="2"/><rect x="8" y="4" width="7" height="5" fill="{c}"/><rect x="7" y="13" width="10" height="5" fill="none" stroke="{c}" stroke-width="1.5"/>',
    "undo": '<path d="M9 7H16a5 5 0 010 10H8M9 7l-4 4M9 7L5 3" fill="none" stroke="{c}" stroke-width="2"/>',
    "redo": '<path d="M15 7H8a5 5 0 000 10h8M15 7l4 4M15 7l4-4" fill="none" stroke="{c}" stroke-width="2"/>',
    "zoomfit": '<path d="M3 9V3h6M15 3h6v6M21 15v6h-6M9 21H3v-6" fill="none" stroke="{c}" stroke-width="2"/>',
    "template": '<rect x="3" y="3" width="8" height="8" fill="none" stroke="{c}" stroke-width="2"/><rect x="13" y="3" width="8" height="8" fill="{c}"/><rect x="3" y="13" width="8" height="8" fill="{c}"/><rect x="13" y="13" width="8" height="8" fill="none" stroke="{c}" stroke-width="2"/>',
    "settings": '<circle cx="12" cy="12" r="3.5" fill="none" stroke="{c}" stroke-width="2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1" stroke="{c}" stroke-width="2"/>',
    "measure": '<path d="M3 17L17 3l4 4L7 21z" fill="none" stroke="{c}" stroke-width="2"/><path d="M8 12l2 2M11 9l2 2M14 6l2 2" stroke="{c}" stroke-width="1.5"/>',
    "wall": '<rect x="3" y="9" width="18" height="6" fill="{c}"/><path d="M3 9v6M21 9v6" stroke="{c}" stroke-width="2"/>',
    "area": '<path d="M4 18l2-12 12 2 2 10z" fill="{c}" opacity="0.35" stroke="{c}" stroke-width="2"/><text x="8" y="16" font-size="7" fill="{c}">m²</text>',
    "grid": '<path d="M7 3v18M17 3v18M3 7h18M3 17h18" stroke="{c}" stroke-width="1.6" stroke-dasharray="3 1.5"/><circle cx="7" cy="3" r="2" fill="{c}"/><circle cx="17" cy="3" r="2" fill="{c}"/>',
    "dims": '<path d="M3 16h18M3 12v8M21 12v8M6 16l-3 0M18 16h3" stroke="{c}" stroke-width="1.8"/><path d="M5 14l-2 2 2 2M19 14l2 2-2 2" fill="none" stroke="{c}" stroke-width="1.5"/><text x="8" y="11" font-size="7" fill="{c}">4.5</text>',
    "stairs": '<path d="M3 20h4v-4h4v-4h4V8h4V4h2" fill="none" stroke="{c}" stroke-width="2.2"/>',
    "tank": '<rect x="5" y="4" width="14" height="10" rx="1" fill="none" stroke="{c}" stroke-width="2"/><path d="M5 10h14" stroke="{c}" stroke-width="1.2"/><path d="M7 14v7M17 14v7" stroke="{c}" stroke-width="2"/>',
    "compare": '<rect x="3" y="5" width="7" height="14" fill="none" stroke="{c}" stroke-width="2"/><rect x="14" y="9" width="7" height="10" fill="{c}"/><path d="M10 12h4" stroke="{c}" stroke-width="1.5"/>',
    "revision": '<circle cx="12" cy="12" r="8" fill="none" stroke="{c}" stroke-width="2"/><path d="M12 7v5l3 3" stroke="{c}" stroke-width="2" fill="none"/>',
    "calc": '<rect x="5" y="3" width="14" height="18" rx="2" fill="none" stroke="{c}" stroke-width="2"/><rect x="8" y="6" width="8" height="4" fill="{c}"/><path d="M8 14h2M14 14h2M8 18h2M14 18h2" stroke="{c}" stroke-width="2"/>',
    "bbs": '<path d="M4 6h16M4 6v4M20 6v4M4 14h12l4 4" fill="none" stroke="{c}" stroke-width="2.2"/>',
    "drawing": '<rect x="3" y="4" width="18" height="16" fill="none" stroke="{c}" stroke-width="2"/><rect x="12" y="14" width="9" height="6" fill="{c}"/><path d="M6 8h8M6 11h5" stroke="{c}" stroke-width="1.5"/>',
    "ductile": '<path d="M12 2l8 4v6c0 5-4 8-8 10-4-2-8-5-8-10V6z" fill="none" stroke="{c}" stroke-width="2"/><path d="M8 12l3 3 5-6" fill="none" stroke="{c}" stroke-width="2"/>',
    "irregular": '<path d="M4 20V10l4-2v12M10 20V6l4 2v12M16 20V12l4-2v10" fill="none" stroke="{c}" stroke-width="2"/>',
    "modal": '<path d="M2 12c3-8 5-8 8 0s5 8 8 0 3-5 4-3" fill="none" stroke="{c}" stroke-width="2"/>',
    "units": '<text x="2" y="11" font-size="9" font-weight="bold" fill="{c}">kN</text><text x="13" y="21" font-size="9" font-weight="bold" fill="{c}">t</text><path d="M6 20l12-16" stroke="{c}" stroke-width="1.5"/>',
    "optimize": '<path d="M4 20l6-6 4 4 6-10" fill="none" stroke="{c}" stroke-width="2"/><path d="M16 8h4v4" fill="none" stroke="{c}" stroke-width="2"/>',
    "autosize": '<rect x="8" y="4" width="8" height="16" fill="none" stroke="{c}" stroke-width="2"/><path d="M4 12h3M17 12h3M5 10l-2 2 2 2M19 10l2 2-2 2" fill="none" stroke="{c}" stroke-width="1.6"/>',
    "sizes": '<path d="M4 4h16v16H4z" fill="none" stroke="{c}" stroke-width="1.5"/><path d="M4 10h16M4 15h16M10 4v16" stroke="{c}" stroke-width="1.5"/>',
    "jointload": '<path d="M12 2v12M8 10l4 4 4-4" fill="none" stroke="{c}" stroke-width="2"/><rect x="8" y="16" width="8" height="6" fill="{c}"/>',
    "levels": '<path d="M3 6h18M3 12h18M3 18h18" stroke="{c}" stroke-width="2.2"/><path d="M6 6v12M18 6v12" stroke="{c}" stroke-width="1.2"/>',
    "copyfloor": '<rect x="4" y="12" width="14" height="8" fill="none" stroke="{c}" stroke-width="2"/><rect x="7" y="4" width="14" height="8" fill="{c}" opacity="0.5"/>',
    "view3d": '<path d="M12 3l8 4v10l-8 4-8-4V7z" fill="none" stroke="{c}" stroke-width="2"/><path d="M12 11l8-4M12 11L4 7M12 11v10" stroke="{c}" stroke-width="1.5"/>',
    "help": '<circle cx="12" cy="12" r="9" fill="none" stroke="{c}" stroke-width="2"/><path d="M9.5 9a2.5 2.5 0 015 0c0 2-2.5 2-2.5 4" fill="none" stroke="{c}" stroke-width="2"/><circle cx="12" cy="17" r="1.2" fill="{c}"/>',
    "licence": '<rect x="3" y="6" width="18" height="12" rx="2" fill="none" stroke="{c}" stroke-width="2"/><circle cx="8" cy="12" r="2.5" fill="{c}"/><path d="M13 10h5M13 14h3" stroke="{c}" stroke-width="1.6"/>',
    "theme": '<circle cx="12" cy="12" r="8" fill="none" stroke="{c}" stroke-width="2"/><path d="M12 4a8 8 0 010 16z" fill="{c}"/>',
    "delete": '<path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13" fill="none" stroke="{c}" stroke-width="2"/>',
    "move": '<path d="M12 3v18M3 12h18M12 3l-3 3M12 3l3 3M12 21l-3-3M12 21l3-3M3 12l3-3M3 12l3 3M21 12l-3-3M21 12l-3 3" fill="none" stroke="{c}" stroke-width="1.8"/>',
    "mirror": '<path d="M12 3v18" stroke="{c}" stroke-width="1.5" stroke-dasharray="2 2"/><path d="M10 6L3 18h7zM14 6l7 12h-7z" fill="none" stroke="{c}" stroke-width="1.8"/>',
    "renumber": '<text x="3" y="11" font-size="8" fill="{c}">1 2</text><text x="3" y="21" font-size="8" fill="{c}">3 4</text><path d="M17 5v14M14 16l3 3 3-3" fill="none" stroke="{c}" stroke-width="1.6"/>',
    "plan": '<rect x="3" y="3" width="18" height="18" fill="none" stroke="{c}" stroke-width="2"/><path d="M3 12h9V3M12 12v9" stroke="{c}" stroke-width="1.5"/>',
    "folder": '<path d="M3 6h6l2 2h10v11H3z" fill="{c}" opacity="0.4" stroke="{c}" stroke-width="1.6"/>',
    "staad": '<text x="1" y="16" font-size="9" font-weight="bold" fill="{c}">STD</text>',
    "etabs": '<text x="1" y="16" font-size="9" font-weight="bold" fill="{c}">E2K</text>',
    "excel": '<rect x="3" y="3" width="18" height="18" rx="2" fill="{c}"/><path d="M8 8l8 8M16 8l-8 8" stroke="#fff" stroke-width="2.2"/>',
    "pdf": '<path d="M6 2h8l5 5v15H6z" fill="none" stroke="{c}" stroke-width="2"/><text x="7" y="17" font-size="6.5" font-weight="bold" fill="{c}">PDF</text>',
    "chevron_right": '<path d="M9 5l7 7-7 7" fill="none" stroke="{c}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
    "chevron_down": '<path d="M5 9l7 7 7-7" fill="none" stroke="{c}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5" fill="none" stroke="{c}" stroke-width="2"/><path d="M15.5 15.5L21 21" stroke="{c}" stroke-width="2.2" stroke-linecap="round"/>',
    "dxf": '<path d="M6 2h8l5 5v15H6z" fill="none" stroke="{c}" stroke-width="2"/><text x="7" y="17" font-size="6.5" font-weight="bold" fill="{c}">DXF</text>',
}


def icon(name: str, color: str = "#2F7DE1", size: int = 24) -> QIcon:
    body = _SVG.get(name, _SVG["ai"]).format(c=color)
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body}</svg>'
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    ic = QIcon()
    for s in (size, size * 2):
        pm = QPixmap(QSize(s, s))
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        renderer.render(p)
        p.end()
        ic.addPixmap(pm)
    return ic


def color(theme: str, key: str) -> QColor:
    return QColor(PALETTES[theme][key])


def round_popup(w) -> None:
    """Let a popup (QMenu, completer list, ...) show the style sheet's rounded corners cleanly: without a
    translucent window the pixels outside the radius are left unpainted (black on Windows)."""
    w.setWindowFlags(w.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    w.setAttribute(Qt.WA_TranslucentBackground, True)
