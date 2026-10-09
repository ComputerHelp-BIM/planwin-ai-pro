"""Regenerate the README screenshots (assets/screenshots/*.png) from the real main window.

    QT_QPA_PLATFORM=offscreen python tools/screenshots.py

Uses a throw-away settings file, so the user's recent files and preferences are untouched.
"""

from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PLANWIN_SETTINGS"] = os.path.join(tempfile.mkdtemp(prefix="pwai-shots-"), "settings.ini")
OUT = os.path.join(ROOT, "assets", "screenshots")
SIZE = (1600, 950)


def _settle(app, n: int = 4) -> None:
    for _ in range(n):  # wrapped labels and docks need a few layout passes
        app.processEvents()


def main() -> int:
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    for name in ("information", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: 0))
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
    from planwin_ai.gui.main_window import MainWindow

    os.makedirs(OUT, exist_ok=True)
    w = MainWindow()
    w.apply_theme("light")
    w.resize(*SIZE)
    w.show()
    w.show_start()
    _settle(app)
    w.grab().save(os.path.join(OUT, "start_page.png"))

    w.load_template("residential_g4")
    w._run([{"action": "design"}], "Design", quiet=True)
    w.show_plan_view()
    w.chat.show()
    w.chat.raise_()
    w.show_result_tab("Columns")
    w.ribbon.select("Plan")
    _settle(app)
    w.canvas.zoom_extents()
    _settle(app)
    w.grab().save(os.path.join(OUT, "plan_and_ai.png"))

    w.show_3d_view()
    w.view3d.mode.setCurrentText("Design utilisation")
    w.ribbon.select("Design")
    w.show_result_tab("IS 13920")
    _settle(app)
    w.grab().save(os.path.join(OUT, "frame_3d_design.png"))

    w.apply_theme("dark")
    w.show_plan_view()
    w.props_dock.raise_()
    p = w.current_plan()
    w.canvas.select_ids([p.beams[0].id])
    w.ribbon.select("Home")
    _settle(app)
    w.grab().save(os.path.join(OUT, "dark_theme.png"))
    w.dirty = False
    w.close()
    print("screenshots written to", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
