"""Colours, Qt style sheets and vector icons (no external image files needed)."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ACCENT = "#2F7DE1"

PALETTES = {
    "light": {
        "bg": "#F4F6F9", "panel": "#FFFFFF", "text": "#1B2430", "muted": "#5B6675", "border": "#D5DBE3",
        "canvas": "#FBFCFE", "grid_minor": "#EEF1F5", "grid_major": "#DCE2EA", "slab": "#DCEBFF", "slab_cant": "#FFE7C2",
        "slab_grade": "#E5E7EB", "slab_edge": "#7FA6D9", "beam": "#1F4E99", "beam_ext": "#0E2F66", "beam_cant": "#C2410C",
        "column": "#1B2430", "select": "#F59E0B", "error": "#DC2626", "ok": "#16A34A", "warn": "#D97706",
        "text_canvas": "#334155", "chat_user": "#E3EEFF", "chat_bot": "#F1F3F6", "alt_row": "#F6F8FB", "link": "#1D4ED8",
    },
    "dark": {
        "bg": "#14181F", "panel": "#1B2029", "text": "#E6EAF0", "muted": "#98A2B3", "border": "#2C3442",
        "canvas": "#10141A", "grid_minor": "#1A2029", "grid_major": "#262E3A", "slab": "#1E3A5F", "slab_cant": "#5B4320",
        "slab_grade": "#2A2F38", "slab_edge": "#4C77AE", "beam": "#7FB2FF", "beam_ext": "#A9CBFF", "beam_cant": "#FB923C",
        "column": "#E6EAF0", "select": "#FBBF24", "error": "#F87171", "ok": "#4ADE80", "warn": "#FBBF24",
        "text_canvas": "#CBD5E1", "chat_user": "#1F3B63", "chat_bot": "#232A35", "alt_row": "#202734", "link": "#93C5FD",
    },
}


def qss(theme: str) -> str:
    c = PALETTES[theme]
    return f"""
    QMainWindow, QDialog {{ background: {c['bg']}; color: {c['text']}; }}
    QWidget {{ color: {c['text']}; font-size: 9.5pt; }}
    QDockWidget::title {{ background: {c['panel']}; padding: 6px; border-bottom: 1px solid {c['border']}; font-weight: 600; }}
    QToolBar {{ background: {c['panel']}; border-bottom: 1px solid {c['border']}; spacing: 2px; padding: 3px; }}
    QToolButton {{ padding: 4px 6px; border-radius: 6px; }}
    QToolButton:hover {{ background: {c['border']}; }}
    QToolButton:checked {{ background: rgba(47, 125, 225, 0.18); border: 1px solid {ACCENT}; }}
    QToolButton#aiButton {{ background: {ACCENT}; color: white; font-weight: 600; padding: 4px 10px; }}
    QToolButton#aiButton:checked {{ background: #1E5BB0; border: none; }}
    QMenuBar {{ background: {c['panel']}; }}
    QMenu {{ background: {c['panel']}; border: 1px solid {c['border']}; }}
    QMenu::item:selected {{ background: {ACCENT}; color: white; }}
    QTabWidget::pane {{ border: 1px solid {c['border']}; background: {c['panel']}; }}
    QTabBar::tab {{ padding: 6px 12px; background: {c['bg']}; border: 1px solid {c['border']}; border-bottom: none;
                    border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }}
    QTabBar::tab:selected {{ background: {c['panel']}; font-weight: 600; }}
    QTableWidget {{ alternate-background-color: {c['alt_row']}; gridline-color: {c['border']}; }}
    QTableWidget, QListWidget, QTreeWidget, QTextBrowser, QPlainTextEdit, QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
        background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 6px; selection-background-color: {ACCENT}; }}
    QHeaderView {{ background: {c['bg']}; }}
    QTableCornerButton::section {{ background: {c['bg']}; border: none; }}
    QHeaderView::section {{ background: {c['bg']}; padding: 4px; border: none; border-bottom: 1px solid {c['border']}; font-weight: 600; }}
    QPushButton {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 5px 12px; }}
    QPushButton:hover {{ border-color: {ACCENT}; }}
    QPushButton#primary {{ background: {ACCENT}; color: white; border: none; font-weight: 600; }}
    QPushButton#chip {{ border-radius: 11px; padding: 3px 10px; font-size: 8.5pt; }}
    QStatusBar {{ background: {c['panel']}; border-top: 1px solid {c['border']}; }}
    QGroupBox {{ border: 1px solid {c['border']}; border-radius: 8px; margin-top: 12px; padding-top: 8px; font-weight: 600; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
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
