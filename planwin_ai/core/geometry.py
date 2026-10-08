"""Planar geometry helpers used by the plan engine, canvas and importers.

All coordinates are metres in a right-handed X (east) / Y (north) plan system.
Functions are pure and side-effect free so they are easy to unit test.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

Point = tuple[float, float]

#: Default geometric tolerance (m). 5 mm is well below drafting accuracy.
TOL = 5e-3


def dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def same_point(a: Point, b: Point, tol: float = TOL) -> bool:
    return dist(a, b) <= tol


def polygon_area(pts: Sequence[Point]) -> float:
    """Signed area (positive when counter-clockwise)."""
    n = len(pts)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return 0.5 * s


def polygon_centroid(pts: Sequence[Point]) -> Point:
    """Area centroid of a simple polygon (falls back to vertex mean if degenerate)."""
    a = polygon_area(pts)
    if abs(a) < 1e-12:
        n = max(len(pts), 1)
        return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)
    cx = cy = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        c = x1 * y2 - x2 * y1
        cx += (x1 + x2) * c
        cy += (y1 + y2) * c
    return (cx / (6 * a), cy / (6 * a))


def ensure_ccw(pts: Sequence[Point]) -> list[Point]:
    pts = list(pts)
    return pts if polygon_area(pts) >= 0 else pts[::-1]


def point_in_polygon(p: Point, pts: Sequence[Point]) -> bool:
    """Ray casting; points on the boundary count as inside."""
    x, y = p
    n = len(pts)
    inside = False
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        if point_segment_distance(p, a, b) <= TOL:
            return True
        if (a[1] > y) != (b[1] > y):
            xint = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x < xint:
                inside = not inside
    return inside


def project_param(p: Point, a: Point, b: Point) -> float:
    """Parameter t of the projection of p on line a->b (0 at a, 1 at b)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 < 1e-18:
        return 0.0
    return ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    t = max(0.0, min(1.0, project_param(p, a, b)))
    q = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    return dist(p, q)


def point_line_distance(p: Point, a: Point, b: Point) -> float:
    """Perpendicular distance from p to the infinite line through a, b."""
    L = dist(a, b)
    if L < 1e-12:
        return dist(p, a)
    return abs((b[0] - a[0]) * (a[1] - p[1]) - (a[0] - p[0]) * (b[1] - a[1])) / L


def is_point_on_segment(p: Point, a: Point, b: Point, tol: float = TOL) -> bool:
    return point_segment_distance(p, a, b) <= tol


def lerp(a: Point, b: Point, t: float) -> Point:
    return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))


def segment_intersection(a: Point, b: Point, c: Point, d: Point, tol: float = TOL):
    """Intersection of segments ab and cd.

    Returns (point, t_ab, t_cd) for a proper/touching intersection of
    non-parallel segments, otherwise ``None``.
    """
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    denom = r[0] * s[1] - r[1] * s[0]
    if abs(denom) < 1e-12:
        return None
    qp = (c[0] - a[0], c[1] - a[1])
    t = (qp[0] * s[1] - qp[1] * s[0]) / denom
    u = (qp[0] * r[1] - qp[1] * r[0]) / denom
    La, Lc = math.hypot(*r), math.hypot(*s)
    et = tol / La if La else 0
    eu = tol / Lc if Lc else 0
    if -et <= t <= 1 + et and -eu <= u <= 1 + eu:
        return (lerp(a, b, t), min(max(t, 0.0), 1.0), min(max(u, 0.0), 1.0))
    return None


def collinear_overlap(a: Point, b: Point, c: Point, d: Point, tol: float = TOL):
    """Overlap of segment cd lying on segment ab (collinear).

    Returns (t0, t1) parameters on ab of the shared portion, or ``None``.
    """
    if point_line_distance(c, a, b) > tol or point_line_distance(d, a, b) > tol:
        return None
    L = dist(a, b)
    if L < 1e-12:
        return None
    t_c, t_d = project_param(c, a, b), project_param(d, a, b)
    lo, hi = max(0.0, min(t_c, t_d)), min(1.0, max(t_c, t_d))
    if (hi - lo) * L <= tol:
        return None
    return lo, hi


def snap(value: float, step: float) -> float:
    return round(value / step) * step if step > 0 else value


def bbox(points: Iterable[Point]):
    pts = list(points)
    if not pts:
        return (0.0, 0.0, 10.0, 10.0)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def is_rectangle(pts: Sequence[Point], tol: float = 1e-3) -> bool:
    """True for a 4-vertex polygon whose corners are right angles."""
    if len(pts) != 4:
        return False
    for i in range(4):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % 4]
        v1 = (a[0] - b[0], a[1] - b[1])
        v2 = (c[0] - b[0], c[1] - b[1])
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        if n1 < 1e-9 or n2 < 1e-9:
            return False
        if abs((v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)) > tol:
            return False
    return True


def rect_corners(cx: float, cy: float, b: float, d: float, angle_deg: float) -> list[Point]:
    """Corners of a b x d rectangle centred at (cx, cy); b along local x, rotated by angle."""
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    out = []
    for lx, ly in ((-b / 2, -d / 2), (b / 2, -d / 2), (b / 2, d / 2), (-b / 2, d / 2)):
        out.append((cx + lx * ca - ly * sa, cy + lx * sa + ly * ca))
    return out


def mirror_point(p: Point, axis: str, at: float) -> Point:
    """Mirror about a vertical (axis='x', line x=at) or horizontal (axis='y') line."""
    if axis == "x":
        return (2 * at - p[0], p[1])
    return (p[0], 2 * at - p[1])
