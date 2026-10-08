"""Every command of the main window as a :class:`QAction`, and their ribbon layout.

Actions are created once (``win.actions[key]``) and added to the window itself, so their
keyboard shortcuts work whichever ribbon tab is showing.  The ribbon, the File menu and
the canvas context menu only *show* these actions.

Ribbon tabs: Home · Plan · Loads · Frame · Design · Output · View · Help.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QComboBox, QMenu, QToolButton

from .. import APP_NAME, units
from ..services import exports
from .commands.frame import SEISMIC_METHODS
from .ribbon import Ribbon
from .theme import icon

if TYPE_CHECKING:
    from .main_window import MainWindow

#: drawing tools: key, label, icon, shortcut, tooltip
TOOLS = (
    ("select", "Select", "select", "S", "Select objects (Shift adds, drag a window)"),
    ("pan", "Pan", "pan", "H", "Drag to pan, wheel to zoom"),
    ("rect_slab", "Slab", "rect", "R", "Rectangular slab – drag corner to corner"),
    ("poly_slab", "Irregular slab", "poly", "P", "Polygon slab – click the corners"),
    ("column", "Column", "column", "C", "Place a column"),
    ("beam", "Beam", "beam", "B", "Draw a beam – click its two ends"),
    ("wall", "Shear wall", "wall", "W", "Draw an RC shear wall – click its two ends"),
    ("measure", "Measure", "measure", "D", "Distance between two points"),
    ("area", "Area", "area", "A", "Area and perimeter of a polygon"),
    ("dimension", "Dimension", "dims", "Shift+D", "Add a dimension: two points, then the offset"),
)

#: plan-canvas toggles: attribute, label, icon, default, shortcut
CANVAS_FLAGS = (
    ("ortho", "Ortho", "move", False, "F8"),
    ("snap_ends", "Snap ends", "select", True, None),
    ("snap_mid", "Snap midpoints", "beam", True, None),
    ("snap_grid", "Snap grid lines", "grid", True, None),
    ("show_grids", "Show grid lines", "grid", True, None),
    ("show_dims", "Show dimensions", "dims", True, None),
    ("show_loads", "Show loads", "jointload", True, None),
    ("show_marks", "Show marks", "renumber", True, None),
)

EXPORT_ICONS = {
    "staad": "staad",
    "etabs": "etabs",
    "dxf": "dxf",
    "dxf3d": "view3d",
    "excel": "excel",
    "pdf": "pdf",
    "calc": "calc",
    "bbs": "bbs",
    "details": "drawing",
    "project": "save",
}


def _act(win: MainWindow, text, fn, shortcut=None, ic=None, tip=None, checkable=False) -> QAction:
    a = QAction(text, win)
    if ic:
        a.setIcon(icon(ic))
    if shortcut:
        a.setShortcut(QKeySequence(shortcut))
    if tip:
        a.setStatusTip(tip)
    a.setToolTip(f"{text}{f' ({shortcut})' if shortcut else ''}" + (f"\n{tip}" if tip else ""))
    a.setCheckable(checkable)
    a.triggered.connect(fn)
    win.addAction(a)  # shortcut active whichever ribbon tab is showing
    return a


def build_actions(win: MainWindow) -> dict[str, QAction]:
    A: dict[str, QAction] = {}

    def add(key, *args, **kw):
        A[key] = _act(win, *args, **kw)
        return A[key]

    # ---- project
    add("new", "New blank", win.new_blank, "Ctrl+N", "new", "Empty project with one plan and two levels")
    add("template", "New from template", win.new_from_template, "Ctrl+Shift+N", "template", "Start from a building")
    add("open", "Open", win.open_dialog, "Ctrl+O", "open", "Open a .pwai project or a legacy .plw plan")
    add("save", "Save", win.save, "Ctrl+S", "save")
    add("save_as", "Save as", win.save_as, "Ctrl+Shift+S", "save")
    add("import_plw", "Import PlanWin plan (.plw)", win.import_plw, None, "folder")
    add("import_dxf", "Import DXF plan", win.import_dxf, None, "dxf", "SLAB / COLUMN / BEAM layers")
    add("exit", "Exit", win.close, "Alt+F4")
    # ---- edit
    add("undo", "Undo", win.undo, "Ctrl+Z", "undo")
    add("redo", "Redo", win.redo, "Ctrl+Y", "redo")
    add("delete", "Delete", win.delete_selection, None, "delete", "Delete the selection (Del)")
    add("move", "Move / copy", win.move_copy_dialog, "Ctrl+M", "move", "Move or array-copy the selection")
    add("mirror", "Mirror", win.mirror_dialog, None, "mirror", "Mirror the selection about a line")
    add("to_plan", "Copy to new plan", win.copy_selection_to_new_plan, None, "plan", "Selection becomes a new plan")
    for kind in ("slab", "beam", "column"):
        add(f"renumber_{kind}", f"{kind.title()}s", lambda _=False, k=kind: win.renumber(k))
    add("renumber", "Renumber", lambda: None, None, "renumber", "Left→right, top→bottom")
    # ---- tools
    win.tool_group = QActionGroup(win)
    win.tool_actions = {}
    for key, label, ic, sc, tip in TOOLS:
        a = add(f"tool_{key}", label, lambda _=False, t=key: win.set_tool(t), sc, ic, tip, checkable=True)
        win.tool_group.addAction(a)
        win.tool_actions[key] = a
    win.tool_actions["select"].setChecked(True)
    add("zoom", "Zoom extents", win.canvas.zoom_extents, "F", "zoomfit")
    add("auto_columns", "Auto columns", win.auto_columns, None, "autocol", "Columns at all slab corners (Judge)")
    add("auto_beams", "Auto beams", win.auto_beams, None, "autobeam", "Beams along all slab edges")
    add("external", "Mark external beams", win.mark_external, None, "beam", "Wall load on the building perimeter")
    add("grids", "Grid lines", win.grids_dialog, None, "grid", "Define grid lines, or generate them from columns")
    add("analyze_plan", "Analyse plan", win.analyze_plan, "F5", "analyze", "Slab → beam → column load take-down")
    for attr, label, ic, default, sc in CANVAS_FLAGS:
        on = win.settings.value(f"canvas/{attr}", "true" if default else "false") == "true"
        a = add(f"flag_{attr}", label, lambda checked, at=attr: win.set_canvas_flag(at, checked), sc, ic, None, True)
        a.setChecked(on)
        win.set_canvas_flag(attr, on)
    # ---- loads
    add("stairs", "Staircase", win.staircase, None, "stairs", "Staircase wizard – loads on the support beams")
    add("tank", "Water tank", win.water_tank, None, "tank", "Overhead tank wizard – loads on the columns")
    add("joint_loads", "Joint loads", win.joint_loads, None, "jointload", "Extra loads at column tops")
    add("settings", "Project settings", win.project_settings, "Ctrl+,", "settings", "Seismic, wind, materials, SBC")
    # ---- frame
    add("copy_floor", "Copy floor", win.copy_floors, None, "copyfloor", "Duplicate a plan and/or add storeys")
    add("levels", "Levels", win.show_levels, None, "levels", "Storeys and the plan used at each level")
    add("autosize", "Auto-size columns", win.autosize, None, "autosize", "Column sizes from the axial load")
    add("column_sizes", "Column sizes", win.column_sizes, None, "sizes", "Column size by level")
    win.method_group = QActionGroup(win)
    win.method_actions = {}
    for key, label in SEISMIC_METHODS.items():
        a = add(f"method_{key}", label, lambda _=False, k=key: win.set_seismic_method(k), None, None, None, True)
        win.method_group.addAction(a)
        win.method_actions[key] = a
    add("method", "Seismic method", lambda: None, None, "modal", "IS 1893-1 analysis method")
    add("diaphragm", "Rigid diaphragm", win.set_rigid_diaphragm, None, "levels", "Floors as rigid diaphragms", True)
    add("analyze_frame", "Analyse frame", win.analyze_frame, "F6", "frame", "Stack levels, apply IS loads and solve")
    add("view3d", "3-D view", win.show_3d_view, None, "view3d")
    add("plan_view", "Plan view", win.show_plan_view, None, "plan")
    # ---- design
    add("design", "Design all", win.design_all, "F7", "design", "Columns, beams, walls, footings, slabs, BOQ")
    add("optimize", "Optimise sizes", win.optimize, None, "optimize", "Enlarge failing members and re-run")
    add("ductile", "IS 13920", lambda: win.show_check("IS 13920"), None, "ductile", "Ductile detailing checks")
    add("irregular", "Irregularity", lambda: win.show_check("Irregularity"), None, "irregular", "IS 1893 Tables 5/6")
    add("modal", "Modal / RS", lambda: win.show_check("Modal / RS"), None, "modal", "Periods, mass participation")
    add("walls_tab", "Walls", lambda: win.show_check("Walls"), None, "wall", "Shear wall design")
    add("calc_sel", "Calc sheets – selection", win.calc_for_selection, None, "calc", "For the selected members")
    add("boq_floor", "BOQ by floor", lambda: win.show_check("BOQ by floor"), None, "excel", "Quantities per level")
    add("revisions", "Revisions", win.revisions, None, "compare", "Save BOQ revisions and compare them")
    # ---- output (from the export registry)
    for fmt in map(exports.get, exports.keys()):
        add(f"export_{fmt.key}", fmt.label, lambda _=False, k=fmt.key: win.export(k), None, EXPORT_ICONS.get(fmt.key))
    # ---- view / help
    add("dark", "Dark theme", win.toggle_theme, "Ctrl+T", "theme", checkable=True)
    add("ai", "AI Assistant", win.toggle_chat, "Ctrl+K", None, "Open / close the AI side panel", True)
    A["ai"].setIcon(icon("ai", "#FFFFFF"))
    add("ai_settings", "AI settings", win.ai_settings, None, "settings", "Provider, model and API key")
    add("quick_start", "Quick start", win.quick_start, "F1", "help")
    add("about", "About", win.about, None, "help")
    add("licence", "Licence", win.license_dialog, None, "licence", "Activate or view the licence")
    add("log", "Log folder", lambda: win._open_path(_app_dir()), None, "folder")
    return A


def _app_dir() -> str:
    from ..licensing.license import app_data_dir

    return app_data_dir()


def _menu(win: MainWindow, actions: list[QAction]) -> QMenu:
    m = QMenu(win)
    for a in actions:
        m.addAction(a) if a is not None else m.addSeparator()
    return m


def build_ribbon(win: MainWindow, A: dict[str, QAction]) -> Ribbon:
    rb = Ribbon(APP_NAME)
    # ---- File (application) menu and quick access
    fm = rb.app_menu
    for k in ("new", "template", "open"):
        fm.addAction(A[k])
    win.recent_menu = fm.addMenu(icon("folder"), "Open recent")
    for k in ("save", "save_as"):
        fm.addAction(A[k])
    fm.addSeparator()
    fm.addAction(A["import_plw"])
    fm.addAction(A["import_dxf"])
    ex = fm.addMenu(icon("export"), "Export")
    for key in exports.keys():
        ex.addAction(A[f"export_{key}"])
    fm.addSeparator()
    fm.addAction(A["exit"])
    for k in ("save", "undo", "redo"):
        rb.add_quick(A[k], icon(k, "#FFFFFF"))
    ai = QToolButton()
    ai.setObjectName("aiButton")
    ai.setDefaultAction(A["ai"])
    ai.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    rb.add_right(ai)

    # ---- Home
    pg = rb.page("Home")
    g = pg.group("Project")
    g.large(A["template"])
    g.large(A["open"])
    g.large(A["save"])
    g.small(A["new"])
    g.small(A["save_as"])
    g.small(A["settings"])
    g = pg.group("Edit")
    g.small(A["undo"])
    g.small(A["redo"])
    g.small(A["delete"])
    g.small(A["move"])
    g.small(A["mirror"])
    g.small(A["to_plan"])
    g.small(A["renumber"], _menu(win, [A["renumber_slab"], A["renumber_beam"], A["renumber_column"]]))
    g = pg.group("Workflow")
    g.large(A["analyze_plan"])
    g.large(A["analyze_frame"])
    g.large(A["design"])
    g = pg.group("Assistant")
    g.large(A["ai"])
    g.small(A["ai_settings"])

    # ---- Plan
    pg = rb.page("Plan")
    g = pg.group("Select")
    g.large(A["tool_select"])
    g.small(A["tool_pan"])
    g.small(A["zoom"])
    g = pg.group("Draw")
    for k in ("rect_slab", "column", "beam", "wall"):
        g.large(A[f"tool_{k}"])
    g.small(A["tool_poly_slab"])
    g = pg.group("Generate")
    g.large(A["auto_columns"])
    g.large(A["auto_beams"])
    g.small(A["external"])
    g = pg.group("Grids & measure")
    g.large(A["grids"])
    g.small(A["tool_dimension"])
    g.small(A["tool_measure"])
    g.small(A["tool_area"])
    g = pg.group("Snap")
    g.small(A["flag_ortho"])
    g.small(A["flag_snap_ends"])
    g.small(A["flag_snap_mid"])
    g.small(A["flag_snap_grid"])
    win.snap_combo = QComboBox()
    win.snap_combo.setToolTip("Snap step when no object or grid point is near")
    for st in (0.01, 0.05, 0.1, 0.25, 0.5):
        win.snap_combo.addItem(f"Step {st:g} m", st)
    win.snap_combo.setCurrentIndex(1)
    win.snap_combo.currentIndexChanged.connect(
        lambda _i: setattr(win.canvas, "snap_step", win.snap_combo.currentData())
    )
    g.widget(win.snap_combo)

    # ---- Loads
    pg = rb.page("Loads")
    g = pg.group("Wizards")
    g.large(A["stairs"])
    g.large(A["tank"])
    g = pg.group("Loads")
    g.large(A["joint_loads"])
    g.small(A["flag_show_loads"])
    g = pg.group("Code loads")
    g.large(A["settings"])

    # ---- Frame
    pg = rb.page("Frame")
    g = pg.group("Storeys")
    g.large(A["copy_floor"])
    g.large(A["levels"])
    g = pg.group("Sizes")
    g.large(A["autosize"])
    g.large(A["column_sizes"])
    g = pg.group("Seismic")
    g.large(A["method"], _menu(win, list(win.method_actions.values())))
    g.small(A["diaphragm"])
    g.small(A["settings"])
    g = pg.group("Analyse")
    g.large(A["analyze_frame"])
    g.large(A["view3d"])

    # ---- Design
    pg = rb.page("Design")
    g = pg.group("Design")
    g.large(A["design"])
    g.large(A["optimize"])
    g = pg.group("Code checks")
    g.small(A["ductile"])
    g.small(A["irregular"])
    g.small(A["modal"])
    g.small(A["walls_tab"])
    g = pg.group("Documents")
    g.large(A["calc_sel"])
    g.small(A["export_calc"])
    g.small(A["export_bbs"])
    g.small(A["export_details"])
    g = pg.group("Quantities")
    g.large(A["revisions"])
    g.small(A["boq_floor"])

    # ---- Output: one group per export-registry group
    pg = rb.page("Output")
    groups: dict[str, list[str]] = {}
    for key in exports.keys():
        groups.setdefault(exports.get(key).group, []).append(key)
    for title, keys in groups.items():
        g = pg.group(title)
        for key in keys:
            (g.large if len(keys) <= 3 else g.small)(A[f"export_{key}"])

    # ---- View
    pg = rb.page("View")
    g = pg.group("Window")
    g.large(A["plan_view"])
    g.large(A["view3d"])
    g.small(A["zoom"])
    g = pg.group("Display")
    for k in ("show_grids", "show_dims", "show_loads", "show_marks"):
        g.small(A[f"flag_{k}"])
    g = pg.group("Panels")
    for d in win.panel_docks():
        a = d.toggleViewAction()
        a.setIcon(icon("plan"))
        g.small(a)
    g = pg.group("Appearance")
    g.large(A["dark"])
    win.units_combo = QComboBox()
    win.units_combo.setToolTip("Display units – the model is always stored in kN")
    win.units_combo.addItem("kN, kN·m", "SI")
    win.units_combo.addItem("tonnes (t, t·m)", "MKS")
    win.units_combo.setCurrentIndex(win.units_combo.findData(units.current.system))
    win.units_combo.currentIndexChanged.connect(lambda _i: win.set_units(win.units_combo.currentData()))
    g.widget(win.units_combo)

    # ---- Help
    pg = rb.page("Help")
    g = pg.group("Help")
    g.large(A["quick_start"])
    g.large(A["about"])
    g = pg.group("Licence")
    g.large(A["licence"])
    g = pg.group("Support")
    g.large(A["log"])
    rb.select("Home")
    return rb
