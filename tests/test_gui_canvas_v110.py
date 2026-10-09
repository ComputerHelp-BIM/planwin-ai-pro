"""Off-screen tests of the v1.1 plan canvas tools (walls, area, dimensions, snaps, ortho) and the 3-D view.

A minimal fake main window keeps these independent of ``main_window.py``.
"""

import json
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication, QMessageBox

    QMessageBox.information = staticmethod(lambda *a, **k: 0)
    QMessageBox.warning = staticmethod(lambda *a, **k: 0)
    QMessageBox.critical = staticmethod(lambda *a, **k: 0)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
    return QApplication.instance() or QApplication([])


class FakeMain:
    def __init__(self, project=None):
        from planwin_ai.core.model import Plan, Project

        self.project = project or Project(plans=[Plan(name="P1")])
        self.defaults = {
            "slab_thickness": 0.125,
            "slab_live": 2.0,
            "slab_ff": 1.0,
            "slab_other": 0.0,
            "grade": "M25",
            "col_b": 0.23,
            "col_d": 0.45,
            "beam_b": 0.23,
            "beam_d": 0.45,
            "wall_thk": 0.23,
            "wall_t": 0.2,
        }
        self.theme_name = "light"
        self.edits: list[str] = []
        self.canvas = None
        self.fm = self.fa = self.rep = None

    def current_plan(self):
        return self.project.plans[0] if self.project.plans else None

    def mutate(self, desc, fn, analysis=True):
        self.edits.append(desc)
        fn()
        if self.canvas is not None:
            self.canvas.update()

    def plan_result(self, _name):
        return None

    def set_tool(self, tool):
        self.canvas.set_tool(tool)

    def delete_selection(self):
        self.current_plan().remove(self.canvas.selection)

    def focus_properties(self): ...
    def move_copy_dialog(self): ...
    def mirror_dialog(self): ...
    def copy_selection_to_new_plan(self): ...
    def show_beam_diagram(self, _b): ...
    def show_column_breakup(self, _c): ...

    def frame_model(self):
        return self.fm

    def frame_analysis(self):
        return self.fa

    def design_report(self):
        return self.rep


@pytest.fixture
def cv(app):
    from planwin_ai.gui.canvas import PlanCanvas

    main = FakeMain()
    c = PlanCanvas(main)
    main.canvas = c
    c.resize(900, 700)
    c.show()
    app.processEvents()
    c.scale, c.ox, c.oy = 40.0, 100.0, 600.0
    yield c
    c.close()


def _click(c, x, y, button=None, mods=None):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.mouseClick(c, button or Qt.LeftButton, mods or Qt.NoModifier, c.w2s(x, y).toPoint())


# ------------------------------------------------------------------ pure helpers
def test_polygon_stats_and_helpers():
    from planwin_ai.gui.canvas import dim_line, dim_offset, ortho_point, polygon_stats

    a, per = polygon_stats([(0, 0), (4, 0), (4, 3), (0, 3)])
    assert a == pytest.approx(12.0) and per == pytest.approx(14.0)
    assert polygon_stats([(0, 0), (0, 3), (4, 3), (4, 0)])[0] == pytest.approx(12.0)  # clockwise
    assert polygon_stats([(0, 0)]) == (0.0, 0.0)
    assert ortho_point((1, 1), (5, 1.7)) == (5, 1) and ortho_point((1, 1), (1.3, 6)) == (1, 6)
    assert dim_offset((0, 0), (4, 0), (2, 1.5)) == pytest.approx(1.5)
    assert dim_offset((0, 0), (4, 0), (2, -1)) == pytest.approx(-1.0)
    assert dim_line({"x1": 0, "y1": 0, "x2": 4, "y2": 0, "offset": 1}) == ((0, 1), (4, 1))


# ------------------------------------------------------------------ walls
def test_wall_tool_creates_and_hits_wall(cv):
    from planwin_ai.core.model import Beam, Wall

    plan = cv.plan
    plan.beams.append(Beam(mark="B1", x1=0, y1=1, x2=6, y2=1))
    cv.set_tool("wall")
    _click(cv, 1.0, 1.0)
    _click(cv, 5.0, 1.0)
    assert len(plan.walls) == 1 and cv.main.edits[-1] == "Add wall"
    w = plan.walls[0]
    assert (w.mark, w.x1, w.y1, w.x2, w.y2, w.thickness, w.grade) == ("W1", 1.0, 1.0, 5.0, 1.0, 0.2, "M25")
    assert cv.selection == [w.id]
    assert isinstance(cv.hit(3.0, 1.05), Wall)  # walls win over a beam on the same line
    assert cv.hit(5.5, 1.0) is plan.beams[0]
    # window select and Ctrl+A include walls
    cv.set_tool("select")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, cv.w2s(0.5, 0.5).toPoint())
    QTest.mouseMove(cv, cv.w2s(5.5, 1.5).toPoint())
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, cv.w2s(5.5, 1.5).toPoint())
    assert cv.selection == [w.id]
    cv.select_ids([])
    QTest.keyClick(cv, Qt.Key_A, Qt.ControlModifier)
    assert set(cv.selection) == {w.id, plan.beams[0].id}


