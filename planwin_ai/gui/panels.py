"""Dock panels: project tree (plans + levels), properties editor and results tables."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import units
from ..core.model import Beam, Column, Level, PartLoad, Plan, PointLoad, Slab, Wall
from . import theme
from .results_panel import RESULT_TABS, ResultsPanel  # noqa: F401
from .widgets import CollapsibleSection, FlowLayout, ScrollPanel

if TYPE_CHECKING:
    from .main_window import MainWindow

GRADES = ["M20", "M25", "M30", "M35", "M40", "M45", "M50"]


def _spin(v: float, lo=-1e6, hi=1e6, dec=3, step=0.05) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(dec)
    s.setSingleStep(step)
    s.setValue(float(v))
    s.setKeyboardTracking(False)
    return s


def _settings(main) -> Any:
    """The main window's QSettings (remembers which panel sections are open); fakes may have none."""
    return getattr(main, "settings", None)


def _tool_button(text: str, tip: str, fn, icon: str | None = None) -> QToolButton:
    b = QToolButton()
    b.setText(text)
    b.setToolTip(tip)
    b.setAutoRaise(False)
    if icon:
        b.setIcon(theme.icon(icon, theme.ACCENT, 16))
        b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    else:
        b.setToolButtonStyle(Qt.ToolButtonTextOnly)
    b.clicked.connect(fn)
    return b


def _button_row(*buttons: QWidget) -> FlowLayout:
    """Buttons left to right, wrapping onto a second line in a narrow dock."""
    row = FlowLayout(spacing=4)
    for b in buttons:
        row.addWidget(b)
    return row


def _form() -> QFormLayout:
    f = QFormLayout()
    f.setContentsMargins(0, 0, 0, 0)
    f.setRowWrapPolicy(QFormLayout.DontWrapRows)
    f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    f.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    f.setHorizontalSpacing(8)
    f.setVerticalSpacing(5)
    return f


def _add_row(form: QFormLayout, text: str, field: QWidget, tip: str) -> QLabel:
    """Form row with a word-wrapping label; label and field share the one-line tooltip."""
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setBuddy(field)
    lab.setToolTip(tip)
    if not field.toolTip():
        field.setToolTip(tip)
    form.addRow(lab, field)
    return lab


def _clear_layout(lay) -> None:
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w is not None:
            w.hide()
            w.deleteLater()
        elif it.layout() is not None:
            _clear_layout(it.layout())


# =========================================================================== project
class _PlanList(QListWidget):
    """Plan list that prefers its minimum height (about five rows); a growing section gives it any spare
    height, and a short dock scrolls the panel instead of squeezing the levels below it out of view."""

    def sizeHint(self) -> QSize:  # Qt API
        return QSize(super().sizeHint().width(), self.minimumHeight())


LEVEL_HEADERS = ("Level", "Plan", "Ht (m)", "Grade", "LL red%")
LEVEL_TIPS = (
    "Level name",
    "Plan (PlanWin floor) used at this level",
    "Storey height below this level (m)",
    "Concrete grade of the columns at this level",
    "LL red%: live-load reduction for columns per IS 875-2 cl 3.2.1",
)


