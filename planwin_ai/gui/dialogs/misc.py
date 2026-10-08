"""Templates, move/copy, mirror, licence, about and grid lines."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ... import APP_NAME, COMPANY, __version__
from ...ai.templates import TEMPLATES, legacy_samples
from ...core.model import Plan, Project
from .common import _buttons, _dspin


# =========================================================================== templates
class TemplateDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("New from template")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<b>Parametric templates</b> – fully editable, analysis-ready models"))
        self.list = QListWidget()
        for t in TEMPLATES:
            it = QListWidgetItem(f"{t.title}\n    {t.description}")
            it.setData(Qt.UserRole, ("template", t.key))
            self.list.addItem(it)
        for s in legacy_samples():
            it = QListWidgetItem(f"Legacy PlanWin sample: {s}\n    Imported from the original PlanWin sample set")
            it.setData(Qt.UserRole, ("legacy", s))
            self.list.addItem(it)
        self.list.setCurrentRow(1)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        lay.addWidget(self.list)
        lay.addWidget(_buttons(self))

    def choice(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None


# =========================================================================== move / mirror
class MoveCopyDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Move / copy selection")
        f = QFormLayout(self)
        self.dx, self.dy = _dspin(0, -1e4, 1e4, 3, 0.5), _dspin(0, -1e4, 1e4, 3, 0.5)
        self.copy = QCheckBox("Copy (keep originals)")
        self.copy.setChecked(True)
        self.n = QSpinBox()
        self.n.setRange(1, 50)
        f.addRow("ΔX (m)", self.dx)
        f.addRow("ΔY (m)", self.dy)
        f.addRow(self.copy)
        f.addRow("Number of copies", self.n)
        f.addRow(_buttons(self))


class MirrorDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Mirror selection")
        f = QFormLayout(self)
        self.vertical = QRadioButton("About vertical line X = …")
        self.vertical.setChecked(True)
        self.horizontal = QRadioButton("About horizontal line Y = …")
        self.at = _dspin(0, -1e4, 1e4, 3, 0.5)
        self.copy = QCheckBox("Keep originals (mirror copy)")
        self.copy.setChecked(True)
        f.addRow(self.vertical)
        f.addRow(self.horizontal)
        f.addRow("Line position (m)", self.at)
        f.addRow(self.copy)
        f.addRow(_buttons(self))


# =========================================================================== licence & about
class LicenseDialog(QDialog):
    def __init__(self, parent, state_label: str):
        super().__init__(parent)
        self.setWindowTitle("Licence")
        self.resize(520, 340)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>Status:</b> {state_label}"))
        from ...licensing.license import machine_code

        code = QLabel(f"<b>Machine code:</b> {machine_code()} (quote it when ordering a licence)")
        code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(code)
        lay.addWidget(QLabel("Paste the licence text you received from Computer Help, or load the .lic file:"))
        self.text = QPlainTextEdit()
        lay.addWidget(self.text)
        b = QPushButton("Load licence file…")
        b.clicked.connect(self._load)
        lay.addWidget(b)
        lay.addWidget(QLabel("Sales & support: support@buildingsoftware.in · www.buildingsoftware.in"))
        lay.addWidget(_buttons(self))

    def _load(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Licence file", "", "Licence (*.lic *.json);;All files (*)")
        if fn:
            with open(fn, encoding="utf-8") as f:
                self.text.setPlainText(f.read())


def about_text(license_label: str) -> str:
    return (
        f"<h2>{APP_NAME} {__version__}</h2>"
        f"<p>AI-assisted structural pre-processor, analysis and IS-code design for RCC framed buildings.<br>"
        f"Successor to PlanWin / FrameWin by {COMPANY}.</p>"
        "<p>Codes: IS 456:2000 · IS 875 (Parts 1–3) · IS 1893 (Part 1):2016<br>"
        "Exports: STAAD.Pro (.std) · ETABS (.e2k) · DXF 2D/3D · Excel · PDF</p>"
        f"<p><b>{license_label}</b></p>"
        "<p style='color:gray'>Results are design aids and must be verified by a qualified structural engineer.</p>"
        f"<p>© {COMPANY} / Building Software · www.buildingsoftware.in</p>"
    )


# =========================================================================== grid lines
def _letters(i: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA …"""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _cluster(values: list[float], tol: float = 0.05) -> list[float]:
    """Sorted unique values (1 mm) with lines closer than ``tol`` merged at their mean."""
    out: list[list[float]] = []
    for v in sorted({round(x, 3) for x in values}):
        if out and v - out[-1][-1] < tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [round(sum(g) / len(g), 3) for g in out]


def grids_from_columns(plan: Plan) -> list[dict]:
    """Grid lines through the columns: x = const lines named 1, 2, 3 … and y = const lines A, B, C …"""
    xs = _cluster([c.x for c in plan.columns])
    ys = _cluster([c.y for c in plan.columns])
    return [{"name": str(i + 1), "axis": "x", "pos": x} for i, x in enumerate(xs)] + [
        {"name": _letters(i), "axis": "y", "pos": y} for i, y in enumerate(ys)
    ]


class GridsDialog(QDialog):
    """Grid line editor (project.grids, shown on every plan)."""

    def __init__(self, parent, project: Project, plan_name: str):
        super().__init__(parent)
        self.p = project
        self.plan_name = plan_name
        self.setWindowTitle("Grid lines")
        self.resize(460, 480)
        self.t = QTableWidget(0, 3)
        self.t.setHorizontalHeaderLabels(["Name", "Axis", "Position (m)"])
        self.t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.t.verticalHeader().setVisible(False)
        self.set_grids(project.grids)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Axis X = vertical line x = position · Axis Y = horizontal line y = position."))
        lay.addWidget(self.t)
        row = QHBoxLayout()
        for txt, fn in (
            ("+ Row", lambda: self._add_row("", "x", 0.0)),
            ("− Row", lambda: self.t.removeRow(self.t.currentRow()) if self.t.currentRow() >= 0 else None),
            ("Generate from columns", self.generate),
            ("Clear", lambda: self.t.setRowCount(0)),
        ):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(_buttons(self))

    def _add_row(self, name: str, axis: str, pos: float):
        r = self.t.rowCount()
        self.t.insertRow(r)
        self.t.setItem(r, 0, QTableWidgetItem(str(name)))
        cb = QComboBox()
        cb.addItems(["X", "Y"])
        cb.setCurrentText(str(axis).upper())
        self.t.setCellWidget(r, 1, cb)
        self.t.setItem(r, 2, QTableWidgetItem(f"{float(pos):g}"))

    def set_grids(self, grids: list[dict]):
        self.t.setRowCount(0)
        for g in grids:
            self._add_row(g.get("name", ""), g.get("axis", "x"), g.get("pos", 0.0))

    def generate(self):
        plan = self.p.plan(self.plan_name)
        if plan is None or not plan.columns:
            QMessageBox.information(self, "Grid lines", "The plan has no columns.")
            return
        self.set_grids(grids_from_columns(plan))

    def grids(self) -> list[dict]:
        out = []
        for r in range(self.t.rowCount()):
            name = self.t.item(r, 0).text().strip() if self.t.item(r, 0) else ""
            try:
                pos = float(self.t.item(r, 2).text())
            except (AttributeError, ValueError):
                continue
            axis = self.t.cellWidget(r, 1).currentText().lower()
            out.append({"name": name or str(r + 1), "axis": axis, "pos": round(pos, 4)})
        return sorted(out, key=lambda g: (g["axis"], g["pos"]))

    def apply(self):
        self.p.grids = self.grids()
