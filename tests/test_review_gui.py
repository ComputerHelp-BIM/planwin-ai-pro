"""GUI review regressions (offscreen): each test reproduces a defect found in the 1.2.0 review."""

import os
import subprocess
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication, QMessageBox

    QMessageBox.information = staticmethod(lambda *a, **k: 0)
    QMessageBox.warning = staticmethod(lambda *a, **k: 0)
    QMessageBox.critical = staticmethod(lambda *a, **k: 0)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app):
    from planwin_ai.ai.templates import build_template
    from planwin_ai.gui.main_window import MainWindow

    w = MainWindow(build_template("bungalow"))
    yield w
    w.dirty = False
    w.close()


def _tank(win, name="T1"):
    from planwin_ai.design.wizards import WaterTank, apply_water_tank

    pr = win.project
    top = pr.plan(pr.levels[-1].plan)
    marks = [c.mark for c in top.columns[:4]]
    win.mutate("Water tank", lambda: apply_water_tank(win.project, WaterTank(name=name, columns=marks)))


# ---------------------------------------------------------------- joint loads dialog
def test_joint_load_dialog_keeps_the_wizard_tags(win):
    """OK in Joint loads must not strip the 'source' tag of the tank loads: removing or re-applying
    the tank would otherwise leave its old loads behind (double counting)."""
    from planwin_ai.gui import dialogs

    _tank(win)
    assert all(j.get("source") == "Tank T1" for j in win.project.joint_loads)
    dlg = dialogs.JointLoadDialog(win, win.project)
    win.mutate("Joint loads", dlg.apply)
    assert all(j.get("source") == "Tank T1" for j in win.project.joint_loads)
    dialogs.remove_water_tank(win.project, "T1")
    assert win.project.joint_loads == []


def test_joint_load_dialog_rejects_invalid_rows(win, monkeypatch):
    """A typo in a row keeps the dialog open instead of silently dropping that load."""
    from PySide6.QtWidgets import QMessageBox

    from planwin_ai.gui import dialogs

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a) or 0))
    dlg = dialogs.JointLoadDialog(win, win.project)
    dlg._add()
    assert dlg.t.item(0, 0).text() == str(len(win.project.levels))  # "+ Row" used to leave the level blank
    dlg.t.item(0, 2).setText("15O")  # letter O for zero
    dlg.accept()
    assert warned and dlg.result() == 0
    dlg.t.item(0, 2).setText("150")
    dlg.accept()
    assert dlg.result() == 1
    win.mutate("Joint loads", dlg.apply)
    assert win.project.joint_loads[-1]["fz"] == 150.0 and win.project.joint_loads[-1]["level"] == 3


def test_column_sizes_dialog_rejects_invalid_sizes(win, monkeypatch):
    """One bad 'b x d' cell must not throw away every other edit after the dialog has closed."""
    from PySide6.QtWidgets import QMessageBox

    from planwin_ai.gui import dialogs

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a) or 0))
    dlg = dialogs.ColumnSizesDialog(win, win.project)
    dlg.t.item(0, 0).setText("0.4 x 0.6")
    dlg.t.item(1, 0).setText("0.3 x abc")
    dlg.accept()
    assert warned and dlg.result() == 0
    dlg.t.item(1, 0).setText("0.3 x 0.5")
    dlg.accept()
    assert dlg.result() == 1
    win.mutate("Column sizes", dlg.apply)
    assert win.project.column_sizes[dlg.marks[0]]["1"][:2] == [0.4, 0.6]


# ---------------------------------------------------------------- dimensions keep results
def test_dimension_edits_keep_the_analysis_and_design(win):
    win._run([{"action": "design"}], "Design", quiet=True)
    assert win.design_report() is not None
    win.canvas._add_dimension((0.0, 0.0), (3.0, 0.0), (1.0, 1.0))
    assert win.current_plan().dimensions and win.design_report() is not None and win.dirty
    win.undo()  # still one undo step
    assert not win.current_plan().dimensions


