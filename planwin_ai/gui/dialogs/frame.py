"""FrameWin dialogs: auto-size, column sizes by level, joint loads and the copy-floor tool."""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ...core.model import Level, Plan, Project, new_id
from .common import GRADES, _buttons, _dspin


# =========================================================================== autosize
class AutoSizeDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Auto-size columns (FrameWin AUTOSIZE)")
        f = QFormLayout(self)
        self.breadth = _dspin(0.0, 0, 2, 3, 0.05)
        self.breadth.setSpecialValueText("keep plan breadth")
        self.pct = _dspin(1.0, 0.8, 4, 2, 0.1)
        self.same = QCheckBox("Same size on all floors")
        self.same.setChecked(True)
        self.step = _dspin(0.05, 0, 0.5, 3, 0.05)
        self.mf = _dspin(1.25, 1.0, 2.0, 2, 0.05)
        self.inc = _dspin(0.0, 0, 0.3, 3, 0.025)
        for lab, wd in (
            ("Breadth b (m)", self.breadth),
            ("Assumed steel %", self.pct),
            ("", self.same),
            ("Max reduction per floor (m)", self.step),
            ("Moment allowance factor", self.mf),
            ("Increase below ground, each side (m)", self.inc),
        ):
            f.addRow(lab, wd)
        info = QLabel("Depth from Pu·factor ≤ 0.4 fck Ac + 0.67 fy Asc using the cumulative PlanWin column loads.")
        info.setWordWrap(True)
        f.addRow(info)
        f.addRow(_buttons(self))


# =========================================================================== column sizes
class ColumnSizesDialog(QDialog):
    """Per-level column sizes (FrameWin 'Size' grid) with copy up/down."""

    def __init__(self, parent, project: Project):
        super().__init__(parent)
        self.p = project
        self.setWindowTitle("Column sizes by level (b × d in m)")
        self.resize(720, 480)
        marks = []
        self.default = {}
        for i, lv in enumerate(project.levels, start=1):
            plan = project.plan(lv.plan)
            for c in plan.columns if plan else []:
                if c.mark not in marks:
                    marks.append(c.mark)
                self.default[(c.mark, i)] = c
        self.marks = sorted(marks, key=lambda m: (len(m), m))
        n = len(project.levels)
        self.t = QTableWidget(len(self.marks), n)
        self.t.setHorizontalHeaderLabels([lv.name for lv in project.levels])
        self.t.setVerticalHeaderLabels(self.marks)
        for r, mk in enumerate(self.marks):
            for c in range(n):
                col = self.default.get((mk, c + 1))
                if col is None:
                    it = QTableWidgetItem("–")
                    it.setFlags(Qt.ItemIsEnabled)
                else:
                    b, d, _ = project.column_size(mk, c + 1, col)
                    it = QTableWidgetItem(f"{b:g} x {d:g}")
                self.t.setItem(r, c, it)
        lay = QVBoxLayout(self)
        lay.addWidget(
            QLabel(
                "Edit as 'b x d'. Select a cell and use the buttons to copy to all levels above/below or the whole row."
            )
        )
        lay.addWidget(self.t)
        row = QHBoxLayout()
        for txt, fn in (
            ("Copy ⇈ above", lambda: self._copy(1)),
            ("Copy ⇊ below", lambda: self._copy(-1)),
            ("Copy to all columns", self._copy_all),
        ):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(_buttons(self))

    def _copy(self, d):
        r, c = self.t.currentRow(), self.t.currentColumn()
        if r < 0 or c < 0:
            return
        v = self.t.item(r, c).text()
        rng = range(c + 1, self.t.columnCount()) if d > 0 else range(0, c)
        for k in rng:
            if self.t.item(r, k).text() != "–":
                self.t.item(r, k).setText(v)

    def _copy_all(self):
        r, c = self.t.currentRow(), self.t.currentColumn()
        if r < 0 or c < 0:
            return
        v = self.t.item(r, c).text()
        for k in range(self.t.rowCount()):
            if self.t.item(k, c).text() != "–":
                self.t.item(k, c).setText(v)

    def apply(self):
        for r, mk in enumerate(self.marks):
            for c in range(self.t.columnCount()):
                txt = self.t.item(r, c).text().lower().replace("×", "x")
                col = self.default.get((mk, c + 1))
                if col is None or "x" not in txt:
                    continue
                b, d = (float(v) for v in txt.split("x")[:2])
                if b > 0 and d > 0:
                    _, _, ang = self.p.column_size(mk, c + 1, col)
                    self.p.set_column_size(mk, c + 1, b, d, ang)


