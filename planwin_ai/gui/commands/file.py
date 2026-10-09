"""Project files: new / open / save, imports, exports, recent files, autosave and recovery."""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from ... import APP_NAME
from ...ai.actions import execute
from ...ai.templates import build_template, load_legacy_sample
from ...core.model import Level, Plan, Project
from ...io import project_io
from ...licensing.license import app_data_dir
from ...services import exports
from .. import dialogs

log = logging.getLogger("planwin")


class FileCommands:
    # ------------------------------------------------------------------ new / open / save
    def maybe_save(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(
            self,
            APP_NAME,
            "Save changes to the current project?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        )
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def _load_project(self, prj: Project, path: str | None = None):
        self.session.project = prj
        self.session.last.clear()
        self.path = path
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._plan_cache.clear()
        self.current_plan_name = prj.plans[0].name if prj.plans else ""
        self.canvas.selection = []
        self.dirty = path is None
        self.refresh_all()
        self.show_workspace()
        self.canvas.zoom_extents()

    def new_blank(self):
        if not self.maybe_save():
            return
        prj = Project(name="New project")
        prj.plans.append(Plan(name="Typical"))
        prj.levels = [Level("Plinth", "Typical", 1.5), Level("Floor 1", "Typical", 3.0)]
        self._load_project(prj)
        self.set_tool("rect_slab")

    def load_template(self, key: str):
        """Start page: open a template as a new, unsaved project."""
        if self.maybe_save():
            self._load_project(build_template(key))

    def new_from_template(self):
        if not self.maybe_save():
            return
        dlg = dialogs.TemplateDialog(self)
        if dlg.exec() and dlg.choice():
            kind, key = dlg.choice()
            prj = build_template(key) if kind == "template" else load_legacy_sample(key)
            self._load_project(prj)

    def open_dialog(self):
        if not self.maybe_save():
            return
        fn, _ = QFileDialog.getOpenFileName(
            self, "Open project", self._last_dir(), "PlanWin AI Pro (*.pwai);;Legacy PlanWin plan (*.plw)"
        )
        if fn:
            self.open_path(fn)

    def open_path(self, fn: str):
        try:
            if fn.lower().endswith(".plw"):
                self._import_plw_path(fn, new_project=True)
                return
            self._load_project(project_io.load_project(fn), fn)
            self._add_recent(fn)
        except Exception as exc:
            log.exception("open failed")
            QMessageBox.critical(self, "Open", f"Could not open {fn}:\n{exc}")

    def save(self) -> bool:
        if not self.path:
            return self.save_as()
        try:
            project_io.save_project(self.project, self.path)
        except OSError as exc:
            QMessageBox.critical(self, "Save", f"Could not save: {exc}")
            return False
        self.dirty = False
        self._discard_autosave()  # the saved file is now the newest copy
        self._add_recent(self.path)
        self.refresh_all()
        self.statusBar().showMessage(f"Saved {self.path}", 4000)
        return True

    def save_as(self) -> bool:
        fn, _ = QFileDialog.getSaveFileName(
            self,
            "Save project",
            os.path.join(self._last_dir(), exports.safe_filename(self.project.name) + ".pwai"),
            "PlanWin AI Pro (*.pwai)",
        )
        if not fn:
            return False
        self.path = fn if fn.lower().endswith(".pwai") else fn + ".pwai"
        return self.save()

    # ------------------------------------------------------------------ imports
    def import_plw(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Import PlanWin plan", self._last_dir(), "PlanWin plan (*.plw)")
        if fn:
            self._import_plw_path(fn, new_project=False)

    def _import_plw_path(self, fn: str, new_project: bool):
        from ...io.legacy_plw import read_plw

        try:
            plan, rep = read_plw(fn)
        except Exception as exc:
            QMessageBox.critical(self, "Import", f"Could not import {fn}:\n{exc}")
            return
        if new_project:
            prj = Project(name=plan.name)
            prj.plans.append(plan)
            prj.levels = [Level("Plinth", plan.name, 1.5, "M20"), Level("Floor 1", plan.name, 3.0, "M20")]
            self._load_project(prj)
        else:
            self.mutate("Import plan", lambda: self.project.add_plan(plan))
            self.set_current_plan(plan.name)
        QMessageBox.information(
            self,
            "Import",
            f"Imported PlanWin {rep.version} plan '{plan.name}': {rep.slabs} slabs, "
            f"{rep.columns} columns, {rep.beams} beams.\nLoads converted from tonnes to kN; "
            "beam UDLs kept as 'Legacy UDL' loads." + ("\n" + "\n".join(rep.notes) if rep.notes else ""),
        )

    def import_dxf(self):
        from ...io.dxf_io import import_dxf

        fn, _ = QFileDialog.getOpenFileName(self, "Import DXF", self._last_dir(), "DXF (*.dxf)")
        if not fn:
            return
        unit, ok = QInputDialog.getItem(self, "Drawing units", "Units used in the drawing:", ["m", "mm"], 0, False)
        if not ok:
            return
        try:
            plan, notes = import_dxf(fn, unit, os.path.splitext(os.path.basename(fn))[0])
        except Exception as exc:
            QMessageBox.critical(self, "Import DXF", f"Could not read {fn}:\n{exc}")
            return
        self.mutate("Import DXF", lambda: self.project.add_plan(plan))
        self.show_workspace()
        self.set_current_plan(plan.name)
        QMessageBox.information(
            self,
            "Import DXF",
            f"{len(plan.slabs)} slabs, {len(plan.columns)} columns, {len(plan.beams)} beams."
            + ("\n" + "\n".join(notes) if notes else "")
            + "\nNext: Auto beams, then Analyse plan.",
        )

    # ------------------------------------------------------------------ exports
    def export_default_name(self, key: str) -> str:
        """Default file name of an export – the same rule as the CLI and the AI assistant."""
        fmt = exports.get(key)
        name = exports.safe_filename(self.project.name)
        if fmt.per_plan:
            name += "_" + exports.safe_filename(self.current_plan_name, "plan")
        return name + fmt.suffix

    def export(self, key: str, params: dict | None = None):
        """Ask for a file name and write export ``key`` (see :mod:`planwin_ai.services.exports`).
        ``params`` are passed to the writer, e.g. the member selection of the calculation sheets."""
        if not self.license.exports_allowed:
            QMessageBox.warning(
                self, "Export", "The trial has expired – exports are disabled. Please activate a licence."
            )
            return
        fmt = exports.get(key)
        fn, _ = QFileDialog.getSaveFileName(
            self, fmt.label, os.path.join(self._last_dir(), self.export_default_name(key)), fmt.file_filter
        )
        if not fn:
            return
        act = {"action": "export", "format": fmt.key, "path": fn, **(params or {})}
        if fmt.per_plan:
            act["plan"] = self.current_plan_name
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = execute(self.session, [act])
        finally:
            QApplication.restoreOverrideCursor()
        self.results.refresh()  # the export may have run analysis / design on demand
        if res.errors:
            QMessageBox.warning(self, "Export", "\n".join(res.errors))
        else:
            self.settings.setValue("last_dir", os.path.dirname(fn))
            msg = "\n".join(res.messages)
            if self.license.watermark:
                msg += f"\n\n({self.license.watermark})"
            QMessageBox.information(self, "Export", msg)

    # ------------------------------------------------------------------ recent files
    def _last_dir(self) -> str:
        return self.settings.value("last_dir", os.path.expanduser("~"))

    def _recent(self) -> list[str]:
        rec = self.settings.value("recent", []) or []
        return [rec] if isinstance(rec, str) else [str(r) for r in rec]  # one entry may come back as a str

    def _add_recent(self, fn: str):
        rec = [r for r in self._recent() if r != fn]
        rec.insert(0, fn)
        self.settings.setValue("recent", rec[:8])
        self.settings.setValue("last_dir", os.path.dirname(fn))
        self._update_recent()

    def _update_recent(self):
        self.recent_menu.clear()
        rec = self._recent()
        for r in rec:
            self.recent_menu.addAction(r, lambda f=r: self.maybe_save() and self.open_path(f))
        self.recent_menu.setEnabled(bool(rec))

    def _open_path(self, path):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ------------------------------------------------------------------ autosave / recovery
    @staticmethod
    def _autosave_path() -> str:
        return os.path.join(app_data_dir(), "autosave.pwai")

    def _discard_autosave(self):
        try:
            os.remove(self._autosave_path())
        except OSError:
            pass

    def _do_autosave(self):
        if not self.dirty:
            return
        try:
            project_io.save_project(self.project, self._autosave_path())
        except OSError:
            log.exception("autosave failed")

    def _startup_checks(self):
        auto = self._autosave_path()
        # The autosave is deleted on every save and clean exit, so one that still exists
        # belongs to the session that crashed – never to an older, already-saved project.
        if os.path.exists(auto) and self.settings.value("clean_exit", "true") == "false":
            if (
                QMessageBox.question(
                    self, "Recover", "PlanWin AI Pro did not close normally. Recover the autosaved project?"
                )
                == QMessageBox.Yes
            ):
                try:
                    self._load_project(project_io.load_project(auto))
                except Exception as exc:
                    QMessageBox.warning(self, "Recover", f"Recovery failed: {exc}")
            else:
                self._discard_autosave()
        self.settings.setValue("clean_exit", "false")
        if self.license.mode == "expired":
            QMessageBox.warning(
                self,
                "Licence",
                "Your trial has expired. Modelling still works, but exports are disabled.\nHelp ▸ Licence to activate.",
            )

    def closeEvent(self, ev):
        if not self.maybe_save():
            ev.ignore()
            return
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("ribbon_collapsed", "true" if self.ribbon.collapsed else "false")
        self.settings.setValue("clean_exit", "true")
        self._discard_autosave()
        ev.accept()