def test_dimension_context_actions_do_not_invalidate(win, monkeypatch):
    """The canvas context menu's dimension actions are drawing-only edits."""
    from planwin_ai.gui import canvas as canvas_mod

    win.canvas._add_dimension((0.0, 0.0), (3.0, 0.0), (1.0, 1.0))
    win._run([{"action": "design"}], "Design", quiet=True)
    seen = []

    class FakeMenu:
        def __init__(self, *_a):
            pass

        def addAction(self, text, fn=None):  # noqa: N802
            seen.append((text, fn))

            class A:
                def setEnabled(self, *_):  # noqa: N802
                    pass

            return A()

        def addSeparator(self):  # noqa: N802
            pass

        def exec(self, *_a):
            pass

    monkeypatch.setattr(canvas_mod, "QMenu", FakeMenu)
    win.canvas.resize(800, 600)
    win.canvas.zoom_extents()

    class Ev:
        def position(self):
            return win.canvas.w2s(1.5, 1.0)

        def globalPosition(self):  # noqa: N802
            return win.canvas.w2s(1.5, 1.0)

    win.canvas._context_menu(Ev())
    actions = dict(seen)
    actions["Delete dimension"]()
    assert not win.current_plan().dimensions and win.design_report() is not None


# ---------------------------------------------------------------- failed edits
def test_a_failed_edit_leaves_the_panels_on_the_restored_model(win):
    """After a failed edit is rolled back, a Properties edit must change the window's model, not the
    discarded copy the panel still pointed at."""
    plan = win.current_plan()
    beam = plan.beams[0]
    win.canvas.select_ids([beam.id])

    def fail():
        raise ValueError("a plan named 'X' already exists")

    win.mutate("Copy floor", fail)
    win.props.widgets["d"][0].setValue(0.9)
    win.props._apply()
    assert win.current_plan().find(beam.id).d == 0.9


# ---------------------------------------------------------------- revisions and undo
def test_revisions_are_an_undo_step(win, monkeypatch):
    """Saving a BOQ revision is undoable on its own; undo must not silently drop it with an older edit."""
    from planwin_ai.gui import dialogs

    win._run([{"action": "design"}], "Design", quiet=True)
    win.mutate("grid", lambda: win.project.grids.append({"name": "A", "axis": "x", "pos": 0.0}), analysis=False)

    class FakeRevisions(dialogs.RevisionsDialog):
        def exec(self):
            self.save_revision("R1")
            return 0

    monkeypatch.setattr(dialogs, "RevisionsDialog", FakeRevisions)
    win.revisions()
    assert [r["label"] for r in win.project.meta["revisions"]] == ["R1"] and win.dirty
    win.undo()  # undoes the revision only
    assert not win.project.meta.get("revisions") and win.project.grids
    win.redo()
    assert [r["label"] for r in win.project.meta["revisions"]] == ["R1"] and win.project.grids