class ProjectPanel(QWidget):
    planChanged = Signal(str)

    def __init__(self, main: MainWindow):
        super().__init__()
        self.main = main
        st = _settings(main)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.scroll = ScrollPanel()
        lay.addWidget(self.scroll)

        # ---- plans
        self.plans_section = self.scroll.add_section("Plans (PlanWin)", "project_plans", True, st, grow=True)
        self.plans_section.header.setToolTip("Floor plans of the project (PlanWin)")
        v1 = self.plans_section.body_layout
        self.plans = _PlanList()
        self.plans.setToolTip("Floor plans – click one to edit it on the canvas")
        self.plans.setMinimumHeight(self._list_height(5))
        self.plans.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.plans.currentTextChanged.connect(self._plan_selected)
        v1.addWidget(self.plans, 1)
        v1.addLayout(
            _button_row(
                _tool_button("New", "Create a new empty plan", self._new_plan, "new"),
                _tool_button(
                    "Copy", "Copy the current plan under a new name (PlanWin Save As)", self._copy_plan, "copyfloor"
                ),
                _tool_button("Rename", "Rename the current plan (levels using it follow)", self._rename_plan, "design"),
                _tool_button(
                    "Delete", "Delete the current plan and the levels that use it", self._delete_plan, "delete"
                ),
            )
        )
        form = _form()
        self.ftype = QComboBox()
        self.ftype.addItems(["typical", "ground", "roof"])
        self.ftype.setToolTip("Floor type of the current plan (roof beams carry no storey walls)")
        self.ftype.activated.connect(self._plan_props)
        self.fha = _spin(3.0, 0, 50, 3, 0.1)
        self.fha.setToolTip("Storey height above this plan, used for wall loads on its beams (m)")
        self.fha.editingFinished.connect(self._plan_props)
        _add_row(form, "Floor type", self.ftype, self.ftype.toolTip())
        _add_row(form, "Floor height (m)", self.fha, self.fha.toolTip())
        v1.addLayout(form)

        # ---- levels
        self.levels_section = self.scroll.add_section("Levels (FrameWin)", "project_levels", True, st, grow=True)
        self.levels_section.header.setToolTip("Storeys of the 3-D frame, listed bottom to top")
        v2 = self.levels_section.body_layout
        hint = QLabel("Bottom to top")
        hint.setObjectName("PanelHint")
        hint.setToolTip("The first row is the lowest level")
        v2.addWidget(hint)
        self.levels = QTableWidget(0, len(LEVEL_HEADERS))
        self.levels.setHorizontalHeaderLabels(list(LEVEL_HEADERS))
        for c, tip in enumerate(LEVEL_TIPS):
            self.levels.horizontalHeaderItem(c).setToolTip(tip)
        self.levels.setToolTip(
            "Ht = storey height below this level. LL red% = IS 875-2 live load reduction for column sizing."
        )
        hh = self.levels.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        hh.setMinimumSectionSize(44)
        self.levels.verticalHeader().setVisible(False)
        self.levels.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.levels.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.levels.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.levels.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.levels.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.levels.itemChanged.connect(self._level_item_changed)
        self._row_h = self._level_row_height()
        self.levels.verticalHeader().setDefaultSectionSize(self._row_h)
        self.levels.setMinimumHeight(self._table_height(6))
        v2.addWidget(self.levels, 1)
        v2.addLayout(
            _button_row(
                _tool_button("+ Level", "Insert a level above the selected row", self._add_level),
                _tool_button("− Level", "Delete the selected level", self._del_level),
                _tool_button("▲", "Move the selected level one row up (one storey lower)", lambda: self._move(-1)),
                _tool_button("▼", "Move the selected level one row down (one storey higher)", lambda: self._move(1)),
                _tool_button("Fill ↑", "Copy the selected cell to every level above it", self._fill_up),
            )
        )
        self.height_lbl = QLabel()
        self.height_lbl.setToolTip("Total building height above the base and number of levels")
        v2.addWidget(self.height_lbl)

        # ---- stairs and tanks
        self.extras_section = self.scroll.add_section("Stairs & tanks", "project_extras", True, st)
        self.extras_section.header.setToolTip("Staircases and water tanks added with the wizards")
        self.extras_lbl = QLabel()  # staircases and water tanks from the wizards
        self.extras_lbl.setWordWrap(True)
        self.extras_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.extras_lbl.setToolTip("Staircase loads go to their support beams, tank loads to the columns")
        self.extras_empty = QLabel("None yet – use the Staircase and Water tank wizards")
        self.extras_empty.setObjectName("PanelHint")
        self.extras_empty.setWordWrap(True)
        self.extras_empty.setToolTip("Staircases and water tanks added with the wizards appear here")
        self.extras_section.body_layout.addWidget(self.extras_lbl)
        self.extras_section.body_layout.addWidget(self.extras_empty)
        self._loading = False

    # ----------------------------------------------------------- sizing
    def _list_height(self, rows: int) -> int:
        return rows * (self.plans.fontMetrics().height() + 8) + 8

    def _level_row_height(self, combos=()) -> int:
        """Row height that fits the plan / grade combo boxes (as styled by the current style sheet)."""
        if not combos:
            probe = QComboBox()
            probe.addItem("M25")
            combos = [probe]
        hint = 0
        for cb in combos:
            cb.ensurePolished()
            hint = max(hint, cb.sizeHint().height())
        return max(hint + 2, self.fontMetrics().height() + 10)

    def _table_height(self, rows: int) -> int:
        t = self.levels
        header = t.horizontalHeader().sizeHint().height()
        scrollbar = t.horizontalScrollBar().sizeHint().height()
        return header + rows * self._row_h + scrollbar + 2 * t.frameWidth() + 2

    def _fit_level_columns(self) -> None:
        """Columns wide enough for their contents; the plan column fits the longest plan name."""
        t = self.levels
        combos = [t.cellWidget(r, c) for r in range(t.rowCount()) for c in (1, 3)]
        self._row_h = self._level_row_height(combos)
        t.verticalHeader().setDefaultSectionSize(self._row_h)
        t.setMinimumHeight(self._table_height(6))
        t.resizeColumnsToContents()
        for c in (1, 3):
            widest = max((t.cellWidget(r, c).sizeHint().width() for r in range(t.rowCount())), default=0)
            header = t.horizontalHeader().sectionSizeFromContents(c).width()
            t.setColumnWidth(c, max(widest + 4, header, t.columnWidth(c)))

    # ----------------------------------------------------------- refresh
    def refresh(self):
        pr = self.main.project
        cur = self.main.current_plan_name
        self.plans.blockSignals(True)
        self.plans.clear()
        for p in pr.plans:
            it = QListWidgetItem(f"{p.name}")
            it.setData(Qt.UserRole, p.name)
            it.setToolTip(
                f"{p.floor_type}: {len(p.slabs)} slabs, {len(p.columns)} columns, {len(p.beams)} beams, "
                f"{len(p.walls)} walls"
            )
            self.plans.addItem(it)
            if p.name == cur:
                self.plans.setCurrentItem(it)
        self.plans.blockSignals(False)
        self.plans_section.set_badge(str(len(pr.plans)))
        plan = self.main.current_plan()
        if plan:
            self.ftype.setCurrentText(plan.floor_type)
            self.fha.blockSignals(True)
            self.fha.setValue(plan.floor_height_above)
            self.fha.blockSignals(False)
        self._loading = True
        self.levels.setRowCount(len(pr.levels))
        names = [p.name for p in pr.plans]
        for r, lv in enumerate(pr.levels):
            it = QTableWidgetItem(lv.name)
            it.setToolTip(LEVEL_TIPS[0])
            self.levels.setItem(r, 0, it)
            cb = QComboBox()
            cb.setSizeAdjustPolicy(QComboBox.AdjustToContents)
            cb.addItems(names)
            cb.setCurrentText(lv.plan)
            cb.setToolTip(LEVEL_TIPS[1])
            cb.currentTextChanged.connect(lambda t, row=r: self._level_plan(row, t))
            self.levels.setCellWidget(r, 1, cb)
            it = QTableWidgetItem(f"{lv.height:g}")
            it.setToolTip(LEVEL_TIPS[2])
            self.levels.setItem(r, 2, it)
            gcb = QComboBox()
            gcb.setSizeAdjustPolicy(QComboBox.AdjustToContents)
            gcb.addItems(GRADES)
            gcb.setCurrentText(lv.grade)
            gcb.setToolTip(LEVEL_TIPS[3])
            gcb.currentTextChanged.connect(lambda t, row=r: self._level_grade(row, t))
            self.levels.setCellWidget(r, 3, gcb)
            it = QTableWidgetItem(f"{lv.live_reduction:g}")
            it.setToolTip(LEVEL_TIPS[4])
            self.levels.setItem(r, 4, it)
        self._loading = False
        self._fit_level_columns()
        self.levels_section.set_badge(str(len(pr.levels)))
        self.height_lbl.setText(f"Total height {pr.elevations()[-1]:.2f} m · {len(pr.levels)} levels")
        extras = []
        if pr.stairs:
            extras.append("Stairs: " + ", ".join(f"{s.get('name')} ({s.get('plan')})" for s in pr.stairs))
        if pr.water_tanks:
            tanks = (f"{t.get('name')} ({t.get('capacity_l', 0):g} L)" for t in pr.water_tanks)
            extras.append("Tanks: " + ", ".join(tanks))
        self.extras_lbl.setText("\n".join(extras))
        self.extras_lbl.setVisible(bool(extras))
        self.extras_empty.setVisible(not extras)
        self.extras_section.set_badge(str(len(pr.stairs) + len(pr.water_tanks)))

    # ----------------------------------------------------------- plans
    def _plan_selected(self, _t):
        it = self.plans.currentItem()
        if it:
            self.main.set_current_plan(it.data(Qt.UserRole))

    def _new_plan(self):
        name, ok = QInputDialog.getText(self, "New plan", "Plan name:", text="Plan")
        if ok and name.strip():
            self.main.mutate("New plan", lambda: self.main.project.add_plan(Plan(name=name.strip())))
            self.main.set_current_plan(self.main.project.plans[-1].name)

    def _copy_plan(self):
        src = self.main.current_plan()
        if not src:
            return
        name, ok = QInputDialog.getText(self, "Copy plan", "New plan name:", text=f"{src.name} copy")
        if ok and name.strip():
            import copy

            from ..core.model import new_id

            def fn():
                p = copy.deepcopy(src)
                p.name = name.strip()
                for o in p.slabs + p.columns + p.beams + p.walls:
                    o.id = new_id()
                self.main.project.add_plan(p)

            self.main.mutate("Copy plan", fn)
            self.main.set_current_plan(self.main.project.plans[-1].name)

    def _rename_plan(self):
        p = self.main.current_plan()
        if not p:
            return
        name, ok = QInputDialog.getText(self, "Rename plan", "Name:", text=p.name)
        if ok and name.strip() and name.strip() != p.name:
            if self.main.project.plan(name.strip()):
                QMessageBox.warning(self, "Rename", "A plan with that name already exists")
                return
            old = p.name

            def fn():
                p.name = name.strip()
                for lv in self.main.project.levels:
                    if lv.plan == old:
                        lv.plan = p.name

            self.main.mutate("Rename plan", fn)
            self.main.set_current_plan(p.name)

    def _delete_plan(self):
        p = self.main.current_plan()
        if not p:
            return
        used = [lv.name for lv in self.main.project.levels if lv.plan == p.name]
        msg = f"Delete plan '{p.name}'?" + (
            f"\nIt is used by levels: {', '.join(used)} (they will be removed)." if used else ""
        )
        if QMessageBox.question(self, "Delete plan", msg) != QMessageBox.Yes:
            return

        def fn():
            pr = self.main.project
            pr.plans = [q for q in pr.plans if q is not p]
            pr.levels = [lv for lv in pr.levels if lv.plan != p.name]

        self.main.mutate("Delete plan", fn)
        self.main.set_current_plan(self.main.project.plans[0].name if self.main.project.plans else "")

    def _plan_props(self, *_):
        p = self.main.current_plan()
        if not p:
            return
        ft, fh = self.ftype.currentText(), float(self.fha.value())
        if ft == p.floor_type and abs(fh - p.floor_height_above) < 1e-9:
            return

        def fn():
            p.floor_type, p.floor_height_above = ft, fh

        self.main.mutate("Plan properties", fn)

    # ----------------------------------------------------------- levels
    def _level_item_changed(self, item: QTableWidgetItem):
        if self._loading:
            return
        r, c = item.row(), item.column()
        lv = self.main.project.levels[r]
        txt = item.text().strip()
        try:
            if c == 0 and txt:
                self.main.mutate("Level name", lambda: setattr(lv, "name", txt))
            elif c == 2:
                v = float(txt)
                if v <= 0:
                    raise ValueError
                self.main.mutate("Level height", lambda: setattr(lv, "height", v))
            elif c == 4:
                v = float(txt)
                self.main.mutate("LL reduction", lambda: setattr(lv, "live_reduction", min(max(v, 0), 50)))
        except ValueError:
            QMessageBox.warning(self, "Levels", "Please enter a positive number")
            self.refresh()

    def _level_plan(self, row, text):
        if not self._loading and text:
            self.main.mutate("Level plan", lambda: setattr(self.main.project.levels[row], "plan", text))

    def _level_grade(self, row, text):
        if not self._loading and text:
            self.main.mutate("Level grade", lambda: setattr(self.main.project.levels[row], "grade", text))

    def _add_level(self):
        pr = self.main.project
        plan = self.main.current_plan_name or (pr.plans[0].name if pr.plans else "")
        if not plan:
            QMessageBox.information(self, "Levels", "Create a plan first")
            return
        r = self.levels.currentRow()
        pos = r + 1 if r >= 0 else len(pr.levels)
        h = pr.levels[-1].height if pr.levels else 3.0
        g = pr.levels[-1].grade if pr.levels else "M25"
        self.main.mutate("Add level", lambda: pr.levels.insert(pos, Level(f"Level {len(pr.levels) + 1}", plan, h, g)))

    def _del_level(self):
        r = self.levels.currentRow()
        if r >= 0:
            self.main.mutate("Delete level", lambda: self.main.project.levels.pop(r))

    def _move(self, d):
        r = self.levels.currentRow()
        lv = self.main.project.levels
        if 0 <= r < len(lv) and 0 <= r + d < len(lv):

            def fn():
                lv[r], lv[r + d] = lv[r + d], lv[r]

            self.main.mutate("Move level", fn)
            self.levels.selectRow(r + d)

    def _fill_up(self):
        r, c = self.levels.currentRow(), self.levels.currentColumn()
        lv = self.main.project.levels
        if r < 0:
            return

        def fn():
            for k in range(r + 1, len(lv)):
                if c == 1:
                    lv[k].plan = lv[r].plan
                elif c == 3:
                    lv[k].grade = lv[r].grade
                elif c == 4:
                    lv[k].live_reduction = lv[r].live_reduction
                else:
                    lv[k].height = lv[r].height

        self.main.mutate("Fill levels upward", fn)


