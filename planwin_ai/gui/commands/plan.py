"""PlanWin commands: drawing tools, selection editing, generators, grids, floors and plan analysis."""

from __future__ import annotations

import copy

from PySide6.QtWidgets import QInputDialog, QMessageBox

from ...core import geometry as G
from ...core.model import Beam, Column, Plan, Slab, Wall, new_id
from ...core.plan_engine import auto_beams, auto_columns, mark_external_beams
from .. import dialogs

_PREFIX = {Slab: "S", Column: "C", Beam: "B", Wall: "W"}


def _collection(plan: Plan, obj) -> list:
    """The plan list an object of this type lives in."""
    return {Slab: plan.slabs, Column: plan.columns, Beam: plan.beams, Wall: plan.walls}[type(obj)]


def _translate(o, dx: float, dy: float) -> None:
    if isinstance(o, Slab):
        o.points = [[p[0] + dx, p[1] + dy] for p in o.points]
    elif isinstance(o, Column):
        o.x += dx
        o.y += dy
    else:  # beams and walls are both segments x1,y1 → x2,y2
        o.x1 += dx
        o.x2 += dx
        o.y1 += dy
        o.y2 += dy


def _mirror(o, axis: str, at: float) -> None:
    if isinstance(o, Slab):
        o.points = [list(G.mirror_point(tuple(p), axis, at)) for p in o.points][::-1]  # keep CCW order
        if o.cant_edge is not None:
            o.cant_edge = (len(o.points) - 2 - o.cant_edge) % len(o.points)
    elif isinstance(o, Column):
        o.x, o.y = G.mirror_point((o.x, o.y), axis, at)
        o.angle = (-o.angle) % 180
    else:
        (o.x1, o.y1), (o.x2, o.y2) = G.mirror_point(o.p1, axis, at), G.mirror_point(o.p2, axis, at)