# ---------------------------------------------------------------- plans and levels
def test_rename_plan_updates_staircases(win, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    win.project.stairs.append({"name": "ST1", "plan": "Typical"})
    win.set_current_plan("Typical")
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Typ2", True)))
    win.project_panel._rename_plan()
    assert "Typ2" in [lv.plan for lv in win.project.levels]
    assert win.project.stairs[0]["plan"] == "Typ2"


def test_delete_plan_removes_its_staircases(win):
    win.project.stairs.append({"name": "ST1", "plan": "Typical"})
    win.set_current_plan("Typical")
    win.project_panel._delete_plan()
    assert win.project.plan("Typical") is None and win.project.stairs == []


def test_inserting_a_level_keeps_column_sizes_and_joint_loads_with_their_level(win):
    pr = win.project
    _tank(win)
    pr = win.project
    n = len(pr.levels)
    mark = next(iter(pr.column_sizes))
    pr.column_sizes[mark] = {str(i): [0.3 + 0.1 * i, 0.6, 0.0] for i in range(1, n + 1)}
    before = {lv.name: pr.column_sizes[mark][str(i)] for i, lv in enumerate(pr.levels, start=1)}
    t = win.project_panel.levels
    t.setCurrentCell(0, 0)
    win.project_panel._add_level()  # inserted above the bottom level
    pr = win.project
    assert len(pr.levels) == n + 1
    for i, lv in enumerate(pr.levels, start=1):
        if lv.name in before:
            assert pr.column_sizes[mark][str(i)] == before[lv.name], lv.name
    assert {j["level"] for j in pr.joint_loads} == {n + 1}  # the tank stays on the roof
    assert pr.water_tanks[0]["level"] in (0, n + 1)


def test_moving_and_deleting_levels_keeps_level_data(win):
    pr = win.project
    n = len(pr.levels)
    mark = next(iter(pr.column_sizes))
    pr.column_sizes[mark] = {str(i): [0.3 + 0.1 * i, 0.6, 0.0] for i in range(1, n + 1)}
    pr.joint_loads.append({"level": 2, "mark": mark, "fz": 10.0, "case": "D"})
    pr.seismic.base_level = 2
    name2 = pr.levels[1].name
    size2 = pr.column_sizes[mark]["2"]
    t = win.project_panel.levels
    t.setCurrentCell(1, 0)
    win.project_panel._move(1)  # level 2 becomes level 3
    pr = win.project
    assert pr.levels[2].name == name2
    assert pr.column_sizes[mark]["3"] == size2 and pr.joint_loads[-1]["level"] == 3
    assert pr.seismic.base_level == 3
    t.setCurrentCell(0, 0)
    win.project_panel._del_level()  # the bottom level goes: everything moves down one
    pr = win.project
    assert pr.levels[1].name == name2
    assert pr.column_sizes[mark]["2"] == size2 and pr.joint_loads[-1]["level"] == 2
    assert str(n) not in pr.column_sizes[mark] and pr.seismic.base_level == 2
    win.undo()
    win.undo()
    assert win.project.column_sizes[mark]["2"] == size2 and win.project.levels[1].name == name2


def test_renumbering_beams_keeps_the_staircase_on_its_beams(win):
    plan = win.current_plan()
    b0, b1 = plan.beams[0], plan.beams[1]
    win.mutate("marks", lambda: (setattr(b0, "mark", "B90"), setattr(b1, "mark", "B91")))
    ids = [b0.id, b1.id]
    win.project.stairs.append({"name": "ST1", "plan": plan.name, "support_beams": ["B90", "B91"]})
    win.project.stairs.append({"name": "ST2", "plan": "elsewhere", "support_beams": ["B90"]})
    win.renumber("beam")
    marks = {b.id: b.mark for b in win.current_plan().beams}
    assert "B90" not in marks.values()
    assert win.project.stairs[0]["support_beams"] == [marks[i] for i in ids]
    assert win.project.stairs[1]["support_beams"] == ["B90"]  # another plan's staircase is not touched


def test_blank_level_name_is_restored_in_the_table(win):
    t = win.project_panel.levels
    old = win.project.levels[0].name
    t.item(0, 0).setText("")
    assert win.project.levels[0].name == old and t.item(0, 0).text() == old


# ---------------------------------------------------------------- keyboard
def test_typing_in_the_levels_table_edits_instead_of_switching_tools(win, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    win.show()
    win.activateWindow()
    app.processEvents()
    t = win.project_panel.levels
    t.setFocus()
    t.setCurrentCell(0, 0)
    app.processEvents()
    QTest.keyClick(t, Qt.Key_R)  # "Roof": R is the slab tool's shortcut
    app.processEvents()
    assert win.canvas.tool == "select"
    assert t.state() == t.State.EditingState


def test_typing_in_the_beam_load_tables_edits_instead_of_switching_tools(win, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    win.show()
    win.activateWindow()
    app.processEvents()
    plan = win.current_plan()
    win.canvas.select_ids([plan.beams[0].id])
    win.props.wedge_tbl.insertRow(0)
    t = win.props.wedge_tbl
    t.setFocus()
    t.setCurrentCell(0, 4)  # Case D/L: D is the measure tool's shortcut
    app.processEvents()
    QTest.keyClick(t, Qt.Key_D)
    app.processEvents()
    assert win.canvas.tool == "select"
    assert t.state() == t.State.EditingState
    # on the canvas the single-letter shortcuts still work
    t.closePersistentEditor(t.currentItem())
    win.canvas.setFocus()
    app.processEvents()
    QTest.keyClick(win.canvas, Qt.Key_D)
    assert win.canvas.tool == "measure"


def test_middle_button_pan_restores_the_drawing_cursor(win):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    win.set_tool("beam")
    c = win.canvas
    c.resize(600, 400)
    assert c.cursor().shape() == Qt.CrossCursor
    QTest.mousePress(c, Qt.MiddleButton, Qt.NoModifier, QPoint(100, 100))
    QTest.mouseMove(c, QPoint(140, 120))
    QTest.mouseRelease(c, Qt.MiddleButton, Qt.NoModifier, QPoint(140, 120))
    assert c.cursor().shape() == Qt.CrossCursor and c.tool == "beam"


def test_typing_in_the_command_search_never_runs_a_tool_shortcut(win, app):
    """Once the results popup is open it has the keyboard: 'a' of "wall" used to start the Area tool."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest

    win.show()
    win.activateWindow()
    app.processEvents()
    cs = win.command_search
    win.cmd["search"].trigger()
    app.processEvents()
    for ch in "wall":
        QTest.keyClick(QGuiApplication.focusWindow(), ch)  # where the window system sends the key
        app.processEvents()
    assert cs.text() == "wall" and win.canvas.tool == "select"
    assert any(c.name.startswith("Shear wall") for c in cs.results)
    QTest.keyClick(QGuiApplication.focusWindow(), Qt.Key_Escape)
    app.processEvents()
    assert cs.text() == "" and not cs.popup.isVisible()


# ---------------------------------------------------------------- properties set value
def test_multi_select_can_set_every_object_to_the_first_value(win):
    from PySide6.QtTest import QTest

    plan = win.current_plan()
    win.mutate("t", lambda: setattr(win.current_plan().slabs[1], "thickness", 0.2))
    plan = win.current_plan()
    first = plan.slabs[0].thickness
    win.canvas.select_ids([plan.slabs[0].id, plan.slabs[1].id])
    sp = win.props.widgets["thickness"][0]
    sp.lineEdit().selectAll()
    QTest.keyClicks(sp.lineEdit(), f"{first:g}")
    sp.interpretText()
    win.props._apply()
    plan = win.current_plan()
    assert plan.slabs[0].thickness == first and plan.slabs[1].thickness == first
    # untouched differing fields still keep each object's own value
    assert win.props.objs and "thickness" in win.props.widgets


def test_multi_select_untouched_fields_keep_their_own_values(win):
    plan = win.current_plan()
    win.mutate("t", lambda: setattr(win.current_plan().slabs[1], "thickness", 0.2))
    plan = win.current_plan()
    t0, t1 = plan.slabs[0].thickness, plan.slabs[1].thickness
    win.canvas.select_ids([plan.slabs[0].id, plan.slabs[1].id])
    win.props.widgets["live"][0].setValue(5.0)
    win.props._apply()
    plan = win.current_plan()
    assert (plan.slabs[0].thickness, plan.slabs[1].thickness) == (t0, t1)
    assert plan.slabs[0].live == plan.slabs[1].live == 5.0


# ---------------------------------------------------------------- theme
def test_theme_switch_recolours_the_results_navigator(win):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor

    from planwin_ai.gui.theme import PALETTES

    win.apply_theme("dark")
    try:
        it = win.results._items["Drift"]  # empty until the design runs: muted text
        assert it.data(0, Qt.ForegroundRole) == QColor(PALETTES["dark"]["muted"])
    finally:
        win.apply_theme("light")
    assert win.results._items["Drift"].data(0, Qt.ForegroundRole) == QColor(PALETTES["light"]["muted"])


# ---------------------------------------------------------------- files
def test_recent_files_with_an_ampersand_show_the_real_path(win, tmp_path):
    """A '&' in a folder or file name (common on Windows: "R&D", "Smith & Co") is a menu mnemonic."""
    fn = str(tmp_path / "R&D" / "Smith & Co.pwai")
    win._add_recent(fn)
    a = win.recent_menu.actions()[0]
    assert a.iconText() == fn  # iconText is the text without mnemonic markers


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
def test_licence_file_saved_by_notepad_loads(win, tmp_path, monkeypatch, encoding):
    """Notepad saves UTF-8 with a BOM (or "Unicode" = UTF-16); the BOM must not reach the JSON parser."""
    import json

    from PySide6.QtWidgets import QFileDialog

    from planwin_ai.gui import dialogs

    lic = tmp_path / "x.lic"
    lic.write_text('{"name": "Ünïcode Co", "sig": "x"}', encoding=encoding)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(lic), "")))
    dlg = dialogs.LicenseDialog(win, "Trial")
    dlg._load()
    assert json.loads(dlg.text.toPlainText())["name"] == "Ünïcode Co"


def test_licence_dialog_survives_a_binary_file(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from planwin_ai.gui import dialogs

    bad = tmp_path / "x.lic"
    bad.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00\xd8")
    warned = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(bad), "")))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a) or 0))
    dlg = dialogs.LicenseDialog(win, "Trial")
    dlg._load()  # used to raise UnicodeDecodeError inside the slot
    assert warned


def test_failed_save_as_keeps_the_previous_path(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    good = str(tmp_path / "a.pwai")
    win.path = good
    assert win.save()
    blocker = tmp_path / "file"
    blocker.write_text("x")
    bad = str(blocker / "b.pwai")  # parent is a file: cannot be written
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (bad, "")))
    win.mutate("t", lambda: setattr(win.project, "client", "X"))
    assert win.save_as() is False
    assert win.path == good and win.dirty


# ---------------------------------------------------------------- assistant thread
def test_closing_while_the_assistant_works_does_not_abort(tmp_path):
    """Quitting while the AI provider is still answering used to abort the process
    ("QThread: Destroyed while thread is still running")."""
    script = tmp_path / "close_busy.py"
    script.write_text(
        "import os, sys, time\n"
        f"sys.path.insert(0, {ROOT!r})\n"
        "from PySide6.QtWidgets import QApplication\n"
        "app = QApplication([])\n"
        "from planwin_ai.ai.templates import build_template\n"
        "from planwin_ai.gui.main_window import MainWindow\n"
        "w = MainWindow(build_template('bungalow'))\n"
        "w.assistant.plan = lambda text, context=None: (time.sleep(3), ('ok', []))[1]\n"
        "w.chat.send('hello')\n"
        "app.processEvents()\n"
        "w.dirty = False\n"
        "w.close()\n"
        "del w\n"
        "print('done', flush=True)\n"
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "done" in r.stdout


def test_assistant_changes_wait_for_an_open_dialog(win, app):
    """A reply that arrives while a modal dialog is open (settings, a wizard, "Save changes?") must not
    replace the model under the dialog: its OK would then edit a project that is no longer the window's."""
    import time

    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication, QDialog

    dlg = QDialog(win)
    dlg.setModal(True)
    dlg.show()
    app.processEvents()
    assert QApplication.activeModalWidget() is dlg
    before = win.project
    win.chat._busy = True
    win.chat._done("", [{"action": "set_sbc", "sbc": 321}])
    assert win.project is before and win.project.design.sbc != 321  # not while the dialog is open
    dlg.close()
    t0 = time.time()
    while win.project.design.sbc != 321 and time.time() - t0 < 5:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    assert win.project.design.sbc == 321 and not win.chat._busy


def test_assistant_changes_wait_for_an_open_menu(win, app):
    """Same for a context menu: its actions hold objects of the model it was opened on."""
    import time

    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication, QMenu

    m = QMenu(win)
    m.addAction("Toggle cantilever")
    m.popup(win.mapToGlobal(win.rect().center()))
    app.processEvents()
    assert QApplication.activePopupWidget() is m
    win.chat._busy = True
    win.chat._done("", [{"action": "set_sbc", "sbc": 322}])
    assert win.project.design.sbc != 322
    m.close()
    t0 = time.time()
    while win.project.design.sbc != 322 and time.time() - t0 < 5:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    assert win.project.design.sbc == 322


def test_assistant_reply_still_reaches_the_window(win, app):
    import time

    from PySide6.QtCore import QCoreApplication

    win.assistant.plan = lambda text, context=None: ("plain answer", [])
    win.chat.send("hello")
    t0 = time.time()
    while win.chat._busy and time.time() - t0 < 10:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert not win.chat._busy
    assert win.chat._msgs[-1][1] == "plain answer"
