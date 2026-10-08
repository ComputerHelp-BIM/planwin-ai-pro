"""Release hygiene: every place that carries the version number must agree (SemVer MAJOR.MINOR.PATCH)."""

import re
from pathlib import Path

import planwin_ai

ROOT = Path(__file__).resolve().parents[1]


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", planwin_ai.__version__)


def test_version_in_sync_everywhere():
    v = planwin_ai.__version__
    major, minor, patch = v.split(".")
    assert f'version = "{v}"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    info = (ROOT / "packaging" / "version_info.txt").read_text(encoding="utf-8")
    assert f"filevers=({major}, {minor}, {patch}, 0)" in info and f"prodvers=({major}, {minor}, {patch}, 0)" in info
    assert f"'FileVersion', '{v}'" in info and f"'ProductVersion', '{v}'" in info
    assert f'#define AppVersion "{v}"' in (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert f"## [{v}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
