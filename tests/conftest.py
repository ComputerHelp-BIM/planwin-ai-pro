import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _isolated_appdata(monkeypatch, tmp_path):
    """Keep licence/trial/autosave files out of the real user profile."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    yield


@pytest.fixture
def tmpdir_path():
    with tempfile.TemporaryDirectory() as d:
        yield d
