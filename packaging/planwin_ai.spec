# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for PlanWin AI Pro.
#   pyinstaller packaging/planwin_ai.spec --noconfirm            -> dist/PlanWinAIPro/ (folder build, used by installer)
#   set PLANWIN_ONEFILE=1 && pyinstaller packaging/planwin_ai.spec  -> dist/PlanWinAIPro.exe (single portable file)
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ONEFILE = os.environ.get("PLANWIN_ONEFILE") == "1"

D = os.path.join(ROOT, "planwin_ai", "data")
datas = [(os.path.join(D, "*.csv"), "planwin_ai/data"), (os.path.join(D, "*.png"), "planwin_ai/data"),
         (os.path.join(D, "legacy_samples", "*.plw"), "planwin_ai/data/legacy_samples"),
         (os.path.join(D, "templates", "*.pwai"), "planwin_ai/data/templates")]
datas += collect_data_files("ezdxf")
hidden = collect_submodules("planwin_ai") + ["planwin_ai.data", "planwin_ai.data.templates", "planwin_ai.data.legacy_samples",
                                              "keyring.backends.Windows", "keyring.backends.null",
                                              "scipy.sparse.linalg", "scipy.sparse.csgraph", "PySide6.QtSvg"]
excludes = ["tkinter", "matplotlib", "IPython", "pytest", "PyQt5", "PyQt6", "PySide2", "pandas",
            "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtMultimedia",
            "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf"]

a = Analysis([os.path.join(ROOT, "packaging", "launcher.py")], pathex=[ROOT], datas=datas, hiddenimports=hidden,
             excludes=excludes, noarchive=False)
pyz = PYZ(a.pure)
icon = os.path.join(ROOT, "assets", "planwin_ai.ico")

# Windows version resource from the single version source (planwin_ai/__init__.py)
_ns = {}
with open(os.path.join(ROOT, "planwin_ai", "__init__.py"), encoding="utf-8") as _f:
    exec(compile(_f.read(), "planwin_ai/__init__.py", "exec"), _ns)
_v = _ns["__version__"]
_major, _minor, _patch = (int(x) for x in _v.split("."))
with open(os.path.join(ROOT, "packaging", "version_info.template.txt"), encoding="utf-8") as _f:
    _info = _f.read().format(version=_v, major=_major, minor=_minor, patch=_patch)
os.makedirs(os.path.join(ROOT, "build"), exist_ok=True)
version = os.path.join(ROOT, "build", "version_info.txt")
with open(version, "w", encoding="utf-8") as _f:
    _f.write(_info)

if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="PlanWinAIPro", console=False, icon=icon,
              version=version, upx=False, runtime_tmpdir=None)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PlanWinAIPro", console=False, icon=icon,
              version=version, upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="PlanWinAIPro", upx=False)
