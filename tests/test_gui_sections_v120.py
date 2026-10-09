"""Off-screen tests of the 1.2.0 panel layout: collapsible sections, scroll panels, level table sizing,
tooltips and the properties editor (no black empty area, Enter applies, Revert)."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from planwin_ai.ai.templates import build_template  # noqa: E402
from planwin_ai.core.model import Wall  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication, QMessageBox

    QMessageBox.information = staticmethod(lambda *a, **k: 0)
    QMessageBox.warning = staticmethod(lambda *a, **k: 0)
    QMessageBox.critical = staticmethod(lambda *a, **k: 0)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
    return QApplication.instance() or QApplication([])


@pytest.fixture
def light(app):
    """The light style sheet for one test (the previous sheet and palette are restored afterwards)."""
    from planwin_ai.gui import theme

    sheet, pal = app.styleSheet(), app.palette()
    app.setStyleSheet(theme.qss("light"))
    yield app
    app.setStyleSheet(sheet)
    app.setPalette(pal)


@pytest.fixture
def ini(tmp_path):
    from PySide6.QtCore import QSettings

    return QSettings(str(tmp_path / "panels.ini"), QSettings.IniFormat)


class FakeMain:
    """The parts of MainWindow the panels use."""

    def __init__(self, project, plan="Typical", settings=None):
        self.project = project
        self.current_plan_name = plan
        self.mutations: list[str] = []
        if settings is not None:
            self.settings = settings

    def current_plan(self):
        return self.project.plan(self.current_plan_name)

    def set_current_plan(self, name):
        self.current_plan_name = name

    def plan_result(self, name):
        from planwin_ai.core.plan_engine import PlanEngine

        return PlanEngine(self.project.plan(name)).run()

    def frame_model(self):
        return None

    def frame_analysis(self):
        return None

    def design_report(self):
        return None

    def mutate(self, desc, fn):
        fn()
        self.mutations.append(desc)


def _settle(app):
    for _ in range(3):  # height-for-width layouts settle over a couple of passes
        app.processEvents()


# ------------------------------------------------------------------ widgets
def test_collapsible_section_toggles_and_persists(app, ini):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel

    from planwin_ai.gui.widgets import CollapsibleSection

    sec = CollapsibleSection("Levels", "test_levels", True, ini)
    assert sec.objectName() == "PanelSection"
    assert sec.header.objectName() == "SectionHeader" and sec.body.objectName() == "SectionBody"
    assert sec.header.isCheckable() and sec.header.toolButtonStyle() == Qt.ToolButtonTextBesideIcon
    sec.body_layout.addWidget(QLabel("body"))
    sec.resize(250, 300)
    sec.show()
    seen = []
    sec.toggled.connect(seen.append)
    assert sec.expanded and sec.body.isVisible() and sec.header.arrowType() == Qt.DownArrow
    sec.header.click()  # the header collapses it
    assert not sec.expanded and not sec.body.isVisible() and sec.header.arrowType() == Qt.RightArrow
    assert seen == [False]
    ini.sync()
    assert str(ini.value("panels/test_levels")).lower() == "false"
    again = CollapsibleSection("Levels", "test_levels", True, ini)  # remembered across sessions
    assert not again.expanded and not again.body.isVisibleTo(again)
    sec.set_expanded(True)
    assert sec.expanded and sec.body.isVisible() and seen == [False, True]
    assert str(ini.value("panels/test_levels")).lower() == "true"
    sec.set_badge("7")
    assert sec.badge.text() == "7" and sec.badge.isVisible()
    sec.set_badge("")
    assert not sec.badge.isVisible()
    # no settings: nothing is stored and a broken store never stops a panel opening
    plain = CollapsibleSection("A & B", "k", False)
    assert not plain.expanded and plain.title == "A & B"

    class Broken:
        def value(self, *a):
            raise RuntimeError

        def setValue(self, *a):
            raise RuntimeError

    b = CollapsibleSection("B", "k", True, Broken())
    b.set_expanded(False)
    assert not b.expanded


def test_scroll_panel_is_transparent_and_stacks_sections(app):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFrame, QLabel

    from planwin_ai.gui.widgets import ScrollPanel

    sp = ScrollPanel()
    assert sp.objectName() == "PanelScroll" and sp.widgetResizable() and sp.frameShape() == QFrame.NoFrame
    assert not sp.viewport().autoFillBackground() and not sp.inner.autoFillBackground()
    assert sp.inner.testAttribute(Qt.WA_StyledBackground)
    a = sp.add_section("A", "a")
    b = sp.add_section("B", "b", grow=True)
    lbl = sp.add_widget(QLabel("hint"))
    order = [sp.inner_layout.itemAt(i).widget() for i in range(sp.inner_layout.count() - 1)]
    assert order == [a, b, lbl]
    assert sp.inner_layout.itemAt(sp.inner_layout.count() - 1).spacerItem() is not None  # trailing stretch


def test_flow_layout_wraps_buttons(app):
    from PySide6.QtWidgets import QPushButton, QWidget

    from planwin_ai.gui.widgets import FlowLayout

    w = QWidget()
    lay = FlowLayout(w)
    buttons = [QPushButton(f"Button {i}") for i in range(4)]
    for b in buttons:
        lay.addWidget(b)
    wide = lay.heightForWidth(2000)
    narrow = lay.heightForWidth(buttons[0].sizeHint().width() + 10)
    assert narrow > wide >= buttons[0].sizeHint().height()
    assert lay.count() == 4 and lay.itemAt(9) is None


# ------------------------------------------------------------------ properties panel
def _is_dark(c):
    return c.red() < 60 and c.green() < 60 and c.blue() < 60


def test_empty_properties_panel_has_no_black_box(light):
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QMainWindow

    from planwin_ai.gui.panels import PropertiesPanel

    main = FakeMain(build_template("bungalow"))
    pp = PropertiesPanel(main)
    pp.resize(300, 800)
    pp.show_selection([])
    pp.show()
    _settle(light)
    img = pp.grab().toImage()
    assert not _is_dark(img.pixelColor(img.width() // 2, img.height() // 2))
    assert not _is_dark(img.pixelColor(img.width() // 2, int(img.height() * 0.75)))
    assert pp.empty_lbl.isVisible() and pp.title.text() == "Nothing selected"
    assert not any(s.isVisible() for s in pp.sections.values())
    assert not pp.apply_btn.isEnabled() and not pp.revert_btn.isEnabled()
    pp.close()
    # a dark native palette (Windows in dark mode) under the light style sheet: the scroll viewport must not
    # paint it – the style-sheet window background shows through instead
    pal = QPalette(light.palette())
    for role in (QPalette.Window, QPalette.Base, QPalette.Button):
        pal.setColor(role, QColor("#000000"))
    light.setPalette(pal)
    mw = QMainWindow()
    pp = PropertiesPanel(main)
    mw.setCentralWidget(pp)
    mw.resize(300, 800)
    mw.show()
    pp.show_selection([])
    _settle(light)
    img = mw.grab().toImage()
    for y in (0.5, 0.75):
        assert not _is_dark(img.pixelColor(img.width() // 2, int(img.height() * y)))
    mw.close()


def test_properties_sections_follow_the_selection(app):
    from planwin_ai.gui.panels import FIELD_SECTION, PropertiesPanel

    p = build_template("bungalow")
    plan = p.plan("Typical")
    plan.walls.append(Wall(mark="W1", thickness=0.2))
    main = FakeMain(p)
    pp = PropertiesPanel(main)
    pp.resize(300, 800)
    pp.show()
    pp.show_selection([plan.beams[0].id])
    _settle(app)
    vis = {n for n, s in pp.sections.items() if s.isVisible()}
    assert vis == {"General", "Geometry", "Loads", "Point loads", "Part loads", "Info"}
    assert pp.loads_tbl is not None and pp.wedge_tbl is not None
    for name, (w, _typ) in pp.widgets.items():  # each field sits in its section's form
        sec = pp.sections[FIELD_SECTION.get(name, "General")]
        assert sec.isAncestorOf(w), name
    pp.show_selection([plan.columns[0].id])
    vis = {n for n, s in pp.sections.items() if s.isVisible()}
    assert vis == {"General", "Geometry", "Info"} and pp.loads_tbl is None
    pp.show_selection([plan.slabs[0].id, plan.beams[0].id])
    assert pp.title.text() == "2 mixed objects selected" and pp.empty_lbl.isVisible()
    assert not pp.apply_btn.isEnabled()
    # every editor, label, table header and button explains itself in one line
    for ids in ([plan.slabs[0].id], [plan.columns[0].id], [plan.beams[0].id], [plan.walls[0].id]):
        pp.show_selection(ids)
        for name, (w, _typ) in pp.widgets.items():
            assert w.toolTip() and "\n" not in w.toolTip(), name
            assert pp.form.labelForField(w).toolTip(), name
    pp.show_selection([plan.beams[0].id])
    for t in (pp.loads_tbl, pp.wedge_tbl):
        assert all(t.horizontalHeaderItem(c).toolTip() for c in range(t.columnCount()))
    from PySide6.QtWidgets import QAbstractButton

    for b in pp.findChildren(QAbstractButton):
        if b.isVisibleTo(pp) and not b.objectName().startswith("qt_"):
            assert b.toolTip(), b.text()
    assert pp.apply_btn.objectName() == "primary"


def test_properties_enter_applies_and_revert_restores(app):
    from planwin_ai.gui.panels import PropertiesPanel

    p = build_template("bungalow")
    plan = p.plan("Typical")
    s = plan.slabs[0]
    main = FakeMain(p)
    pp = PropertiesPanel(main)
    pp.show_selection([s.id])
    live = pp.widgets["live"][0]
    live.setValue(4.5)
    pp.revert_btn.click()
    assert pp.widgets["live"][0].value() == pytest.approx(s.live) and main.mutations == []
    live = pp.widgets["live"][0]
    live.lineEdit().setText("4.5")
    live.interpretText()
    live.lineEdit().returnPressed.emit()
    _settle(app)
    assert s.live == pytest.approx(4.5) and main.mutations == ["Edit properties"]
    room = pp.widgets["room"][0]
    room.setText("Kitchen")
    room.returnPressed.emit()
    _settle(app)
    assert s.room == "Kitchen" and s.live == pytest.approx(4.5)
    assert pp.form.rowCount() == len(pp.widgets)


def test_properties_keeps_public_attributes(app):
    from planwin_ai.gui.panels import PropertiesPanel

    pp = PropertiesPanel(FakeMain(build_template("bungalow")))
    for name in ("title", "info", "apply_btn", "revert_btn", "widgets", "initial", "objs", "form", "inner"):
        assert hasattr(pp, name), name
    for name in ("_apply", "_read", "show_selection", "_update_info"):
        assert callable(getattr(pp, name)), name
    assert pp.loads_tbl is None and pp.wedge_tbl is None


# ------------------------------------------------------------------ project panel
def test_project_panel_levels_table_shows_full_plan_names(app, ini):
    from PySide6.QtWidgets import QAbstractButton, QComboBox

    from planwin_ai.gui.panels import ProjectPanel

    p = build_template("residential_g4")
    longest = "Typical floor (A wing)"
    old = p.plans[1].name
    p.plans[1].name = longest
    for lv in p.levels:
        if lv.plan == old:
            lv.plan = longest
    main = FakeMain(p, plan=longest, settings=ini)
    pp = ProjectPanel(main)
    pp.resize(280, 700)
    pp.show()
    pp.refresh()
    _settle(app)
    t = pp.levels
    combos = [t.cellWidget(r, 1) for r in range(t.rowCount())]
    assert all(isinstance(c, QComboBox) for c in combos)
    for cb in combos:
        assert cb.sizeHint().width() >= cb.fontMetrics().horizontalAdvance(longest)
        assert t.columnWidth(1) >= cb.sizeHint().width()
    assert any(cb.currentText() == longest for cb in combos)
    # at least six rows fit without scrolling the table vertically
    header = t.horizontalHeader().height() or t.horizontalHeader().sizeHint().height()
    assert t.minimumHeight() >= header + 6 * t.verticalHeader().defaultSectionSize()
    assert t.verticalHeader().defaultSectionSize() >= combos[0].sizeHint().height()
    assert t.horizontalHeader().stretchLastSection()
    # the plans list shows about five rows, and nothing makes the dock scroll sideways
    assert pp.plans.minimumHeight() >= 5 * pp.plans.fontMetrics().height()
    assert pp.scroll.horizontalScrollBar().maximum() == 0
    # badges, tooltips
    assert pp.plans_section.badge.text() == str(len(p.plans))
    assert pp.levels_section.badge.text() == str(len(p.levels))
    for c in range(t.columnCount()):
        assert t.horizontalHeaderItem(c).toolTip(), c
    for w in (pp.plans, pp.ftype, pp.fha, pp.height_lbl):
        assert w.toolTip()
    for b in pp.findChildren(QAbstractButton):
        if not b.objectName().startswith("qt_"):  # Qt's own table corner button
            assert b.toolTip(), b.text()
    # sections remember being collapsed
    pp.levels_section.header.click()
    assert ProjectPanel(main).levels_section.expanded is False


def test_project_panel_keeps_public_attributes_and_extras(app):
    from planwin_ai.gui.panels import ProjectPanel

    p = build_template("bungalow")
    main = FakeMain(p)
    pp = ProjectPanel(main)
    pp.refresh()
    for name in ("plans", "levels", "ftype", "fha", "height_lbl", "extras_lbl", "planChanged", "refresh"):
        assert hasattr(pp, name), name
    assert pp.extras_section.badge.text() == "0" and not pp.extras_lbl.isVisibleTo(pp)
    assert pp.extras_empty.isVisibleTo(pp)
    p.stairs.append({"name": "ST1", "plan": "Typical"})
    p.water_tanks.append({"name": "T1", "capacity_l": 5000})
    pp.refresh()
    assert "ST1" in pp.extras_lbl.text() and "T1 (5000 L)" in pp.extras_lbl.text()
    assert pp.extras_section.badge.text() == "2" and pp.extras_lbl.isVisibleTo(pp)
    assert pp.height_lbl.text().startswith("Total height")
    # the level editing still goes through mutate()
    pp.levels.setCurrentCell(0, 2)
    pp._add_level()
    assert main.mutations == ["Add level"]


def test_scroll_panel_never_scrolls_sideways(app):
    """A child wider than the dock (Windows fonts made the levels table ~165 px too wide) must not
    make the whole panel scroll sideways; it is clipped or scrolls inside itself."""
    from PySide6.QtWidgets import QPushButton

    from planwin_ai.gui.widgets import ScrollPanel

    sp = ScrollPanel()
    sec = sp.add_section("Wide", "wide")
    wide = QPushButton("wide")
    wide.setMinimumWidth(600)
    sec.body_layout.addWidget(wide)
    for i in range(30):
        sec.body_layout.addWidget(QPushButton(f"row {i}"))
    sp.resize(280, 400)
    sp.show()
    _settle(app)
    assert sp.horizontalScrollBar().maximum() == 0
    assert sp.inner.width() == sp.viewport().width()
    assert sp.verticalScrollBar().maximum() > 0  # still scrolls vertically