# =========================================================================== properties
SLAB_FIELDS = [
    ("mark", "Mark", "str"),
    ("thickness", "Thickness (m)", "float"),
    ("live", "Live load (kN/m²)", "float"),
    ("floor_finish", "Floor finish (kN/m²)", "float"),
    ("other", "Other dead (kN/m²)", "float"),
    ("density", "Concrete density (kN/m³)", "float"),
    (
        "distribution",
        "Load distribution",
        ["auto", "two_way", "one_way", "one_way_long", "cantilever", "uniform", "on_grade"],
    ),
    ("cant_edge", "Cantilever fixed edge #", "int_opt"),
    ("grade", "Grade", GRADES),
    ("room", "Room / use", "str"),
]
COLUMN_FIELDS = [
    ("mark", "Mark", "str"),
    ("x", "X (m)", "float"),
    ("y", "Y (m)", "float"),
    ("b", "Breadth b (m)", "float"),
    ("d", "Depth d (m)", "float"),
    ("angle", "Angle (°)", "float"),
    ("grade", "Grade", GRADES),
]
BEAM_FIELDS = [
    ("mark", "Mark", "str"),
    ("b", "Breadth (m)", "float"),
    ("d", "Depth (m)", "float"),
    ("grade", "Grade", GRADES),
    ("role", "Role", ["auto", "primary", "secondary"]),
    ("cantilever", "Cantilever", "bool"),
    ("external", "External beam", "bool"),
    ("wall_thk", "Wall thickness (m)", "float"),
    ("wall_height", "Wall height (m, blank = auto)", "float_opt"),
    ("wall_density", "Wall density (kN/m³)", "float"),
    ("plaster_thk", "Plaster both faces (m)", "float"),
    ("parapet", "Parapet height (m, blank = none)", "float_opt"),
    ("include_self", "Consider self weight", "bool"),
    ("include_wall", "Consider wall load", "bool"),
    ("include_plaster", "Consider plaster load", "bool"),
    ("x1", "Start X", "float"),
    ("y1", "Start Y", "float"),
    ("x2", "End X", "float"),
    ("y2", "End Y", "float"),
]
WALL_FIELDS = [
    ("mark", "Mark", "str"),
    ("x1", "Start X (m)", "float"),
    ("y1", "Start Y (m)", "float"),
    ("x2", "End X (m)", "float"),
    ("y2", "End Y (m)", "float"),
    ("thickness", "Thickness (m)", "float"),
    ("grade", "Grade", GRADES),
    ("length", "Length (m)", "ro"),
]
#: kN based fields: shown and typed in the display units (``units.current``), stored in kN
FIELD_QTY = {
    "live": "area",
    "floor_finish": "area",
    "other": "area",
    "density": "unit_weight",
    "wall_density": "unit_weight",
}
_ENGINE, _SHOWN = Qt.UserRole, Qt.UserRole + 1

