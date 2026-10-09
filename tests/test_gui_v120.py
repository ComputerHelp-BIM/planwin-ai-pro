"""Off-screen tests of the v1.2 main-window features: start page, command search (Ctrl+Q)
and one-line tooltips on every command."""

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
def start_win(app):
    """A window opened the way the exe starts: no project, start page enabled."""
    from planwin_ai.gui.main_window import MainWindow

    w = MainWindow()
    w.settings.setValue("start/show", "true")
    w.show_start()
    yield w
    w.dirty = False
    w.close()


def test_every_command_has_a_one_line_tooltip(start_win):
    for key, a in start_win.cmd.items():
        tip = a.toolTip()
        assert " – " in tip and "\n" not in tip, (key, tip)
        assert len(tip.split(" – ", 1)[1]) >= 10, (key, tip)
        if not a.shortcut().isEmpty():
            assert f"({a.shortcut().toString()})" in tip, (key, tip)


def test_start_page_hides_panels_and_template_opens_workspace(start_win):
    w = start_win
    assert w.on_start_page()
    assert not any(d.isVisible() for d in w.panel_docks())
    assert len(w.start_page.template_cards) == 10
    w.start_page.template_cards["bungalow"].on_click()
    assert not w.on_start_page() and w.project.name.lower().startswith("bungalow")


def test_start_page_lists_existing_recent_files_only(start_win, tmp_path):
    w = start_win
    real = tmp_path / "real.pwai"
    real.write_text("{}")
    w.settings.setValue("recent", [str(real), str(tmp_path / "gone.pwai")])
    w.start_page.refresh()
    assert w.start_page.recent_grid.count() == 1


def test_start_page_ai_prompt_goes_to_the_assistant(start_win, monkeypatch):
    sent = []
    monkeypatch.setattr(start_win.chat, "send", sent.append)
    start_win.start_page.prompt.setText("G+2 office in Pune")
    start_win.start_page._ask_ai()
    assert sent == ["G+2 office in Pune"] and not start_win.on_start_page()


def test_drawing_tool_leaves_the_start_page(start_win):
    start_win.cmd["tool_beam"].trigger()
    assert not start_win.on_start_page() and start_win.canvas.tool == "beam"


def test_command_search_ranks_and_runs(start_win):
    from planwin_ai.gui.command_search import rank

    cmds = start_win.search_commands()
    assert rank(cmds, "stair")[0].name == "Staircase"
    assert any("STAAD" in c.name for c in rank(cmds, "export staad"))
    assert rank(cmds, "drift")[0].name == "Show results: Drift"
    assert rank(cmds, "zzzz-nothing") == []
    # running a result: the search box is cleared and the command runs
    box = start_win.command_search
    box.setText("plan view")
    box._update("plan view")
    assert box.results and box.popup.count() >= 1
    box.run(box.results[0])
    assert box.text() == "" and not start_win.on_start_page()


def test_ctrl_q_focuses_the_search_box(start_win):
    start_win.cmd["search"].trigger()
    assert start_win.cmd["search"].shortcut().toString() == "Ctrl+Q"
