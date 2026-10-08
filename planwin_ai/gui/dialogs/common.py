"""Shared dialog helpers: spin boxes (plain and unit-aware), button boxes, mark lists and the diagram plot."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QListWidget,
    QListWidgetItem,
    QWidget,
)

from ... import units
from ..theme import PALETTES

GRADES = ["M20", "M25", "M30", "M35", "M40", "M45", "M50"]


def _dspin(v, lo=0.0, hi=1e6, dec=3, step=0.1):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(dec)
    s.setSingleStep(step)
    s.setValue(float(v))
    return s


def _buttons(dlg: QDialog) -> QDialogButtonBox:
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    return bb


def _qspin(value_kn: float, quantity: str, lo=0.0, hi=1e6, dec=3, step=0.1) -> QDoubleSpinBox:
    """Spin box showing an engine (kN based) value in the display units of ``units.current``."""
    u = units.current
    conv = u.mks and u.show(1.0, quantity) != 1.0
    s = _dspin(u.show(value_kn, quantity), u.show(lo, quantity), u.show(hi, quantity), dec + (1 if conv else 0), step)
    s.setSuffix(f" {u.label(quantity)}")
    _qset(s, value_kn, quantity)
    return s


def _qset(s: QDoubleSpinBox, value_kn: float, quantity: str) -> None:
    """Set an engine value; remembered so that an unedited value is returned unconverted."""
    s.setValue(units.current.show(value_kn, quantity))
    s.setProperty("kn", float(value_kn))
    s.setProperty("shown", s.value())
    s.setProperty("qty", quantity)


def _qval(s: QDoubleSpinBox) -> float:
    """Engine (kN based) value of a :func:`_qspin` – the original value when it was not edited."""
    if s.property("shown") is not None and s.value() == s.property("shown"):
        return float(s.property("kn"))
    return units.current.parse(s.value(), s.property("qty") or "length")


def _marks_list(marks: list[str], selected: list[str] = ()) -> QListWidget:
    """Multi-selection list of member marks."""
    lst = QListWidget()
    lst.setSelectionMode(QAbstractItemView.MultiSelection)
    _set_marks(lst, marks, selected)
    lst.setMaximumHeight(130)
    return lst


def _set_marks(lst: QListWidget, marks: list[str], selected: list[str] = ()) -> None:
    lst.blockSignals(True)
    lst.clear()
    for m in marks:
        it = QListWidgetItem(m)
        lst.addItem(it)
        it.setSelected(m in selected)
    lst.blockSignals(False)
    lst.itemSelectionChanged.emit()


def _selected_marks(lst: QListWidget) -> list[str]:
    return [lst.item(i).text() for i in range(lst.count()) if lst.item(i).isSelected()]


def _sorted_marks(objs) -> list[str]:
    return sorted({o.mark for o in objs}, key=lambda m: (len(m), m))


class _Plot(QWidget):
    def __init__(self, x, y, title, unit, theme):
        super().__init__()
        self.x, self.y, self.title, self.unit, self.theme = np.asarray(x), np.asarray(y), title, unit, theme
        self.setMinimumHeight(170)

    def paintEvent(self, _):
        pal = PALETTES[self.theme]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(pal["panel"]))
        m = 40
        W, H = self.width() - 2 * m, self.height() - 2 * m
        if len(self.x) < 2 or W <= 0:
            return
        L = float(self.x[-1]) or 1.0
        ymax = float(np.max(np.abs(self.y))) or 1.0

        def pt(xv, yv):
            # sagging plotted below the axis (engineering convention)
            return QPointF(m + xv / L * W, m + H / 2 + yv / ymax * H / 2)

        p.setPen(QPen(QColor(pal["muted"]), 1))
        p.drawLine(pt(0, 0), pt(L, 0))
        p.setPen(QPen(QColor("#2F7DE1"), 2))
        for i in range(len(self.x) - 1):
            p.drawLine(pt(self.x[i], self.y[i]), pt(self.x[i + 1], self.y[i + 1]))
        p.setPen(QColor(pal["text"]))
        p.drawText(8, 16, f"{self.title}   max {np.max(self.y):.1f} / min {np.min(self.y):.1f} {self.unit}")
        for k in range(9):
            xv = L * k / 8
            yv = float(np.interp(xv, self.x, self.y))
            q = pt(xv, yv)
            p.drawText(QPointF(q.x() - 14, q.y() + (14 if yv >= 0 else -4)), f"{yv:.1f}")