#: properties sections in display order: (title, settings key)
PROP_SECTIONS = (
    ("General", "props_general"),
    ("Geometry", "props_geometry"),
    ("Loads", "props_loads"),
    ("Point loads", "props_point_loads"),
    ("Part loads", "props_part_loads"),
    ("Info", "props_info"),
)
#: section of each field (anything not listed is "General")
FIELD_SECTION = {
    **dict.fromkeys(("x", "y", "x1", "y1", "x2", "y2", "angle", "length"), "Geometry"),
    **dict.fromkeys(
        (
            "live",
            "floor_finish",
            "other",
            "density",
            "distribution",
            "cant_edge",
            "wall_thk",
            "wall_height",
            "wall_density",
            "plaster_thk",
            "parapet",
            "include_self",
            "include_wall",
            "include_plaster",
        ),
        "Loads",
    ),
}
#: one-line tooltip per field; "<Kind>.<field>" overrides the plain field name ("{kind}" = slab, beam …)
FIELD_TIPS = {
    "mark": "{Kind} mark shown on the plan and in the reports",
    "grade": "Concrete grade (IS 456), e.g. M25",
    "Slab.thickness": "Slab thickness (m)",
    "Wall.thickness": "Shear wall thickness (m)",
    "live": "Live load on the slab (kN/m², IS 875-2)",
    "floor_finish": "Floor finish load on the slab (kN/m²)",
    "other": "Other superimposed dead load, e.g. partitions (kN/m²)",
    "density": "Unit weight of concrete for the slab self weight (kN/m³)",
    "distribution": "How the slab load goes to its beams: auto, two-way, one-way, cantilever …",
    "cant_edge": "Edge number fixed to the support of a cantilever slab (blank = auto)",
    "room": "Room name or use, for reference only",
    "x": "X coordinate of the column centre (m)",
    "y": "Y coordinate of the column centre (m)",
    "Column.b": "Column breadth b, the shorter side (m)",
    "Column.d": "Column depth d, the longer side (m)",
    "angle": "Rotation of the column on plan (degrees)",
    "Beam.b": "Beam width (m)",
    "Beam.d": "Overall beam depth (m)",
    "role": "Primary beams carry secondary beams; auto decides from the supports",
    "cantilever": "Beam is a cantilever (fixed at one end only)",
    "external": "Beam on the outer edge of the building",
    "wall_thk": "Thickness of the masonry wall on the beam (m)",
    "wall_height": "Height of the wall on the beam (m); blank = storey height minus beam depth",
    "wall_density": "Unit weight of the wall masonry (kN/m³)",
    "plaster_thk": "Total plaster thickness on both wall faces (m)",
    "parapet": "Parapet height on a roof beam (m); blank = none",
    "include_self": "Add the beam self weight to its load",
    "include_wall": "Add the wall load on the beam",
    "include_plaster": "Add the plaster load on the wall",
    "x1": "Start point X coordinate (m)",
    "y1": "Start point Y coordinate (m)",
    "x2": "End point X coordinate (m)",
    "y2": "End point Y coordinate (m)",
    "length": "Wall length on plan (m, read only)",
}
POINT_LOAD_TIPS = (
    "Distance of the load from the beam start (m)",
    "Dead load (kN)",
    "Live load (kN)",
    "Description",
)
PART_LOAD_TIPS = (
    "Start of the part load from the beam start (m)",
    "Loaded length (m)",
    "Intensity at the start (kN/m)",
    "Intensity at the end (kN/m)",
    "Load case: D = dead, L = live",
    "Description (wizard tags such as 'Stair ST1' are kept)",
)
EMPTY_HINT = (
    "Select a slab, beam, column or wall on the plan. Multi-select (Shift / window) to set values "
    "for many elements at once – like PlanWin 'Set Value'."
)


