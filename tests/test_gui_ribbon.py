"""Off-screen tests of the v1.1 ribbon main window: commands, shortcuts, seismic options,
units, wall editing and the calculation sheets for the selection."""

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


@pytest.fixture
def win(app):
    from planwin_ai.ai.templates import build_template
    from planwin_ai.gui.main_window import MainWindow

    w = MainWindow(build_template("bungalow"))
    yield w
    w.dirty = False
    w.close()


def test_ribbon_tabs_and_unique_shortcuts(win):
    from collections import Counter

    assert list(win.ribbon.pages) == ["Home", "Plan", "Loads", "Frame", "Design", "Output", "View", "Help"]
    keys = Counter(a.shortcut().toString() for a in win.cmd.values() if not a.shortcut().isEmpty())
    assert not [k for k, n in keys.items() if n > 1]
    # every action is on the window, so its shortcut works whichever tab is showing
    assert all(a in win.actions() for a in win.cmd.values())


def test_every_export_format_has_a_ribbon_button(win):
    from planwin_ai.services import exports

    shown = {b.defaultAction() for b in win.ribbon.action_buttons()}
    for key in exports.keys():
        assert win.cmd[f"export_{key}"] in shown, key


def test_tool_actions_drive_the_canvas(win):
    win.cmd["tool_wall"].trigger()
    assert win.canvas.tool == "wall" and win.cmd["tool_wall"].isChecked()
    win.set_tool("select")
    assert win.cmd["tool_select"].isChecked()


def test_seismic_method_and_diaphragm_are_undoable_and_synced(win):
    win.cmd["method_response_spectrum"].trigger()
    assert win.project.seismic.method == "response_spectrum"
    win.cmd["diaphragm"].trigger()  # toggles off
    assert win.project.seismic.rigid_diaphragm is False
    win.undo()
    win.undo()
    assert win.project.seismic.method == "auto" and win.project.seismic.rigid_diaphragm is True
    assert win.cmd["method_auto"].isChecked() and win.cmd["diaphragm"].isChecked()


def test_units_switch_is_display_only(win):
    from planwin_ai import units

    before = win.snapshot()
    try:
        win.set_units("MKS")
        assert units.current.mks and win.units_combo.currentData() == "MKS"
        assert win.snapshot() == before
    finally:
        win.set_units("SI")
    assert not units.current.mks


def test_mutate_without_analysis_keeps_results(win):
    win._run([{"action": "design"}], "Design", quiet=True)
    assert win.design_report() is not None
    win.mutate("grid", lambda: win.project.grids.append({"name": "A", "axis": "x", "pos": 0.0}), analysis=False)
    assert win.design_report() is not None and win.dirty
    win.undo()
    assert win.project.grids == [] and win.design_report() is None


def test_move_copy_and_mirror_handle_walls(win, monkeypatch):
    from planwin_ai.core.model import Wall
    from planwin_ai.gui import dialogs

    plan = win.current_plan()
    win.mutate("wall", lambda: plan.walls.append(Wall(mark="W1", x1=0, y1=0, x2=2, y2=0)))
    plan = win.current_plan()
    win.canvas.selection = [plan.walls[0].id]

    class Spin:
        def __init__(self, v):
            self.v = v

        def value(self):
            return self.v

        def isChecked(self):  # noqa: N802
            return self.v

    class FakeMove:
        def __init__(self, *_a):
            self.dx, self.dy, self.copy, self.n = Spin(0.0), Spin(5.0), Spin(True), Spin(2)

        def exec(self):
            return True

    monkeypatch.setattr(dialogs, "MoveCopyDialog", FakeMove)
    win.move_copy_dialog()
    walls = win.current_plan().walls
    assert [w.mark for w in walls] == ["W1", "W2", "W3"] and [w.y1 for w in walls] == [0, 5, 10]
    assert len({w.id for w in walls}) == 3

    class FakeMirror:
        def __init__(self, *_a):
            self.vertical, self.at, self.copy = Spin(True), Spin(10.0), Spin(True)

        def exec(self):
            return True

    win.canvas.selection = [walls[0].id]
    monkeypatch.setattr(dialogs, "MirrorDialog", FakeMirror)
    win.mirror_dialog()
    w4 = win.current_plan().walls[-1]
    assert w4.mark == "W4" and {w4.x1, w4.x2} == {20.0, 18.0}


def test_calc_sheets_for_selection(win, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    out = tmp_path / "calc.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    plan = win.current_plan()
    win.canvas.selection = [plan.beams[0].id, plan.columns[0].id]
    win.calc_for_selection()
    assert out.exists() and out.stat().st_size > 1000
    rep = win.design_report()
    assert rep is not None  # designed on demand


def test_export_default_names_follow_the_registry(win):
    assert win.export_default_name("staad").endswith(".std")
    assert win.current_plan_name.replace(" ", "_") in win.export_default_name("dxf")


def test_new_dialogs_are_wired_to_the_ribbon(win, monkeypatch):
    """Each wizard / editor opens from its ribbon command and applies as one undo step."""
    from planwin_ai.gui import dialogs

    for name in ("GridsDialog", "CopyFloorsDialog", "StairDialog", "TankDialog", "SettingsDialog"):
        monkeypatch.setattr(getattr(dialogs, name), "exec", lambda self: True)
    n_plans, n_undo = len(win.project.plans), len(win.undo_stack)
    win.cmd["grids"].trigger()
    win.cmd["copy_floor"].trigger()
    # with no support beams / columns picked the wizards are refused and rolled back (no undo step);
    # valid input is covered by tests/test_gui_panels_v110.py
    win.cmd["stairs"].trigger()
    win.cmd["tank"].trigger()
    assert not any(str(j.get("source", "")).startswith("Tank") for j in win.project.joint_loads)
    win.cmd["settings"].trigger()
    assert len(win.undo_stack) == n_undo + 3
    assert len(win.project.plans) >= n_plans
    monkeypatch.setattr(dialogs.RevisionsDialog, "exec", lambda self: 0)
    win.cmd["revisions"].trigger()  # opens and closes without changes
    for _ in range(3):
        win.undo()
    assert len(win.project.plans) == n_plans
