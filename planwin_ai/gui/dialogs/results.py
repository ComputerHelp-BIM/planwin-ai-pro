"""Result dialogs: beam diagrams and BOQ design revisions."""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ...core.model import Project
from ...design.quantities import compare_revisions, revision_snapshot
from ...design.report import DesignReport
from .common import _Plot

if TYPE_CHECKING:
    from ..main_window import MainWindow


# =========================================================================== beam diagram
class BeamDiagramDialog(QDialog):
    def __init__(self, parent: MainWindow, beam, br):
        super().__init__(parent)
        self.setWindowTitle(f"Beam {beam.mark} – factored 1.5(D+L) diagrams (PlanWin)")
        self.resize(760, 520)
        dia = br.diagram(True)
        lay = QVBoxLayout(self)
        sup = ", ".join(f"{s.kind} @ {s.x:.2f} m" for s in br.supports)
        lay.addWidget(QLabel(f"Span {br.length:.3f} m · {beam.b * 1000:.0f}×{beam.d * 1000:.0f} mm · supports: {sup}"))
        lay.addWidget(
            _Plot(dia["x"], dia["M"], "Bending moment (sagging +, drawn below axis)", "kN·m", parent.theme_name)
        )
        lay.addWidget(_Plot(dia["x"], dia["V"], "Shear force", "kN", parent.theme_name))
        from ...core.model import grade_fck
        from ...design import is456

        fck = grade_fck(beam.grade)
        fy = parent.project.design.fy_main
        cov = parent.project.design.beam_cover * 1000
        rows = []
        for k in range(9):
            xv = br.length * k / 8
            M = float(np.interp(xv, dia["x"], dia["M"]))
            V = float(np.interp(xv, dia["x"], dia["V"]))
            fl = is456.flexure(abs(M), fck, fy, beam.b * 1000, beam.d * 1000, cov)
            rows.append((f"{k}/8", M, V, fl.ast if M > 0 else 0.0, fl.ast if M < 0 else 0.0))
        t = QTableWidget(len(rows), 5)
        t.setHorizontalHeaderLabels(["Section", "Mu kN·m", "Vu kN", "Bottom Ast mm²", "Top Ast mm²"])
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                t.setItem(r, c, QTableWidgetItem(f"{v:.1f}" if isinstance(v, float) else v))
        lay.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


# =========================================================================== design revisions
class RevisionsDialog(QDialog):
    """BOQ revisions kept in ``project.meta["revisions"]``: save, delete and compare.

    Edits ``project.meta`` directly and sets ``self.changed`` – saving a revision does not change
    the model, so the caller marks the project dirty without invalidating the results.
    """

    HEADERS = ["Group", "Item", "A", "B", "Change", "Change %"]

    def __init__(self, parent, project: Project, rep: DesignReport | None):
        super().__init__(parent)
        self.p = project
        self.rep = rep
        self.changed = False
        self.setWindowTitle("Design revisions (BOQ)")
        self.resize(820, 600)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setMaximumHeight(170)
        self.save_btn = QPushButton("Save current design as revision…")
        self.save_btn.setEnabled(self._current() is not None)
        self.save_btn.setToolTip("Run the design first" if self._current() is None else "")
        self.save_btn.clicked.connect(lambda: self.save_revision())
        self.del_btn = QPushButton("Delete")
        self.del_btn.clicked.connect(self.delete_selected)
        self.cmp_btn = QPushButton("Compare")
        self.cmp_btn.setToolTip("Two selected revisions, or one selected revision against the current design")
        self.cmp_btn.clicked.connect(self.compare_selected)
        self.caption = QLabel("Select two revisions (or one and the current design) and press Compare.")
        self.t = QTableWidget(0, len(self.HEADERS))
        self.t.setHorizontalHeaderLabels(self.HEADERS)
        self.t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.t.verticalHeader().setVisible(False)
        self.t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        lay = QVBoxLayout(self)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        for b in (self.save_btn, self.del_btn, self.cmp_btn):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(self.caption)
        lay.addWidget(self.t, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._refresh()

    def revisions(self) -> list[dict]:
        return self.p.meta.get("revisions", [])

    def _current(self) -> dict | None:
        boq = getattr(self.rep, "boq", None) if self.rep is not None else None
        if not boq or "total_concrete" not in boq:
            return None
        return revision_snapshot(boq, "Current design", time.strftime("%Y-%m-%d %H:%M"))

    def _refresh(self):
        self.list.clear()
        for r in self.revisions():
            t = r.get("total", {})
            self.list.addItem(
                f"{r.get('label', '?')}  ·  {r.get('date', '')}  ·  concrete {t.get('concrete', 0):.1f} m³, "
                f"steel {t.get('steel', 0) / 1000:.2f} t, cost ₹ {t.get('cost', 0):,.0f}"
            )

    def save_revision(self, label: str | None = None) -> dict | None:
        cur = self._current()
        if cur is None:
            return None
        if label is None:
            n = len(self.revisions()) + 1
            label, ok = QInputDialog.getText(self, "Save revision", "Revision label:", text=f"R{n}")
            if not ok:
                return None
        cur["label"] = label.strip() or f"R{len(self.revisions()) + 1}"
        self.p.meta.setdefault("revisions", []).append(cur)
        self.changed = True
        self._refresh()
        self.list.setCurrentRow(self.list.count() - 1)
        return cur

    def _selected(self) -> list[int]:
        return sorted(self.list.row(it) for it in self.list.selectedItems())

    def delete_selected(self):
        rows = set(self._selected())
        if not rows:
            return
        self.p.meta["revisions"] = [r for i, r in enumerate(self.revisions()) if i not in rows]
        self.changed = True
        self._refresh()

    def compare_selected(self):
        rows = self._selected()
        revs = self.revisions()
        if len(rows) >= 2:
            self.compare(revs[rows[0]], revs[rows[-1]])
        elif len(rows) == 1 and self._current() is not None:
            self.compare(revs[rows[0]], self._current())
        else:
            QMessageBox.information(
                self, "Compare", "Select two revisions, or one revision with a current design available."
            )

    def compare(self, a: dict, b: dict) -> list[tuple]:
        """Fill the table with :func:`compare_revisions` rows (increases red, decreases green)."""
        rows = compare_revisions(a, b)
        self.caption.setText(f"<b>A</b> = {a.get('label')} ({a.get('date')}) · <b>B</b> = {b.get('label')}")
        self.t.setRowCount(len(rows))
        for r, (group, item, va, vb, ch, pct) in enumerate(rows):
            pct_s = "new" if math.isinf(pct) else f"{pct:+.1f}"
            colour = QColor(220, 38, 38, 60) if ch > 1e-9 else QColor(22, 163, 74, 60) if ch < -1e-9 else None
            for c, v in enumerate((group, item, f"{va:,.2f}", f"{vb:,.2f}", f"{ch:+,.2f}", pct_s)):
                it = QTableWidgetItem(v)
                if c >= 2:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if colour is not None:
                    it.setBackground(colour)
                self.t.setItem(r, c, it)
        self.t.resizeColumnsToContents()
        return rows
