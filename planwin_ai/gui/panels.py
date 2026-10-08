"""Dock panels: project tree (plans + levels), properties editor and results tables."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.model import Beam, Column, Level, PartLoad, Plan, PointLoad, Slab, Wall

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


# =========================================================================== project
class ProjectPanel(QWidget):
    planChanged = Signal(str)

    def __init__(self, main: MainWindow):
        super().__init__()
        self.main = main
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        g1 = QGroupBox("Plans (PlanWin)")
        v1 = QVBoxLayout(g1)
        self.plans = QListWidget()
        self.plans.currentTextChanged.connect(self._plan_selected)
        v1.addWidget(self.plans)
        row = QHBoxLayout()
        for txt, fn, tip in (
            ("New", self._new_plan, "Create an empty plan"),
            ("Copy", self._copy_plan, "Save current plan under a new name (PlanWin Save As)"),
            ("Rename", self._rename_plan, "Rename plan (levels are updated)"),
            ("Delete", self._delete_plan, "Delete plan"),
        ):
            b = QPushButton(txt)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        v1.addLayout(row)
        form = QFormLayout()
        self.ftype = QComboBox()
        self.ftype.addItems(["typical", "ground", "roof"])
        self.ftype.activated.connect(self._plan_props)
        self.fha = _spin(3.0, 0, 50, 3, 0.1)
        self.fha.editingFinished.connect(self._plan_props)
        form.addRow("Floor type", self.ftype)
        form.addRow("Floor height above (m)", self.fha)
        v1.addLayout(form)
        lay.addWidget(g1, 2)

        g2 = QGroupBox("Levels (FrameWin) – bottom to top")
        v2 = QVBoxLayout(g2)
        self.levels = QTableWidget(0, 5)
        self.levels.setHorizontalHeaderLabels(["Level", "Plan", "Ht (m)", "Grade", "LL red%"])
        self.levels.setToolTip(
            "Ht = storey height below this level. LL red% = IS 875-2 live load reduction for column sizing."
        )
        self.levels.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.levels.verticalHeader().setVisible(False)
        self.levels.itemChanged.connect(self._level_item_changed)
        v2.addWidget(self.levels)
        row2 = QHBoxLayout()
        for txt, fn in (
            ("+ Level", self._add_level),
            ("− Level", self._del_level),
            ("▲", lambda: self._move(-1)),
            ("▼", lambda: self._move(1)),
            ("Fill ↑", self._fill_up),
        ):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            row2.addWidget(b)
        v2.addLayout(row2)
        self.height_lbl = QLabel()
        v2.addWidget(self.height_lbl)
        lay.addWidget(g2, 3)
        self._loading = False

    # ----------------------------------------------------------- refresh
    def refresh(self):
        pr = self.main.project
        cur = self.main.current_plan_name
        self.plans.blockSignals(True)
        self.plans.clear()
        for p in pr.plans:
            it = QListWidgetItem(f"{p.name}")
            it.setData(Qt.UserRole, p.name)
            it.setToolTip(f"{p.floor_type}: {len(p.slabs)} slabs, {len(p.columns)} columns, {len(p.beams)} beams")
            self.plans.addItem(it)
            if p.name == cur:
                self.plans.setCurrentItem(it)
        self.plans.blockSignals(False)
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
            self.levels.setItem(r, 0, QTableWidgetItem(lv.name))
            cb = QComboBox()
            cb.addItems(names)
            cb.setCurrentText(lv.plan)
            cb.currentTextChanged.connect(lambda t, row=r: self._level_plan(row, t))
            self.levels.setCellWidget(r, 1, cb)
            self.levels.setItem(r, 2, QTableWidgetItem(f"{lv.height:g}"))
            gcb = QComboBox()
            gcb.addItems(GRADES)
            gcb.setCurrentText(lv.grade)
            gcb.currentTextChanged.connect(lambda t, row=r: self._level_grade(row, t))
            self.levels.setCellWidget(r, 3, gcb)
            self.levels.setItem(r, 4, QTableWidgetItem(f"{lv.live_reduction:g}"))
        self._loading = False
        self.height_lbl.setText(f"Total height {pr.elevations()[-1]:.2f} m · {len(pr.levels)} levels")

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
                for o in p.slabs + p.columns + p.beams:
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
WALL_FIELDS = [
    ("mark", "Mark", "str"),
    ("thickness", "Thickness (m)", "float"),
    ("grade", "Grade", GRADES),
    ("x1", "Start X", "float"),
    ("y1", "Start Y", "float"),
    ("x2", "End X", "float"),
    ("y2", "End Y", "float"),
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


class PropertiesPanel(QWidget):
    def __init__(self, main: MainWindow):
        super().__init__()
        self.main = main
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        self.title = QLabel("Nothing selected")
        self.title.setStyleSheet("font-weight:600; font-size:11pt")
        outer.addWidget(self.title)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.inner = QWidget()
        self.form = QFormLayout(self.inner)
        scroll.setWidget(self.inner)
        outer.addWidget(scroll, 1)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(self.info)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self._apply)
        outer.addWidget(self.apply_btn)
        self.widgets: dict[str, tuple[Any, str]] = {}
        self.initial: dict[str, Any] = {}  # value of each field when the selection was shown
        self.objs: list = []
        self.loads_tbl: QTableWidget | None = None
        self.wedge_tbl: QTableWidget | None = None
        self.apply_btn.setEnabled(False)

    def show_selection(self, ids: list[str]):
        plan = self.main.current_plan()
        while self.form.rowCount():
            self.form.removeRow(0)
        self.widgets.clear()
        self.initial.clear()
        self.loads_tbl = self.wedge_tbl = None
        self.objs = [plan.find(i) for i in ids] if plan else []
        self.objs = [o for o in self.objs if o is not None]
        if not self.objs:
            self.title.setText("Nothing selected")
            self.info.setText(
                "Select a slab, beam or column on the plan. Multi-select (Shift / window) to set values "
                "for many elements at once – like PlanWin 'Set Value'."
            )
            self.apply_btn.setEnabled(False)
            return
        kinds = {type(o) for o in self.objs}
        if len(kinds) > 1:
            self.title.setText(f"{len(self.objs)} mixed objects selected")
            self.info.setText("Select objects of one type to edit them together.")
            self.apply_btn.setEnabled(False)
            return
        o = self.objs[0]
        spec = (
            SLAB_FIELDS
            if isinstance(o, Slab)
            else COLUMN_FIELDS
            if isinstance(o, Column)
            else WALL_FIELDS
            if isinstance(o, Wall)
            else BEAM_FIELDS
        )
        kind = type(o).__name__
        self.title.setText(
            f"{kind} {o.mark}"
            if len(self.objs) == 1
            else f"{len(self.objs)} {kind.lower()}s – set value (only the fields you change are applied)"
        )
        for name, label, typ in spec:
            if len(self.objs) > 1 and name in ("mark", "x", "y", "x1", "y1", "x2", "y2"):
                continue
            v = getattr(o, name)
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
                w = _spin(v, -1e5, 1e5, 3, 0.05)
            else:
                w = QLineEdit(str(v))
            if len(self.objs) > 1 and any(getattr(x, name) != v for x in self.objs[1:]):
                label += " *"
                w.setToolTip("Values differ in the selection (showing the first one). Change it to set all.")
            self.form.addRow(label, w)
            self.widgets[name] = (w, typ if not isinstance(typ, list) else "choice")
            self.initial[name] = self._read(name)
        if isinstance(o, Beam) and len(self.objs) == 1:
            self.loads_tbl = self._table(
                ["Dist (m)", "Dead (kN)", "Live (kN)", "Desc"],
                [[p.dist, p.dead, p.live, p.desc] for p in o.point_loads],
            )
            self.form.addRow(QLabel("Point loads (up to 20)"))
            self.form.addRow(self.loads_tbl)
            self.form.addRow(self._tbl_buttons(self.loads_tbl, [0, 0, 0, "P"]))
            self.wedge_tbl = self._table(
                ["Start (m)", "Length (m)", "w1 (kN/m)", "w2 (kN/m)", "Case D/L"],
                [[w.start, w.length, w.w1, w.w2, w.case] for w in o.part_loads],
            )
            self.form.addRow(QLabel("Wedge / part loads"))
            self.form.addRow(self.wedge_tbl)
            self.form.addRow(self._tbl_buttons(self.wedge_tbl, [0, 1, 0, 0, "D"]))
        self.apply_btn.setEnabled(True)
        self._update_info()

    def _table(self, headers, rows):
        t = QTableWidget(len(rows), len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        t.setMinimumHeight(110)
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                t.setItem(r, c, QTableWidgetItem(f"{v:g}" if isinstance(v, float) else str(v)))
        return t

    def _tbl_buttons(self, t, default):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        a = QPushButton("+ Row")
        d = QPushButton("− Row")

        def add():
            r = t.rowCount()
            t.insertRow(r)
            for c, v in enumerate(default):
                t.setItem(r, c, QTableWidgetItem(str(v)))

        a.clicked.connect(add)
        d.clicked.connect(lambda: t.removeRow(t.currentRow()) if t.currentRow() >= 0 else None)
        h.addWidget(a)
        h.addWidget(d)
        h.addStretch(1)
        return w

    def _update_info(self):
        o = self.objs[0] if self.objs else None
        plan = self.main.current_plan()
        if o is None or plan is None:
            return
        res = self.main.plan_result(plan.name)
        if isinstance(o, Slab):
            txt = (
                f"Area {o.area:.2f} m² · Dead {o.dead:.2f} kN/m² · Live {o.live_load:.2f} kN/m² · "
                f"Total {(o.dead + o.live_load) * o.area:.1f} kN"
            )
        elif isinstance(o, Beam):
            c = o.udl_components(plan.floor_height_above, plan.floor_type)
            txt = (
                f"Span {o.length:.3f} m · wall ht {o.wall_h(plan.floor_height_above, plan.floor_type):.2f} m\n"
                f"Self {c['self']:.2f} + wall {c['wall']:.2f} + plaster {c['plaster']:.2f} = {sum(c.values()):.2f} kN/m"
            )
            if res and o.id in res.beams:
                br = res.beams[o.id]
                txt += (
                    f"\nTotal on beam: D {br.total('D'):.1f} kN, L {br.total('L'):.1f} kN "
                    f"(eq. UDL {br.equivalent_udl():.2f} kN/m)"
                )
                txt += "\nSupports: " + ", ".join(f"{s.kind} @ {s.x:.2f} m" for s in br.supports)
        elif isinstance(o, Wall):
            txt = f"Shear wall {o.length:.3f} m long × {o.thickness * 1000:.0f} mm, {o.grade}"
        else:
            txt = f"{o.b * 1000:.0f} × {o.d * 1000:.0f} mm at ({o.x:.3f}, {o.y:.3f})"
            if res:
                cl = res.columns.get(o.id)
                if cl:
                    txt += f"\nLoad from this level: D {cl.dead:.1f} kN, L {cl.live:.1f} kN"
        self.info.setText(txt)

    def _read(self, name: str):
        """Current value of a field widget (raises ValueError for unparsable text)."""
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
                # multi-select "Set Value": untouched fields keep each object's own value
                if len(self.objs) == 1 or v != self.initial.get(name):
                    vals[name] = v
            loads = wedges = None
            if self.loads_tbl is not None:
                loads = []
                for r in range(self.loads_tbl.rowCount()):
                    cell = [self.loads_tbl.item(r, c).text() if self.loads_tbl.item(r, c) else "0" for c in range(4)]
                    loads.append(PointLoad(float(cell[0]), float(cell[1]), float(cell[2]), cell[3] or "P"))
                if len(loads) > 20:
                    raise ValueError("maximum 20 point loads per beam")
            if self.wedge_tbl is not None:
                wedges = []
                for r in range(self.wedge_tbl.rowCount()):
                    cell = [self.wedge_tbl.item(r, c).text() if self.wedge_tbl.item(r, c) else "0" for c in range(5)]
                    case = "L" if cell[4].strip().upper().startswith("L") else "D"
                    wedges.append(PartLoad(float(cell[0]), float(cell[1]), float(cell[2]), float(cell[3]), case))
        except ValueError as exc:
            QMessageBox.warning(self, "Properties", f"Invalid value: {exc}")
            return
        for k in ("b", "d", "thickness"):
            if k in vals and vals[k] is not None and vals[k] <= 0:
                QMessageBox.warning(self, "Properties", f"{k} must be positive")
                return
        if isinstance(self.objs[0], Column) and "b" in vals and "d" in vals and vals["d"] < vals["b"]:
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


# =========================================================================== results
class ResultsPanel(QTabWidget):
    issueActivated = Signal(object)

    def __init__(self, main: MainWindow):
        super().__init__()
        self.main = main
        self.tables: dict[str, QTableWidget] = {}
        for name in (
            "Issues",
            "Column loads",
            "Beam loads",
            "Columns",
            "Beams",
            "Footings",
            "Slabs",
            "Lateral",
            "Drift",
            "BOQ & cost",
        ):
            t = QTableWidget()
            t.setEditTriggers(QAbstractItemView.NoEditTriggers)
            t.setSelectionBehavior(QAbstractItemView.SelectRows)
            t.setAlternatingRowColors(True)
            t.verticalHeader().setVisible(False)
            t.setSortingEnabled(True)
            self.tables[name] = t
            self.addTab(t, name)
        self.tables["Issues"].cellDoubleClicked.connect(self._issue_clicked)
        self._issues: list = []

    def _fill(self, name: str, headers: list[str], rows: list[list], bad_col: int | None = None):
        t = self.tables[name]
        t.setSortingEnabled(False)
        t.clear()
        t.setColumnCount(len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            bad = bad_col is not None and row[bad_col] in ("NO", False, "error")
            for c, v in enumerate(row):
                it = QTableWidgetItem()
                if isinstance(v, float):
                    it.setData(Qt.DisplayRole, round(v, 3))
                else:
                    it.setData(Qt.DisplayRole, v if isinstance(v, int) else str(v))
                if bad:
                    it.setBackground(QColor(220, 38, 38, 60))
                t.setItem(r, c, it)
        t.resizeColumnsToContents()
        t.setSortingEnabled(name != "Issues")  # issue rows map to objects by index

    def _clear(self, *names: str):
        for n in names:
            t = self.tables[n]
            t.setSortingEnabled(False)
            t.clear()
            t.setRowCount(0)
            t.setColumnCount(0)

    def refresh(self):
        """Rebuild every table. Tables whose results were invalidated by an edit are
        emptied so that out-of-date design results are never shown next to a changed model."""
        m = self.main
        plan = m.current_plan()
        issues = []
        self._issues = []
        res = m.plan_result(plan.name) if plan else None
        if not res:
            self._clear("Column loads", "Beam loads")
        if plan:
            if res:
                for i in res.issues:
                    issues.append([plan.name, i.level, i.kind, i.message])
                    self._issues.append(i)
                self._fill(
                    "Column loads",
                    ["Column", "Dead kN", "Live kN", "Total kN", "From beams"],
                    [[c.mark, c.dead, c.live, c.total, ", ".join(p[0] for p in c.parts)] for c in res.columns.values()],
                )
                bmap = {b.id: b for b in plan.beams}
                rows = []
                for bid, br in res.beams.items():
                    b = bmap.get(bid)
                    if b:
                        rows.append(
                            [
                                b.mark,
                                br.length,
                                br.total("D"),
                                br.total("L"),
                                br.equivalent_udl(),
                                " / ".join(f"{s.kind[0].upper()}@{s.x:.2f}" for s in br.supports),
                            ]
                        )
                self._fill(
                    "Beam loads", ["Beam", "Span m", "Total D kN", "Total L kN", "Eq. UDL kN/m", "Supports"], rows
                )
        fm = m.frame_model()
        if not fm:
            self._clear("Lateral")
        if fm:
            for i in fm.issues:
                issues.append(["Frame", i.level, i.kind, i.message])
                self._issues.append(i)
            rows = [[k, "Seismic", r.T, r.sa_g, r.Ah, r.W, r.Vb] for k, r in fm.seismic.items()]
            rows += [[k, "Wind", "", "", "", "", sum(r.forces)] for k, r in fm.wind.items()]
            self._fill("Lateral", ["Case", "Type", "T (s)", "Sa/g", "Ah", "W (kN)", "Base shear (kN)"], rows)
        self._fill("Issues", ["Where", "Level", "Type", "Message"], issues, bad_col=1)
        rep = m.design_report()
        if not rep:
            self._clear("Columns", "Beams", "Footings", "Slabs", "Drift", "BOQ & cost")
        if rep:
            self._fill(
                "Columns",
                ["Level", "Col", "b", "D", "Pu kN", "Mux", "Muy", "p %", "Bars", "Ties", "Ratio", "OK", "Governing"],
                [
                    [
                        c.level,
                        c.mark,
                        c.b,
                        c.d,
                        c.Pu,
                        c.Mux,
                        c.Muy,
                        c.steel_pct,
                        c.bars,
                        c.ties,
                        c.utilisation,
                        "YES" if c.ok else "NO",
                        c.governing,
                    ]
                    for c in rep.columns
                ],
                bad_col=11,
            )
            self._fill(
                "Beams",
                [
                    "Level",
                    "Beam",
                    "b",
                    "D",
                    "Span",
                    "Mu+",
                    "Mu- L",
                    "Mu- R",
                    "Vu",
                    "Bottom",
                    "Top L",
                    "Top R",
                    "Stirrups",
                    "OK",
                    "Notes",
                ],
                [
                    [
                        b.level,
                        b.mark,
                        b.b,
                        b.d,
                        b.span,
                        b.M_sag,
                        b.M_hog_l,
                        b.M_hog_r,
                        b.V_max,
                        b.bottom,
                        b.top_l,
                        b.top_r,
                        b.stirrups,
                        "YES" if b.ok else "NO",
                        "; ".join(b.notes),
                    ]
                    for b in rep.beams
                ],
                bad_col=13,
            )
            self._fill(
                "Footings",
                ["Col", "P kN", "L m", "B m", "D m", "Bars ∥L", "Bars ∥B", "q kN/m²", "OK"],
                [
                    [f.mark, f.P_service, f.L, f.B, f.D, f.bars_L, f.bars_B, f.q, "YES" if f.ok else "NO"]
                    for f in rep.footings
                ],
                bad_col=8,
            )
            self._fill(
                "Slabs",
                ["Plan", "Slab", "lx", "ly", "Type", "D mm", "Short", "Long", "Top", "OK", "Notes"],
                [
                    [
                        pn,
                        s.mark,
                        s.lx,
                        s.ly,
                        s.kind,
                        s.D_mm,
                        s.ast_x,
                        s.ast_y,
                        s.ast_neg,
                        "YES" if s.ok else "NO",
                        "; ".join(s.notes),
                    ]
                    for pn, s in rep.slabs
                ],
                bad_col=9,
            )
            self._fill(
                "Drift",
                ["Case", "Level", "Drift mm", "Ratio", "OK"],
                [[d["case"], d["level"], d["drift_mm"], d["ratio"], "YES" if d["ok"] else "NO"] for d in rep.drifts],
                bad_col=4,
            )
            lines = [list(x) for x in rep.boq.get("lines", [])]
            lines.append(["TOTAL", "", "", "", rep.boq.get("cost", 0.0)])
            lines.append(["Steel/concrete ratio", "kg/m³", rep.boq.get("steel_per_m3", 0.0), "", ""])
            self._fill("BOQ & cost", ["Item", "Unit", "Quantity", "Rate ₹", "Amount ₹"], lines)

    def _issue_clicked(self, row, _col):
        if 0 <= row < len(self._issues):
            self.issueActivated.emit(self._issues[row])
