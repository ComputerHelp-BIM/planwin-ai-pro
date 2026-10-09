"""Interactive 2-D plan editor (PlanWin drawing area)."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPolygonF,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import QMenu, QWidget

from ..core import geometry as G
from ..core.model import Beam, Column, Plan, Slab, Wall
from .theme import PALETTES

if TYPE_CHECKING:
    from .main_window import MainWindow

TOOLS = ("select", "pan", "rect_slab", "poly_slab", "column", "beam", "wall", "measure", "area", "dimension")
#: wall fill per theme when the palette has no "wall" key
_WALL_COLOURS = {"light": "#8B5CF6", "dark": "#A78BFA"}
Pt = tuple[float, float]


def polygon_stats(pts) -> tuple[float, float]:
    """(area m², perimeter m) of the closed polygon through ``pts``."""
    q = [(float(a[0]), float(a[1])) for a in pts]
    if len(q) < 2:
        return 0.0, 0.0
    per = sum(G.dist(q[i], q[(i + 1) % len(q)]) for i in range(len(q)))
    return abs(G.polygon_area(q)), per


def ortho_point(base, pt) -> Pt:
    """``pt`` constrained to the horizontal or vertical through ``base`` (whichever is nearer)."""
    return (pt[0], base[1]) if abs(pt[0] - base[0]) >= abs(pt[1] - base[1]) else (base[0], pt[1])


def dim_offset(a, b, p) -> float:
    """Signed perpendicular distance of ``p`` from the line a → b (positive to the left)."""
    L = G.dist(a, b)
    if L < 1e-9:
        return 0.0
    return ((p[1] - a[1]) * (b[0] - a[0]) - (p[0] - a[0]) * (b[1] - a[1])) / L


def dim_line(dm) -> tuple[Pt, Pt]:
    """End points of the dimension line of a plan dimension dict."""
    x1, y1, x2, y2 = (float(dm[k]) for k in ("x1", "y1", "x2", "y2"))
    off = float(dm.get("offset", 0.0))
    L = math.hypot(x2 - x1, y2 - y1) or 1.0
    nx, ny = -(y2 - y1) / L * off, (x2 - x1) / L * off
    return (x1 + nx, y1 + ny), (x2 + nx, y2 + ny)


# ------------------------------------------------------------------ label layout
Rect = tuple[float, float, float, float]  # screen x, y, width, height
#: beam / wall labels longer than this share of the member's on-screen length are shortened, then hidden
LABEL_FILL = 0.85
#: diagonal quadrants around a column, in default order of preference
QUADRANTS = ("NE", "NW", "SE", "SW")


def rects_overlap(a: Rect, b: Rect, pad: float = 0.0) -> bool:
    """True when the two rectangles (grown by ``pad`` px) intersect; touching edges do not count."""
    return (
        a[0] < b[0] + b[2] + pad and b[0] < a[0] + a[2] + pad and a[1] < b[1] + b[3] + pad and b[1] < a[1] + a[3] + pad
    )


class LabelPlacer:
    """Greedy label collision avoidance: the first label to claim a piece of screen keeps it.

    Labels are offered in priority order, each with alternative positions; ``place`` takes the first
    alternative that overlaps nothing already placed (nor any obstacle) and returns its index, or None.
    """

    def __init__(self, obstacles=(), pad: float = 1.0):
        self.pad = pad
        self.taken: list[Rect] = [tuple(r) for r in obstacles]

    def free(self, r: Rect) -> bool:
        return not any(rects_overlap(r, t, self.pad) for t in self.taken)

    def place(self, options) -> int | None:
        for i, r in enumerate(options):
            if self.free(r):
                self.taken.append(tuple(r))
                return i
        return None


def place_labels(options_per_label, obstacles=(), pad: float = 1.0) -> list[int | None]:
    """Index of the chosen alternative for each label (priority order), None when every alternative collides."""
    placer = LabelPlacer(obstacles, pad)
    return [placer.place(opts) for opts in options_per_label]


def framing_dirs(at: Pt, segments, tol: float) -> set[str]:
    """Compass directions (E W N S) in which the members ``segments`` [(p1, p2), ...] leave the point ``at``."""
    out: set[str] = set()

    def heading(dx: float, dy: float) -> str:
        return ("E" if dx > 0 else "W") if abs(dx) >= abs(dy) else ("N" if dy > 0 else "S")

    for a, b in segments:
        da, db = G.dist(at, a), G.dist(at, b)
        if da <= tol and db > tol:
            out.add(heading(b[0] - a[0], b[1] - a[1]))
        elif db <= tol and da > tol:
            out.add(heading(a[0] - b[0], a[1] - b[1]))
        elif da > tol and db > tol and G.point_segment_distance(at, a, b) <= tol:  # runs through the point
            out.add(heading(b[0] - a[0], b[1] - a[1]))
            out.add(heading(a[0] - b[0], a[1] - b[1]))
    return out


def label_quadrants(dirs: set[str]) -> list[str]:
    """Diagonal quadrants for a column label, the ones bordered by fewest framing members first."""
    return sorted(QUADRANTS, key=lambda q: (sum(d in dirs for d in q), QUADRANTS.index(q)))


class PlanCanvas(QWidget):
    selectionChanged = Signal(list)
    status = Signal(str)
    cursorMoved = Signal(float, float)
    orthoChanged = Signal(bool)

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
        self.show_dims = True
        self.show_grids = True
        # object snaps; priority: ends > grid intersections > midpoints > grid line > step grid
        self.snap_ends = True
        self.snap_mid = True
        self.snap_grid = True
        self.ortho = False
        self.last_area: tuple[float, float] | None = None  # (area, perimeter) of the last closed area
        self._press: QPointF | None = None
        self._press_world: Pt | None = None
        self._drag_rect: tuple[Pt, Pt] | None = None
        self._poly: list[Pt] = []
        self._beam_start: Pt | None = None
        self._area_pts: list[Pt] = []
        self._area_closed: list[Pt] | None = None
        self._dim_pts: list[Pt] = []
        self._mouse_world = (0.0, 0.0)
        self._snap_pt: Pt | None = None
        self._snap_kind: str | None = None  # end | grid | mid | line | None (step grid)
        self._snap_line: tuple[str, float] | None = None
        self._panning = False
        self._last_pos: QPointF | None = None
        self.highlight_at: Pt | None = None
        #: labels drawn by the last paint: (kind, object id, screen rect), in priority order
        self.painted_labels: list[tuple[str, str, Rect]] = []

    # ----------------------------------------------------------- helpers
    @property
    def plan(self) -> Plan | None:
        return self.main.current_plan()

    @property
    def pal(self):
        return PALETTES[self.main.theme_name]

    @property
    def wall_colour(self) -> str:
        return self.pal.get("wall", _WALL_COLOURS.get(self.main.theme_name, "#7C3AED"))

    def grid_lines(self) -> list[tuple[str, str, float]]:
        """Project grid lines as (name, axis, pos); axis "x" is the line x = pos."""
        out = []
        for g in getattr(getattr(self.main, "project", None), "grids", None) or []:
            try:
                ax = "y" if str(g.get("axis", "x")).lower() == "y" else "x"
                out.append((str(g.get("name", "")), ax, float(g.get("pos", 0.0))))
            except (AttributeError, TypeError, ValueError):
                continue
        return out

    def w2s(self, x: float, y: float) -> QPointF:
        return QPointF(self.ox + x * self.scale, self.oy - y * self.scale)

    def s2w(self, p: QPointF) -> Pt:
        return ((p.x() - self.ox) / self.scale, (self.oy - p.y()) / self.scale)

    def _tool_cursor(self) -> Qt.CursorShape:
        return {"pan": Qt.OpenHandCursor, "select": Qt.ArrowCursor}.get(self.tool, Qt.CrossCursor)

    def set_tool(self, tool: str):
        self.tool = tool
        self._poly.clear()
        self._beam_start = None
        self._drag_rect = None
        self._area_pts.clear()
        self._area_closed = None
        self._dim_pts.clear()
        self.setCursor(self._tool_cursor())
        hints = {
            "select": "Click to select (Shift adds), drag a window, right-click for options, Del to delete",
            "pan": "Drag to pan, wheel to zoom",
            "rect_slab": "Drag from corner to corner to create a slab (snaps to existing corners)",
            "poly_slab": "Click points of an irregular slab; click the first point or press Enter to close",
            "column": "Click at a junction to place a column",
            "beam": "Click the start and end of the beam (Shift or F8 for ortho)",
            "wall": "Click the start and end of the wall centre line (Shift or F8 for ortho)",
            "measure": "Click two points to measure (Shift or F8 for ortho)",
            "area": "Click the corners of an area; Enter, right-click, double-click or the first point closes it",
            "dimension": "Click the two points to dimension, then click where the dimension line goes",
        }
        self.status.emit(hints.get(tool, ""))
        self.update()

    def set_ortho(self, on: bool):
        """Ortho mode: second points are constrained horizontal / vertical (F8 toggles)."""
        self.ortho = bool(on)
        self.orthoChanged.emit(self.ortho)
        self.status.emit(f"Ortho {'ON' if self.ortho else 'OFF'}")
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

    def snap(self, pos: QPointF) -> Pt:
        x, y = self.s2w(pos)
        plan = self.plan
        tol = 12.0 / self.scale
        st = self.snap_step

        def step(v: float) -> float:
            return round(round(v / st) * st, 4)

        def nearest(cands):
            best, bd = None, tol
            for q in cands:
                d = math.hypot(q[0] - x, q[1] - y)
                if d < bd:
                    best, bd = (float(q[0]), float(q[1])), d
            return best

        grids = self.grid_lines() if self.snap_grid else []
        gx = [g[2] for g in grids if g[1] == "x"]
        gy = [g[2] for g in grids if g[1] == "y"]
        tiers: list[tuple[str, list]] = []
        if plan and self.snap_ends:
            ends = [q for s in plan.slabs for q in s.pts] + [c.pos for c in plan.columns]
            ends += [q for o in plan.beams + plan.walls for q in (o.p1, o.p2)]
            tiers.append(("end", ends))
        if gx and gy:
            tiers.append(("grid", [(a, b) for a in gx for b in gy]))
        if plan and self.snap_mid:
            mids = [G.lerp(o.p1, o.p2, 0.5) for o in plan.beams + plan.walls]
            for s in plan.slabs:
                pts = s.pts
                mids += [G.lerp(pts[i], pts[(i + 1) % len(pts)], 0.5) for i in range(len(pts))]
            tiers.append(("mid", mids))
        self._snap_line = None
        for kind, cands in tiers:
            best = nearest(cands)
            if best is not None:
                self._snap_pt, self._snap_kind = best, kind
                return best
        near = [(abs(x - g[2]) if g[1] == "x" else abs(y - g[2]), g[1], g[2]) for g in grids]
        if near:
            d, axis, at = min(near)
            if d < tol:
                best = (at, step(y)) if axis == "x" else (step(x), at)
                self._snap_pt, self._snap_kind, self._snap_line = best, "line", (axis, at)
                return best
        self._snap_pt = self._snap_kind = None
        return (step(x), step(y))

    def _base_point(self) -> Pt | None:
        """Previous point of the drawing operation in progress (the ortho reference)."""
        if self.tool in ("beam", "wall", "measure"):
            return self._beam_start
        if self.tool == "poly_slab" and self._poly:
            return self._poly[-1]
        if self.tool == "area" and self._area_pts and self._area_closed is None:
            return self._area_pts[-1]
        if self.tool == "dimension" and len(self._dim_pts) == 1:
            return self._dim_pts[0]
        return None

    def pick(self, pos: QPointF, modifiers=Qt.NoModifier) -> Pt:
        """Snapped world point for a drawing tool, constrained by ortho (F8) or a held Shift."""
        pt = self.snap(pos)
        base = self._base_point()
        if base is None or not (self.ortho or modifiers & Qt.ShiftModifier):
            return pt
        if self._snap_kind in ("end", "grid", "mid"):  # object snaps override ortho, as in CAD
            return pt
        q = ortho_point(base, pt)
        if self._snap_kind == "line" and self._snap_line:
            axis, at = self._snap_line
            if axis == "x" and q[1] == base[1]:
                q = (at, base[1])
            elif axis == "y" and q[0] == base[0]:
                q = (base[0], at)
            else:
                self._snap_kind = None
        else:
            self._snap_kind = None
        self._snap_pt = q if self._snap_kind else None
        return q

    # ----------------------------------------------------------- hit test
    def hit(self, x: float, y: float):
        plan = self.plan
        if not plan:
            return None
        tol = 6.0 / self.scale
        for c in reversed(plan.columns):
            if G.point_in_polygon((x, y), c.corners()) or math.hypot(c.x - x, c.y - y) <= tol:
                return c
        for w in reversed(plan.walls):
            if w.contains((x, y), tol):
                return w
        for b in reversed(plan.beams):
            if G.point_segment_distance((x, y), b.p1, b.p2) <= max(tol, b.b / 2):
                return b
        for s in reversed(plan.slabs):
            if len(s.points) >= 3 and G.point_in_polygon((x, y), s.pts):
                return s
        return None

    def dim_at(self, x: float, y: float) -> int | None:
        """Index in ``plan.dimensions`` of the dimension whose dimension line passes near (x, y)."""
        plan = self.plan
        if not plan or not self.show_dims:
            return None
        best, bd = None, 8.0 / self.scale
        for i, dm in enumerate(plan.dimensions):
            try:
                a, b = dim_line(dm)
            except (KeyError, TypeError, ValueError):
                continue
            d = G.point_segment_distance((x, y), a, b)
            if d <= bd:
                best, bd = i, d
        return best

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
        if self.show_grids:
            self._paint_grid_lines(p, plan)
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
        # beams
        for b in plan.beams:
            w = max(b.b * self.scale, 2.0)
            colr = pal["beam_cant"] if b.cantilever else (pal["beam_ext"] if b.external else pal["beam"])
            pen = QPen(QColor(pal["select"] if b.id in sel else colr), w, Qt.SolidLine, Qt.FlatCap)
            if b.role == "secondary":
                pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.drawLine(self.w2s(*b.p1), self.w2s(*b.p2))
        # walls
        wcol = QColor(self.wall_colour)
        for wl in plan.walls:
            col = QColor(pal["select"]) if wl.id in sel else wcol
            p.setBrush(col)
            p.setPen(QPen(col.darker(140), 1))
            p.drawPolygon(QPolygonF([self.w2s(*pt) for pt in wl.corners()]))
        # columns
        for c in plan.columns:
            poly = QPolygonF([self.w2s(*pt) for pt in c.corners()])
            p.setBrush(QColor(pal["select"] if c.id in sel else pal["column"]))
            p.setPen(Qt.NoPen)
            p.drawPolygon(poly)
        self.painted_labels = []
        if self.show_marks:
            self._paint_labels(p, plan, res)
        if self.show_grids:
            self._paint_grid_bubbles(p, plan)
        if self.show_dims:
            col = QColor(pal["text_canvas"])
            for dm in plan.dimensions:
                try:
                    a = (float(dm["x1"]), float(dm["y1"]))
                    b = (float(dm["x2"]), float(dm["y2"]))
                    off = float(dm.get("offset", 0.0))
                except (KeyError, TypeError, ValueError):
                    continue
                self._paint_dim(p, a, b, off, col)
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

    def _paint_labels(self, p: QPainter, plan: Plan, res):
        """Member labels, decluttered: columns, then beams, slabs and walls claim screen space in that order.

        A label that would overlap one already drawn tries its alternative positions, then a shorter text
        (mark only), and is left out when nothing fits; labels never cover a column.
        """
        pal = self.pal
        fm = QFontMetricsF(p.font())
        th = fm.height()
        text_col = QColor(pal["text_canvas"])
        pill = QColor(pal["canvas"])
        pill.setAlpha(200)
        obstacles = []
        for c in plan.columns:
            r = QPolygonF([self.w2s(*q) for q in c.corners()]).boundingRect()
            obstacles.append((r.x(), r.y(), r.width(), r.height()))
        placer = LabelPlacer(obstacles)
        view = QRectF(self.rect())
        # columns: in the diagonal quadrant away from the members framing in, on a soft pill
        segs = [(b.p1, b.p2) for b in plan.beams] + [(w.p1, w.p2) for w in plan.walls]
        for c in plan.columns:
            texts = [c.mark]
            if self.show_loads and res:
                cl = res.columns.get(c.id)
                if cl and cl.total > 0:
                    texts.insert(0, f"{c.mark}  {cl.total:.0f} kN")
            pt = self.w2s(c.x, c.y)
            off = max(c.b, c.d) * self.scale / 2 + 3
            quads = label_quadrants(framing_dirs(c.pos, segs, max(c.b, c.d) / 2 + 0.05))
            opts = []
            for t in texts:
                w, h = fm.horizontalAdvance(t) + 8, th + 2
                for q in quads:
                    x = pt.x() + off if q[1] == "E" else pt.x() - off - w
                    y = pt.y() - off - h if q[0] == "N" else pt.y() + off
                    opts.append((t, (x, y, w, h)))
            # positions inside the view first (stable: keeps the quadrant and text order otherwise)
            opts.sort(key=lambda o: not view.contains(QRectF(*o[1])))
            i = placer.place([r for _, r in opts])
            if i is None:
                continue
            t, r = opts[i]
            rect = QRectF(*r)
            p.setPen(Qt.NoPen)
            p.setBrush(pill)
            p.drawRoundedRect(rect, 4, 4)
            p.setPen(text_col)
            p.drawText(rect, Qt.AlignCenter, t)
            self.painted_labels.append(("column", c.id, r))
        # beams: centred on the span, beside the beam; shortened, then hidden, when longer than the beam
        if self.scale > 12:
            for b in plan.beams:
                texts = [b.mark]
                if self.show_loads and res and b.id in res.beams:
                    texts.insert(0, f"{b.mark}  {res.beams[b.id].equivalent_udl():.1f} kN/m")
                half = max(b.b * self.scale, 2.0) / 2
                self._place_along(p, fm, placer, ("beam", b.id), b.p1, b.p2, texts, half)
        # slabs: only when the text fits inside the slab
        for s in plan.slabs:
            if len(s.points) < 3:
                continue
            poly = QPolygonF([self.w2s(*q) for q in s.pts])
            c = self.w2s(*G.polygon_centroid(s.pts))
            texts = [s.mark]
            if self.show_loads and self.scale > 18:
                texts.insert(0, f"{s.mark}\n{s.dead:.1f}+{s.live_load:.1f}")
            opts = []
            for t in texts:
                br = fm.boundingRect(QRectF(0, 0, 4000, 4000), Qt.AlignCenter, t)
                w, h = br.width() + 4, br.height()
                r = QRectF(c.x() - w / 2, c.y() - h / 2, w, h)
                g = r.adjusted(-4, -4, 4, 4)
                corners = (g.topLeft(), g.topRight(), g.bottomLeft(), g.bottomRight())
                if all(poly.containsPoint(q, Qt.OddEvenFill) for q in corners):
                    opts.append((t, r))
            i = placer.place([(r.x(), r.y(), r.width(), r.height()) for _, r in opts])
            if i is None:
                continue
            t, r = opts[i]
            p.setPen(text_col)
            p.drawText(r, Qt.AlignCenter, t)
            self.painted_labels.append(("slab", s.id, (r.x(), r.y(), r.width(), r.height())))
        # walls
        if self.scale > 12:
            for wl in plan.walls:
                half = wl.thickness * self.scale / 2
                self._place_along(p, fm, placer, ("wall", wl.id), wl.p1, wl.p2, [wl.mark], half, below=True)

    def _place_along(
        self, p: QPainter, fm: QFontMetricsF, placer: LabelPlacer, key, a: Pt, b: Pt, texts, half, below: bool = False
    ):
        """Label written along a member at mid-span, beside its centre line (``half`` = half width in px).

        Tries each text (longest first) on the preferred side, then the other side; texts longer than
        ``LABEL_FILL`` of the member's on-screen length are skipped.
        """
        A, B = self.w2s(*a), self.w2s(*b)
        length = math.hypot(B.x() - A.x(), B.y() - A.y())
        ang = math.degrees(math.atan2(B.y() - A.y(), B.x() - A.x()))
        if ang >= 90 - 1e-6 or ang < -90 - 1e-6:  # keep text readable: left-to-right or bottom-to-top
            ang += -180 if ang > 0 else 180
        mid = (A + B) / 2
        tf = QTransform()
        tf.translate(mid.x(), mid.y())
        tf.rotate(ang)
        h, gap = fm.height(), 2.0
        opts = []
        for t in texts:
            w = fm.horizontalAdvance(t)
            if w > LABEL_FILL * length:
                continue
            above = QRectF(-w / 2, -half - gap - h, w, h)
            under = QRectF(-w / 2, half + gap, w, h)
            for local in (under, above) if below else (above, under):
                br = tf.mapRect(local)
                opts.append((t, local, (br.x(), br.y(), br.width(), br.height())))
        i = placer.place([o[2] for o in opts])
        if i is None:
            return
        t, local, r = opts[i]
        p.save()
        p.setTransform(tf, True)
        p.setPen(QColor(self.pal["text_canvas"]))
        p.drawText(local, Qt.AlignCenter, t)
        p.restore()
        self.painted_labels.append((key[0], key[1], r))

    def _paint_dim(self, p: QPainter, a: Pt, b: Pt, off: float, col: QColor):
        """Dimension: extension lines, dimension line with oblique ticks and the length in m."""
        L = G.dist(a, b)
        if L < 1e-6:
            return
        nx, ny = -(b[1] - a[1]) / L, (b[0] - a[0]) / L
        A, B = self.w2s(*a), self.w2s(*b)
        DA = self.w2s(a[0] + nx * off, a[1] + ny * off)
        DB = self.w2s(b[0] + nx * off, b[1] + ny * off)
        p.setPen(QPen(col, 1))
        p.setBrush(Qt.NoBrush)
        ext = DA - A
        n_px = math.hypot(ext.x(), ext.y())
        if n_px > 4:
            u = ext / n_px
            for P0, P1 in ((A, DA), (B, DB)):
                p.drawLine(P0 + u * 3, P1 + u * 4)
        p.drawLine(DA, DB)
        d = DB - DA
        dl = math.hypot(d.x(), d.y()) or 1.0
        ux, uy = d.x() / dl, d.y() / dl
        tick = QPointF((ux - uy) * 4, (uy + ux) * 4)  # 45° slash
        p.setPen(QPen(col, 1.6))
        for P in (DA, DB):
            p.drawLine(P - tick, P + tick)
        mid = (DA + DB) / 2
        ang = math.degrees(math.atan2(d.y(), d.x()))
        if ang > 90 or ang < -90:
            ang += 180
        p.save()
        p.translate(mid)
        p.rotate(ang)
        p.setPen(col)
        p.drawText(QRectF(-60, -16, 120, 14), Qt.AlignHCenter | Qt.AlignBottom, f"{L:.3f}")
        p.restore()

    def _grid_anchor(self, plan: Plan | None) -> tuple[float, float]:
        """Screen (x, y) where grid bubbles sit: left / top of the plan extents, kept in view."""
        r = 11
        if plan and plan.all_points():
            x0, _, _, y1 = plan.extents()
        else:
            ys = [g[2] for g in self.grid_lines() if g[1] == "y"] or [0.0]
            xs = [g[2] for g in self.grid_lines() if g[1] == "x"] or [0.0]
            x0, y1 = min(xs), max(ys)
        tl = self.w2s(x0, y1)
        bx = min(max(tl.x() - 36, r + 4), self.width() - r - 4)
        by = min(max(tl.y() - 36, r + 4), self.height() - r - 4)
        return bx, by

    def _paint_grid_lines(self, p: QPainter, plan: Plan | None):
        lines = self.grid_lines()
        if not lines:
            return
        p.setPen(QPen(QColor(self.pal["muted"]), 1, Qt.DashDotLine))
        for _, axis, at in lines:
            if axis == "x":
                sx = self.w2s(at, 0).x()
                p.drawLine(QPointF(sx, 0), QPointF(sx, self.height()))
            else:
                sy = self.w2s(0, at).y()
                p.drawLine(QPointF(0, sy), QPointF(self.width(), sy))

    def _paint_grid_bubbles(self, p: QPainter, plan: Plan | None):
        lines = self.grid_lines()
        if not lines:
            return
        pal = self.pal
        bx, by = self._grid_anchor(plan)
        r = 11.0
        f = QFont(self.font())
        f.setPointSizeF(8)
        f.setBold(True)
        p.save()
        p.setFont(f)
        for name, axis, at in lines:
            c = QPointF(self.w2s(at, 0).x(), by) if axis == "x" else QPointF(bx, self.w2s(0, at).y())
            p.setPen(QPen(QColor(pal["muted"]), 1.2))
            p.setBrush(QColor(pal["canvas"]))
            p.drawEllipse(c, r, r)
            p.setPen(QColor(pal["text_canvas"]))
            p.drawText(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), Qt.AlignCenter, name)
        p.restore()

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
            a, b = self._beam_start, self._mouse_world
            if self.tool == "wall":
                t = max(self.main.defaults.get("wall_t", 0.2) * self.scale, 2.0)
                p.setPen(QPen(QColor(self.wall_colour), t, Qt.SolidLine, Qt.FlatCap))
            else:
                p.setPen(QPen(QColor(pal["select"]), 1.5 if self.tool == "measure" else 3))
            p.drawLine(self.w2s(*a), self.w2s(*b))
            p.setPen(QColor(pal["text"]))
            txt = f"{G.dist(a, b):.2f} m"
            if self.tool == "measure":
                txt = f"{G.dist(a, b):.3f} m   ΔX {b[0] - a[0]:.3f}   ΔY {b[1] - a[1]:.3f}"
            p.drawText(self.w2s(*b) + QPointF(10, -10), txt)
        if self._area_closed or self._area_pts:
            closed = self._area_closed is not None
            pts = list(self._area_closed) if closed else self._area_pts + [self._mouse_world]
            ok = QColor(pal["ok"])
            fill = QColor(ok)
            fill.setAlpha(70 if closed else 40)
            p.setPen(QPen(ok, 2.5 if closed else 1.5, Qt.SolidLine if closed else Qt.DashLine))
            p.setBrush(fill)
            p.drawPolygon(QPolygonF([self.w2s(*q) for q in pts]))
            a, per = polygon_stats(pts)
            at = self.w2s(*G.polygon_centroid(pts)) if closed and len(pts) >= 3 else self.w2s(*self._mouse_world)
            p.setPen(QColor(pal["text"]))
            txt = f"A = {a:.3f} m²   P = {per:.2f} m"
            if closed:  # below the centroid, clear of the slab mark
                p.drawText(QRectF(at.x() - 120, at.y() + 22, 240, 16), Qt.AlignCenter, txt)
            else:
                p.drawText(at + QPointF(10, 18), txt)
        if self._dim_pts:
            col = QColor(pal["select"])
            if len(self._dim_pts) == 1:
                p.setPen(QPen(col, 1, Qt.DashLine))
                p.drawLine(self.w2s(*self._dim_pts[0]), self.w2s(*self._mouse_world))
            else:
                a, b = self._dim_pts
                self._paint_dim(p, a, b, dim_offset(a, b, self._mouse_world), col)
        if self._snap_pt and self.tool not in ("select", "pan"):
            q = self.w2s(*self._snap_pt)
            p.setPen(QPen(QColor(pal["ok"]), 2))
            p.setBrush(Qt.NoBrush)
            if self._snap_kind == "mid":
                p.drawPolygon(QPolygonF([q + QPointF(0, -7), q + QPointF(6, 5), q + QPointF(-6, 5)]))
            elif self._snap_kind in ("grid", "line"):
                p.drawLine(q + QPointF(-6, -6), q + QPointF(6, 6))
                p.drawLine(q + QPointF(-6, 6), q + QPointF(6, -6))
            else:
                p.drawRect(QRectF(q.x() - 6, q.y() - 6, 12, 12))
        if self.ortho and self.tool not in ("select", "pan"):
            p.setPen(QColor(pal["muted"]))
            p.drawText(QPointF(16, 20), "ORTHO")

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
            if self._area_pts:
                self._close_area()
                return
            self._context_menu(ev)
            return
        if ev.button() != Qt.LeftButton or self.plan is None:
            return
        pt = self.pick(ev.position(), ev.modifiers()) if self.tool not in ("select",) else self.s2w(ev.position())
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
        elif self.tool in ("beam", "wall", "measure"):
            if self._beam_start is None:
                self._beam_start = pt
            else:
                if self.tool == "beam":
                    self._add_beam(self._beam_start, pt)
                elif self.tool == "wall":
                    self._add_wall(self._beam_start, pt)
                else:
                    a = self._beam_start
                    self.status.emit(f"Distance {G.dist(a, pt):.3f} m   ΔX {pt[0] - a[0]:.3f}   ΔY {pt[1] - a[1]:.3f}")
                self._beam_start = None
        elif self.tool == "area":
            if self._area_closed is not None:
                self._area_closed = None
                self._area_pts = []
            if len(self._area_pts) >= 3 and G.dist(pt, self._area_pts[0]) < 10 / self.scale:
                self._close_area()
            else:
                self._area_pts.append(pt)
        elif self.tool == "dimension":
            if len(self._dim_pts) < 2:
                if not self._dim_pts or G.dist(pt, self._dim_pts[0]) > 1e-6:
                    self._dim_pts.append(pt)
            else:
                a, b = self._dim_pts
                self._dim_pts = []
                self._add_dimension(a, b, pt)
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
        w = self.pick(ev.position(), ev.modifiers()) if self.tool not in ("select", "pan") else self.s2w(ev.position())
        self._mouse_world = w
        self.cursorMoved.emit(*w)
        if self._drag_rect and self._press_world:
            self._drag_rect = (self._press_world, w)
        self.update()

    def mouseReleaseEvent(self, ev: QMouseEvent):
        if self._panning:
            self._panning = False
            self.setCursor(self._tool_cursor())  # a drawing tool keeps its cross after a middle-button pan
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
                for o in self.plan.beams + self.plan.walls:
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
        elif self.tool == "area" and len(self._area_pts) >= 3:
            self._close_area()
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
            self._area_pts.clear()
            self._area_closed = None
            self._dim_pts.clear()
            if self.tool != "select":
                self.main.set_tool("select")
            else:
                self.select_ids([])
        elif k in (Qt.Key_Return, Qt.Key_Enter) and self._poly:
            self._finish_poly()
        elif k in (Qt.Key_Return, Qt.Key_Enter) and self._area_pts:
            self._close_area()
        elif k == Qt.Key_F8:
            self.set_ortho(not self.ortho)
        elif k == Qt.Key_Delete and self.selection:
            self.main.delete_selection()
        elif k == Qt.Key_F:
            self.zoom_extents()
        elif k == Qt.Key_A and ev.modifiers() & Qt.ControlModifier and self.plan:
            pl = self.plan
            self.select_ids([o.id for o in pl.slabs + pl.beams + pl.walls + pl.columns])
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

    def _close_area(self):
        pts = list(self._area_pts)
        self._area_pts = []
        if len(pts) < 3:
            self._area_closed = None
            self.update()
            return
        a, per = polygon_stats(pts)
        self._area_closed = pts
        self.last_area = (a, per)
        self.status.emit(f"Area {a:.3f} m²  Perimeter {per:.2f} m")
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

    def _add_wall(self, a, b):
        if G.dist(a, b) < 0.1:
            return
        plan = self.plan
        d = self.main.defaults

        def fn():
            w = Wall(
                mark=plan.next_mark("W"),
                x1=a[0],
                y1=a[1],
                x2=b[0],
                y2=b[1],
                thickness=d.get("wall_t", 0.2),
                grade=d["grade"],
            )
            plan.walls.append(w)
            self.selection = [w.id]

        self.main.mutate("Add wall", fn)

    def _add_dimension(self, a, b, at):
        if G.dist(a, b) < 1e-3:
            return
        plan = self.plan
        dm = {
            "x1": round(a[0], 4),
            "y1": round(a[1], 4),
            "x2": round(b[0], 4),
            "y2": round(b[1], 4),
            "offset": round(dim_offset(a, b, at), 4),
        }
        # dimensions are drawing annotations: the analysis and design results stay valid
        self.main.mutate("Add dimension", lambda: plan.dimensions.append(dm), analysis=False)

    # ----------------------------------------------------------- context menu
    def _context_menu(self, ev: QMouseEvent):
        x, y = self.s2w(ev.position())
        obj = self.hit(x, y)
        di = self.dim_at(x, y)
        plan = self.plan
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
        if isinstance(obj, Wall):
            m.addSeparator()
            m.addAction(f"Wall length {obj.length:.2f} m").setEnabled(False)
        if isinstance(obj, Column):
            m.addSeparator()
            m.addAction("Load analysis (break-up)", lambda: self.main.show_column_breakup(obj))
            m.addAction(
                "Rotate 90°",
                lambda: self.main.mutate("Rotate column", lambda: setattr(obj, "angle", (obj.angle + 90) % 180)),
            )
        if plan is not None and plan.dimensions:
            m.addSeparator()
            if di is not None:
                m.addAction(
                    "Delete dimension",
                    lambda: self.main.mutate("Delete dimension", lambda: self._del_dim(di), analysis=False),
                )
            m.addAction(
                "Clear all dimensions on this plan",
                lambda: self.main.mutate("Clear dimensions", lambda: plan.dimensions.clear(), analysis=False),
            )
        m.addSeparator()
        m.addAction("Zoom extents (F)", self.zoom_extents)
        m.exec(ev.globalPosition().toPoint())

    def _del_dim(self, i: int):
        plan = self.plan
        if plan is not None and 0 <= i < len(plan.dimensions):
            del plan.dimensions[i]

    def _set_cant_edge(self, s: Slab, x: float, y: float):
        pts = s.pts
        best = min(range(len(pts)), key=lambda i: G.point_segment_distance((x, y), pts[i], pts[(i + 1) % len(pts)]))

        def fn():
            s.distribution = "cantilever"
            s.cant_edge = best

        self.main.mutate("Cantilever slab", fn)
