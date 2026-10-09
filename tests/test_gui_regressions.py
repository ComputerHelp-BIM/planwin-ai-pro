"""Off-screen GUI regression tests for 1.0.1 fixes."""

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
    from planwin_ai.gui.main_window import MainWindow

    w = MainWindow()
    yield w
    w.dirty = False
    w.close()


def test_ai_answer_without_actions_keeps_user_undo_history(win):
    plan = win.current_plan()
    win.mutate("user edit", lambda: setattr(plan.slabs[0], "live", 9.0))
    win.undo()
    assert len(win.redo_stack) == 1
    win.redo()
    n = len(win.undo_stack)
    win.chat._done("just an answer", [])  # what the chat panel does when the worker returns
    assert len(win.undo_stack) == n


def test_ai_change_is_one_undo_step(win):
    n = len(win.undo_stack)
    win.chat._done("", [{"action": "set_sbc", "sbc": 321}])
    assert len(win.undo_stack) == n + 1 and win.project.design.sbc == 321
    win.undo()
    assert win.project.design.sbc != 321


def test_multi_select_set_value_only_applies_changed_fields(win):
    plan = win.current_plan()
    b1, b2 = plan.beams[0], plan.beams[1]
    win.mutate("prep", lambda: (setattr(b1, "d", 0.45), setattr(b2, "d", 0.75), setattr(b2, "wall_thk", 0.115)))
    plan = win.current_plan()
    ids = [plan.beams[0].id, plan.beams[1].id]
    win.props.show_selection(ids)
    win.props.widgets["include_plaster"][0].setChecked(False)
    win.props._apply()
    plan = win.current_plan()
    a, b = plan.find(ids[0]), plan.find(ids[1])
    assert (a.d, b.d, b.wall_thk) == (0.45, 0.75, 0.115)
    assert a.include_plaster is False and b.include_plaster is False


def test_results_tables_cleared_when_results_are_invalidated(win):
    win.before_ai_change()
    final, res = win.run_ai_actions("", [{"action": "design"}])
    win.after_ai_change(res)
    assert win.results.tables["Columns"].rowCount() > 0
    win.mutate("edit", lambda: setattr(win.current_plan().slabs[0], "live", 5.0))
    for name in ("Columns", "Beams", "Footings", "Slabs", "Drift", "BOQ & cost", "Lateral"):
        assert win.results.tables[name].rowCount() == 0, name


def test_autosave_is_removed_after_save(win, tmp_path):
    win.dirty = True
    win._do_autosave()
    assert os.path.exists(win._autosave_path())
    win.path = str(tmp_path / "p.pwai")
    assert win.save()
    assert not os.path.exists(win._autosave_path())
