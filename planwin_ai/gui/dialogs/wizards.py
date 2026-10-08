"""Staircase and overhead water-tank wizards (loads tagged by name, so re-applying never double-counts)."""

from __future__ import annotations

import copy
from dataclasses import fields

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ... import units
from ...core.model import Project
from ...design.wizards import (
    Staircase,
    StairDesign,
    TankLoads,
    WaterTank,
    apply_staircase,
    apply_water_tank,
    design_staircase,
    tank_loads,
)
from .common import (
    GRADES,
    _buttons,
    _dspin,
    _marks_list,
    _qset,
    _qspin,
    _qval,
    _selected_marks,
    _set_marks,
    _sorted_marks,
)

NEW = "New…"


def _from_dict(cls, d: dict):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in names})


def remove_staircase(project: Project, name: str) -> None:
    """Delete a staircase definition and its tagged part loads on every plan."""
    tag = f"Stair {name}"
    for plan in project.plans:
        for b in plan.beams:
            b.part_loads = [pl for pl in b.part_loads if pl.desc != tag]
    project.stairs = [s for s in project.stairs if s.get("name") != name]


def remove_water_tank(project: Project, name: str) -> None:
    """Delete a water-tank definition and its tagged joint loads."""
    tag = f"Tank {name}"
    project.joint_loads = [j for j in project.joint_loads if j.get("source") != tag]
    project.water_tanks = [t for t in project.water_tanks if t.get("name") != name]


class _Wizard(QDialog):
    """Existing-definition combo, Remove button, validation on OK and the remove/apply switch."""

    kind = ""

    def __init__(self, parent, project: Project, existing: list[dict]):
        super().__init__(parent)
        self.p = project
        self.remove = False
        self.existing = QComboBox()
        self.existing.addItem(NEW)
        self.existing.addItems([d.get("name", "") for d in existing])
        self.del_btn = QPushButton(f"Remove {self.kind.lower()}")
        self.del_btn.setToolTip(f"Delete the selected {self.kind.lower()} and the loads it created")
        self.del_btn.clicked.connect(self._remove)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setMinimumHeight(90)

    def _top(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(f"{self.kind}:"))
        row.addWidget(self.existing, 1)
        row.addWidget(self.del_btn)
        return row

    def _old_name(self) -> str | None:
        t = self.existing.currentText()
        return None if t == NEW else t

    def _remove(self):
        if self._old_name() is None:
            return
        self.remove = True
        super().accept()

    def accept(self):
        try:
            self._apply_to(copy.deepcopy(self.p))  # validate on a scratch copy before closing
        except ValueError as exc:
            QMessageBox.warning(self, self.windowTitle(), str(exc))
            return
        super().accept()

    def _apply_to(self, project: Project):
        raise NotImplementedError

    def apply(self):
        """Perform the change on the project (run through ``MainWindow.mutate``)."""
        self._apply_to(self.p)


