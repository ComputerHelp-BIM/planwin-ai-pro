import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# GUI preferences (recent files, theme, start page …) go to a throw-away INI file, never the user's
os.environ["PLANWIN_SETTINGS"] = os.path.join(tempfile.mkdtemp(prefix="pwai-settings-"), "settings.ini")


@pytest.fixture(autouse=True)
def _isolated_appdata(monkeypatch, tmp_path):
    """Keep licence/trial/autosave files out of the real user profile and registry."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    from planwin_ai.licensing import license as lic

    # Trial copies go to two tmp files only - never HKCU or the real home directory.
    stores = [lic.FileStore(lic._trial_path), lic.FileStore(str(tmp_path / "xdg-data" / "PlanWinAIPro" / ".trial"))]
    monkeypatch.setattr(lic, "TRIAL_STORES", stores)
    monkeypatch.setattr(lic, "machine_id", lambda: "test-machine-0001")
    monkeypatch.setattr(lic, "_written_this_run", False)
    yield


@pytest.fixture
def tmpdir_path():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture(autouse=True)
def _delete_leftover_windows():
    """Delete the top-level windows a GUI test leaves behind. Closed windows otherwise stay alive
    for the whole session, and every later QApplication.setStyleSheet re-styles all of them."""
    yield
    if "PySide6.QtWidgets" not in sys.modules:
        return
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for w in app.topLevelWidgets():
        if getattr(w, "dirty", None) is not None:
            w.dirty = False  # no "save changes?" on close
        w.close()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
