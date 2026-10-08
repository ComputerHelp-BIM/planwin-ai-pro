"""Off-screen GUI smoke test: window builds, edits undo/redo, analysis and AI run."""

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


def test_main_window_workflow(app, tmp_path):
    from planwin_ai.core.model import Slab
    from planwin_ai.gui.main_window import MainWindow

    w = MainWindow()
    w.show()
    app.processEvents()
    plan = w.current_plan()
    n0 = len(plan.slabs)
    w.mutate("add", lambda: plan.slabs.append(Slab(mark="SX", points=[[20, 0], [24, 0], [24, 4], [20, 4]])))
    assert len(w.current_plan().slabs) == n0 + 1
    w.undo()
    assert len(w.current_plan().slabs) == n0
    w.redo()
    assert len(w.current_plan().slabs) == n0 + 1
    w.undo()
    w.analyze_plan()
    assert w.plan_result(w.current_plan_name) is not None
    final, res = w.run_ai_actions("", [{"action": "design"}])
    w.after_ai_change(res)
    assert w.design_report() is not None
    w.apply_theme("dark")
    w.view3d.refresh()
    app.processEvents()
    w.path = str(tmp_path / "t.pwai")
    assert w.save()
    w.dirty = False
    w.close()