# =========================================================================== staircase
class StairDialog(_Wizard):
    """Staircase wizard: flight between two support beams, IS 456 cl 33 design and beam part loads."""

    kind = "Staircase"

    def __init__(self, parent, project: Project, plan_name: str):
        super().__init__(parent, project, project.stairs)
        self.setWindowTitle("Staircase wizard")
        self.resize(560, 640)
        self.design: StairDesign | None = None
        self.name = QLineEdit()
        self.plan = QComboBox()
        self.plan.addItems([p.name for p in project.plans])
        self.beams = _marks_list([])
        self.start, self.width = _dspin(0, 0, 100, 3, 0.1), _dspin(1.2, 0.3, 10, 3, 0.05)
        self.going, self.landing = _dspin(3.0, 0.3, 20, 3, 0.1), _dspin(1.2, 0, 5, 3, 0.05)
        self.riser, self.tread = _dspin(0.15, 0.05, 0.3, 3, 0.005), _dspin(0.30, 0.1, 0.5, 3, 0.01)
        self.waist = _dspin(0.2, 0.075, 0.5, 3, 0.01)
        self.live, self.finish = _qspin(3.0, "area", 0, 50, 2, 0.5), _qspin(1.0, "area", 0, 20, 2, 0.25)
        self.grade = QComboBox()
        self.grade.addItems(GRADES)
        f = QFormLayout()
        for lab, wd in (
            ("Name", self.name),
            ("Plan", self.plan),
            ("Support beams", self.beams),
            ("Flight starts along beams at (m)", self.start),
            ("Flight width (m)", self.width),
            ("Going, horizontal (m)", self.going),
            ("Landing width each end (m)", self.landing),
            ("Riser (m)", self.riser),
            ("Tread (m)", self.tread),
            ("Waist slab thickness (m)", self.waist),
            ("Live load", self.live),
            ("Finishes", self.finish),
            ("Grade", self.grade),
        ):
            f.addRow(lab, wd)
        lay = QVBoxLayout(self)
        lay.addLayout(self._top())
        lay.addLayout(f)
        lay.addWidget(self.preview)
        lay.addWidget(_buttons(self))
        self.plan.currentTextChanged.connect(self._plan_changed)
        self.existing.currentTextChanged.connect(self._load)
        for s in (self.start, self.width, self.going, self.landing, self.riser, self.tread, self.waist):
            s.valueChanged.connect(self._preview)
        self.live.valueChanged.connect(self._preview)
        self.finish.valueChanged.connect(self._preview)
        self.name.textChanged.connect(self._preview)
        self.grade.currentTextChanged.connect(self._preview)
        self.beams.itemSelectionChanged.connect(self._preview)
        self._fill(self._new(plan_name or (project.plans[0].name if project.plans else "")))

    def _new(self, plan: str) -> Staircase:
        return Staircase(name=self._free_name(), plan=plan, support_beams=[])  # user picks the beams

    def _free_name(self) -> str:
        used = {s.get("name") for s in self.p.stairs}
        return next(f"ST{i}" for i in range(1, 1000) if f"ST{i}" not in used)

    def _plan_changed(self, name: str, selected: list[str] | None = None):
        plan = self.p.plan(name)
        _set_marks(self.beams, _sorted_marks(plan.beams) if plan else [], selected or [])

    def _load(self, name: str):
        d = next((s for s in self.p.stairs if s.get("name") == name), None)
        self._fill(_from_dict(Staircase, d) if d else self._new(self.plan.currentText()))

    def _fill(self, st: Staircase):
        self.name.setText(st.name)
        self.plan.blockSignals(True)
        self.plan.setCurrentText(st.plan)
        self.plan.blockSignals(False)
        self._plan_changed(self.plan.currentText(), list(st.support_beams))
        for sp, v in (
            (self.start, st.start),
            (self.width, st.width),
            (self.going, st.going),
            (self.landing, st.landing),
            (self.riser, st.riser),
            (self.tread, st.tread),
            (self.waist, st.waist),
        ):
            sp.setValue(v)
        _qset(self.live, st.live, "area")
        _qset(self.finish, st.finish, "area")
        self.grade.setCurrentText(st.grade)
        self.del_btn.setEnabled(self._old_name() is not None)
        self._preview()

    def staircase(self) -> Staircase:
        return Staircase(
            name=self.name.text().strip() or "ST1",
            plan=self.plan.currentText(),
            support_beams=_selected_marks(self.beams),
            start=self.start.value(),
            width=self.width.value(),
            going=self.going.value(),
            landing=self.landing.value(),
            riser=self.riser.value(),
            tread=self.tread.value(),
            waist=self.waist.value(),
            live=_qval(self.live),
            finish=_qval(self.finish),
            grade=self.grade.currentText(),
        )

    def _preview(self, *_):
        u = units.current
        st = self.staircase()
        try:
            d = design_staircase(st, self.p.design.fy_main, self.p.design.slab_cover)
        except (ValueError, ZeroDivisionError) as exc:
            self.preview.setText(f"<span style='color:#dc2626'>{exc}</span>")
            return
        ok = "<b style='color:#16a34a'>OK</b>" if d.ok else "<b style='color:#dc2626'>NOT OK</b>"
        notes = "".join(f"<br>• {n}" for n in d.notes)
        beams = ", ".join(st.support_beams) or "<span style='color:#dc2626'>select the support beams</span>"
        self.preview.setText(
            f"w dead {u.fmt(d.w_dead, 'area')} · live {u.fmt(d.w_live, 'area')} · span {d.span:.2f} m<br>"
            f"Reaction on each beam ({beams}): D {u.fmt(d.reaction_dead, 'line')}, "
            f"L {u.fmt(d.reaction_live, 'line')}<br>"
            f"Mu {u.fmt(d.Mu, 'moment')}/m · main {d.main} · distribution {d.distribution} · {ok}{notes}"
        )

    def _apply_to(self, project: Project):
        old = self._old_name()
        if self.remove:
            if old:
                remove_staircase(project, old)
            return
        st = self.staircase()
        if not st.support_beams:
            raise ValueError("select the beams that support the flight")
        if old:
            remove_staircase(project, old)  # plan or name may have changed
        des = apply_staircase(project, st)
        if project is self.p:
            self.design = des