def field_tip(kind: str, name: str) -> str:
    """One-line tooltip for property ``name`` of a ``kind`` ("Slab", "Beam" …) in the display units."""
    tip = FIELD_TIPS.get(f"{kind}.{name}") or FIELD_TIPS.get(name) or name.replace("_", " ").capitalize()
    return units.current.text(tip.format(Kind=kind, kind=kind.lower()))


class _SectionForms:
    """The per-section form layouts seen as the single form the panel used to have."""

    def __init__(self, forms: dict[str, QFormLayout]):
        self._forms = forms

    def labelForField(self, field: QWidget):  # mirrors QFormLayout
        for f in self._forms.values():
            lab = f.labelForField(field)
            if lab is not None:
                return lab
        return None

    def rowCount(self) -> int:  # mirrors QFormLayout
        return sum(f.rowCount() for f in self._forms.values())


class PropertiesPanel(QWidget):
    def __init__(self, main: MainWindow):
        super().__init__()
        self.main = main
        st = _settings(main)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.title = QLabel("Nothing selected")
        self.title.setObjectName("PanelTitle")
        self.title.setStyleSheet("font-weight:600; font-size:11pt")
        self.title.setWordWrap(True)
        self.title.setContentsMargins(10, 8, 10, 4)
        outer.addWidget(self.title)

        self.scroll = ScrollPanel()
        self.inner = self.scroll.inner
        self.empty_lbl = QLabel(EMPTY_HINT)
        self.empty_lbl.setObjectName("PanelHint")
        self.empty_lbl.setWordWrap(True)
        self.empty_lbl.setAlignment(Qt.AlignCenter)
        self.empty_lbl.setContentsMargins(16, 24, 16, 24)
        self.scroll.add_widget(self.empty_lbl)
        self.sections: dict[str, CollapsibleSection] = {}
        self._forms: dict[str, QFormLayout] = {}
        for name, key in PROP_SECTIONS:
            sec = self.scroll.add_section(name, key, True, st)
            self.sections[name] = sec
            if name in ("General", "Geometry", "Loads"):
                f = _form()
                sec.body_layout.addLayout(f)
                self._forms[name] = f
            sec.hide()
        self.sections["General"].header.setToolTip("Mark, grade and size")
        self.sections["Geometry"].header.setToolTip("Position on the plan (m)")
        self.sections["Loads"].header.setToolTip("Loads carried by the member")
        self.sections["Point loads"].header.setToolTip("Concentrated loads on the beam (up to 20)")
        self.sections["Part loads"].header.setToolTip("Wedge / trapezoidal part loads on the beam")
        self.sections["Info"].header.setToolTip("Computed values for the selection")
        self.form = _SectionForms(self._forms)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.setToolTip("Computed from the current model (read only)")
        self.sections["Info"].body_layout.addWidget(self.info)
        outer.addWidget(self.scroll, 1)

        bar = QWidget()
        bar.setObjectName("PanelButtons")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 6, 8, 8)
        row.addStretch(1)
        self.revert_btn = QPushButton("Revert")
        self.revert_btn.setToolTip("Discard the edits and show the stored values again")
        self.revert_btn.clicked.connect(self._revert)
        row.addWidget(self.revert_btn)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.setToolTip("Apply the changed fields to the selection (Enter in a field does the same)")
        self.apply_btn.clicked.connect(self._apply)
        row.addWidget(self.apply_btn)
        outer.addWidget(bar)
        self.widgets: dict[str, tuple[Any, str]] = {}
        self.initial: dict[str, Any] = {}  # value of each field (display units) when the selection was shown
        self.objs: list = []
        self.loads_tbl: QTableWidget | None = None
        self.wedge_tbl: QTableWidget | None = None
        self._set_editable(False)
        self._show_message("Nothing selected", EMPTY_HINT)

    # ----------------------------------------------------------- states
    def _set_editable(self, on: bool) -> None:
        self.apply_btn.setEnabled(on)
        self.revert_btn.setEnabled(on)

    def _show_message(self, title: str, text: str) -> None:
        """Empty / not editable state: a centred hint instead of the sections."""
        self.title.setText(title)
        self.info.setText(text)
        self.empty_lbl.setText(text)
        self.empty_lbl.show()
        for sec in self.sections.values():
            sec.hide()
        self._set_editable(False)

    def _clear(self) -> None:
        for f in self._forms.values():
            while f.rowCount():
                f.removeRow(0)
        for name in ("Point loads", "Part loads"):
            _clear_layout(self.sections[name].body_layout)
        self.widgets.clear()
        self.initial.clear()
        self.loads_tbl = self.wedge_tbl = None

    def show_selection(self, ids: list[str]):
        plan = self.main.current_plan()
        u = units.current
        self._clear()
        self.objs = [plan.find(i) for i in ids] if plan else []
        self.objs = [o for o in self.objs if o is not None]
        if not self.objs:
            self._show_message("Nothing selected", EMPTY_HINT)
            return
        kinds = {type(o) for o in self.objs}
        if len(kinds) > 1:
            self._show_message(
                f"{len(self.objs)} mixed objects selected", "Select objects of one type to edit them together."
            )
            return
        o = self.objs[0]
        spec = {Slab: SLAB_FIELDS, Column: COLUMN_FIELDS, Beam: BEAM_FIELDS, Wall: WALL_FIELDS}[type(o)]
        kind = type(o).__name__
        self.title.setText(
            f"{kind} {o.mark}"
            if len(self.objs) == 1
            else f"{len(self.objs)} {kind.lower()}s – set value (only the fields you change are applied)"
        )
        for name, label, typ in spec:
            multi = len(self.objs) > 1
            if multi and (typ == "ro" or name in ("mark", "x", "y", "x1", "y1", "x2", "y2")):
                continue
            v = getattr(o, name)
            label = u.text(label)
            tip = field_tip(kind, name)
            form = self._forms[FIELD_SECTION.get(name, "General")]
            if typ == "ro":
                _add_row(form, label, QLabel(f"{v:.3f}"), tip)
                continue
            q = FIELD_QTY.get(name)
            if isinstance(typ, list):
                w = QComboBox()
                w.addItems(typ)
                w.setCurrentText(str(v))
            elif typ == "bool":
                w = QCheckBox()
                w.setChecked(bool(v))
            elif typ in ("float_opt", "int_opt"):
                w = QLineEdit("" if v is None else f"{v:g}")
                w.setPlaceholderText("auto")
            elif typ == "float":
                conv = q is not None and u.show(1.0, q) != 1.0
                w = _spin(u.show(v, q) if q else v, -1e5, 1e5, 4 if conv else 3, 0.05)
            else:
                w = QLineEdit(str(v))
            w.setToolTip(tip)
            if multi and any(getattr(x, name) != v for x in self.objs[1:]):
                label += " *"
                w.setToolTip(f"{tip} – values differ in the selection (showing the first); change it to set all")
            _add_row(form, label, w, w.toolTip())
            self._enter_applies(w)
            self.widgets[name] = (w, typ if not isinstance(typ, list) else "choice")
            self.initial[name] = self._read(name)
        if isinstance(o, Beam) and len(self.objs) == 1:
            self.loads_tbl = self._table(
                [u.text(h) for h in ("Dist (m)", "Dead (kN)", "Live (kN)", "Desc")],
                [[p.dist, p.dead, p.live, p.desc] for p in o.point_loads],
                [None, "force", "force", None],
                POINT_LOAD_TIPS,
            )
            self._load_section("Point loads", self.loads_tbl, [0, 0, 0, "P"], "point load")
            self.wedge_tbl = self._table(
                [u.text(h) for h in ("Start (m)", "Length (m)", "w1 (kN/m)", "w2 (kN/m)", "Case D/L", "Desc")],
                [[w.start, w.length, w.w1, w.w2, w.case, w.desc] for w in o.part_loads],
                [None, None, "line", "line", None, None],
                PART_LOAD_TIPS,
            )
            self._load_section("Part loads", self.wedge_tbl, [0, 1, 0, 0, "D", "W"], "part load")
        self.empty_lbl.hide()
        for name, sec in self.sections.items():
            if name in self._forms:
                sec.setVisible(self._forms[name].rowCount() > 0)
            elif name in ("Point loads", "Part loads"):
                sec.setVisible(self.loads_tbl is not None)
            else:
                sec.show()
        self._set_editable(True)
        self._update_info()

    def _enter_applies(self, w: QWidget) -> None:
        """Enter in a text or number field applies the edits (deferred: applying rebuilds the fields)."""
        edit = w.lineEdit() if isinstance(w, QDoubleSpinBox) else w if isinstance(w, QLineEdit) else None
        if edit is not None:
            edit.returnPressed.connect(lambda: QTimer.singleShot(0, self._apply))

    def _revert(self) -> None:
        self.show_selection([o.id for o in self.objs])

    def _load_section(self, name: str, t: QTableWidget, default: list, what: str) -> None:
        sec = self.sections[name]
        sec.body_layout.addWidget(t)
        sec.body_layout.addWidget(self._tbl_buttons(t, default, what, lambda: sec.set_badge(str(t.rowCount()))))
        sec.set_badge(str(t.rowCount()))

    def _table(self, headers, rows, qtys, tips=None):
        """Load table; kN based cells keep their engine value so an unedited cell is never re-converted."""
        u = units.current
        t = QTableWidget(len(rows), len(headers))
        t.setHorizontalHeaderLabels(headers)
        for c, tip in enumerate(tips or ()):
            t.horizontalHeaderItem(c).setToolTip(u.text(tip))
        t.verticalHeader().setVisible(False)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        hh.setMinimumSectionSize(40)
        t.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        row_h = t.verticalHeader().defaultSectionSize()
        t.setMinimumHeight(hh.sizeHint().height() + 3 * row_h + t.horizontalScrollBar().sizeHint().height() + 4)
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                if isinstance(v, (int, float)):
                    txt = f"{u.show(v, qtys[c]) if qtys[c] else v:g}"
                    it = QTableWidgetItem(txt)
                    it.setData(_ENGINE, float(v))
                    it.setData(_SHOWN, txt)
                else:
                    it = QTableWidgetItem(str(v))
                t.setItem(r, c, it)
        t.resizeColumnsToContents()
        return t

    @staticmethod
    def _cell(t: QTableWidget, r: int, c: int, qty: str | None = None) -> float:
        it = t.item(r, c)
        if it is None:
            return 0.0
        if it.data(_SHOWN) is not None and it.text() == it.data(_SHOWN):
            return float(it.data(_ENGINE))
        v = float(it.text())
        return units.current.parse(v, qty) if qty else v

    @staticmethod
    def _text(t: QTableWidget, r: int, c: int, default: str) -> str:
        it = t.item(r, c)
        return (it.text().strip() if it else "") or default

    def _tbl_buttons(self, t, default, what: str = "row", changed=None):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)

        def add():
            r = t.rowCount()
            t.insertRow(r)
            for c, v in enumerate(default):
                t.setItem(r, c, QTableWidgetItem(str(v)))
            if changed:
                changed()

        def remove():
            if t.currentRow() >= 0:
                t.removeRow(t.currentRow())
                if changed:
                    changed()

        h.addWidget(_tool_button("+ Row", f"Add a {what} (applied with Apply)", add))
        h.addWidget(_tool_button("− Row", f"Remove the selected {what}", remove))
        h.addStretch(1)
        return w

    def _update_info(self):
        o = self.objs[0] if self.objs else None
        plan = self.main.current_plan()
        if o is None or plan is None:
            return
        u = units.current
        res = self.main.plan_result(plan.name)
        if isinstance(o, Slab):
            txt = (
                f"Area {o.area:.2f} m² · Dead {u.fmt(o.dead, 'area')} · Live {u.fmt(o.live_load, 'area')} · "
                f"Total {u.fmt((o.dead + o.live_load) * o.area, 'force', 1)}"
            )
        elif isinstance(o, Beam):
            c = o.udl_components(plan.floor_height_above, plan.floor_type)
            txt = (
                f"Span {o.length:.3f} m · wall ht {o.wall_h(plan.floor_height_above, plan.floor_type):.2f} m\n"
                f"Self {u.show(c['self'], 'line'):.2f} + wall {u.show(c['wall'], 'line'):.2f} + plaster "
                f"{u.show(c['plaster'], 'line'):.2f} = {u.fmt(sum(c.values()), 'line')}"
            )
            if res and o.id in res.beams:
                br = res.beams[o.id]
                txt += (
                    f"\nTotal on beam: D {u.fmt(br.total('D'), 'force', 1)}, L {u.fmt(br.total('L'), 'force', 1)} "
                    f"(eq. UDL {u.fmt(br.equivalent_udl(), 'line')})"
                )
                txt += "\nSupports: " + ", ".join(f"{s.kind} @ {s.x:.2f} m" for s in br.supports)
        elif isinstance(o, Wall):
            txt = (
                f"Length {o.length:.3f} m · {o.thickness * 1000:.0f} mm thick · at {o.angle:.1f}° · "
                f"plan area {o.length * o.thickness:.3f} m²\n"
                "Stacked between levels by mark (like columns); modelled as a wide column with rigid links."
            )
        else:
            txt = f"{o.b * 1000:.0f} × {o.d * 1000:.0f} mm at ({o.x:.3f}, {o.y:.3f})"
            if res:
                cl = res.columns.get(o.id)
                if cl:
                    txt += f"\nLoad from this level: D {u.fmt(cl.dead, 'force', 1)}, L {u.fmt(cl.live, 'force', 1)}"
        self.info.setText(txt)

    def _read(self, name: str):
        """Current value of a field widget in display units (raises ValueError for unparsable text)."""
        w, typ = self.widgets[name]
        if typ == "choice":
            return w.currentText()
        if typ == "bool":
            return w.isChecked()
        if typ == "float":
            return float(w.value())
        if typ in ("float_opt", "int_opt"):
            t = w.text().strip()
            return None if not t else (int(float(t)) if typ == "int_opt" else float(t))
        return w.text().strip()

    def _apply(self):
        if not self.objs:
            return
        vals = {}
        try:
            for name in self.widgets:
                v = self._read(name)
                # untouched fields keep each object's own value: "Set Value" on a multi-selection, and no
                # display-unit round trip (kN -> t -> kN) for a value that was not edited
                if v == self.initial.get(name):
                    continue
                q = FIELD_QTY.get(name)
                vals[name] = units.current.parse(v, q) if q and v is not None else v
            loads = wedges = None
            if self.loads_tbl is not None:
                t = self.loads_tbl
                loads = [
                    PointLoad(
                        self._cell(t, r, 0),
                        self._cell(t, r, 1, "force"),
                        self._cell(t, r, 2, "force"),
                        self._text(t, r, 3, "P"),
                    )
                    for r in range(t.rowCount())
                ]
                if len(loads) > 20:
                    raise ValueError("maximum 20 point loads per beam")
            if self.wedge_tbl is not None:
                t = self.wedge_tbl
                wedges = [
                    PartLoad(
                        self._cell(t, r, 0),
                        self._cell(t, r, 1),
                        self._cell(t, r, 2, "line"),
                        self._cell(t, r, 3, "line"),
                        "L" if self._text(t, r, 4, "D").upper().startswith("L") else "D",
                        self._text(t, r, 5, "W"),  # keeps wizard tags such as "Stair ST1"
                    )
                    for r in range(t.rowCount())
                ]
        except ValueError as exc:
            QMessageBox.warning(self, "Properties", f"Invalid value: {exc}")
            return
        for k in ("b", "d", "thickness"):
            if k in vals and vals[k] is not None and vals[k] <= 0:
                QMessageBox.warning(self, "Properties", f"{k} must be positive")
                return
        o0 = self.objs[0]
        if isinstance(o0, Column) and ("b" in vals or "d" in vals) and vals.get("d", o0.d) < vals.get("b", o0.b):
            QMessageBox.information(
                self,
                "Properties",
                "Note: column depth is less than breadth (FrameWin convention "
                "keeps depth ≥ breadth; rotate the column instead).",
            )
        objs = list(self.objs)

        def fn():
            for o in objs:
                for k, v in vals.items():
                    setattr(o, k, v)
                if loads is not None:
                    o.point_loads = loads
                if wedges is not None:
                    o.part_loads = wedges

        self.main.mutate("Edit properties", fn)
        self.show_selection([o.id for o in objs])
