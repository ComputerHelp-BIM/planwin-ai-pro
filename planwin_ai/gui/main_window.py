"""PlanWin AI Pro main window.

The window owns the state (project session, undo/redo, plan-analysis cache, panels) and
the ribbon; the command handlers live in the mixins of :mod:`.commands`, and every command
is defined once as a QAction in :mod:`.ribbon_layout`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtWidgets import QApplication, QDockWidget, QLabel, QMainWindow, QMessageBox, QTabWidget

from .. import APP_NAME, COMPANY, __version__, units
from ..ai.actions import ActionResult, Session
from ..ai.assistant import Assistant
from ..ai.providers import ProviderConfig
from ..ai.templates import build_template
from ..core.model import Plan, Project
from ..core.plan_engine import PlanEngine, PlanResult
from ..io import project_io
from ..licensing.license import current_state
from . import ribbon_layout
from .canvas import PlanCanvas
from .chat import ChatDock
from .commands import AppCommands, FileCommands, FrameCommands, PlanCommands
from .panels import ProjectPanel, PropertiesPanel, ResultsPanel
from .view3d import Frame3DView

log = logging.getLogger("planwin")
UNDO_LIMIT = 60


class MainWindow(FileCommands, PlanCommands, FrameCommands, AppCommands, QMainWindow):
    def __init__(self, project: Project | None = None, path: str | None = None):
        super().__init__()
        self.settings = QSettings(COMPANY, "PlanWinAIPro")
        self.theme_name = self.settings.value("theme", "light")
        units.set_system(self.settings.value("units", "SI"))
        self.license = current_state()
        self.session = Session(
            project or build_template("residential_g4"),
            out_dir=os.path.expanduser("~"),
            watermark=self.license.watermark,
            exports_allowed=self.license.exports_allowed,
        )
        cfg = ProviderConfig(
            self.settings.value("ai/provider", "offline"),
            self.settings.value("ai/model", ""),
            self.settings.value("ai/base_url", ""),
        )
        self.assistant = Assistant(self.session, cfg)
        self.path: str | None = path
        self.dirty = False
        self.undo_stack: list[str] = []
        self.redo_stack: list[str] = []
        self._ai_snapshot: str | None = None
        self._plan_cache: dict[str, PlanResult] = {}
        self.current_plan_name = self.project.plans[0].name if self.project.plans else ""
        self.defaults = {
            "slab_thickness": 0.125,
            "slab_live": 2.0,
            "slab_ff": 1.0,
            "slab_other": 0.5,
            "col_b": 0.3,
            "col_d": 0.45,
            "beam_b": 0.23,
            "beam_d": 0.45,
            "wall_thk": 0.23,  # masonry wall on beams
            "wall_t": 0.20,  # RC shear wall thickness
            "grade": "M25",
        }

        self.setWindowTitle(APP_NAME)
        self.resize(1500, 920)
        self.canvas = PlanCanvas(self)
        self.view3d = Frame3DView(self)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("ViewTabs")
        self.tabs.addTab(self.canvas, "Plan (PlanWin)")
        self.tabs.addTab(self.view3d, "3-D Frame (FrameWin)")
        self.tabs.currentChanged.connect(lambda i: self.view3d.refresh() if i == 1 else None)
        self.setCentralWidget(self.tabs)

        self.project_panel = ProjectPanel(self)
        self.props = PropertiesPanel(self)
        self.results = ResultsPanel(self)
        self.chat = ChatDock(self)
        self.project_dock = self._dock("Project", self.project_panel, Qt.LeftDockWidgetArea)
        self.props_dock = self._dock("Properties", self.props, Qt.RightDockWidgetArea)
        self.addDockWidget(Qt.RightDockWidgetArea, self.chat)
        self.tabifyDockWidget(self.props_dock, self.chat)
        self.results_dock = self._dock("Results and checks", self.results, Qt.BottomDockWidgetArea)

        self.canvas.selectionChanged.connect(self.props.show_selection)
        self.canvas.status.connect(lambda s: self.statusBar().showMessage(s, 8000))
        self.canvas.cursorMoved.connect(lambda x, y: self.coord_lbl.setText(f"X {x:8.3f}  Y {y:8.3f} m"))
        self.results.issueActivated.connect(self._goto_issue)

        self.coord_lbl = QLabel()
        self.lic_lbl = QLabel(self.license.label())
        self.cmd = ribbon_layout.build_actions(self)
        self.undo_act, self.redo_act, self.ai_toggle = self.cmd["undo"], self.cmd["redo"], self.cmd["ai"]
        self.ribbon = ribbon_layout.build_ribbon(self, self.cmd)
        self.setMenuWidget(self.ribbon)
        self.ribbon.set_collapsed(self.settings.value("ribbon_collapsed", "false") == "true")
        self.chat.visibilityChanged.connect(self.ai_toggle.setChecked)
        self.canvas.orthoChanged.connect(self.cmd["flag_ortho"].setChecked)  # F8 / Shift on the canvas
        self._update_recent()
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

    # ================================================================ model access
    @property
    def project(self) -> Project:
        return self.session.project

    def current_plan(self) -> Plan | None:
        return self.project.plan(self.current_plan_name) if self.current_plan_name else None

    def plan_result(self, name: str) -> PlanResult | None:
        if name not in self._plan_cache:
            plan = self.project.plan(name)
            if plan is None:
                return None
            d = self.project.design
            try:
                self._plan_cache[name] = PlanEngine(
                    plan, None, d.two_way_ratio_limit, d.continuity_in_load_transfer
                ).run()
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

    # ================================================================ UI helpers
    def _dock(self, title, widget, area) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setObjectName(title.replace(" ", "_"))
        d.setWidget(widget)
        self.addDockWidget(area, d)
        return d

    def panel_docks(self) -> list[QDockWidget]:
        return [self.project_dock, self.props_dock, self.chat, self.results_dock]

    # ================================================================ state changes
    def snapshot(self) -> str:
        return project_io.project_to_json(self.project)

    def push_undo(self, snapshot: str | None = None):
        self.undo_stack.append(snapshot if snapshot is not None else self.snapshot())
        if len(self.undo_stack) > UNDO_LIMIT:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def mutate(self, desc: str, fn: Callable[[], object], analysis: bool = True):
        """Run a model change with undo support and refresh everything.

        ``analysis=False`` for edits that cannot change any result (grid lines, dimensions):
        the analysis and design results are then kept."""
        self.push_undo()
        try:
            fn()
        except Exception as exc:
            log.exception("edit failed")
            self.project_from_json(self.undo_stack.pop())
            QMessageBox.warning(self, desc, f"Could not complete '{desc}': {exc}")
            return
        if analysis:
            self.invalidate()
        else:
            self.dirty = True
            self.refresh_all()
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
        plan = self.current_plan()
        self.props.show_selection([i for i in self.canvas.selection if plan and plan.find(i)])
        self.results.refresh()
        if self.tabs.currentIndex() == 1:
            self.view3d.refresh()
        self.canvas.update()
        self.undo_act.setEnabled(bool(self.undo_stack))
        self.redo_act.setEnabled(bool(self.redo_stack))
        self.sync_frame_actions()
        name = os.path.basename(self.path) if self.path else self.project.name
        title = f"{name}{' •' if self.dirty else ''} – {APP_NAME} {__version__}"
        self.setWindowTitle(title)
        self.ribbon.title.setText(f"{name}{' •' if self.dirty else ''}")

    def set_current_plan(self, name: str):
        if name == self.current_plan_name:
            return
        self.current_plan_name = name
        self.canvas.selection = []
        self.refresh_all()
        self.canvas.zoom_extents()

    # ================================================================ AI hooks
    def before_ai_change(self):
        """Remember the model before the assistant acts; it becomes an undo step only if
        the actions really change the model (a plain answer must not touch undo/redo)."""
        self._ai_snapshot = self.snapshot()

    def run_ai_actions(self, reply: str, actions: list):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            return self.assistant.apply(reply, actions)
        finally:
            QApplication.restoreOverrideCursor()

    def after_ai_change(self, res: ActionResult | None):
        snap, self._ai_snapshot = self._ai_snapshot, None
        if res is None:
            return
        if res.changed:
            if snap is not None:
                self.push_undo(snap)
            self.dirty = True
            self._plan_cache.clear()
            if not self.project.plan(self.current_plan_name):
                self.current_plan_name = self.project.plans[0].name if self.project.plans else ""
            self.canvas.selection = []
            self.refresh_all()
            self.canvas.zoom_extents()
        if res.analysed:
            self.results.refresh()
            self.view3d.refresh()
            self.results_dock.raise_()
