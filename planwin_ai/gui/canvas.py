"""Interactive 2-D plan editor (PlanWin drawing area)."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import QMenu, QWidget

from ..core import geometry as G
from ..core.model import Beam, Column, Plan, Slab
from .theme import PALETTES

if TYPE_CHECKING:
    from .main_window import MainWindow

TOOLS = ("select", "pan", "rect_slab", "poly_slab", "column", "beam", "measure")


class PlanCanvas(QWidget):
    selectionChanged = Signal(list)
    status = Signal(str)
    cursorMoved = Signal(float, float)

    def __init__(self, main: MainWindow):
        super().__init__()
        self.main = main
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(400, 300)
        self.scale = 40.0
        self.ox, self.oy = 60.0, 500.0
        self.tool = "select"
        self.selection: list[str] = []
        self.snap_step = 0.05
        self.show_loads = True
        self.show_marks = True
        self._press: QPointF | None = None
        self._press_world: tuple[float, float] | None = None
        self._drag_rect: tuple[tuple[float, float], tuple[float, float]] | None = None
        self._poly: list[tuple[float, float]] = []
        self._beam_start: tuple[float, float] | None = None
        self._mouse_world = (0.0, 0.0)
        self._snap_pt: tuple[float, float] | None = None
        self._panning = False
        self._last_pos: QPointF | None = None
        self.highlight_at: tuple[float, float] | None = None

    # ----------------------------------------------------------- helpers
    @property
    def plan(self) -> Plan | None:
        return self.main.current_plan()

    @property
    def pal(self):
        return PALETTES[self.main.theme_name]

    def w2s(self, x: float, y: float) -> QPointF:
        return QPointF(self.ox + x * self.scale, self.oy - y * self.scale)

    def s2w(self, p: QPointF) -> tuple[float, float]:
        return ((p.x() - self.ox) / self.scale, (self.oy - p.y()) / self.scale)

    def set_tool(self, tool: str):
        self.tool = tool
        self._poly.clear()
        self._beam_start = None
        self._drag_rect = None
        self.setCursor(Qt.OpenHandCursor if tool == "pan" else (Qt.ArrowCursor if tool == "select" else Qt.CrossCursor))
        hints = {
            "select": "Click to select (Shift adds), drag a window, right-click for options, Del to delete",
            "pan": "Drag to pan, wheel to zoom",
            "rect_slab": "Drag from corner to corner to create a slab (snaps to existing corners)",
            "poly_slab": "Click points of an irregular slab; click the first point or press Enter to close",
            "column": "Click at a junction to place a column",
            "beam": "Click the start and end of the beam",
            "measure": "Click two points to measure",
        }
        self.status.emit(hints.get(tool, ""))
        self.update()

    def zoom_extents(self):
        plan = self.plan
        if not plan or not plan.all_points():
            x0, y0, x1, y1 = 0, 0, 20, 12
        else:
            x0, y0, x1, y1 = plan.extents()
        w, h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
        m = 50
        self.scale = max(min((self.width() - 2 * m) / w, (self.height() - 2 * m) / h), 2.0)
        self.ox = (self.width() - w * self.scale) / 2 - x0 * self.scale
        self.oy = (self.height() + h * self.scale) / 2 + y0 * self.scale
        self.update()

    def focus_point(self, x: float, y: float):
        self.ox = self.width() / 2 - x * self.scale
        self.oy = self.height() / 2 + y * self.scale
        self.highlight_at = (x, y)
        self.update()

    def snap(self, pos: QPointF) -> tuple[float, float]:
        x, y = self.s2w(pos)
        plan = self.plan
        best, bd = None, 12.0 / self.scale
        if plan:
            cands = []
            for s in plan.slabs:
                cands.extend(s.pts)
            cands.extend(c.pos for c in plan.columns)
            for b in plan.beams:
                cands.extend((b.p1, b.p2))
            for p in cands:
                d = math.hypot(p[0] - x, p[1] - y)
                if d < bd:
                    best, bd = p, d
        if best is not None:
            self._snap_pt = best
            return best
        self._snap_pt = None
        st = self.snap_step
        return (round(round(x / st) * st, 4), round(round(y / st) * st, 4))

    # ----------------------------------------------------------- hit test
    def hit(self, x: float, y: float):
        plan = self.plan
        if not plan:
            return None
        tol = 6.0 / self.scale
        for c in reversed(plan.columns):
            if G.point_in_polygon((x, y), c.corners()) or math.hypot(c.x - x, c.y - y) <= tol:
                return c
        for b in reversed(plan.beams):
            if G.point_segment_distance((x, y), b.p1, b.p2) <= max(tol, b.b / 2):
                return b
        for s in reversed(plan.slabs):
            if len(s.points) >= 3 and G.point_in_polygon((x, y), s.pts):
                return s
        return None

    def select_ids(self, ids: list[str]):
        self.selection = ids
        self.selectionChanged.emit(list(ids))
        self.update()

    # ----------------------------------------------------------- painting
    def paintEvent(self, _ev):
        pal = self.pal
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(pal["canvas"]))
        self._paint_grid(p)
        plan = self.plan
        if plan is None:
            p.setPen(QColor(pal["muted"]))
            p.drawText(
                self.rect(), Qt.AlignCenter, "No plan – create one from the Project panel or ask the AI assistant"
            )
            return
        res = self.main.plan_result(plan.name)
        sel = set(self.selection)
        font = QFont(self.font())
        font.setPointSizeF(max(min(self.scale / 6.0, 10.0), 6.5))
        p.setFont(font)
        # slabs
        for s in plan.slabs:
            if len(s.points) < 3:
                continue
            poly = QPolygonF([self.w2s(*pt) for pt in s.pts])
            col = (
                pal["slab_cant"]
                if s.distribution == "cantilever"
                else (pal["slab_grade"] if s.distribution == "on_grade" else pal["slab"])
            )
            p.setBrush(QColor(col))
            p.setPen(QPen(QColor(pal["select"] if s.id in sel else pal["slab_edge"]), 2.5 if s.id in sel else 1))
            p.drawPolygon(poly)
            if s.distribution == "cantilever" and s.cant_edge is not None and s.cant_edge < len(s.points):
                a, b = s.pts[s.cant_edge], s.pts[(s.cant_edge + 1) % len(s.pts)]
                p.setPen(QPen(QColor(pal["beam_cant"]), 3, Qt.DashLine))
                p.drawLine(self.w2s(*a), self.w2s(*b))
            if self.show_marks:
                cx, cy = G.polygon_centroid(s.pts)
                c = self.w2s(cx, cy)
                p.setPen(QColor(pal["text_canvas"]))
                txt = s.mark
                if self.show_loads and self.scale > 18:
                    txt += f"\n{s.dead:.1f}+{s.live_load:.1f}"
                p.drawText(QRectF(c.x() - 60, c.y() - 18, 120, 36), Qt.AlignCenter, txt)
        # beams
        for b in plan.beams:
            w = max(b.b * self.scale, 2.0)
            colr = pal["beam_cant"] if b.cantilever else (pal["beam_ext"] if b.external else pal["beam"])
            pen = QPen(QColor(pal["select"] if b.id in sel else colr), w, Qt.SolidLine, Qt.FlatCap)
            if b.role == "secondary":
                pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.drawLine(self.w2s(*b.p1), self.w2s(*b.p2))
            if self.show_marks and self.scale > 12:
                mid = self.w2s((b.x1 + b.x2) / 2, (b.y1 + b.y2) / 2)
                ang = -math.degrees(math.atan2(b.y2 - b.y1, b.x2 - b.x1))
                if ang > 90 or ang < -90:
                    ang += 180
                p.save()
                p.translate(mid)
                p.rotate(ang)
                p.setPen(QColor(pal["text_canvas"]))
                label = b.mark
                if self.show_loads and res and b.id in res.beams:
                    label += f"  {res.beams[b.id].equivalent_udl():.1f} kN/m"
                p.drawText(QRectF(-80, -w / 2 - 16, 160, 14), Qt.AlignHCenter | Qt.AlignBottom, label)
                p.restore()
        # columns
        for c in plan.columns:
            poly = QPolygonF([self.w2s(*pt) for pt in c.corners()])
            p.setBrush(QColor(pal["select"] if c.id in sel else pal["column"]))
            p.setPen(Qt.NoPen)
            p.drawPolygon(poly)
            if self.show_marks:
                pt = self.w2s(c.x, c.y)
                off = max(c.b, c.d) * self.scale / 2 + 3
                p.setPen(QColor(pal["text_canvas"]))
                label = c.mark
                if self.show_loads and res:
                    cl = res.columns.get(c.id)
                    if cl and cl.total > 0:
                        label += f" {cl.total:.0f}kN"
                p.drawText(QPointF(pt.x() + off, pt.y() - off), label)
        # issues
        if res:
            p.setPen(QPen(QColor(pal["error"]), 2))
            p.setBrush(Qt.NoBrush)
            for iss in res.errors:
                if iss.at:
                    q = self.w2s(*iss.at)
                    p.drawEllipse(q, 9, 9)
        if self.highlight_at:
            p.setPen(QPen(QColor(pal["select"]), 3))
            p.drawEllipse(self.w2s(*self.highlight_at), 16, 16)
        self._paint_tool(p)
        self._paint_scale(p)

    def _paint_grid(self, p: QPainter):
        pal = self.pal
        x0, y1 = self.s2w(QPointF(0, 0))
        x1, y0 = self.s2w(QPointF(self.width(), self.height()))
        step = 1.0
        while step * self.scale < 14:
            step *= 5
        major = step * 5
        for st, col in ((step, pal["grid_minor"]), (major, pal["grid_major"])):
            p.setPen(QPen(QColor(col), 1))
            x = math.floor(x0 / st) * st
            while x <= x1:
                s = self.w2s(x, 0)
                p.drawLine(QPointF(s.x(), 0), QPointF(s.x(), self.height()))
                x += st
            y = math.floor(y0 / st) * st
            while y <= y1:
                s = self.w2s(0, y)
                p.drawLine(QPointF(0, s.y()), QPointF(self.width(), s.y()))
                y += st
        o = self.w2s(0, 0)
        p.setPen(QPen(QColor("#E11D48"), 1.5))
        p.drawLine(o, QPointF(o.x() + 30, o.y()))
        p.setPen(QPen(QColor("#16A34A"), 1.5))
        p.drawLine(o, QPointF(o.x(), o.y() - 30))

    def _paint_tool(self, p: QPainter):
        pal = self.pal
        pen = QPen(QColor(pal["select"]), 1.5, Qt.DashLine)
        p.setPen(pen)
        p.setBrush(QColor(47, 125, 225, 40))
        if self._drag_rect:
            (ax, ay), (bx, by) = self._drag_rect
            r = QRectF(self.w2s(ax, ay), self.w2s(bx, by)).normalized()
            p.drawRect(r)
            if self.tool == "rect_slab":
                p.setPen(QColor(pal["text"]))
                p.drawText(
                    r.adjusted(4, 4, 0, 0), Qt.AlignLeft | Qt.AlignTop, f"{abs(bx - ax):.2f} × {abs(by - ay):.2f} m"
                )
        if self._poly:
            pts = [self.w2s(*q) for q in self._poly] + [self.w2s(*self._mouse_world)]
            p.drawPolyline(QPolygonF(pts))
        if self._beam_start:
            p.setPen(QPen(QColor(pal["select"]), 3))
            a, b = self._beam_start, self._mouse_world
            p.drawLine(self.w2s(*a), self.w2s(*b))
            p.setPen(QColor(pal["text"]))
            p.drawText(self.w2s(*b) + QPointF(10, -10), f"{G.dist(a, b):.2f} m")
        if self._snap_pt and self.tool not in ("select", "pan"):
            q = self.w2s(*self._snap_pt)
            p.setPen(QPen(QColor(pal["ok"]), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(q.x() - 6, q.y() - 6, 12, 12))

    def _paint_scale(self, p: QPainter):
        pal = self.pal
        target = 120 / self.scale
        mag = 10 ** math.floor(math.log10(target))
        length = max(v * mag for v in (1, 2, 5) if v * mag <= target) if target >= mag else mag
        px = length * self.scale
        y = self.height() - 18
        p.setPen(QPen(QColor(pal["muted"]), 2))
        p.drawLine(QPointF(16, y), QPointF(16 + px, y))
        p.drawLine(QPointF(16, y - 4), QPointF(16, y + 4))
        p.drawLine(QPointF(16 + px, y - 4), QPointF(16 + px, y + 4))
        p.drawText(QPointF(20 + px, y + 4), f"{length:g} m")

    # ----------------------------------------------------------- mouse
    def wheelEvent(self, ev: QWheelEvent):
        f = 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15
        pos = ev.position()
        wx, wy = self.s2w(pos)
        self.scale = min(max(self.scale * f, 1.0), 2000.0)
        self.ox = pos.x() - wx * self.scale
        self.oy = pos.y() + wy * self.scale
        self.update()

    def mousePressEvent(self, ev: QMouseEvent):
        self.setFocus()
        self.highlight_at = None
        if ev.button() == Qt.MiddleButton or (self.tool == "pan" and ev.button() == Qt.LeftButton):
            self._panning = True
            self._last_pos = ev.position()
            self.setCursor(Qt.ClosedHandCursor)
            return
        if ev.button() == Qt.RightButton:
            if self._poly:
                self._finish_poly()
                return
            self._context_menu(ev)
            return
        if ev.button() != Qt.LeftButton or self.plan is None:
            return
        pt = self.snap(ev.position()) if self.tool not in ("select",) else self.s2w(ev.position())
        self._press = ev.position()
        self._press_world = pt
        if self.tool == "rect_slab":
            self._drag_rect = (pt, pt)
        elif self.tool == "poly_slab":
            if len(self._poly) >= 3 and G.dist(pt, self._poly[0]) < 10 / self.scale:
                self._finish_poly()
            else:
                self._poly.append(pt)
        elif self.tool == "column":
            self._add_column(pt)
        elif self.tool in ("beam", "measure"):
            if self._beam_start is None:
                self._beam_start = pt
            else:
                if self.tool == "beam":
                    self._add_beam(self._beam_start, pt)
                    self._beam_start = None
                else:
                    a = self._beam_start
                    self.status.emit(f"Distance {G.dist(a, pt):.3f} m   ΔX {pt[0] - a[0]:.3f}   ΔY {pt[1] - a[1]:.3f}")
                    self._beam_start = None
        elif self.tool == "select":
            self._drag_rect = (pt, pt)
        self.update()

    def mouseMoveEvent(self, ev: QMouseEvent):
        if self._panning and self._last_pos is not None:
            d = ev.position() - self._last_pos
            self.ox += d.x()
            self.oy += d.y()
            self._last_pos = ev.position()
            self.update()
            return
        w = self.snap(ev.position()) if self.tool not in ("select", "pan") else self.s2w(ev.position())
        self._mouse_world = w
        self.cursorMoved.emit(*w)
        if self._drag_rect and self._press_world:
            self._drag_rect = (self._press_world, w)
        self.update()

    def mouseReleaseEvent(self, ev: QMouseEvent):
        if self._panning:
            self._panning = False
            self.setCursor(Qt.OpenHandCursor if self.tool == "pan" else Qt.ArrowCursor)
            return
        if ev.button() != Qt.LeftButton or self.plan is None:
            return
        if self.tool == "rect_slab" and self._drag_rect:
            (ax, ay), (bx, by) = self._drag_rect
            self._drag_rect = None
            if abs(bx - ax) > 0.1 and abs(by - ay) > 0.1:
                x0, x1, y0, y1 = min(ax, bx), max(ax, bx), min(ay, by), max(ay, by)
                self._add_slab([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
        elif self.tool == "select" and self._drag_rect:
            (ax, ay), (bx, by) = self._drag_rect
            self._drag_rect = None
            add = bool(ev.modifiers() & Qt.ShiftModifier)
            if abs(bx - ax) * self.scale < 4 and abs(by - ay) * self.scale < 4:
                obj = self.hit(ax, ay)
                ids = list(self.selection) if add else []
                if obj:
                    if obj.id in ids:
                        ids.remove(obj.id)
                    else:
                        ids.append(obj.id)
                self.select_ids(ids)
            else:
                x0, x1, y0, y1 = min(ax, bx), max(ax, bx), min(ay, by), max(ay, by)
                inside = lambda q: x0 <= q[0] <= x1 and y0 <= q[1] <= y1  # noqa: E731
                ids = list(self.selection) if add else []
                for o in self.plan.slabs:
                    if all(inside(q) for q in o.pts):
                        ids.append(o.id)
                for o in self.plan.beams:
                    if inside(o.p1) and inside(o.p2):
                        ids.append(o.id)
                for o in self.plan.columns:
                    if inside(o.pos):
                        ids.append(o.id)
                self.select_ids(list(dict.fromkeys(ids)))
        self.update()

    def mouseDoubleClickEvent(self, ev: QMouseEvent):
        if self.tool == "poly_slab" and len(self._poly) >= 3:
            self._finish_poly()
        elif self.tool == "select":
            obj = self.hit(*self.s2w(ev.position()))
            if isinstance(obj, Beam):
                self.main.show_beam_diagram(obj)

    def keyPressEvent(self, ev: QKeyEvent):
        k = ev.key()
        if k == Qt.Key_Escape:
            self._poly.clear()
            self._beam_start = None
            self._drag_rect = None
            if self.tool != "select":
                self.main.set_tool("select")
            else:
                self.select_ids([])
        elif k in (Qt.Key_Return, Qt.Key_Enter) and self._poly:
            self._finish_poly()
        elif k == Qt.Key_Delete and self.selection:
            self.main.delete_selection()
        elif k == Qt.Key_F:
            self.zoom_extents()
        elif k == Qt.Key_A and ev.modifiers() & Qt.ControlModifier and self.plan:
            self.select_ids([o.id for o in self.plan.slabs + self.plan.beams + self.plan.columns])
        else:
            super().keyPressEvent(ev)
        self.update()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if not getattr(self, "_fitted", False) and self.width() > 100:
            self._fitted = True
            self.zoom_extents()

    # ----------------------------------------------------------- creation
    def _add_slab(self, pts):
        d = self.main.defaults
        plan = self.plan

        def fn():
            s = Slab(
                mark=plan.next_mark("S"),
                points=[[round(x, 4), round(y, 4)] for x, y in G.ensure_ccw([tuple(q) for q in pts])],
                thickness=d["slab_thickness"],
                live=d["slab_live"],
                floor_finish=d["slab_ff"],
                other=d["slab_other"],
                grade=d["grade"],
            )
            if plan.floor_type == "ground":
                s.distribution = "on_grade"
            plan.slabs.append(s)
            self.selection = [s.id]

        self.main.mutate("Add slab", fn)

    def _finish_poly(self):
        pts = list(self._poly)
        self._poly.clear()
        if len(pts) >= 3 and abs(G.polygon_area(pts)) > 0.01:
            self._add_slab([list(q) for q in pts])
        self.update()

    def _add_column(self, pt):
        plan = self.plan
        if plan.column_at(pt, 0.05):
            self.status.emit("A column already exists here")
            return
        d = self.main.defaults

        def fn():
            c = Column(mark=plan.next_mark("C"), x=pt[0], y=pt[1], b=d["col_b"], d=d["col_d"], grade=d["grade"])
            plan.columns.append(c)
            self.selection = [c.id]

        self.main.mutate("Add column", fn)

    def _add_beam(self, a, b):
        if G.dist(a, b) < 0.1:
            return
        plan = self.plan
        d = self.main.defaults

        def fn():
            bm = Beam(
                mark=plan.next_mark("B"),
                x1=a[0],
                y1=a[1],
                x2=b[0],
                y2=b[1],
                b=d["beam_b"],
                d=d["beam_d"],
                grade=d["grade"],
                wall_thk=d["wall_thk"],
            )
            plan.beams.append(bm)
            self.selection = [bm.id]

        self.main.mutate("Add beam", fn)

    # ----------------------------------------------------------- context menu
    def _context_menu(self, ev: QMouseEvent):
        x, y = self.s2w(ev.position())
        obj = self.hit(x, y)
        m = QMenu(self)
        if obj is not None and obj.id not in self.selection:
            self.select_ids([obj.id])
        if self.selection:
            m.addAction("Properties", self.main.focus_properties)
            m.addAction("Delete selection", self.main.delete_selection)
            m.addAction("Move / copy selection…", self.main.move_copy_dialog)
            m.addAction("Mirror selection…", self.main.mirror_dialog)
            m.addAction("Copy selection to new plan", self.main.copy_selection_to_new_plan)
        if isinstance(obj, Slab):
            m.addSeparator()
            m.addAction("Make cantilever – fixed at this edge", lambda: self._set_cant_edge(obj, x, y))
            for lab, dist in (
                ("Two-way (auto)", "auto"),
                ("One-way", "one_way"),
                ("Slab on grade", "on_grade"),
                ("Uniform to edges", "uniform"),
            ):
                m.addAction(
                    f"Distribution: {lab}",
                    lambda d=dist, s=obj: self.main.mutate("Slab distribution", lambda: setattr(s, "distribution", d)),
                )
        if isinstance(obj, Beam):
            m.addSeparator()
            m.addAction("Bending moment / shear diagram", lambda: self.main.show_beam_diagram(obj))
            m.addAction(
                "Toggle cantilever",
                lambda: self.main.mutate("Cantilever", lambda: setattr(obj, "cantilever", not obj.cantilever)),
            )
            for role in ("auto", "primary", "secondary"):
                m.addAction(
                    f"Role: {role}", lambda r=role: self.main.mutate("Beam role", lambda: setattr(obj, "role", r))
                )
        if isinstance(obj, Column):
            m.addSeparator()
            m.addAction("Load analysis (break-up)", lambda: self.main.show_column_breakup(obj))
            m.addAction(
                "Rotate 90°",
                lambda: self.main.mutate("Rotate column", lambda: setattr(obj, "angle", (obj.angle + 90) % 180)),
            )
        m.addSeparator()
        m.addAction("Zoom extents (F)", self.zoom_extents)
        m.exec(ev.globalPosition().toPoint())

    def _set_cant_edge(self, s: Slab, x: float, y: float):
        pts = s.pts
        best = min(range(len(pts)), key=lambda i: G.point_segment_distance((x, y), pts[i], pts[(i + 1) % len(pts)]))

        def fn():
            s.distribution = "cantilever"
            s.cant_edge = best

        self.main.mutate("Cantilever slab", fn)