# =========================================================================== joint loads
class JointLoadDialog(QDialog):
    """Water-tank and other concentrated loads on column tops (FrameWin)."""

    def __init__(self, parent, project: Project):
        super().__init__(parent)
        self.p = project
        self.setWindowTitle("Joint loads (e.g. water tank)")
        self.resize(520, 360)
        self.t = QTableWidget(len(project.joint_loads), 4)
        self.t.setHorizontalHeaderLabels(["Level #", "Column mark", "Fz down (kN)", "Case D/L"])
        self.t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for r, jl in enumerate(project.joint_loads):
            for c, k in enumerate(("level", "mark", "fz", "case")):
                self.t.setItem(r, c, QTableWidgetItem(str(jl.get(k, ""))))
        lay = QVBoxLayout(self)
        lay.addWidget(
            QLabel(f"Level # counts from 1 (= {project.levels[0].name if project.levels else 'first level'}).")
        )
        lay.addWidget(self.t)
        row = QHBoxLayout()
        a = QPushButton("+ Row")
        d = QPushButton("− Row")
        a.clicked.connect(lambda: self._add())
        d.clicked.connect(lambda: self.t.removeRow(self.t.currentRow()))
        row.addWidget(a)
        row.addWidget(d)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(_buttons(self))

    def _add(self):
        r = self.t.rowCount()
        self.t.insertRow(r)
        for c, v in enumerate((len(self.p.levels), "C1", "150", "D")):
            self.t.setItem(r, c, QTableWidgetItem(v))

    def apply(self):
        out = []
        for r in range(self.t.rowCount()):
            try:
                out.append(
                    {
                        "level": int(self.t.item(r, 0).text()),
                        "mark": self.t.item(r, 1).text().strip(),
                        "fz": float(self.t.item(r, 2).text()),
                        "case": self.t.item(r, 3).text().strip() or "D",
                    }
                )
            except (ValueError, AttributeError):
                continue
        self.p.joint_loads = out


# =========================================================================== copy floor
def duplicate_plan(project: Project, src: Plan, name: str) -> Plan:
    """Deep copy of ``src`` with fresh object ids; marks are kept so FrameWin stacks the members."""
    p = copy.deepcopy(src)
    p.name = name
    for o in p.slabs + p.columns + p.beams + p.walls:
        o.id = new_id()
    return project.add_plan(p)


class CopyFloorsDialog(QDialog):
    """Copy floor: duplicate a plan under a new name and/or add storeys on top that use a plan."""

    COPY = "‹the duplicated plan›"

    def __init__(self, parent, project: Project, plan_name: str):
        super().__init__(parent)
        self.p = project
        self.new_plan: str | None = None  # name of the duplicated plan after apply()
        self.setWindowTitle("Copy floor")
        names = [pl.name for pl in project.plans]
        top = project.levels[-1] if project.levels else None
        self.src = QComboBox()
        self.src.addItems(names)
        self.src.setCurrentText(plan_name)
        self.dup = QCheckBox("Duplicate the plan as")
        self.dup.setChecked(True)
        self.dup_name = QLineEdit(self._free(f"{self.src.currentText()} copy"))
        self.add = QCheckBox("Add levels on top")
        self.n = QSpinBox()
        self.n.setRange(1, 100)
        self.lv_plan = QComboBox()
        self.lv_plan.addItems([self.COPY, *names])
        self.lv_plan.setCurrentText(plan_name)
        self.height = _dspin(top.height if top else 3.0, 1.0, 20, 3, 0.1)
        self.grade = QComboBox()
        self.grade.addItems(GRADES)
        self.grade.setCurrentText(top.grade if top else "M25")
        f = QFormLayout(self)
        f.addRow("Source plan", self.src)
        f.addRow(self.dup, self.dup_name)
        f.addRow(self.add)
        f.addRow("Number of new levels", self.n)
        f.addRow("Plan at the new levels", self.lv_plan)
        f.addRow("Storey height (m)", self.height)
        f.addRow("Concrete grade", self.grade)
        note = QLabel(
            "Duplicated plans get new object ids but keep every mark, so columns and walls stack with the "
            "floors below. New levels are added above the current top level and named 'Floor n' (n = level number)."
        )
        note.setWordWrap(True)
        f.addRow(note)
        f.addRow(_buttons(self))
        self.src.currentTextChanged.connect(lambda t: self.dup_name.setText(self._free(f"{t} copy")))

    def _free(self, base: str) -> str:
        name, i = base, 2
        while self.p.plan(name):
            name, i = f"{base} ({i})", i + 1
        return name

    def apply(self):
        pr = self.p
        src = pr.plan(self.src.currentText())
        if src is None:
            raise ValueError("choose a source plan")
        if self.dup.isChecked():
            name = self.dup_name.text().strip() or f"{src.name} copy"
            if pr.plan(name):
                raise ValueError(f"a plan named '{name}' already exists")
            self.new_plan = duplicate_plan(pr, src, name).name
        if self.add.isChecked():
            lp = self.lv_plan.currentText()
            if lp == self.COPY:
                lp = self.new_plan or src.name
            if pr.plan(lp) is None:
                raise ValueError(f"plan '{lp}' not found")
            used = {lv.name for lv in pr.levels}
            k = len(pr.levels) + 1  # level number
            for _ in range(self.n.value()):
                while f"Floor {k}" in used:
                    k += 1
                used.add(f"Floor {k}")
                pr.levels.append(Level(f"Floor {k}", lp, self.height.value(), self.grade.currentText()))
