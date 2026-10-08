"""FrameWin and design commands: loads wizards, sizing, seismic options, analysis, design,
code checks, calculation sheets for the selection and BOQ revisions."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from ...ai.actions import ActionResult, execute
from ...core.model import Beam, Column, Slab
from .. import dialogs

SEISMIC_METHODS = {
    "auto": "Auto (IS 1893 cl 7.7.1)",
    "static": "Equivalent static",
    "response_spectrum": "Response spectrum",
}


class FrameCommands:
    # ------------------------------------------------------------------ running actions
    def _run(self, actions: list[dict], title: str, quiet: bool = False) -> ActionResult | None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        t0 = time.time()
        try:
            res = execute(self.session, actions)
        finally:
            QApplication.restoreOverrideCursor()
        self.results.refresh()
        self.view3d.refresh()
        self.results_dock.raise_()
        if res.errors:
            self.show_result_tab("Issues")
            QMessageBox.warning(self, title, "\n".join(res.errors) + "\n\nSee the Issues tab (double-click to locate).")
        else:
            self.statusBar().showMessage(f"{title} finished in {time.time() - t0:.1f} s", 6000)
            if not quiet:
                QMessageBox.information(self, title, "\n".join(res.messages))
        return res

    def analyze_frame(self):
        res = self._run([{"action": "analyze"}], "3-D analysis")
        if res and not res.errors:
            self.tabs.setCurrentIndex(1)

    def design_all(self):
        res = self._run([{"action": "design"}], "Design")
        if res and not res.errors:
            self.show_result_tab("Columns")
            self.tabs.setCurrentIndex(1)
            self.view3d.mode.setCurrentText("Design utilisation")

    def _ensure_design(self):
        """The current design report, designing first (with a wait cursor, no pop-up) if needed."""
        if self.design_report() is None:
            res = self._run([{"action": "design"}], "Design", quiet=True)
            if res is None or res.errors:
                return None
        return self.design_report()

    def optimize(self):
        if (
            QMessageBox.question(
                self,
                "Optimise sizes",
                "Enlarge failing columns, beams and walls and re-run analysis/design "
                "up to 5 times? (Undo restores the current sizes.)",
            )
            != QMessageBox.Yes
        ):
            return
        self.push_undo()
        res = self._run([{"action": "optimize_sizes"}, {"action": "design"}], "Optimise sizes")
        if res is not None:
            self.dirty = True
            self._plan_cache.clear()
            self.refresh_all()

    # ------------------------------------------------------------------ sizes and loads
    def autosize(self):
        dlg = dialogs.AutoSizeDialog(self)
        if not dlg.exec():
            return
        from ...design.runner import autosize_columns

        b = dlg.breadth.value() or None
        self.mutate(
            "Auto-size columns",
            lambda: autosize_columns(
                self.project,
                b,
                dlg.pct.value(),
                dlg.same.isChecked(),
                dlg.step.value(),
                dlg.mf.value(),
                dlg.inc.value(),
            ),
        )
        self.column_sizes()

    def column_sizes(self):
        if not self.project.levels:
            return
        dlg = dialogs.ColumnSizesDialog(self, self.project)
        if dlg.exec():
            self.mutate("Column sizes", dlg.apply)

    def joint_loads(self):
        dlg = dialogs.JointLoadDialog(self, self.project)
        if dlg.exec():
            self.mutate("Joint loads", dlg.apply)

    def staircase(self):
        if not self.current_plan():
            return
        dlg = dialogs.StairDialog(self, self.project, self.current_plan_name)
        if dlg.exec():
            self.mutate("Staircase", dlg.apply)

    def water_tank(self):
        if not self.project.levels:
            QMessageBox.information(self, "Water tank", "Define the levels first (Project panel ▸ Levels).")
            return
        dlg = dialogs.TankDialog(self, self.project)
        if dlg.exec():
            self.mutate("Water tank", dlg.apply)

    def project_settings(self):
        dlg = dialogs.SettingsDialog(self, self.project)
        if dlg.exec():
            self.mutate("Project settings", dlg.apply)

    # ------------------------------------------------------------------ seismic options
    def set_seismic_method(self, method: str):
        if self.project.seismic.method != method:
            self.mutate(
                f"Seismic method: {SEISMIC_METHODS[method]}", lambda: setattr(self.project.seismic, "method", method)
            )

    def set_rigid_diaphragm(self, on: bool):
        if self.project.seismic.rigid_diaphragm != on:
            self.mutate(
                "Rigid diaphragm " + ("on" if on else "off"),
                lambda: setattr(self.project.seismic, "rigid_diaphragm", on),
            )

    def sync_frame_actions(self):
        """Checked state of the seismic options follows the project (undo, open, AI edits)."""
        s = self.project.seismic
        if s.method in self.method_actions:
            self.method_actions[s.method].setChecked(True)
        self.cmd["diaphragm"].setChecked(bool(s.rigid_diaphragm))

    # ------------------------------------------------------------------ checks and documents
    def show_check(self, tab: str):
        """Show a results tab that needs the design (IS 13920, irregularity, modal …)."""
        if self._ensure_design() is None:
            return
        self.results_dock.show()
        self.results_dock.raise_()
        self.show_result_tab(tab)

    def calc_for_selection(self):
        """Calculation sheets for the beams, columns (with their footings) and slabs selected on
        the plan, at every level that uses this plan."""
        plan = self.current_plan()
        objs = [o for o in (plan.find(i) for i in self.canvas.selection) if o is not None] if plan else []
        beams = {o.mark for o in objs if isinstance(o, Beam)}
        cols = {o.mark for o in objs if isinstance(o, Column)}
        slabs = [(plan.name, o.mark) for o in objs if isinstance(o, Slab)]
        if not (beams or cols or slabs):
            QMessageBox.information(
                self, "Calculation sheets", "Select beams, columns or slabs on the plan first (or use Output ▸ Calc)."
            )
            return
        rep = self._ensure_design()
        if rep is None:
            return
        levels = {lv.name for lv in self.project.levels if lv.plan == plan.name}
        ids = [b.member_id for b in rep.beams if b.mark in beams and b.level in levels]
        ids += [c.member_id for c in rep.columns if c.mark in cols and c.level in levels]
        footings = sorted({f.mark for f in rep.footings if f.mark in cols})
        if not (ids or footings or slabs):
            QMessageBox.information(
                self, "Calculation sheets", f"No designed members of plan '{plan.name}' are in the selection."
            )
            return
        self.export("calc", {"member_ids": ids, "footing_marks": footings, "slabs": slabs})

    def revisions(self):
        dlg = dialogs.RevisionsDialog(self, self.project, self.design_report())
        dlg.exec()
        if getattr(dlg, "changed", False):  # revisions live in project.meta – no re-analysis needed
            self.dirty = True
            self.refresh_all()
