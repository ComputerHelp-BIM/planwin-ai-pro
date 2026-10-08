"""Lightweight 3-D wireframe / solid view of the space frame (pure QPainter).

Avoids OpenGL so the frozen .exe runs on any Windows PC, including
virtual machines and remote desktops without GPU drivers.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Optional

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from .theme import PALETTES

if TYPE_CHECKING:
    from .main_window import MainWindow


class _Canvas3D(QWidget):
    def __init__(self, owner: "Frame3DView"):
        super().__init__()
        self.o = owner
        self.yaw, self.pitch = -35.0, 25.0
        self.zoom = 1.0
        self.pan = QPointF(0, 0)
        self._last: Optional[QPointF] = None
        self._btn = None
        self.setMinimumSize(300, 300)

    def project(self, P: np.ndarray, center: np.ndarray, size: float) -> np.ndarray:
        y, p = math.radians(self.yaw), math.radians(self.pitch)
        Rz = np.array([[math.cos(y), -math.sin(y), 0], [math.sin(y), math.cos(y), 0], [0, 0, 1]])
        Rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
        Q = (P - center) @ Rz.T @ Rx.T
        s = min(self.width(), self.height()) / max(size, 1e-6) * 0.8 * self.zoom
        sx = self.width() / 2 + Q[:, 0] * s + self.pan.x()
        sy = self.height() / 2 - Q[:, 2] * s + self.pan.y()
        return np.column_stack([sx, sy, Q[:, 1]])

    def paintEvent(self, _):
        pal = PALETTES[self.o.main.theme_name]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(pal["canvas"]))
        fm = self.o.main.frame_model()
        if fm is None or not fm.members:
            p.setPen(QColor(pal["muted"]))
            p.drawText(self.rect(), Qt.AlignCenter, "Build the 3-D frame (FrameWin ▸ Build 3-D model) to see it here")
            return
        ids = sorted(fm.nodes)
        idx = {n: i for i, n in enumerate(ids)}
        P = np.array([[fm.nodes[n].x, fm.nodes[n].y, fm.nodes[n].z] for n in ids], dtype=float)
        mode = self.o.mode.currentText()
        fa = self.o.main.frame_analysis()
        scale_def = self.o.def_scale.value()
        if fa is not None and mode.startswith("Deflected") and scale_def > 0:
            case = mode.split(":")[-1].strip()
            factors = {case: 1.0}
            D = np.array([fa.displacement(n, factors)[:3] for n in ids])
            mx = float(np.abs(D).max()) or 1.0
            ext = float(np.ptp(P, axis=0).max()) or 1.0
            P = P + D / mx * ext * 0.05 * scale_def / 10
        lvl_filter = self.o.level.currentIndex() - 1
        center = (P.min(axis=0) + P.max(axis=0)) / 2
        size = float(np.ptp(P, axis=0).max()) or 1.0
        S = self.project(P, center, size)
        util = {}
        rep = self.o.main.design_report()
        if rep is not None and mode == "Design utilisation":
            for c in rep.columns:
                util[c.member_id] = (c.utilisation, c.ok)
            for b in rep.beams:
                util[b.member_id] = (b.utilisation, b.ok)
        lines = []
        for mid, m in fm.members.items():
            if lvl_filter >= 0 and m.level != lvl_filter + 1:
                continue
            a, b = S[idx[m.n1]], S[idx[m.n2]]
            depth = (a[2] + b[2]) / 2
            lines.append((depth, mid, m, a, b))
        lines.sort(key=lambda t: -t[0])
        for _, mid, m, a, b in lines:
            if mode == "Design utilisation" and mid in util:
                u, ok = util[mid]
                col = QColor(pal["error"]) if not ok else (QColor(pal["warn"]) if u > 0.85 else QColor(pal["ok"]))
            else:
                col = QColor(pal["column"] if m.kind == "column" else pal["beam"])
            w = 3.0 if m.kind == "column" else 2.0
            p.setPen(QPen(col, w))
            p.drawLine(QPointF(a[0], a[1]), QPointF(b[0], b[1]))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal["error"]))
        for n, nd in fm.nodes.items():
            if nd.support:
                s = S[idx[n]]
                p.drawRect(int(s[0]) - 4, int(s[1]) - 4, 8, 8)
        p.setPen(QColor(pal["muted"]))
        p.drawText(10, self.height() - 10, "Drag: orbit · Right-drag: pan · Wheel: zoom · Double-click: reset")

    def mousePressEvent(self, e):
        self._last = e.position()
        self._btn = e.button()

    def mouseMoveEvent(self, e):
        if self._last is None:
            return
        d = e.position() - self._last
        self._last = e.position()
        if self._btn == Qt.LeftButton:
            self.yaw += d.x() * 0.5
            self.pitch = max(min(self.pitch + d.y() * 0.5, 89), -89)
        else:
            self.pan += d
        self.update()

    def mouseReleaseEvent(self, e):
        self._last = None

    def mouseDoubleClickEvent(self, e):
        self.yaw, self.pitch, self.zoom, self.pan = -35.0, 25.0, 1.0, QPointF(0, 0)
        self.update()

    def wheelEvent(self, e):
        self.zoom *= 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.update()


class Frame3DView(QWidget):
    def __init__(self, main: "MainWindow"):
        super().__init__()
        self.main = main
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        self.mode = QComboBox()
        self.level = QComboBox()
        self.def_scale = QSlider(Qt.Horizontal)
        self.def_scale.setRange(0, 100)
        self.def_scale.setValue(20)
        self.def_scale.setMaximumWidth(140)
        bar.addWidget(QLabel("Show:"))
        bar.addWidget(self.mode)
        bar.addWidget(QLabel("Level:"))
        bar.addWidget(self.level)
        bar.addWidget(QLabel("Deflection scale:"))
        bar.addWidget(self.def_scale)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.canvas = _Canvas3D(self)
        lay.addWidget(self.canvas, 1)
        for w in (self.mode, self.level):
            w.currentIndexChanged.connect(self.canvas.update)
        self.def_scale.valueChanged.connect(self.canvas.update)
        self.refresh()

    def refresh(self):
        cur = self.mode.currentText()
        self.mode.blockSignals(True)
        self.mode.clear()
        self.mode.addItem("Members")
        self.mode.addItem("Design utilisation")
        fa = self.main.frame_analysis()
        if fa is not None:
            for c in fa.res.cases:
                self.mode.addItem(f"Deflected: {c}")
        i = self.mode.findText(cur)
        self.mode.setCurrentIndex(max(i, 0))
        self.mode.blockSignals(False)
        self.level.blockSignals(True)
        cl = self.level.currentIndex()
        self.level.clear()
        self.level.addItem("All levels")
        for lv in self.main.project.levels:
            self.level.addItem(lv.name)
        self.level.setCurrentIndex(cl if 0 <= cl < self.level.count() else 0)
        self.level.blockSignals(False)
        self.canvas.update()
