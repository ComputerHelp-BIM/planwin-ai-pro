"""Colours, Qt style sheets and vector icons (no external image files needed)."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ACCENT = "#2F7DE1"

PALETTES = {
    "light": {
        "bg": "#F4F6F9",
        "panel": "#FFFFFF",
        "text": "#1B2430",
        "muted": "#5B6675",
        "border": "#D5DBE3",
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
        "select": "#F59E0B",
        "error": "#DC2626",
        "ok": "#16A34A",
        "warn": "#D97706",
        "text_canvas": "#334155",
        "chat_user": "#E3EEFF",
        "chat_bot": "#F1F3F6",
        "alt_row": "#F6F8FB",
        "link": "#1D4ED8",
    },
    "dark": {
        "bg": "#14181F",
        "panel": "#1B2029",
        "text": "#E6EAF0",
        "muted": "#98A2B3",
        "border": "#2C3442",
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
        "select": "#FBBF24",
        "error": "#F87171",
        "ok": "#4ADE80",
        "warn": "#FBBF24",
        "text_canvas": "#CBD5E1",
        "chat_user": "#1F3B63",
        "chat_bot": "#232A35",
        "alt_row": "#202734",
        "link": "#93C5FD",
    },
}


def qss(theme: str) -> str:
    c = PALETTES[theme]
    return f"""
    QMainWindow, QDialog {{ background: {c["bg"]}; color: {c["text"]}; }}
    QWidget {{ color: {c["text"]}; font-size: 9.5pt; }}
    QDockWidget::title {{ background: {c["panel"]}; padding: 6px; border-bottom: 1px solid {c["border"]}; font-weight: 600; }}
    QToolBar {{ background: {c["panel"]}; border-bottom: 1px solid {c["border"]}; spacing: 2px; padding: 3px; }}
    QToolButton {{ padding: 4px 6px; border-radius: 6px; }}
    QToolButton:hover {{ background: {c["border"]}; }}
    QToolButton:checked {{ background: rgba(47, 125, 225, 0.18); border: 1px solid {ACCENT}; }}
    QToolButton#aiButton {{ background: {ACCENT}; color: white; font-weight: 600; padding: 4px 10px; }}
    QToolButton#aiButton:checked {{ background: #1E5BB0; border: none; }}
    QMenuBar {{ background: {c["panel"]}; }}
    QMenu {{ background: {c["panel"]}; border: 1px solid {c["border"]}; }}
    QMenu::item:selected {{ background: {ACCENT}; color: white; }}
    QTabWidget::pane {{ border: 1px solid {c["border"]}; background: {c["panel"]}; }}
    QTabBar::tab {{ padding: 6px 12px; background: {c["bg"]}; border: 1px solid {c["border"]}; border-bottom: none;
                    border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }}
    QTabBar::tab:selected {{ background: {c["panel"]}; font-weight: 600; }}
    QTableWidget {{ alternate-background-color: {c["alt_row"]}; gridline-color: {c["border"]}; }}
    QTableWidget, QListWidget, QTreeWidget, QTextBrowser, QPlainTextEdit, QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
        background: {c["panel"]}; border: 1px solid {c["border"]}; border-radius: 6px; selection-background-color: {ACCENT}; }}
    QHeaderView {{ background: {c["bg"]}; }}
    QTableCornerButton::section {{ background: {c["bg"]}; border: none; }}
    QHeaderView::section {{ background: {c["bg"]}; padding: 4px; border: none; border-bottom: 1px solid {c["border"]}; font-weight: 600; }}
    QPushButton {{ background: {c["panel"]}; border: 1px solid {c["border"]}; border-radius: 6px; padding: 5px 12px; }}
    QPushButton:hover {{ border-color: {ACCENT}; }}
    QPushButton#primary {{ background: {ACCENT}; color: white; border: none; font-weight: 600; }}
    QPushButton#chip {{ border-radius: 11px; padding: 3px 10px; font-size: 8.5pt; }}
    QStatusBar {{ background: {c["panel"]}; border-top: 1px solid {c["border"]}; }}
    QGroupBox {{ border: 1px solid {c["border"]}; border-radius: 8px; margin-top: 12px; padding-top: 8px; font-weight: 600; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
    QWidget#Ribbon {{ background: {c["panel"]}; border-bottom: 1px solid {c["border"]}; }}
    QWidget#RibbonTop {{ background: {ACCENT}; }}
    QWidget#RibbonBody, QWidget#Ribbon QScrollArea {{ background: {c["panel"]}; border: none; }}
    QToolButton#AppButton {{ background: #1E5BB0; color: white; font-weight: 600; padding: 4px 14px; border-radius: 0; }}
    QToolButton#AppButton::menu-indicator {{ image: none; }}
    QToolButton#QuickButton {{ padding: 2px; border-radius: 4px; }}
    QToolButton#QuickButton:hover {{ background: rgba(255, 255, 255, 0.25); }}
    QLabel#RibbonTitle {{ color: white; font-weight: 600; padding-left: 12px; }}
    QTabBar#RibbonTabs::tab {{ background: transparent; border: none; color: white; padding: 6px 14px; margin: 0;
                              border-top-left-radius: 4px; border-top-right-radius: 4px; }}
    QTabBar#RibbonTabs::tab:selected {{ background: {c["panel"]}; color: {c["text"]}; font-weight: 600; }}
    QTabBar#RibbonTabs::tab:hover:!selected {{ background: rgba(255, 255, 255, 0.18); }}
    QFrame#RibbonGroup {{ border-right: 1px solid {c["border"]}; }}
    QLabel#RibbonGroupTitle {{ color: {c["muted"]}; font-size: 8pt; }}
    QToolButton#RibbonLarge {{ padding: 3px 6px; border-radius: 6px; min-width: 52px; }}
    QToolButton#RibbonSmall {{ padding: 1px 6px; border-radius: 4px; text-align: left; }}
    QToolButton#RibbonLarge:checked, QToolButton#RibbonSmall:checked {{ background: rgba(47, 125, 225, 0.18);
                                                                       border: 1px solid {ACCENT}; }}
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
