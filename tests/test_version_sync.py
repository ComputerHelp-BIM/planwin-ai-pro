"""Release hygiene: the version has one source (planwin_ai/__init__.py) and SemVer form."""

import re
from pathlib import Path

import planwin_ai

ROOT = Path(__file__).resolve().parents[1]


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", planwin_ai.__version__)


def test_pyproject_reads_the_package_version():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in text
    assert 'version = { attr = "planwin_ai.__version__" }' in text
    assert not re.search(r'^version\s*=\s*"', text, re.M), "hard-coded version in pyproject.toml"


def test_exe_version_resource_template_renders():
    """The PyInstaller spec fills the template; evaluate it with stand-ins for the
    Windows-only PyInstaller classes to catch syntax or placeholder mistakes."""
    v = planwin_ai.__version__
    major, minor, patch = (int(x) for x in v.split("."))
    text = (ROOT / "packaging" / "version_info.template.txt").read_text(encoding="utf-8")
    rendered = text.format(version=v, major=major, minor=minor, patch=patch)

    def rec(name):
        return lambda *a, **k: (name, a, k)

    names = (
        "VSVersionInfo",
        "FixedFileInfo",
        "StringFileInfo",
        "StringTable",
        "StringStruct",
        "VarFileInfo",
        "VarStruct",
    )
    info = eval(rendered, {n: rec(n) for n in names})  # noqa: S307 – trusted repository file
    assert info[2]["ffi"][2]["filevers"] == (major, minor, patch, 0)
    assert f"'ProductVersion', '{v}'" in rendered and f"'FileVersion', '{v}'" in rendered


def test_installer_takes_the_version_from_the_build():
    iss = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert not re.search(r'#define AppVersion "\d', iss), "hard-coded version in installer.iss"
    assert "/DAppVersion" in (ROOT / "scripts" / "build_windows.bat").read_text(encoding="utf-8")
    assert "/DAppVersion" in (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")


def test_changelog_has_an_entry_for_this_version():
    assert f"## [{planwin_ai.__version__}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
