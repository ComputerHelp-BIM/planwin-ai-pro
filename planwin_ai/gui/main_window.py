"""PlanWin AI Pro main window."""

from __future__ import annotations

import copy
import json
import logging
import os
import time
from typing import Callable, Optional

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QInputDialog, QLabel, QMainWindow, QMessageBox,
                               QTabWidget, QToolBar)

from .. import APP_NAME, COMPANY, __version__
from ..ai.actions import ActionResult, Session, execute
from ..ai.assistant import Assistant
from ..ai.providers import ProviderConfig
from ..ai.templates import build_template, load_legacy_sample
from ..core import geometry as G
from ..core.model import Beam, Column, Level, Plan, Project, Slab, new_id
from ..core.plan_engine import PlanEngine, PlanResult, auto_beams, auto_columns, mark_external_beams
from ..io import project_io
from ..licensing.license import app_data_dir, current_state, install_license
from . import dialogs
from .canvas import PlanCanvas
from .chat import ChatDock
from .panels import ProjectPanel, PropertiesPanel, ResultsPanel
from .theme import icon, qss
from .view3d import Frame3DView

log = logging.getLogger("planwin")
UNDO_LIMIT = 60


class MainWindow(QMainWindow):
    def __init__(self, project: Optional[Project] = None, path: Optional[str] = None):
        super().__init__()
        self.settings = QSettings(COMPANY, "PlanWinAIPro")
        self.theme_name = self.settings.value("theme", "light")
        self.license = current_state()
        self.session = Session(project or build_template("residential_g4"), out_dir=os.path.expanduser("~"),
                               watermark=self.license.watermark, exports_allowed=self.license.exports_allowed)
        cfg = ProviderConfig(self.settings.value("ai/provider", "offline"), self.settings.value("ai/model", ""),
                             self.settings.value("ai/base_url", ""))
        self.assistant = Assistant(self.session, cfg)
        self.path: Optional[str] = path
        self.dirty = False
        self.undo_stack: list[str] = []
        self.redo_stack: list[str] = []
        self._plan_cache: dict[str, PlanResult] = {}
        self.current_plan_name = self.project.plans[0].name if self.project.plans else ""
        self.defaults = {"slab_thickness": 0.125, "slab_live": 2.0, "slab_ff": 1.0, "slab_other": 0.5, "col_b": 0.3,
                         "col_d": 0.45, "beam_b": 0.23, "beam_d": 0.45, "wall_thk": 0.23, "grade": "M25"}

        self.setWindowTitle(APP_NAME)
        self.resize(1500, 920)
        self.canvas = PlanCanvas(self)
        self.view3d = Frame3DView(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.canvas, "Plan (PlanWin)")
        self.tabs.addTab(self.view3d, "3-D Frame (FrameWin)")
        self.tabs.currentChanged.connect(lambda i: self.view3d.refresh() if i == 1 else None)
        self.setCentralWidget(self.tabs)

        self.project_panel = ProjectPanel(self)
        self.props = PropertiesPanel(self)
        self.results = ResultsPanel(self)
        self.chat = ChatDock(self)
        self._dock("Project", self.project_panel, Qt.LeftDockWidgetArea)
        self.props_dock = self._dock("Properties", self.props, Qt.RightDockWidgetArea)
        self.addDockWidget(Qt.RightDockWidgetArea, self.chat)
        self.tabifyDockWidget(self.props_dock, self.chat)
        self.results_dock = self._dock("Results and checks", self.results, Qt.BottomDockWidgetArea)

        self.canvas.selectionChanged.connect(self.props.show_selection)
        self.canvas.status.connect(lambda s: self.statusBar().showMessage(s, 8000))
        self.canvas.cursorMoved.connect(lambda x, y: self.coord_lbl.setText(f"X {x:8.3f}  Y {y:8.3f} m"))
        self.results.issueActivated.connect(self._goto_issue)

        self._build_actions()
        self.coord_lbl = QLabel()
        self.lic_lbl = QLabel(self.license.label())
        self.statusBar().addPermanentWidget(self.coord_lbl)
        self.statusBar().addPermanentWidget(self.lic_lbl)
        self.apply_theme(self.theme_name)
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        self.refresh_all()
        self._autosave = QTimer(self)
        self._autosave.timeout.connect(self._do_autosave)
        self._autosave.start(5 * 60 * 1000)
        QTimer.singleShot(300, self._startup_checks)

    # ================================================================ properties
    @property
    def project(self) -> Project:
        return self.session.project

    def current_plan(self) -> Optional[Plan]:
        return self.project.plan(self.current_plan_name) if self.current_plan_name else None

    def plan_result(self, name: str) -> Optional[PlanResult]:
        if name not in self._plan_cache:
            plan = self.project.plan(name)
            if plan is None:
                return None
            d = self.project.design
            try:
                self._plan_cache[name] = PlanEngine(plan, None, d.two_way_ratio_limit, d.continuity_in_load_transfer).run()
            except Exception as exc:  # never break painting
                log.exception("plan analysis failed")
                self.statusBar().showMessage(f"Plan analysis failed: {exc}", 8000)
                return None
        return self._plan_cache[name]

    def frame_model(self):
        return self.session.last.get("fm")

    def frame_analysis(self):
        return self.session.last.get("fa")

    def design_report(self):
        return self.session.last.get("rep")

    # ================================================================ UI build
    def _dock(self, title, widget, area) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setObjectName(title.replace(" ", "_"))
        d.setWidget(widget)
        self.addDockWidget(area, d)
        return d

    def _act(self, text, fn, shortcut=None, ic=None, tip=None, checkable=False) -> QAction:
        a = QAction(text, self)
        if ic:
            a.setIcon(icon(ic))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setStatusTip(tip)
            a.setToolTip(f"{text}\n{tip}")
        a.setCheckable(checkable)
        a.triggered.connect(fn)
        return a

    def _build_actions(self):
        mb = self.menuBar()
        # ---- file
        m = mb.addMenu("&File")
        m.addAction(self._act("New blank project", self.new_blank, "Ctrl+N", "new"))
        m.addAction(self._act("New from template…", self.new_from_template, "Ctrl+Shift+N", "template"))
        m.addAction(self._act("Open…", self.open_dialog, "Ctrl+O", "open"))
        self.recent_menu = m.addMenu("Open recent")
        m.addAction(self._act("Save", self.save, "Ctrl+S", "save"))
        m.addAction(self._act("Save as…", self.save_as, "Ctrl+Shift+S"))
        m.addSeparator()
        m.addAction(self._act("Import legacy PlanWin plan (.plw)…", self.import_plw))
        m.addAction(self._act("Import DXF (SLAB / COLUMN / BEAM layers)…", self.import_dxf))
        ex = m.addMenu(icon("export"), "Export")
        for label, fmt, flt in (("STAAD.Pro file (.std)…", "staad", "STAAD (*.std)"), ("ETABS file (.e2k)…", "etabs", "ETABS (*.e2k)"),
                                ("DXF – current plan…", "dxf", "DXF (*.dxf)"), ("DXF – 3-D frame…", "dxf3d", "DXF (*.dxf)"),
                                ("Excel workbook…", "excel", "Excel (*.xlsx)"), ("PDF report…", "pdf", "PDF (*.pdf)")):
            ex.addAction(self._act(label, lambda _=False, f=fmt, fl=flt: self.export(f, fl)))
        m.addSeparator()
        m.addAction(self._act("Exit", self.close, "Alt+F4"))
        # ---- edit
        m = mb.addMenu("&Edit")
        self.undo_act = self._act("Undo", self.undo, "Ctrl+Z", "undo")
        self.redo_act = self._act("Redo", self.redo, "Ctrl+Y", "redo")
        m.addAction(self.undo_act)
        m.addAction(self.redo_act)
        m.addSeparator()
        m.addAction(self._act("Delete selection", self.delete_selection))
        m.addAction(self._act("Move / copy selection…", self.move_copy_dialog, "Ctrl+M"))
        m.addAction(self._act("Mirror selection…", self.mirror_dialog))
        m.addAction(self._act("Copy selection to new plan", self.copy_selection_to_new_plan))
        rn = m.addMenu("Auto renumber (left→right, top→bottom)")
        for kind in ("slab", "beam", "column"):
            rn.addAction(self._act(f"{kind.title()}s", lambda _=False, k=kind: self.renumber(k)))
        # ---- planwin
        m = mb.addMenu("&PlanWin")
        self.tool_group = QActionGroup(self)
        tb = QToolBar("Tools")
        tb.setObjectName("tools")
        tb.setMovable(False)
        self.addToolBar(tb)
        for a in (self._act("New", self.new_from_template, ic="template", tip="New from template"),
                  self._act("Open", self.open_dialog, ic="open"), self._act("Save", self.save, ic="save"),
                  self.undo_act, self.redo_act):
            tb.addAction(a)
        tb.addSeparator()
        self.tool_actions = {}
        for tool, label, ic, key in (("select", "Select", "select", "S"), ("pan", "Pan", "pan", "H"),
                                     ("rect_slab", "Rectangular slab", "rect", "R"), ("poly_slab", "Irregular slab", "poly", "P"),
                                     ("column", "Column", "column", "C"), ("beam", "Beam", "beam", "B"),
                                     ("measure", "Measure", "measure", "D")):
            a = self._act(label, lambda _=False, t=tool: self.set_tool(t), key, ic, checkable=True)
            self.tool_group.addAction(a)
            self.tool_actions[tool] = a
            tb.addAction(a)
            m.addAction(a)
        self.tool_actions["select"].setChecked(True)
        m.addSeparator()
        acol = self._act("Auto columns (Judge)", self.auto_columns, ic="autocol", tip="Place columns at all slab corners")
        abeam = self._act("Auto beams", self.auto_beams, ic="autobeam", tip="Create beams along all slab edges")
        aplan = self._act("Analyse plan (load take-down)", self.analyze_plan, "F5", "analyze",
                          tip="Slab → beam → column load transfer with equilibrium check")
        for a in (acol, abeam, aplan):
            m.addAction(a)
            tb.addAction(a)
        m.addAction(self._act("Mark external beams", lambda: self.mutate("External beams", lambda: mark_external_beams(self.current_plan()))))
        m.addAction(self._act("Zoom extents", self.canvas.zoom_extents, "F", "zoomfit"))
        tb.addSeparator()
        # ---- framewin
        m = mb.addMenu("Frame&Win")
        afr = self._act("Build & analyse 3-D frame", self.analyze_frame, "F6", "frame", tip="Stack levels, apply IS loads and solve")
        ades = self._act("Design all (IS 456)", self.design_all, "F7", "design", tip="Columns, beams, footings, slabs, BOQ")
        m.addAction(afr)
        m.addAction(ades)
        m.addSeparator()
        m.addAction(self._act("Auto-size columns…", self.autosize))
        m.addAction(self._act("Optimise sizes until design passes", self.optimize, tip="Enlarge failing columns/beams and re-run (max 5 iterations)"))
        m.addAction(self._act("Column sizes by level…", self.column_sizes))
        m.addAction(self._act("Joint loads (water tank)…", self.joint_loads))
        m.addAction(self._act("Project settings (seismic, wind, design)…", self.project_settings, "Ctrl+,", "settings"))
        tb.addAction(afr)
        tb.addAction(ades)
        tb.addSeparator()
        exp_btn = self._act("Export STAAD", lambda: self.export("staad", "STAAD (*.std)"), ic="export", tip="Write STAAD.Pro .std")
        tb.addAction(exp_btn)
        tb.addAction(self._act("Export ETABS", lambda: self.export("etabs", "ETABS (*.e2k)"), ic="export", tip="Write ETABS .e2k"))
        tb.addSeparator()
        self.ai_toggle = self._act("AI Assistant", self.toggle_chat, "Ctrl+K", None, tip="Open / close the AI side panel", checkable=True)
        self.ai_toggle.setIcon(icon("ai", "#FFFFFF"))
        tb.addAction(self.ai_toggle)
        ai_btn = tb.widgetForAction(self.ai_toggle)
        ai_btn.setObjectName("aiButton")
        ai_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        # ---- view
        m = mb.addMenu("&View")
        a = self._act("Show loads on plan", self._toggle_loads, checkable=True)
        a.setChecked(True)
        m.addAction(a)
        a = self._act("Show marks", self._toggle_marks, checkable=True)
        a.setChecked(True)
        m.addAction(a)
        m.addAction(self._act("Dark theme", lambda: self.apply_theme("dark" if self.theme_name == "light" else "light"), "Ctrl+T"))
        m.addSeparator()
        for d in (self.findChild(QDockWidget, "Project"), self.props_dock, self.chat, self.results_dock):
            if d:
                m.addAction(d.toggleViewAction())
        m.addAction(self.ai_toggle)
        snap = m.addMenu("Snap step")
        g = QActionGroup(self)
        for st in (0.01, 0.05, 0.1, 0.25, 0.5):
            a = self._act(f"{st:g} m", lambda _=False, s=st: setattr(self.canvas, "snap_step", s), checkable=True)
            a.setChecked(st == 0.05)
            g.addAction(a)
            snap.addAction(a)
        # ---- tools / help
        m = mb.addMenu("&Tools")
        m.addAction(self._act("AI assistant settings…", self.ai_settings))
        m.addAction(self._act("Licence…", self.license_dialog))
        m.addAction(self._act("Open log folder", lambda: self._open_path(app_data_dir())))
        m = mb.addMenu("&Help")
        m.addAction(self._act("Quick start", self.quick_start, "F1"))
        m.addAction(self._act("About", self.about))
        self.chat.visibilityChanged.connect(self.ai_toggle.setChecked)
        self._update_recent()

    # ================================================================ state changes
    def snapshot(self) -> str:
        return project_io.project_to_json(self.project)

    def push_undo(self):
        self.undo_stack.append(self.snapshot())
        if len(self.undo_stack) > UNDO_LIMIT:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def mutate(self, desc: str, fn: Callable[[], object]):
        """Run a model change with undo support and refresh everything."""
        self.push_undo()
        try:
            fn()
        except Exception as exc:
            log.exception("edit failed")
            self.project_from_json(self.undo_stack.pop())
            QMessageBox.warning(self, desc, f"Could not complete '{desc}': {exc}")
            return
        self.invalidate()
        self.statusBar().showMessage(desc, 3000)

    def invalidate(self):
        self.dirty = True
        self._plan_cache.clear()
        self.session.last.clear()
        self.refresh_all()

    def project_from_json(self, text: str):
        self.session.project = project_io.project_from_json(text)

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(self.snapshot())
        self.project_from_json(self.undo_stack.pop())
        self._after_replace()

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(self.snapshot())
        self.project_from_json(self.redo_stack.pop())
        self._after_replace()

    def _after_replace(self):
        if not self.project.plan(self.current_plan_name):
            self.current_plan_name = self.project.plans[0].name if self.project.plans else ""
        self.canvas.selection = []
        self.invalidate()

    def refresh_all(self):
        self.project_panel.refresh()
        self.props.show_selection([i for i in self.canvas.selection if self.current_plan() and self.current_plan().find(i)])
        self.results.refresh()
        if self.tabs.currentIndex() == 1:
            self.view3d.refresh()
        self.canvas.update()
        self.undo_act.setEnabled(bool(self.undo_stack))
        self.redo_act.setEnabled(bool(self.redo_stack))
        name = os.path.basename(self.path) if self.path else self.project.name
        self.setWindowTitle(f"{name}{' •' if self.dirty else ''} – {APP_NAME} {__version__}")

    def set_current_plan(self, name: str):
        if name == self.current_plan_name:
            return
        self.current_plan_name = name
        self.canvas.selection = []
        self.refresh_all()
        self.canvas.zoom_extents()

    def set_tool(self, tool: str):
        if tool in self.tool_actions:
            self.tool_actions[tool].setChecked(True)
        self.canvas.set_tool(tool)
        self.tabs.setCurrentIndex(0)

    def focus_properties(self):
        self.props_dock.show()
        self.props_dock.raise_()

    # ================================================================ AI hooks
    def toggle_chat(self):
        if self.chat.isVisible() and not self.chat.visibleRegion().isEmpty():
            self.chat.hide()
        else:
            self.chat.show()
            self.chat.raise_()
            self.chat.input.setFocus()

    def before_ai_change(self):
        self.push_undo()

    def run_ai_actions(self, reply: str, actions: list):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            return self.assistant.apply(reply, actions)
        finally:
            QApplication.restoreOverrideCursor()

    def after_ai_change(self, res: Optional[ActionResult]):
        if res is None:
            return
        if res.changed:
            self.dirty = True
            self._plan_cache.clear()
            if not self.project.plan(self.current_plan_name):
                self.current_plan_name = self.project.plans[0].name if self.project.plans else ""
            self.canvas.selection = []
            self.refresh_all()
            self.canvas.zoom_extents()
        elif self.undo_stack and not res.analysed:
            self.undo_stack.pop()  # nothing changed – drop the snapshot
        if res.analysed:
            self.results.refresh()
            self.view3d.refresh()
            self.results_dock.raise_()

    # ================================================================ files
    def maybe_save(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, APP_NAME, "Save changes to the current project?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def _load_project(self, prj: Project, path: Optional[str] = None):
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
        self.canvas.zoom_extents()

    def new_blank(self):
        if not self.maybe_save():
            return
        prj = Project(name="New project")
        prj.plans.append(Plan(name="Typical"))
        prj.levels = [Level("Plinth", "Typical", 1.5), Level("Floor 1", "Typical", 3.0)]
        self._load_project(prj)
        self.set_tool("rect_slab")

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
        fn, _ = QFileDialog.getOpenFileName(self, "Open project", self._last_dir(),
                                            "PlanWin AI Pro (*.pwai);;Legacy PlanWin plan (*.plw)")
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
        self._add_recent(self.path)
        self.refresh_all()
        self.statusBar().showMessage(f"Saved {self.path}", 4000)
        return True

    def save_as(self) -> bool:
        fn, _ = QFileDialog.getSaveFileName(self, "Save project", os.path.join(self._last_dir(), self.project.name + ".pwai"),
                                            "PlanWin AI Pro (*.pwai)")
        if not fn:
            return False
        self.path = fn if fn.lower().endswith(".pwai") else fn + ".pwai"
        return self.save()

    def import_plw(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Import PlanWin plan", self._last_dir(), "PlanWin plan (*.plw)")
        if fn:
            self._import_plw_path(fn, new_project=False)

    def _import_plw_path(self, fn: str, new_project: bool):
        from ..io.legacy_plw import read_plw

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
        QMessageBox.information(self, "Import", f"Imported PlanWin {rep.version} plan '{plan.name}': {rep.slabs} slabs, "
                                                f"{rep.columns} columns, {rep.beams} beams.\nLoads converted from tonnes to kN; "
                                                "beam UDLs kept as 'Legacy UDL' loads." + ("\n" + "\n".join(rep.notes) if rep.notes else ""))

    def import_dxf(self):
        from ..io.dxf_io import import_dxf

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
        self.set_current_plan(plan.name)
        QMessageBox.information(self, "Import DXF", f"{len(plan.slabs)} slabs, {len(plan.columns)} columns, {len(plan.beams)} beams."
                                                    + ("\n" + "\n".join(notes) if notes else "") +
                                                    "\nNext: Auto beams, then Analyse plan.")

    def export(self, fmt: str, flt: str):
        if not self.license.exports_allowed:
            QMessageBox.warning(self, "Export", "The trial has expired – exports are disabled. Please activate a licence.")
            return
        ext = {"staad": ".std", "etabs": ".e2k", "dxf": "_2DPLAN.dxf", "dxf3d": "_3d.dxf", "excel": ".xlsx", "pdf": ".pdf"}[fmt]
        base = (self.current_plan_name if fmt == "dxf" else self.project.name).replace(" ", "_")
        fn, _ = QFileDialog.getSaveFileName(self, "Export", os.path.join(self._last_dir(), base + ext), flt)
        if not fn:
            return
        act = {"action": "export", "format": fmt, "path": fn}
        if fmt == "dxf":
            act["plan"] = self.current_plan_name
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = execute(self.session, [act])
        finally:
            QApplication.restoreOverrideCursor()
        self.results.refresh()
        if res.errors:
            QMessageBox.warning(self, "Export", "\n".join(res.errors))
        else:
            self.settings.setValue("last_dir", os.path.dirname(fn))
            msg = "\n".join(res.messages)
            if self.license.watermark:
                msg += f"\n\n({self.license.watermark})"
            QMessageBox.information(self, "Export", msg)

    # ================================================================ PlanWin commands
    def delete_selection(self):
        plan = self.current_plan()
        ids = list(self.canvas.selection)
        if not plan or not ids:
            return
        self.canvas.selection = []
        self.mutate(f"Delete {len(ids)} object(s)", lambda: plan.remove(ids))

    def auto_columns(self):
        plan = self.current_plan()
        if not plan:
            return
        d = self.defaults
        self.mutate("Auto columns", lambda: auto_columns(plan, d["col_b"], d["col_d"], d["grade"]))
        self.statusBar().showMessage("Columns placed at slab corners – delete the ones you don't need (Del)", 8000)

    def auto_beams(self):
        plan = self.current_plan()
        if not plan:
            return
        self.mutate("Auto beams", lambda: auto_beams(plan))

    def renumber(self, kind: str):
        plan = self.current_plan()
        if not plan:
            return
        if kind == "column" and len(self.project.plans) > 1:
            if QMessageBox.question(self, "Renumber columns", "Column marks link levels in FrameWin and must stay the same "
                                    "on every floor. Renumber anyway?") != QMessageBox.Yes:
                return
        self.mutate(f"Renumber {kind}s", lambda: plan.renumber(kind))

    def analyze_plan(self):
        plan = self.current_plan()
        if not plan:
            return
        self._plan_cache.pop(plan.name, None)
        res = self.plan_result(plan.name)
        self.results.refresh()
        self.results_dock.raise_()
        self.results.setCurrentIndex(0 if res and res.issues else 1)
        if res:
            msg = (f"Applied DL {res.applied['D']:.1f} kN + LL {res.applied['L']:.1f} kN\n"
                   f"Column reactions DL {res.reacted['D']:.1f} kN + LL {res.reacted['L']:.1f} kN\n"
                   f"Difference {res.imbalance_pct:.3f} %  (should be ~0)\n\n"
                   f"{len(res.errors)} error(s), {len(res.warnings)} warning(s).")
            (QMessageBox.warning if res.errors else QMessageBox.information)(self, "Analysis summary", msg)

    def move_copy_dialog(self):
        plan = self.current_plan()
        ids = list(self.canvas.selection)
        if not plan or not ids:
            return
        dlg = dialogs.MoveCopyDialog(self)
        if not dlg.exec():
            return
        dx, dy, copy_, n = dlg.dx.value(), dlg.dy.value(), dlg.copy.isChecked(), dlg.n.value()

        def fn():
            objs = [plan.find(i) for i in ids]
            new_sel = []
            for k in range(1, (n if copy_ else 1) + 1):
                for o in objs:
                    tgt = copy.deepcopy(o) if copy_ else o
                    _translate(tgt, dx * k, dy * k)
                    if copy_:
                        tgt.id = new_id()
                        prefix = "S" if isinstance(tgt, Slab) else "C" if isinstance(tgt, Column) else "B"
                        tgt.mark = plan.next_mark(prefix)
                        (plan.slabs if isinstance(tgt, Slab) else plan.columns if isinstance(tgt, Column) else plan.beams).append(tgt)
                    new_sel.append(tgt.id)
            self.canvas.selection = new_sel
        self.mutate("Move/copy", fn)

    def mirror_dialog(self):
        plan = self.current_plan()
        ids = list(self.canvas.selection)
        if not plan or not ids:
            return
        dlg = dialogs.MirrorDialog(self)
        if not dlg.exec():
            return
        axis = "x" if dlg.vertical.isChecked() else "y"
        at, keep = dlg.at.value(), dlg.copy.isChecked()

        def fn():
            for o in [plan.find(i) for i in ids]:
                tgt = copy.deepcopy(o) if keep else o
                if isinstance(tgt, Slab):
                    tgt.points = [list(G.mirror_point(tuple(p), axis, at)) for p in tgt.points][::-1]
                    if tgt.cant_edge is not None:
                        tgt.cant_edge = (len(tgt.points) - 2 - tgt.cant_edge) % len(tgt.points)
                elif isinstance(tgt, Column):
                    tgt.x, tgt.y = G.mirror_point((tgt.x, tgt.y), axis, at)
                    tgt.angle = (-tgt.angle) % 180
                else:
                    (tgt.x1, tgt.y1), (tgt.x2, tgt.y2) = G.mirror_point(tgt.p1, axis, at), G.mirror_point(tgt.p2, axis, at)
                if keep:
                    tgt.id = new_id()
                    if isinstance(tgt, Slab):
                        tgt.mark = plan.next_mark("S"); plan.slabs.append(tgt)
                    elif isinstance(tgt, Column):
                        if plan.column_at(tgt.pos, 0.02):
                            continue
                        tgt.mark = plan.next_mark("C"); plan.columns.append(tgt)
                    else:
                        tgt.mark = plan.next_mark("B"); plan.beams.append(tgt)
        self.mutate("Mirror", fn)

    def copy_selection_to_new_plan(self):
        plan = self.current_plan()
        ids = set(self.canvas.selection)
        if not plan or not ids:
            return
        name, ok = QInputDialog.getText(self, "Copy selection to new plan", "New plan name:", text=f"{plan.name} part")
        if not ok or not name.strip():
            return

        def fn():
            p = Plan(name=name.strip(), floor_type=plan.floor_type, floor_height_above=plan.floor_height_above)
            p.slabs = [copy.deepcopy(o) for o in plan.slabs if o.id in ids]
            p.columns = [copy.deepcopy(o) for o in plan.columns if o.id in ids]
            p.beams = [copy.deepcopy(o) for o in plan.beams if o.id in ids]
            for o in p.slabs + p.columns + p.beams:
                o.id = new_id()
            self.project.add_plan(p)
        self.mutate("Copy to new plan", fn)
        self.set_current_plan(self.project.plans[-1].name)

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
        lines = "\n".join(f"  from {m}: D {d:.2f} kN, L {l:.2f} kN" for m, d, l in cl.parts) or "  (no beams frame into it)"
        QMessageBox.information(self, f"Column {col.mark} – load analysis",
                                f"Load from this level\nDead {cl.dead:.2f} kN · Live {cl.live:.2f} kN · Total {cl.total:.2f} kN\n\n{lines}")

    # ================================================================ FrameWin commands
    def _run(self, actions: list[dict], title: str) -> Optional[ActionResult]:
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
            self.results.setCurrentIndex(0)
            QMessageBox.warning(self, title, "\n".join(res.errors) + "\n\nSee the Issues tab (double-click to locate).")
        else:
            self.statusBar().showMessage(f"{title} finished in {time.time() - t0:.1f} s", 6000)
            QMessageBox.information(self, title, "\n".join(res.messages))
        return res

    def analyze_frame(self):
        res = self._run([{"action": "analyze"}], "3-D analysis")
        if res and not res.errors:
            self.tabs.setCurrentIndex(1)

    def design_all(self):
        res = self._run([{"action": "design"}], "Design")
        if res and not res.errors:
            self.results.setCurrentIndex(3)
            self.tabs.setCurrentIndex(1)
            self.view3d.mode.setCurrentText("Design utilisation")

    def optimize(self):
        if QMessageBox.question(self, "Optimise sizes", "Enlarge failing columns and beams and re-run analysis/design "
                                "up to 5 times? (Undo restores the current sizes.)") != QMessageBox.Yes:
            return
        self.push_undo()
        res = self._run([{"action": "optimize_sizes"}, {"action": "design"}], "Optimise sizes")
        if res is not None:
            self.dirty = True
            self._plan_cache.clear()
            self.refresh_all()

    def autosize(self):
        dlg = dialogs.AutoSizeDialog(self)
        if not dlg.exec():
            return
        from ..design.runner import autosize_columns

        b = dlg.breadth.value() or None
        self.mutate("Auto-size columns", lambda: autosize_columns(self.project, b, dlg.pct.value(), dlg.same.isChecked(),
                                                                  dlg.step.value(), dlg.mf.value(), dlg.inc.value()))
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

    def project_settings(self):
        dlg = dialogs.SettingsDialog(self, self.project)
        if dlg.exec():
            self.mutate("Project settings", dlg.apply)

    # ================================================================ misc
    def ai_settings(self):
        dlg = dialogs.AISettingsDialog(self, self.assistant.config)
        if dlg.exec():
            cfg = dlg.apply()
            self.assistant.config = cfg
            self.settings.setValue("ai/provider", cfg.provider)
            self.settings.setValue("ai/model", cfg.model)
            self.settings.setValue("ai/base_url", cfg.base_url)
            self.chat.refresh_provider()

    def license_dialog(self):
        dlg = dialogs.LicenseDialog(self, self.license.label())
        if dlg.exec() and dlg.text.toPlainText().strip():
            try:
                self.license = install_license(dlg.text.toPlainText())
            except (ValueError, json.JSONDecodeError) as exc:
                QMessageBox.critical(self, "Licence", f"Licence not accepted: {exc}")
                return
            self.session.watermark = self.license.watermark
            self.session.exports_allowed = self.license.exports_allowed
            self.lic_lbl.setText(self.license.label())
            QMessageBox.information(self, "Licence", self.license.label())

    def about(self):
        QMessageBox.about(self, f"About {APP_NAME}", dialogs.about_text(self.license.label()))

    def quick_start(self):
        QMessageBox.information(self, "Quick start", (
            "1. File ▸ New from template, or press Ctrl+K and describe your building to the AI assistant.\n"
            "2. PlanWin: draw slabs (R / P), place columns (C or Auto columns), then Auto beams.\n"
            "3. Analyse plan (F5) – check the Issues tab until the load difference is ~0 %.\n"
            "4. Project panel ▸ Levels: assign a plan to each level with storey heights.\n"
            "5. FrameWin ▸ Auto-size columns, Project settings (zone, wind, SBC).\n"
            "6. Build & analyse 3-D frame (F6), then Design all (F7).\n"
            "7. Export STAAD / ETABS / DXF / Excel / PDF.\n\n"
            "Shortcuts: S select · H pan · R rectangle slab · P polygon slab · C column · B beam · D measure · "
            "F zoom extents · Del delete · Ctrl+Z/Y undo/redo · double-click beam for BM/SF."))

    def apply_theme(self, name: str):
        self.theme_name = name
        self.settings.setValue("theme", name)
        QApplication.instance().setStyleSheet(qss(name))
        if hasattr(self, "chat"):
            self.chat.rerender()
        self.canvas.update()
        self.view3d.canvas.update()

    def _toggle_loads(self):
        self.canvas.show_loads = not self.canvas.show_loads
        self.canvas.update()

    def _toggle_marks(self):
        self.canvas.show_marks = not self.canvas.show_marks
        self.canvas.update()

    def _goto_issue(self, issue):
        if issue.at:
            self.tabs.setCurrentIndex(0)
            self.canvas.focus_point(*issue.at)
        if issue.obj_id and self.current_plan() and self.current_plan().find(issue.obj_id):
            self.canvas.select_ids([issue.obj_id])

    def _last_dir(self) -> str:
        return self.settings.value("last_dir", os.path.expanduser("~"))

    def _add_recent(self, fn: str):
        rec = [r for r in (self.settings.value("recent", []) or []) if r != fn]
        rec.insert(0, fn)
        self.settings.setValue("recent", rec[:8])
        self.settings.setValue("last_dir", os.path.dirname(fn))
        self._update_recent()

    def _update_recent(self):
        self.recent_menu.clear()
        rec = self.settings.value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        for r in rec:
            self.recent_menu.addAction(r, lambda f=r: self.maybe_save() and self.open_path(f))
        self.recent_menu.setEnabled(bool(rec))

    def _open_path(self, path):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _do_autosave(self):
        if not self.dirty:
            return
        try:
            project_io.save_project(self.project, os.path.join(app_data_dir(), "autosave.pwai"))
        except OSError:
            log.exception("autosave failed")

    def _startup_checks(self):
        auto = os.path.join(app_data_dir(), "autosave.pwai")
        if os.path.exists(auto) and self.settings.value("clean_exit", "true") == "false":
            if QMessageBox.question(self, "Recover", "PlanWin AI Pro did not close normally. Recover the autosaved project?") == QMessageBox.Yes:
                try:
                    self._load_project(project_io.load_project(auto))
                except Exception as exc:
                    QMessageBox.warning(self, "Recover", f"Recovery failed: {exc}")
        self.settings.setValue("clean_exit", "false")
        if self.license.mode == "expired":
            QMessageBox.warning(self, "Licence", "Your trial has expired. Modelling still works, but exports are disabled.\n"
                                                 "Tools ▸ Licence to activate.")

    def closeEvent(self, ev):
        if not self.maybe_save():
            ev.ignore()
            return
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("clean_exit", "true")
        ev.accept()


def _translate(o, dx, dy):
    if isinstance(o, Slab):
        o.points = [[p[0] + dx, p[1] + dy] for p in o.points]
    elif isinstance(o, Column):
        o.x += dx
        o.y += dy
    else:
        o.x1 += dx; o.x2 += dx; o.y1 += dy; o.y2 += dy
