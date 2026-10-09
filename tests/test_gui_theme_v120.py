"""v1.2 Fluent theme (style sheets parse cleanly, palettes stay complete) and the decluttered plan labels."""

import itertools
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


# ------------------------------------------------------------------ theme
def _widget_zoo():
    """One of everything the style sheet styles, so every rule is parsed and applied while polishing."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QDockWidget,
        QDoubleSpinBox,
        QFrame,
        QGroupBox,
        QLabel,
        QLineEdit,
        QListWidget,
        QMainWindow,
        QMenu,
        QPlainTextEdit,
        QPushButton,
        QRadioButton,
        QScrollArea,
        QSpinBox,
        QTableWidget,
        QTabWidget,
        QToolButton,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )

    mw = QMainWindow()
    body = QWidget()
    lay = QVBoxLayout(body)
    named = {
        QWidget: ["StartPage", "StartHero", "PanelSection", "SectionBody", "Ribbon", "RibbonTop"],
        QLabel: ["StartTitle", "StartSubtitle", "StartCardTitle", "StartCardText", "Badge", "RibbonTitle"],
        QFrame: ["StartCard", "RibbonGroup"],
        QLineEdit: ["StartPrompt", "CommandSearch", "ResultsFilter", ""],
        QListWidget: ["CommandPopup", ""],
        QToolButton: ["TableAction", "SectionHeader", "RibbonLarge", "RibbonSmall", "AppButton", "QuickButton", ""],
        QPushButton: ["primary", "chip", ""],
    }
    for cls, names in named.items():
        for n in names:
            w = cls()
            w.setObjectName(n)
            lay.addWidget(w)
    badge = QLabel("3")
    badge.setObjectName("Badge")
    badge.setProperty("kind", "error")
    tb = QToolButton()
    tb.setCheckable(True)
    tb.setChecked(True)
    tb.setArrowType(Qt.RightArrow)
    nav = QTreeWidget()
    nav.setObjectName("ResultsNav")
    QTreeWidgetItem(QTreeWidgetItem(nav, ["Analysis"]), ["Column loads"])
    combo = QComboBox()
    combo.addItems(["a", "b"])
    table = QTableWidget(3, 3)
    table.setCellWidget(0, 0, QComboBox())
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "One")
    tabs.addTab(QWidget(), "Two")
    scroll = QScrollArea()
    scroll.setWidget(QLabel("x\n" * 80))
    box = QGroupBox("Group")
    QVBoxLayout(box).addWidget(QCheckBox("check"))
    for w in (badge, tb, nav, combo, table, tabs, scroll, box, QRadioButton("r"), QSpinBox(), QDoubleSpinBox()):
        lay.addWidget(w)
    lay.addWidget(QPlainTextEdit("text"))
    mw.setCentralWidget(body)
    dock = QDockWidget("Properties", mw)
    dock.setWidget(QLabel("dock"))
    mw.addDockWidget(Qt.RightDockWidgetArea, dock)
    menu = QMenu(mw)
    menu.addAction("One")
    menu.addSeparator()
    menu.addMenu("Sub").addAction("Two")
    return mw, menu


@pytest.mark.parametrize("name", ["light", "dark"])
def test_qss_parses_without_qt_warnings(app, name):
    from PySide6.QtCore import qInstallMessageHandler

    from planwin_ai.gui.theme import qpalette, qss

    msgs: list[str] = []
    prev = qInstallMessageHandler(lambda _t, _c, m: msgs.append(m))
    try:
        sheet = qss(name)
        app.setStyleSheet(sheet)
        app.setPalette(qpalette(name))
        mw, menu = _widget_zoo()
        mw.resize(900, 1400)
        mw.show()
        menu.ensurePolished()
        app.processEvents()
        assert not mw.grab().isNull()
        mw.close()
    finally:
        qInstallMessageHandler(prev)
        app.setStyleSheet("")
    bad = [m for m in msgs if "style" in m.lower() or "parse" in m.lower()]
    assert not bad, bad
    assert sheet.count("{") == sheet.count("}")


def test_qss_covers_new_object_names_and_scroll_area_fix():
    from planwin_ai.gui.theme import qss

    for name in ("light", "dark"):
        sheet = qss(name)
        for sel in (
            "#StartPage",
            "#StartHero",
            "#StartTitle",
            "#StartSubtitle",
            "QFrame#StartCard",
            "#StartCardTitle",
            "#StartCardText",
            "QLineEdit#StartPrompt",
            "QLineEdit#CommandSearch",
            "QListWidget#CommandPopup",
            "QTreeWidget#ResultsNav",
            "QLineEdit#ResultsFilter",
            "QToolButton#TableAction",
            "QLabel#Badge",
            "QWidget#PanelSection",
            "QToolButton#SectionHeader",
            "QWidget#SectionBody",
            "QScrollBar:vertical",
            "QScrollBar:horizontal",
            "QToolTip",
            "QScrollArea > QWidget#qt_scrollarea_viewport > QWidget",
        ):
            assert sel in sheet, (name, sel)
        # the checked ribbon toggle is a soft pill: no visible border
        rule = sheet.split("QToolButton#RibbonLarge:checked, QToolButton#RibbonSmall:checked {")[1].split("}")[0]
        assert "1px solid transparent" in rule and "background" in rule


def test_qss_assets_exist():
    import re

    from planwin_ai.gui.theme import qss

    paths = re.findall(r'url\("([^"]+)"\)', qss("light") + qss("dark"))
    assert paths
    for p in paths:
        assert os.path.isfile(p), p


def test_palettes_complete_and_badges():
    from PySide6.QtGui import QColor

    from planwin_ai.gui.theme import ACCENT, BADGE, PALETTES

    keys = {k: set(v) for k, v in PALETTES.items()}
    assert keys["light"] == keys["dark"]
    for k in (
        "canvas",
        "grid_minor",
        "grid_major",
        "slab",
        "slab_edge",
        "beam",
        "column",
        "select",
        "text",
        "muted",
        "error",
        "ok",
        "panel",
        "border",
        "wall",
        "bg",
        "alt_row",
        "chat_user",
        "chat_bot",
        "link",
        "text_canvas",
    ):
        assert k in keys["light"], k
    for pal in PALETTES.values():
        for k, v in pal.items():
            assert QColor(v).isValid(), (k, v)
    assert {"error", "warn", "ok"} <= set(BADGE)
    assert all(QColor(v).isValid() for v in BADGE.values())
    assert ACCENT == "#2F7DE1"


def test_icons_still_render(app):
    from planwin_ai.gui import theme

    for name in list(theme._SVG):
        assert not theme.icon(name, "#123456", 16).isNull(), name
    assert not theme.icon("chevron_down", "#5B6675").isNull()


# ------------------------------------------------------------------ label layout (pure helpers)
def test_rects_overlap():
    from planwin_ai.gui.canvas import rects_overlap

    assert rects_overlap((0, 0, 10, 10), (5, 5, 10, 10))
    assert not rects_overlap((0, 0, 10, 10), (10, 0, 5, 5))  # touching edges
    assert rects_overlap((0, 0, 10, 10), (11, 0, 5, 5), pad=2)
    assert not rects_overlap((0, 0, 10, 10), (0, 20, 10, 10), pad=2)


def test_place_labels_never_overlap():
    from planwin_ai.gui.canvas import place_labels, rects_overlap

    # three labels competing for the same spot, each with an alternative position
    opts = [
        [(0, 0, 40, 12), (0, 20, 40, 12)],
        [(10, 5, 40, 12), (60, 0, 40, 12)],
        [(0, 0, 40, 12), (5, 10, 30, 10), (200, 200, 10, 10)],
        [(0, 0, 100, 40)],  # nowhere to go -> left out
    ]
    got = place_labels(opts)
    assert got == [0, 1, 2, None]
    chosen = [opts[i][j] for i, j in enumerate(got) if j is not None]
    for a, b in itertools.combinations(chosen, 2):
        assert not rects_overlap(a, b)
    # obstacles are respected too
    assert place_labels([[(0, 0, 10, 10), (30, 0, 10, 10)]], obstacles=[(5, 5, 4, 4)]) == [1]


def test_framing_dirs_and_quadrants():
    from planwin_ai.gui.canvas import QUADRANTS, framing_dirs, label_quadrants

    segs = [((0, 0), (4, 0)), ((0, 0), (0, 4)), ((4, 0), (8, 0)), ((4, -4), (4, 4))]
    assert framing_dirs((0, 0), segs, 0.3) == {"E", "N"}
    assert framing_dirs((4, 0), segs, 0.3) == {"E", "W", "N", "S"}  # beam running through counts both ways
    assert framing_dirs((8, 0), segs, 0.3) == {"W"}
    assert framing_dirs((20, 20), segs, 0.3) == set()
    assert label_quadrants({"E", "N"})[0] == "SW"
    assert label_quadrants({"W", "S"})[0] == "NE"
    assert label_quadrants({"E", "S"})[0] == "NW"
    assert label_quadrants(set()) == list(QUADRANTS)
    assert label_quadrants({"E", "W", "N", "S"}) == list(QUADRANTS)


# ------------------------------------------------------------------ label layout on the canvas
def _grid_plan():
    from planwin_ai.core.model import Beam, Column, Plan, Slab, Wall

    plan = Plan(name="P1", floor_type="typical", floor_height_above=3.0)
    xs = ys = (0.0, 4.0, 8.0)
    n = itertools.count(1)
    for y in ys:
        for x in xs:
            plan.columns.append(Column(mark=f"C{next(n)}", x=x, y=y))
    n = itertools.count(1)
    for y in ys:
        for a, b in zip(xs, xs[1:]):
            plan.beams.append(Beam(mark=f"B{next(n)}", x1=a, y1=y, x2=b, y2=y))
    for x in xs:
        for a, b in zip(ys, ys[1:]):
            plan.beams.append(Beam(mark=f"B{next(n)}", x1=x, y1=a, x2=x, y2=b))
    n = itertools.count(1)
    for x0, y0 in itertools.product(xs[:2], ys[:2]):
        pts = [[x0, y0], [x0 + 4, y0], [x0 + 4, y0 + 4], [x0, y0 + 4]]
        plan.slabs.append(Slab(mark=f"S{next(n)}", points=pts))
    plan.walls.append(Wall(mark="W1", x1=0.0, y1=0.0, x2=4.0, y2=0.0))
    return plan


class FakeMain:
    def __init__(self, plan, theme="light"):
        from planwin_ai.core.model import Project
        from planwin_ai.core.plan_engine import PlanEngine

        self.project = Project(plans=[plan])
        self.theme_name = theme
        self.defaults = {"wall_t": 0.2}
        self._res = PlanEngine(plan).run()

    def current_plan(self):
        return self.project.plans[0]

    def plan_result(self, _name):
        return self._res


def _canvas(app, plan, theme="light"):
    from planwin_ai.gui.canvas import PlanCanvas

    cv = PlanCanvas(FakeMain(plan, theme))
    cv.resize(900, 700)
    cv.show()
    app.processEvents()  # the first resize zooms to the extents; the tests set their own view afterwards
    return cv


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("scale", [6.0, 14.0, 40.0, 140.0])
def test_canvas_labels_do_not_overlap(app, theme, scale):
    from planwin_ai.gui.canvas import rects_overlap

    cv = _canvas(app, _grid_plan(), theme)
    cv.scale = scale
    cv.ox, cv.oy = 450 - 4 * scale, 350 + 4 * scale  # plan centred
    img = cv.grab()
    assert not img.isNull()
    labels = cv.painted_labels
    rects = [r for _, _, r in labels]
    for a, b in itertools.combinations(rects, 2):
        assert not rects_overlap(a, b), (a, b)
    kinds = {k for k, _, _ in labels}
    if scale >= 40:
        assert {"column", "beam", "slab"} <= kinds
        assert sum(k == "column" for k, _, _ in labels) == 9  # every column keeps its label
    if scale <= 6:
        assert "beam" not in kinds  # too small for beam labels
    cv.close()


def test_canvas_column_labels_avoid_framing_beams(app):
    """Corner columns put their label outside the building, clear of both beams."""
    plan = _grid_plan()
    cv = _canvas(app, plan)
    cv.scale, cv.ox, cv.oy = 50.0, 250.0, 550.0
    cv.grab()
    where = {oid: r for k, oid, r in cv.painted_labels if k == "column"}
    by_mark = {c.mark: c for c in plan.columns}
    c1 = by_mark["C1"]  # (0, 0): beams go E and N -> label to the SW
    r = where[c1.id]
    p = cv.w2s(c1.x, c1.y)
    assert r[0] + r[2] <= p.x() and r[1] >= p.y()
    c9 = by_mark["C9"]  # (8, 8): beams go W and S -> label to the NE
    r = where[c9.id]
    p = cv.w2s(c9.x, c9.y)
    assert r[0] >= p.x() and r[1] + r[3] <= p.y()
    cv.close()


def test_canvas_beam_label_shortens_then_hides(app):
    from PySide6.QtGui import QFont, QFontMetricsF

    from planwin_ai.gui.canvas import LABEL_FILL

    plan = _grid_plan()
    cv = _canvas(app, plan)
    cv.ox, cv.oy = 100.0, 600.0
    for scale in (14.0, 20.0, 30.0, 80.0):
        cv.scale = scale
        cv.grab()
        font = QFont(cv.font())
        font.setPointSizeF(max(min(scale / 6.0, 10.0), 6.5))
        fm = QFontMetricsF(font)
        for k, oid, r in cv.painted_labels:
            if k != "beam":
                continue
            b = plan.find(oid)
            assert max(r[2], r[3]) <= LABEL_FILL * b.length * scale + 1.0
            assert fm.horizontalAdvance(b.mark) <= max(r[2], r[3]) + 0.5
    cv.show_marks = False
    cv.grab()
    assert cv.painted_labels == []
    cv.close()
