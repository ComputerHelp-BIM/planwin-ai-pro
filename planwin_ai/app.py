"""GUI entry point: ``python -m planwin_ai`` or the PlanWinAIPro.exe."""

from __future__ import annotations

import logging
import os
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler


def _setup_logging() -> str:
    from .licensing.license import app_data_dir

    path = os.path.join(app_data_dir(), "planwin.log")
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    return path


def selftest() -> int:
    """Headless installation check: ``PlanWinAIPro.exe --selftest`` (used by CI)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from . import __version__
    from .ai.templates import build_template
    from .design.runner import run_full
    from .gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    prj = build_template("bungalow")
    fm, fa, rep = run_full(prj)
    w = MainWindow(prj)
    w.dirty = False
    w.close()
    eq = fa.equilibrium()
    ok = rep.failures == 0 and abs(eq["EQX"]) < 1e-3 and len(fm.members) > 0
    print(
        f"PlanWin AI Pro {__version__} selftest: members={len(fm.members)} DL={eq['DL']:.1f} kN "
        f"failures={rep.failures} -> {'OK' if ok else 'FAILED'}"
    )
    app.quit()
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--selftest" in argv:
        return selftest()
    if "--version" in argv:
        from . import __version__

        print(__version__)
        return 0
    log_path = _setup_logging()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import APP_NAME, COMPANY, __version__
    from .gui.main_window import MainWindow

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(COMPANY)
    app.setApplicationVersion(__version__)
    app.setFont(QFont("Segoe UI", 9))
    from importlib import resources

    from PySide6.QtGui import QIcon

    with resources.as_file(resources.files("planwin_ai.data").joinpath("icon.png")) as ico:
        app.setWindowIcon(QIcon(str(ico)))

    def excepthook(t, v, tb):
        logging.getLogger("planwin").error("Unhandled exception:\n%s", "".join(traceback.format_exception(t, v, tb)))
        QMessageBox.critical(
            None, APP_NAME, f"An unexpected error occurred:\n{v}\n\nDetails were written to\n{log_path}"
        )

    sys.excepthook = excepthook

    def thread_excepthook(args):  # errors in worker threads (e.g. the AI request) are logged too
        if args.exc_type is not SystemExit:
            logging.getLogger("planwin").error(
                "Unhandled exception in thread %s:\n%s",
                getattr(args.thread, "name", "?"),
                "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)),
            )

    threading.excepthook = thread_excepthook
    path = next((a for a in argv[1:] if a.lower().endswith((".pwai", ".plw"))), None)
    win = MainWindow()
    win.show()
    if path:
        if os.path.exists(path):
            win.open_path(path)
        else:  # e.g. a recent-file shortcut to a project that was moved or deleted
            QMessageBox.warning(win, APP_NAME, f"The file could not be found:\n{path}")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
