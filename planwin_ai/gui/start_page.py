"""Start screen shown on launch: create with AI, quick actions, recent projects and the
template gallery (with a plan thumbnail of each template)."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from ..ai.templates import TEMPLATES, build_template
from ..core.model import Plan
from .theme import PALETTES, icon

if TYPE_CHECKING:
    from .main_window import MainWindow

AI_EXAMPLE = "G+4 residential in Pune, 3x2 bays of 4.5 m with mumty and 1.2 m balcony"


class PlanThumb(QWidget):
    """Small read-only drawing of a plan (slabs, beams, columns, walls) fitted to the widget."""

    def __init__(self, plan: Plan | None, theme: Callable[[], str], size=(200, 120)):
        super().__init__()
        self.plan, self.theme = plan, theme
        self.setFixedSize(*size)

    def paintEvent(self, _ev):  # noqa: N802 (Qt API)
        pal = PALETTES[self.theme()]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal["canvas"]))
        p.drawRoundedRect(self.rect(), 8, 8)
        plan = self.plan
        if plan is None or not plan.all_points():
            return
        x0, y0, x1, y1 = plan.extents()
        m = 10
        s = min((self.width() - 2 * m) / max(x1 - x0, 1e-6), (self.height() - 2 * m) / max(y1 - y0, 1e-6))
        ox = (self.width() - (x1 - x0) * s) / 2 - x0 * s
        oy = (self.height() + (y1 - y0) * s) / 2 + y0 * s

        def pt(x, y):
            return QPointF(ox + x * s, oy - y * s)

        p.setPen(QPen(QColor(pal["slab_edge"]), 0.6))
        p.setBrush(QColor(pal["slab"]))
        for sl in plan.slabs:
            if len(sl.points) >= 3:
                p.drawPolygon(QPolygonF([pt(*q) for q in sl.pts]))
        p.setPen(QPen(QColor(pal["beam"]), 1.4))
        for b in plan.beams:
            p.drawLine(pt(*b.p1), pt(*b.p2))
        p.setPen(QPen(QColor(pal.get("wall", "#8B5CF6")), 3))
        for w in plan.walls:
            p.drawLine(pt(*w.p1), pt(*w.p2))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal["column"]))
        for c in plan.columns:
            q = pt(c.x, c.y)
            p.drawRect(int(q.x()) - 2, int(q.y()) - 2, 4, 4)


class Card(QFrame):
    """Clickable card (objectName StartCard): optional thumbnail/icon, title and muted text."""

    def __init__(self, title: str, text: str, on_click: Callable[[], object], tip: str = "", thumb=None, ic=None):
        super().__init__()
        self.setObjectName("StartCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tip or title)
        self.on_click = on_click
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(4)
        if thumb is not None:
            lay.addWidget(thumb, 0, Qt.AlignHCenter)
        head = QHBoxLayout()
        if ic is not None:
            il = QLabel()
            il.setPixmap(icon(ic).pixmap(22, 22))
            head.addWidget(il)
        t = QLabel(title)
        t.setObjectName("StartCardTitle")
        t.setWordWrap(True)
        head.addWidget(t, 1)
        lay.addLayout(head)
        if text:
            d = QLabel(text)
            d.setObjectName("StartCardText")
            d.setWordWrap(True)
            lay.addWidget(d)
        lay.addStretch(1)

    def mouseReleaseEvent(self, ev):  # noqa: N802 (Qt API)
        if ev.button() == Qt.LeftButton and self.rect().contains(ev.position().toPoint()):
            self.on_click()
        super().mouseReleaseEvent(ev)


def _section(title: str) -> QLabel:
    lbl = QLabel(title)
    lbl.setObjectName("StartSection")
    return lbl


class StartPage(QScrollArea):
    def __init__(self, main: MainWindow):
        super().__init__()
        self.main = main
        self.setObjectName("StartPage")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body.setObjectName("StartBody")
        self.setWidget(body)
        outer = QHBoxLayout(body)
        outer.addStretch(1)
        col = QVBoxLayout()
        col.setSpacing(14)
        outer.addLayout(col, 12)
        outer.addStretch(1)
        body.setContentsMargins(24, 24, 24, 24)

        # ---- hero: title and AI prompt
        hero = QFrame()
        hero.setObjectName("StartHero")
        h = QVBoxLayout(hero)
        h.setContentsMargins(20, 18, 20, 18)
        title = QLabel(APP_NAME)
        title.setObjectName("StartTitle")
        sub = QLabel(f"Version {__version__} · RCC buildings to IS 456, IS 875, IS 1893 and IS 13920")
        sub.setObjectName("StartSubtitle")
        h.addWidget(title)
        h.addWidget(sub)
        row = QHBoxLayout()
        self.prompt = QLineEdit()
        self.prompt.setObjectName("StartPrompt")
        self.prompt.setPlaceholderText(f"Describe a building to create it with AI – e.g. “{AI_EXAMPLE}”")
        self.prompt.setToolTip("Type a description and press Enter: the AI assistant builds the model for you")
        self.prompt.returnPressed.connect(self._ask_ai)
        go = QPushButton(icon("ai", "#FFFFFF"), "  Create with AI")
        go.setObjectName("primary")
        go.setToolTip("Create the building described on the left with the AI assistant")
        go.clicked.connect(self._ask_ai)
        row.addWidget(self.prompt, 1)
        row.addWidget(go)
        h.addLayout(row)
        col.addWidget(hero)

        # ---- quick actions
        col.addWidget(_section("Start"))
        quick = QGridLayout()
        quick.setSpacing(10)
        items = (
            ("Blank project", "One plan, two levels – draw from scratch", main.new_blank, "new"),
            ("Open project…", "Open a .pwai project or a legacy .plw plan", main.open_dialog, "open"),
            ("Import DXF plan…", "Slabs, columns and beams from SLAB / COLUMN / BEAM layers", main.import_dxf, "dxf"),
            ("Continue current", "Go to the project that is already open", main.show_workspace, "plan"),
        )
        for i, (t, d, fn, ic) in enumerate(items):
            quick.addWidget(Card(t, d, fn, d, ic=ic), 0, i)
        col.addLayout(quick)

        # ---- recent projects
        self.recent_title = _section("Recent projects")
        col.addWidget(self.recent_title)
        self.recent_grid = QGridLayout()
        self.recent_grid.setSpacing(10)
        col.addLayout(self.recent_grid)

        # ---- templates
        col.addWidget(_section("Templates – pre-optimised buildings that pass design"))
        grid = QGridLayout()
        grid.setSpacing(10)
        self.template_cards: dict[str, Card] = {}
        for i, t in enumerate(TEMPLATES):
            try:
                prj = build_template(t.key)
                plan = next((prj.plan(lv.plan) for lv in prj.levels[1:2] if prj.plan(lv.plan)), None) or (
                    prj.plans[0] if prj.plans else None
                )
                n = len(prj.levels)
            except Exception:  # a broken template must not break the start page
                plan, n = None, 0
            card = Card(
                t.title,
                f"{t.description}" + (f" · {n} levels" if n else ""),
                lambda k=t.key: main.load_template(k),
                f"Open the '{t.title}' template",
                thumb=PlanThumb(plan, lambda: main.theme_name),
            )
            self.template_cards[t.key] = card
            grid.addWidget(card, i // 4, i % 4)
        col.addLayout(grid)

        # ---- footer
        foot = QHBoxLayout()
        self.show_at_start = QCheckBox("Show this page at startup")
        self.show_at_start.setToolTip("Show the start page every time PlanWin AI Pro starts")
        self.show_at_start.setChecked(main.settings.value("start/show", "true") == "true")
        self.show_at_start.toggled.connect(lambda on: main.settings.setValue("start/show", "true" if on else "false"))
        foot.addWidget(self.show_at_start)
        foot.addStretch(1)
        for text, fn, tip in (
            ("Quick start", main.quick_start, "Step-by-step workflow and keyboard shortcuts"),
            ("Licence", main.license_dialog, "Activate or view your licence"),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            foot.addWidget(b)
        col.addLayout(foot)
        col.addStretch(1)

    # ------------------------------------------------------------------ behaviour
    def refresh(self) -> None:
        """Rebuild the recent-project cards (files that no longer exist are skipped)."""
        while self.recent_grid.count():
            w = self.recent_grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        rec = [r for r in self.main._recent() if os.path.exists(r)][:8]
        for i, fn in enumerate(rec):
            when = time.strftime("%d %b %Y %H:%M", time.localtime(os.path.getmtime(fn)))
            card = Card(
                os.path.splitext(os.path.basename(fn))[0],
                f"{os.path.dirname(fn)}\n{when}",
                lambda f=fn: self.main.maybe_save() and self.main.open_path(f),
                fn,
                ic="folder",
            )
            self.recent_grid.addWidget(card, i // 4, i % 4)
        self.recent_title.setVisible(bool(rec))

    def _ask_ai(self) -> None:
        text = self.prompt.text().strip() or AI_EXAMPLE
        self.prompt.clear()
        self.main.start_with_ai(text)