# ------------------------------------------------------------------ ortho and snaps
def test_ortho_constrains_second_point(cv, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    cv.set_tool("beam")
    _click(cv, 0.0, 0.0)
    assert cv._beam_start == (0.0, 0.0)
    assert cv.pick(cv.w2s(3.0, 0.4)) == (3.0, 0.4)
    assert cv.pick(cv.w2s(3.0, 0.4), Qt.ShiftModifier) == (3.0, 0.0)  # Shift held
    seen = []
    cv.orthoChanged.connect(seen.append)
    QTest.keyClick(cv, Qt.Key_F8)
    assert cv.ortho is True and seen == [True]
    assert cv.pick(cv.w2s(0.4, 2.5)) == (0.0, 2.5)
    _click(cv, 3.0, 0.4)
    b = cv.plan.beams[-1]
    assert (b.x1, b.y1, b.x2, b.y2) == (0.0, 0.0, 3.0, 0.0)
    cv.set_ortho(False)
    assert cv.ortho is False and seen == [True, False]


def test_midpoint_and_end_snaps(cv):
    from planwin_ai.core.model import Beam, Slab

    plan = cv.plan
    plan.beams.append(Beam(mark="B1", x1=0, y1=0, x2=4, y2=0))
    plan.slabs.append(Slab(mark="S1", points=[[10, 0], [14, 0], [14, 4], [10, 4]]))
    assert cv.snap(cv.w2s(2.05, 0.03)) == (2.0, 0.0) and cv._snap_kind == "mid"
    assert cv.snap(cv.w2s(14.05, 2.1)) == (14.0, 2.0) and cv._snap_kind == "mid"  # slab edge midpoint
    assert cv.snap(cv.w2s(3.95, 0.05)) == (4.0, 0.0) and cv._snap_kind == "end"
    cv.snap_mid = False
    assert cv.snap(cv.w2s(2.05, 0.03)) == (2.05, 0.05) and cv._snap_kind is None
    cv.snap_ends = False
    assert cv.snap(cv.w2s(3.95, 0.05)) == (3.95, 0.05)


def test_grid_snaps_and_priority(cv):
    from planwin_ai.core.model import Column

    cv.main.project.grids = [
        {"name": "A", "axis": "x", "pos": 0.0},
        {"name": "B", "axis": "x", "pos": 5.0},
        {"name": "1", "axis": "y", "pos": 0.0},
        {"name": "2", "axis": "y", "pos": 4.0},
    ]
    assert cv.snap(cv.w2s(5.1, 3.95)) == (5.0, 4.0) and cv._snap_kind == "grid"
    assert cv.snap(cv.w2s(5.1, 2.0)) == (5.0, 2.0) and cv._snap_kind == "line"
    assert cv.snap(cv.w2s(2.0, 4.12)) == (2.0, 4.0) and cv._snap_kind == "line"
    cv.plan.columns.append(Column(mark="C1", x=5.1, y=4.0))
    assert cv.snap(cv.w2s(5.03, 4.0)) == (5.1, 4.0) and cv._snap_kind == "end"  # ends beat grid points
    cv.snap_grid = False
    assert cv.snap(cv.w2s(5.1, 2.0)) == (5.1, 2.0)


# ------------------------------------------------------------------ area and measure
def test_area_tool_reports_area_and_perimeter(cv):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    msgs = []
    cv.status.connect(msgs.append)
    cv.set_tool("area")
    for x, y in ((10, 10), (14, 10), (14, 13), (10, 13)):
        _click(cv, x, y)
    QTest.keyClick(cv, Qt.Key_Return)
    assert msgs[-1] == "Area 12.000 m²  Perimeter 14.00 m"
    assert cv.last_area == pytest.approx((12.0, 14.0)) and cv._area_closed and not cv._area_pts
    cv.grab()  # closed polygon highlighted
    # next click starts a new polygon; right-click closes it
    for x, y in ((0, 0), (2, 0), (2, 2)):
        _click(cv, x, y)
    assert cv._area_closed is None and len(cv._area_pts) == 3
    _click(cv, 1, 1, Qt.RightButton)
    assert cv.last_area == pytest.approx((2.0, 4 + 8**0.5))
    # clicking the first point closes too
    for x, y in ((20, 0), (23, 0), (23, 1), (20, 0)):
        _click(cv, x, y)
    assert cv.last_area[0] == pytest.approx(1.5)
    assert not cv.plan.slabs and not cv.main.edits  # nothing added to the model
    QTest.keyClick(cv, Qt.Key_Escape)
    assert cv._area_closed is None


def test_measure_tool(cv):
    msgs = []
    cv.status.connect(msgs.append)
    cv.set_tool("measure")
    _click(cv, 0, 0)
    cv._mouse_world = (3.0, 4.0)
    cv.grab()  # live distance drawn
    _click(cv, 3, 4)
    assert msgs[-1].startswith("Distance 5.000 m")


# ------------------------------------------------------------------ dimensions
def test_dimension_tool_and_context_helpers(cv):
    cv.set_tool("dimension")
    _click(cv, 0, 0)
    _click(cv, 4.5, 0)
    cv._mouse_world = (2.0, 0.75)
    cv.grab()  # preview
    _click(cv, 2.0, 0.75)
    assert cv.plan.dimensions == [{"x1": 0.0, "y1": 0.0, "x2": 4.5, "y2": 0.0, "offset": 0.75}]
    assert cv.main.edits[-1] == "Add dimension"
    _click(cv, 0, 0)
    _click(cv, 0, 3)
    _click(cv, -1.25, 1)
    assert cv.plan.dimensions[1]["offset"] == pytest.approx(1.25)  # left of a line going up
    assert cv.dim_at(2.0, 0.76) == 0 and cv.dim_at(-1.25, 2.0) == 1 and cv.dim_at(2.0, 2.0) is None
    cv._del_dim(0)
    assert len(cv.plan.dimensions) == 1


def test_dimensions_round_trip():
    from planwin_ai.core.model import Plan, Project
    from planwin_ai.io import project_io

    p = Project(plans=[Plan(name="P1", dimensions=[{"x1": 0, "y1": 0, "x2": 4.5, "y2": 0, "offset": 1.0}])])
    q = project_io.project_from_json(json.dumps(p.to_dict()))
    assert q.plans[0].dimensions == p.plans[0].dimensions
    d = p.to_dict()
    del d["plans"][0]["dimensions"]
    assert project_io.project_from_json(json.dumps(d)).plans[0].dimensions == []
    assert Plan().dimensions == []


# ------------------------------------------------------------------ painting
def test_paint_with_walls_grids_dims(cv, app):
    from planwin_ai.core.model import Beam, Column, Slab, Wall

    plan = cv.plan
    plan.slabs.append(Slab(mark="S1", points=[[0, 0], [5, 0], [5, 4], [0, 4]]))
    plan.columns.append(Column(mark="C1", x=0, y=0))
    plan.beams.append(Beam(mark="B1", x1=0, y1=0, x2=5, y2=0))
    plan.walls += [Wall(mark="W1", x1=5, y1=0, x2=5, y2=4), Wall(mark="W2", x1=0, y1=4, x2=3, y2=6, thickness=0.3)]
    plan.dimensions += [
        {"x1": 0, "y1": 0, "x2": 5, "y2": 0, "offset": -1.0},
        {"x1": 0, "y1": 0, "x2": 0, "y2": 4, "offset": 0.0},
        {"bad": 1},
    ]
    cv.main.project.grids = [{"name": "A", "axis": "x", "pos": 0}, {"name": "1", "axis": "y", "pos": 4}, {"x": 1}]
    cv.selection = [plan.walls[0].id]
    cv.set_ortho(True)
    for theme in ("light", "dark"):
        cv.main.theme_name = theme
        for tool in ("wall", "area", "dimension"):
            cv.set_tool(tool)
            _click(cv, 1, 1)
            cv._mouse_world = (2.0, 1.5)
            assert not cv.grab().isNull()
        cv.show_grids = cv.show_dims = cv.show_marks = False
        assert not cv.grab().isNull()
        cv.show_grids = cv.show_dims = cv.show_marks = True
        cv.scale = 5.0  # bubbles clamped into view
        assert not cv.grab().isNull()
        cv.scale = 40.0
    cv.main.project.plans.clear()
    assert not cv.grab().isNull()


# ------------------------------------------------------------------ 3-D view
def test_view3d_draws_walls_and_centre_of_mass(app):
    from planwin_ai.ai.templates import build_template
    from planwin_ai.core.model import Wall
    from planwin_ai.design.runner import run_full
    from planwin_ai.gui.view3d import Frame3DView

    p = build_template("bungalow")
    for pl in p.plans:
        pl.walls.append(Wall(mark="W1", x1=0.5, y1=0.0, x2=3.5, y2=0.0, thickness=0.2))
    fm, fa, rep = run_full(p)
    assert not [i for i in fm.issues if i.level == "error"]
    kinds = {m.kind for m in fm.members.values()}
    assert {"wall", "column", "beam"} <= kinds
    assert any(lv.master is not None for lv in fm.levels)
    main = FakeMain(p)
    main.fm, main.fa, main.rep = fm, fa, rep
    v = Frame3DView(main)
    v.resize(800, 600)
    v.show()
    app.processEvents()
    assert v.show_cm.isChecked()
    for theme in ("light", "dark"):
        main.theme_name = theme
        for i in range(v.mode.count()):
            v.mode.setCurrentIndex(i)
            assert not v.canvas.grab().isNull()
    v.level.setCurrentIndex(2)
    v.show_cm.setChecked(False)
    assert not v.canvas.grab().isNull()
    v.close()