# =========================================================================== water tank
class TankDialog(_Wizard):
    """Overhead water tank: RCC tank + water weight shared by the supporting columns as joint loads."""

    kind = "Water tank"

    def __init__(self, parent, project: Project):
        super().__init__(parent, project, project.water_tanks)
        self.setWindowTitle("Overhead water tank")
        self.resize(480, 520)
        self.loads: TankLoads | None = None
        self.name = QLineEdit()
        self.capacity = _dspin(10000, 100, 1e7, 0, 500)
        self.capacity.setSuffix(" L")
        self.depth = _dspin(1.5, 0.3, 10, 2, 0.1)
        self.level = QComboBox()
        self.level.addItem("Top level", 0)
        for i, lv in enumerate(project.levels, start=1):
            self.level.addItem(f"{i}: {lv.name}", i)
        self.columns = _marks_list([])
        f = QFormLayout()
        for lab, wd in (
            ("Name", self.name),
            ("Capacity", self.capacity),
            ("Water depth (m)", self.depth),
            ("Supported at level", self.level),
            ("Supporting columns", self.columns),
        ):
            f.addRow(lab, wd)
        lay = QVBoxLayout(self)
        lay.addLayout(self._top())
        lay.addLayout(f)
        lay.addWidget(self.preview)
        lay.addWidget(_buttons(self))
        self.existing.currentTextChanged.connect(self._load)
        self.level.currentIndexChanged.connect(lambda *_: self._level_changed(_selected_marks(self.columns)))
        for w in (self.capacity, self.depth):
            w.valueChanged.connect(self._preview)
        self.name.textChanged.connect(self._preview)
        self.columns.itemSelectionChanged.connect(self._preview)
        self._fill(WaterTank(name=self._free_name(), columns=[]))

    def _free_name(self) -> str:
        used = {t.get("name") for t in self.p.water_tanks}
        return next(f"T{i}" for i in range(1, 1000) if f"T{i}" not in used)

    def _level_index(self) -> int:
        return int(self.level.currentData() or 0) or len(self.p.levels)

    def _level_changed(self, selected: list[str]):
        i = self._level_index()
        plan = self.p.plan(self.p.levels[i - 1].plan) if 1 <= i <= len(self.p.levels) else None
        _set_marks(self.columns, _sorted_marks(plan.columns) if plan else [], selected)

    def _load(self, name: str):
        d = next((t for t in self.p.water_tanks if t.get("name") == name), None)
        self._fill(_from_dict(WaterTank, d) if d else WaterTank(name=self._free_name(), columns=[]))

    def _fill(self, t: WaterTank):
        self.name.setText(t.name)
        self.capacity.setValue(t.capacity_l)
        self.depth.setValue(t.water_depth)
        self.level.blockSignals(True)
        self.level.setCurrentIndex(max(self.level.findData(int(t.level)), 0))
        self.level.blockSignals(False)
        self._level_changed(list(t.columns))
        self.del_btn.setEnabled(self._old_name() is not None)
        self._preview()

    def tank(self) -> WaterTank:
        return WaterTank(
            name=self.name.text().strip() or "T1",
            capacity_l=self.capacity.value(),
            water_depth=self.depth.value(),
            level=int(self.level.currentData() or 0),
            columns=_selected_marks(self.columns),
        )

    def _preview(self, *_):
        u = units.current
        try:
            t = tank_loads(self.tank(), len(self.p.levels))
        except ValueError as exc:
            self.preview.setText(f"<span style='color:#dc2626'>{exc}</span>")
            return
        self.preview.setText(
            f"Tank {t.side:.2f} × {t.side:.2f} m inside · water {u.fmt(t.water, 'force')} · "
            f"RCC tank {u.fmt(t.tank, 'force')}<br>"
            f"<b>{u.fmt(t.per_column, 'force')} on each of {len(self.tank().columns)} columns</b> "
            f"at level {t.level} (dead load, also in the seismic weight)"
        )

    def _apply_to(self, project: Project):
        old = self._old_name()
        if self.remove:
            if old:
                remove_water_tank(project, old)
            return
        if old:
            remove_water_tank(project, old)
        loads = apply_water_tank(project, self.tank())
        if project is self.p:
            self.loads = loads