class PlanCommands:
    # ------------------------------------------------------------------ tools
    def set_tool(self, tool: str):
        if tool in self.tool_actions:
            self.tool_actions[tool].setChecked(True)
        self.canvas.set_tool(tool)
        self.show_workspace()
        self.tabs.setCurrentIndex(0)

    def set_canvas_flag(self, attr: str, on: bool):
        """Display / snap toggles of the plan canvas (show_grids, snap_mid, ortho …)."""
        if attr == "ortho":
            if self.canvas.ortho != on:  # set_ortho announces the change in the status bar
                self.canvas.set_ortho(on)
        else:
            setattr(self.canvas, attr, on)
        self.settings.setValue(f"canvas/{attr}", "true" if on else "false")
        self.canvas.update()

    # ------------------------------------------------------------------ selection editing
    def _selected(self) -> tuple[Plan | None, list[str]]:
        return self.current_plan(), list(self.canvas.selection)

    def delete_selection(self):
        plan, ids = self._selected()
        if not plan or not ids:
            return
        self.canvas.selection = []
        self.mutate(f"Delete {len(ids)} object(s)", lambda: plan.remove(ids))

    def move_copy_dialog(self):
        plan, ids = self._selected()
        if not plan or not ids:
            return
        dlg = dialogs.MoveCopyDialog(self)
        if not dlg.exec():
            return
        dx, dy, copy_, n = dlg.dx.value(), dlg.dy.value(), dlg.copy.isChecked(), dlg.n.value()

        def fn():
            objs = [o for o in (plan.find(i) for i in ids) if o is not None]
            new_sel = []
            for k in range(1, (n if copy_ else 1) + 1):
                for o in objs:
                    tgt = copy.deepcopy(o) if copy_ else o
                    _translate(tgt, dx * k, dy * k)
                    if copy_:
                        tgt.id = new_id()
                        tgt.mark = plan.next_mark(_PREFIX[type(tgt)])
                        _collection(plan, tgt).append(tgt)
                    new_sel.append(tgt.id)
            self.canvas.selection = new_sel

        self.mutate("Move/copy", fn)

    def mirror_dialog(self):
        plan, ids = self._selected()
        if not plan or not ids:
            return
        dlg = dialogs.MirrorDialog(self)
        if not dlg.exec():
            return
        axis = "x" if dlg.vertical.isChecked() else "y"
        at, keep = dlg.at.value(), dlg.copy.isChecked()

        def fn():
            for o in [o for o in (plan.find(i) for i in ids) if o is not None]:
                tgt = copy.deepcopy(o) if keep else o
                _mirror(tgt, axis, at)
                if keep:
                    if isinstance(tgt, Column) and plan.column_at(tgt.pos, 0.02):
                        continue  # a column on the mirror line already exists
                    tgt.id = new_id()
                    tgt.mark = plan.next_mark(_PREFIX[type(tgt)])
                    _collection(plan, tgt).append(tgt)

        self.mutate("Mirror", fn)

    def copy_selection_to_new_plan(self):
        plan, ids = self._selected()
        if not plan or not ids:
            return
        name, ok = QInputDialog.getText(self, "Copy selection to new plan", "New plan name:", text=f"{plan.name} part")
        if not ok or not name.strip():
            return
        keep = set(ids)

        def fn():
            p = Plan(name=name.strip(), floor_type=plan.floor_type, floor_height_above=plan.floor_height_above)
            p.slabs = [copy.deepcopy(o) for o in plan.slabs if o.id in keep]
            p.columns = [copy.deepcopy(o) for o in plan.columns if o.id in keep]
            p.beams = [copy.deepcopy(o) for o in plan.beams if o.id in keep]
            p.walls = [copy.deepcopy(o) for o in plan.walls if o.id in keep]
            for o in p.slabs + p.columns + p.beams + p.walls:
                o.id = new_id()
            self.project.add_plan(p)

        self.mutate("Copy to new plan", fn)
        self.set_current_plan(self.project.plans[-1].name)

    def renumber(self, kind: str):
        plan = self.current_plan()
        if not plan:
            return
        if kind == "column" and len(self.project.plans) > 1:
            if (
                QMessageBox.question(
                    self,
                    "Renumber columns",
                    "Column marks link levels in FrameWin and must stay the same on every floor. Renumber anyway?",
                )
                != QMessageBox.Yes
            ):
                return
        self.mutate(f"Renumber {kind}s", lambda: plan.renumber(kind))

    # ------------------------------------------------------------------ generators
    def auto_columns(self):
        plan = self.current_plan()
        if not plan:
            return
        d = self.defaults
        self.mutate("Auto columns", lambda: auto_columns(plan, d["col_b"], d["col_d"], d["grade"]))
        self.statusBar().showMessage("Columns placed at slab corners – delete the ones you don't need (Del)", 8000)

    def auto_beams(self):
        plan = self.current_plan()
        if plan:
            self.mutate("Auto beams", lambda: auto_beams(plan))

    def mark_external(self):
        plan = self.current_plan()
        if plan:
            self.mutate("External beams", lambda: mark_external_beams(plan))

    # ------------------------------------------------------------------ grids and floors
    def grids_dialog(self):
        dlg = dialogs.GridsDialog(self, self.project, self.current_plan_name)
        if dlg.exec():
            self.mutate("Grid lines", dlg.apply, analysis=False)  # grids never change the analysis

    def copy_floors(self):
        if not self.current_plan():
            return
        dlg = dialogs.CopyFloorsDialog(self, self.project, self.current_plan_name)
        if dlg.exec():
            self.mutate("Copy floor", dlg.apply)
            if getattr(dlg, "new_plan", None) and self.project.plan(dlg.new_plan):
                self.set_current_plan(dlg.new_plan)

    def show_levels(self):
        self.project_dock.show()
        self.project_dock.raise_()

    # ------------------------------------------------------------------ plan analysis
    def analyze_plan(self):
        plan = self.current_plan()
        if not plan:
            return
        self._plan_cache.pop(plan.name, None)
        res = self.plan_result(plan.name)
        self.results.refresh()
        self.results_dock.raise_()
        self.show_result_tab("Issues" if res and res.issues else "Column loads")
        if res:
            msg = (
                f"Applied DL {res.applied['D']:.1f} kN + LL {res.applied['L']:.1f} kN\n"
                f"Column reactions DL {res.reacted['D']:.1f} kN + LL {res.reacted['L']:.1f} kN\n"
                f"Difference {res.imbalance_pct:.3f} %  (should be ~0)\n\n"
                f"{len(res.errors)} error(s), {len(res.warnings)} warning(s)."
            )
            (QMessageBox.warning if res.errors else QMessageBox.information)(self, "Analysis summary", msg)

    def show_beam_diagram(self, beam: Beam):
        res = self.plan_result(self.current_plan_name)
        if not res or beam.id not in res.beams or not res.beams[beam.id].supports:
            QMessageBox.information(self, "Beam", "This beam has no supports yet – check the Issues tab.")
            return
        dialogs.BeamDiagramDialog(self, beam, res.beams[beam.id]).exec()

    def show_column_breakup(self, col: Column):
        res = self.plan_result(self.current_plan_name)
        cl = res.columns.get(col.id) if res else None
        if not cl:
            return
        lines = (
            "\n".join(f"  from {m}: D {d:.2f} kN, L {l:.2f} kN" for m, d, l in cl.parts) or "  (no beams frame into it)"
        )
        QMessageBox.information(
            self,
            f"Column {col.mark} – load analysis",
            f"Load from this level\nDead {cl.dead:.2f} kN · Live {cl.live:.2f} kN · Total {cl.total:.2f} kN\n\n{lines}",
        )
