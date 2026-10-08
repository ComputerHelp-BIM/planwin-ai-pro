"""Off-screen tests of the 1.1.0 dialogs (settings, wizards, copy floor, grids, revisions) and panels."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from planwin_ai import units  # noqa: E402
from planwin_ai.ai.templates import build_template  # noqa: E402
from planwin_ai.core.model import Wall  # noqa: E402
from planwin_ai.units import T_TO_KN  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication, QMessageBox

    QMessageBox.information = staticmethod(lambda *a, **k: 0)
    QMessageBox.warning = staticmethod(lambda *a, **k: 0)
    QMessageBox.critical = staticmethod(lambda *a, **k: 0)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
    return QApplication.instance() or QApplication([])


@pytest.fixture
def parent(app):
    from PySide6.QtWidgets import QWidget

    w = QWidget()
    yield w
    w.deleteLater()


@pytest.fixture
def mks():
    units.set_system("MKS")
    yield units.current
    units.set_system("SI")


@pytest.fixture(scope="module")
def designed():
    """Bungalow with a shear wall, response spectrum analysis and a full design."""
    from planwin_ai.design.runner import run_full

    p = build_template("bungalow")
    p.seismic.method = "response_spectrum"
    for pl in p.plans:
        pl.walls.append(Wall(mark="W1", x1=1.0, y1=0.0, x2=3.0, y2=0.0, thickness=0.2))
    fm, fa, rep = run_full(p)
    return p, fm, fa, rep


class FakeMain:
    """The parts of MainWindow the panels use."""

    def __init__(self, project, fm=None, fa=None, rep=None, plan="Typical"):
        self.project, self.fm, self.fa, self.rep = project, fm, fa, rep
        self.current_plan_name = plan
        self.mutations: list[str] = []

    def current_plan(self):
        return self.project.plan(self.current_plan_name)

    def plan_result(self, name):
        from planwin_ai.core.plan_engine import PlanEngine

        return PlanEngine(self.project.plan(name)).run()

    def frame_model(self):
        return self.fm

    def frame_analysis(self):
        return self.fa

    def design_report(self):
        return self.rep

    def mutate(self, desc, fn):
        fn()
        self.mutations.append(desc)


# ------------------------------------------------------------------ dialogs
def test_dialogs_package_keeps_the_public_names():
    from planwin_ai.gui import dialogs

    for name in (
        "SettingsDialog",
        "AISettingsDialog",
        "TemplateDialog",
        "AutoSizeDialog",
        "ColumnSizesDialog",
        "MoveCopyDialog",
        "MirrorDialog",
        "JointLoadDialog",
        "LicenseDialog",
        "BeamDiagramDialog",
        "StairDialog",
        "TankDialog",
        "CopyFloorsDialog",
        "GridsDialog",
        "RevisionsDialog",
        "about_text",
        "_dspin",
        "_buttons",
    ):
        assert hasattr(dialogs, name), name
    assert "IS 456" in dialogs.about_text("Trial")


def test_settings_dialog_round_trips_method_and_rigid_diaphragm(parent):
    from planwin_ai.gui import dialogs

    p = build_template("bungalow")
    dlg = dialogs.SettingsDialog(parent, p)
    assert dlg.method.currentData() == "auto" and dlg.rigid.isChecked()
    assert "applies" in dlg.ductile.text()  # zone III, SMRF
    dlg.method.setCurrentIndex(dlg.method.findData("response_spectrum"))
    dlg.rigid.setChecked(False)
    dlg.apply()
    assert p.seismic.method == "response_spectrum" and p.seismic.rigid_diaphragm is False
    dlg = dialogs.SettingsDialog(parent, p)
    assert dlg.method.currentData() == "response_spectrum" and not dlg.rigid.isChecked()
    dlg.zone.setCurrentText("II")
    dlg.R.setCurrentIndex(0)
    assert "does not apply" in dlg.ductile.text()
    dlg.method.setCurrentIndex(dlg.method.findData("static"))
    dlg.apply()
    assert p.seismic.method == "static" and p.seismic.zone == "II"


def _select(lst, marks):
    for i in range(lst.count()):
        lst.item(i).setSelected(lst.item(i).text() in marks)


def _stair_loads(p, name="ST1", plan="Typical"):
    return [x for b in p.plan(plan).beams for x in b.part_loads if x.desc == f"Stair {name}"]


def test_stair_dialog_applies_tagged_loads_idempotently_and_removes_them(parent):
    from planwin_ai.gui import dialogs

    p = build_template("bungalow")
    dlg = dialogs.StairDialog(parent, p, "Typical")
    assert dlg.name.text() == "ST1" and dlg.beams.count() == 12
    _select(dlg.beams, ["B11", "B6"])
    dlg.width.setValue(1.0)
    assert "Mu" in dlg.preview.text() and "B11" in dlg.preview.text()
    dlg.apply()
    assert dlg.design is not None and dlg.design.span > 3.0
    assert len(_stair_loads(p)) == 4 and len(p.stairs) == 1
    # edit the existing definition and apply again: loads are replaced, not added
    dlg = dialogs.StairDialog(parent, p, "Typical")
    dlg.existing.setCurrentText("ST1")
    assert sorted(dlg.staircase().support_beams) == ["B11", "B6"] and dlg.width.value() == pytest.approx(1.0)
    dlg.waist.setValue(0.18)
    dlg.apply()
    assert len(_stair_loads(p)) == 4 and len(p.stairs) == 1 and p.stairs[0]["waist"] == pytest.approx(0.18)
    # rename: the old definition and its loads go
    dlg = dialogs.StairDialog(parent, p, "Typical")
    dlg.existing.setCurrentText("ST1")
    dlg.name.setText("ST2")
    dlg.apply()
    assert not _stair_loads(p) and len(_stair_loads(p, "ST2")) == 4 and [s["name"] for s in p.stairs] == ["ST2"]
    # remove
    dlg = dialogs.StairDialog(parent, p, "Typical")
    dlg.existing.setCurrentText("ST2")
    dlg._remove()
    assert dlg.remove
    dlg.apply()
    assert not _stair_loads(p, "ST2") and not p.stairs


def test_stair_dialog_validation_keeps_dialog_open_without_beams(parent):
    from planwin_ai.gui import dialogs

    p = build_template("bungalow")
    dlg = dialogs.StairDialog(parent, p, "Typical")
    dlg.accept()  # no support beam selected -> warning, not accepted, project untouched
    assert dlg.result() == 0 and not p.stairs


def test_tank_dialog_applies_joint_loads(parent):
    from planwin_ai.gui import dialogs

    p = build_template("bungalow")
    dlg = dialogs.TankDialog(parent, p)
    assert dlg.columns.count() == 9  # top level (Roof) plan
    _select(dlg.columns, ["C1", "C2", "C4", "C5"])
    dlg.capacity.setValue(5000)
    assert "on each of 4 columns" in dlg.preview.text()
    dlg.apply()
    mine = [j for j in p.joint_loads if j.get("source") == "Tank T1"]
    assert len(mine) == 4 and all(j["level"] == len(p.levels) for j in mine)
    assert sum(j["fz"] for j in mine) == pytest.approx(dlg.loads.water + dlg.loads.tank, rel=1e-3)
    dlg = dialogs.TankDialog(parent, p)
    dlg.existing.setCurrentText("T1")
    assert dlg.capacity.value() == 5000 and len(dlg.tank().columns) == 4
    dlg.apply()
    assert len([j for j in p.joint_loads if j.get("source") == "Tank T1"]) == 4 and len(p.water_tanks) == 1
    dlg = dialogs.TankDialog(parent, p)
    dlg.existing.setCurrentText("T1")
    dlg._remove()
    dlg.apply()
    assert not [j for j in p.joint_loads if j.get("source") == "Tank T1"] and not p.water_tanks


def test_copy_floors_dialog_duplicates_plan_and_adds_levels(parent):
    from planwin_ai.gui import dialogs

    p = build_template("bungalow")
    src = p.plan("Typical")
    n_levels = len(p.levels)
    dlg = dialogs.CopyFloorsDialog(parent, p, "Typical")
    dlg.dup_name.setText("Typical B")
    dlg.add.setChecked(True)
    dlg.n.setValue(2)
    dlg.lv_plan.setCurrentText(dlg.COPY)
    dlg.height.setValue(3.1)
    dlg.apply()
    new = p.plan("Typical B")
    assert new is not None and dlg.new_plan == "Typical B"
    for a, b in ((src.slabs, new.slabs), (src.columns, new.columns), (src.beams, new.beams)):
        assert [o.mark for o in a] == [o.mark for o in b]
        assert not {o.id for o in a} & {o.id for o in b}
    assert len(p.levels) == n_levels + 2
    assert [lv.plan for lv in p.levels[-2:]] == ["Typical B", "Typical B"]
    assert all(lv.height == pytest.approx(3.1) for lv in p.levels[-2:])
    assert len({lv.name for lv in p.levels}) == len(p.levels)


def test_grids_dialog_generates_sorted_unique_grids_from_columns(parent):
    from planwin_ai.gui import dialogs

    p = build_template("bungalow")
    p.plan("Typical").columns[0].x += 0.01  # within 50 mm of the 0.0 line -> merged
    dlg = dialogs.GridsDialog(parent, p, "Typical")
    dlg.generate()
    dlg.apply()
    xs = [g for g in p.grids if g["axis"] == "x"]
    ys = [g for g in p.grids if g["axis"] == "y"]
    assert [g["name"] for g in xs] == ["1", "2", "3"] and [g["name"] for g in ys] == ["A", "B", "C"]
    assert [round(g["pos"], 2) for g in ys] == [0.0, 4.0, 7.5]
    assert p.grids == sorted(p.grids, key=lambda g: (g["axis"], g["pos"]))
    dlg = dialogs.GridsDialog(parent, p, "Typical")
    assert dlg.t.rowCount() == 6
    dlg.t.setRowCount(0)
    dlg.apply()
    assert p.grids == []


def test_revisions_dialog_save_and_compare(parent, designed):
    from planwin_ai.gui import dialogs

    p, _fm, _fa, rep = designed
    p.meta.pop("revisions", None)
    dlg = dialogs.RevisionsDialog(parent, p, rep)
    assert dlg.save_btn.isEnabled() and not dlg.changed
    dlg.save_revision("R1")
    assert dlg.changed and p.meta["revisions"][0]["label"] == "R1" and dlg.list.count() == 1
    # one selected revision vs the current design: no change anywhere
    dlg.list.item(0).setSelected(True)
    dlg.compare_selected()
    assert dlg.t.rowCount() > 4 and dlg.t.item(0, 4).text() == "+0.00"
    # a cheaper revision: the current design shows increases (red)
    cheaper = {**p.meta["revisions"][0], "label": "R0", "total": {**p.meta["revisions"][0]["total"]}}
    cheaper["total"]["cost"] *= 0.5
    rows = dlg.compare(cheaper, p.meta["revisions"][0])
    r = next(i for i, row in enumerate(rows) if row[0] == "Total" and "cost" in row[1])
    assert rows[r][4] > 0 and dlg.t.item(r, 4).background().color().red() == 220
    dlg.list.clearSelection()
    dlg.list.item(0).setSelected(True)
    dlg.delete_selected()
    assert p.meta["revisions"] == [] and dlg.list.count() == 0
    assert not dialogs.RevisionsDialog(parent, p, None).save_btn.isEnabled()


# ------------------------------------------------------------------ panels
def test_results_panel_fills_new_tabs_and_converts_units(app, designed, mks):
    from planwin_ai.gui.panels import RESULT_TABS, ResultsPanel

    p, fm, fa, rep = designed
    units.set_system("SI")
    main = FakeMain(p, fm, fa, rep)
    rp = ResultsPanel(main)
    assert list(rp.tables) == list(RESULT_TABS) and RESULT_TABS[:10] == (
        "Issues",
        "Column loads",
        "Beam loads",
        "Columns",
        "Beams",
        "Footings",
        "Slabs",
        "Lateral",
        "Drift",
        "BOQ & cost",
    )
    rp.refresh()
    for name in ("Walls", "IS 13920", "Irregularity", "Modal / RS", "BOQ by floor", "Columns"):
        assert rp.tables[name].rowCount() > 0, name
    modal = rp.tables["Modal / RS"]
    assert modal.item(0, 0).text().startswith("Method: response spectrum")
    assert any(modal.item(r, 0).text() == "RSX" for r in range(modal.rowCount()))
    rp.show_tab("IS 13920")
    assert rp.tabText(rp.currentIndex()) == "IS 13920"

    def header(t, c):
        return t.horizontalHeaderItem(c).text()

    cols = rp.tables["Columns"]
    assert header(cols, 4) == "Pu kN" and header(cols, 5) == "Mux kN·m"
    pu_si = float(cols.item(0, 4).text())
    units.set_system("MKS")
    rp.refresh()
    cols = rp.tables["Columns"]
    assert header(cols, 4) == "Pu t" and header(cols, 5) == "Mux t·m"
    assert float(cols.item(0, 4).text()) == pytest.approx(pu_si / T_TO_KN, abs=2e-3)
    assert header(rp.tables["Footings"], 7) == "q t/m²"
    assert header(rp.tables["Beam loads"], 4) == "Eq. UDL t/m"
    assert rp.tables["Columns"].item(0, 2).text() == f"{round(rep.columns[0].b, 3):g}"  # lengths unconverted
    # invalidated results empty every design tab
    main.rep = main.fm = main.fa = None
    rp.refresh()
    for name in ("Walls", "Combined footings", "IS 13920", "Irregularity", "Modal / RS", "BOQ by floor", "Lateral"):
        assert rp.tables[name].rowCount() == 0, name


def test_properties_panel_edits_wall_and_multi_select_keeps_untouched(app):
    from planwin_ai.gui.panels import PropertiesPanel

    p = build_template("bungalow")
    plan = p.plan("Typical")
    plan.walls += [Wall(mark="W1", thickness=0.2), Wall(mark="W2", x1=0, y1=2, x2=0, y2=5, thickness=0.3)]
    main = FakeMain(p)
    pp = PropertiesPanel(main)
    w1, w2 = plan.walls
    pp.show_selection([w1.id])
    assert pp.title.text() == "Wall W1" and "length" not in pp.widgets and "Length 3.000 m" in pp.info.text()
    pp.widgets["thickness"][0].setValue(0.25)
    pp._apply()
    assert w1.thickness == pytest.approx(0.25) and main.mutations == ["Edit properties"]
    pp.show_selection([w1.id, w2.id])
    assert "x1" not in pp.widgets and "mark" not in pp.widgets
    pp.widgets["grade"][0].setCurrentText("M30")
    pp._apply()
    assert (w1.thickness, w2.thickness) == (0.25, 0.3) and w1.grade == w2.grade == "M30"


def test_properties_panel_mks_display_and_no_round_trip(app, mks):
    from planwin_ai.gui.panels import PropertiesPanel

    p = build_template("bungalow")
    plan = p.plan("Typical")
    s = plan.slabs[0]
    s.live, s.floor_finish = 2.0, 1.234567
    main = FakeMain(p)
    pp = PropertiesPanel(main)
    pp.show_selection([s.id])
    assert pp.form.labelForField(pp.widgets["live"][0]).text() == "Live load (t/m²)"
    assert pp.widgets["live"][0].value() == pytest.approx(2.0 / T_TO_KN, abs=1e-4)
    pp.widgets["live"][0].setValue(0.5)
    pp._apply()
    assert s.live == pytest.approx(0.5 * T_TO_KN) and s.floor_finish == 1.234567  # untouched: exact
    # beam load tables: unedited cells keep the exact kN value, the wizard tag survives an edit
    from planwin_ai.design.wizards import Staircase, apply_staircase

    apply_staircase(p, Staircase(name="ST1", plan="Typical", support_beams=["B11", "B6"], width=1.0))
    b = next(x for x in plan.beams if x.mark == "B11")
    before = [(x.w1, x.desc) for x in b.part_loads]
    pp.show_selection([b.id])
    assert pp.wedge_tbl.horizontalHeaderItem(2).text() == "w1 (t/m)"
    pp.widgets["d"][0].setValue(0.5)
    pp._apply()
    assert b.d == pytest.approx(0.5) and [(x.w1, x.desc) for x in b.part_loads] == before
    pp.show_selection([b.id])
    pp.wedge_tbl.item(0, 2).setText("1")
    pp._apply()
    assert b.part_loads[0].w1 == pytest.approx(T_TO_KN) and b.part_loads[0].desc == "Stair ST1"


def test_project_panel_lists_walls_stairs_and_tanks(app):
    from planwin_ai.gui.panels import ProjectPanel

    p = build_template("bungalow")
    p.plan("Typical").walls.append(Wall())
    p.stairs.append({"name": "ST1", "plan": "Typical"})
    p.water_tanks.append({"name": "T1", "capacity_l": 5000})
    main = FakeMain(p)
    main.set_current_plan = lambda name: None
    pp = ProjectPanel(main)
    pp.refresh()
    tips = [pp.plans.item(i).toolTip() for i in range(pp.plans.count())]
    assert any("1 walls" in t for t in tips)
    assert "ST1" in pp.extras_lbl.text() and "T1 (5000 L)" in pp.extras_lbl.text()
