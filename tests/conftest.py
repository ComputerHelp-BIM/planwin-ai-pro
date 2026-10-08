import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


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
