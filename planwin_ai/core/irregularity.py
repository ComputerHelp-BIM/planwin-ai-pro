"""Plan and vertical irregularity checks to IS 1893 (Part 1):2016 Tables 5 and 6.

Evaluated on the equivalent static analysis (always run).  The checks decide whether a
building is *regular* for cl 7.7.1 (when dynamic analysis may be skipped) and are listed
in the reports so the engineer can review the structural configuration.

Not evaluated automatically: strength irregularity / weak storey (Table 6 (v)), which
needs storey shear capacities – it is listed as "not checked".
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from . import geometry as G

if TYPE_CHECKING:
    from .frame import FrameAnalysis


@dataclass
class Irregularity:
    table: str  # "Plan (Table 5)" | "Vertical (Table 6)"
    name: str
    clause: str
    irregular: bool | None  # None = not evaluated
    detail: str
    severe: bool = False  # beyond the limit where the code asks to reconfigure

    @property
    def status(self) -> str:
        if self.irregular is None:
            return "not checked"
        return ("IRREGULAR – reconfigure" if self.severe else "IRREGULAR") if self.irregular else "regular"


# --------------------------------------------------------------------- helpers
def _raster(plan, step: float = 0.1):
    """Occupancy grid of the plan's suspended slabs: (grid, x0, y0, step)."""
    slabs = [s for s in plan.slabs if len(s.points) >= 3]
    if not slabs:
        return None
    pts = [p for s in slabs for p in s.pts]
    x0, y0, x1, y1 = G.bbox(pts)
    nx, ny = max(int(math.ceil((x1 - x0) / step)), 1), max(int(math.ceil((y1 - y0) / step)), 1)
    if nx * ny > 400_000:  # keep it cheap on very large plans
        step = math.sqrt((x1 - x0) * (y1 - y0) / 400_000)
        nx, ny = max(int(math.ceil((x1 - x0) / step)), 1), max(int(math.ceil((y1 - y0) / step)), 1)
    grid = np.zeros((ny, nx), dtype=bool)
    xs = x0 + (np.arange(nx) + 0.5) * step
    ys = y0 + (np.arange(ny) + 0.5) * step
    for s in slabs:
        sx0, sy0, sx1, sy1 = G.bbox(s.pts)
        ix = np.where((xs >= sx0) & (xs <= sx1))[0]
        iy = np.where((ys >= sy0) & (ys <= sy1))[0]
        for j in iy:
            for i in ix:
                if not grid[j, i] and G.point_in_polygon((xs[i], ys[j]), s.pts):
                    grid[j, i] = True
    return grid, x0, y0, step


def _reentrant(grid: np.ndarray) -> tuple[float, float]:
    """Largest re-entrant projection as a fraction of the plan dimension, in X and Y."""
    ny, nx = grid.shape
    px = py = 0.0
    for j in range(ny):
        row = np.where(grid[j])[0]
        if row.size:
            px = max(px, (nx - (row[-1] - row[0] + 1)) / nx)
    for i in range(nx):
        col = np.where(grid[:, i])[0]
        if col.size:
            py = max(py, (ny - (col[-1] - col[0] + 1)) / ny)
    return px, py


def _openings(grid: np.ndarray) -> float:
    """Area of enclosed openings (cells not reachable from outside) / gross enclosed area."""
    ny, nx = grid.shape
    outside = np.zeros_like(grid)
    q = deque()
    for i in range(nx):
        for j in (0, ny - 1):
            if not grid[j, i] and not outside[j, i]:
                outside[j, i] = True
                q.append((j, i))
    for j in range(ny):
        for i in (0, nx - 1):
            if not grid[j, i] and not outside[j, i]:
                outside[j, i] = True
                q.append((j, i))
    while q:
        j, i = q.popleft()
        for dj, di in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            a, b = j + dj, i + di
            if 0 <= a < ny and 0 <= b < nx and not grid[a, b] and not outside[a, b]:
                outside[a, b] = True
                q.append((a, b))
    holes = int((~grid & ~outside).sum())
    gross = int(grid.sum()) + holes
    return holes / gross if gross else 0.0


# --------------------------------------------------------------------- checks
def check_irregularities(fa: FrameAnalysis) -> list[Irregularity]:
    m = fa.model
    p = m.p
    out: list[Irregularity] = []
    base = p.seismic.base_level
    levels = m.levels
    upper = [i for i in range(1, len(levels)) if i > base]
    plans = [(i, p.plan(levels[i].plan)) for i in upper]

    # ---- Table 5 (i) torsional irregularity: Δmax / Δmin of floor displacement
    worst, where = 0.0, ""
    for case, tcase, axis in (("EQX", "ETX", 0), ("EQY", "ETY", 1)):
        if case not in fa.res.disp:
            continue
        # accidental eccentricity on either side of the centre of mass (cl 7.8.2): the worse one governs
        variants = [({case: 1.0, tcase: sg}, f"{case}{'+' if sg > 0 else '-'}{tcase}") for sg in (1.0, -1.0)]
        if tcase not in fa.res.disp:
            variants = [({case: 1.0}, case)]
        for fac, label in variants:
            for i in upper:
                nodes = list(levels[i].column_nodes.values()) + list(levels[i].wall_nodes.values())
                if len(nodes) < 2:
                    continue
                other = 1 - axis
                coord = [(m.nodes[n].y if other == 1 else m.nodes[n].x) for n in nodes]
                lo, hi = nodes[int(np.argmin(coord))], nodes[int(np.argmax(coord))]
                d1 = abs(fa.displacement(lo, fac)[axis])
                d2 = abs(fa.displacement(hi, fac)[axis])
                if min(d1, d2) > 1e-9:
                    r = max(d1, d2) / min(d1, d2)
                    if r > worst:
                        worst, where = r, f"{levels[i].name} ({label})"
    out.append(
        Irregularity(
            "Plan (Table 5)",
            "Torsional irregularity",
            "Table 5 (i)",
            worst > 1.5 if worst else False,
            f"max/min floor displacement = {worst:.2f} at {where or '-'} (limit 1.5; > 2.0 reconfigure)",
            severe=worst > 2.0,
        )
    )

    # ---- Table 5 (ii) re-entrant corners, (iii) floor openings
    reent, opening = (0.0, ""), (0.0, "")
    seen = set()
    for _i, plan in plans:
        if plan is None or plan.name in seen:
            continue
        seen.add(plan.name)
        r = _raster(plan)
        if r is None:
            continue
        grid = r[0]
        px, py = _reentrant(grid)
        if max(px, py) > reent[0]:
            reent = (max(px, py), plan.name)
        op = _openings(grid)
        if op > opening[0]:
            opening = (op, plan.name)
    out.append(
        Irregularity(
            "Plan (Table 5)",
            "Re-entrant corners",
            "Table 5 (ii)",
            reent[0] > 0.15,
            f"largest projection = {reent[0]:.0%} of the plan dimension in plan '{reent[1] or '-'}' (limit 15 %)",
        )
    )
    out.append(
        Irregularity(
            "Plan (Table 5)",
            "Floor slabs with excessive openings",
            "Table 5 (iii)",
            opening[0] > 0.5,
            f"openings = {opening[0]:.0%} of the floor area in plan '{opening[1] or '-'}' (limit 50 %)",
        )
    )

    # ---- Table 5 (iv) out-of-plane offsets / Table 6 (vi) floating columns
    floats = [i.message for i in m.issues if "floats" in i.message or "offset" in i.message]
    out.append(
        Irregularity(
            "Plan (Table 5)",
            "Out-of-plane offsets in vertical elements",
            "Table 5 (iv)",
            bool(floats),
            "; ".join(floats[:3]) or "columns and walls continuous to the foundation",
        )
    )

    # ---- Table 5 (v) non-parallel lateral force system
    skew = sorted(
        {
            f"{c.mark} ({c.angle:g}°)"
            for _, pl in plans
            if pl
            for c in pl.columns
            if min(c.angle % 90, 90 - c.angle % 90) > 1.0
        }
    )
    skew += sorted(
        {f"wall {w.mark}" for _, pl in plans if pl for w in pl.walls if min(w.angle % 90, 90 - w.angle % 90) > 1.0}
    )
    out.append(
        Irregularity(
            "Plan (Table 5)",
            "Non-parallel lateral force system",
            "Table 5 (v)",
            bool(skew),
            ", ".join(skew[:6]) or "all vertical elements parallel to the X/Y axes",
        )
    )

    # ---- Table 6 (i) stiffness irregularity (soft storey): storey stiffness < storey above
    soft = []
    for case, axis in (("EQX", 0), ("EQY", 1)):
        if case not in fa.res.disp or case not in m.seismic:
            continue
        forces = m.seismic[case].forces
        shear = [sum(forces[k] for k in range(i, len(forces))) for i in range(len(forces))]
        k = {}
        for i in range(1, len(levels)):
            drifts = [
                abs(fa.displacement(nid, {case: 1})[axis] - fa.displacement(below, {case: 1})[axis])
                for nid, below in m.storey_joint_pairs(i)
            ]
            d = float(np.mean(drifts)) if drifts else 0.0
            if d > 1e-9 and shear[i] > 1e-9:
                k[i] = shear[i] / d
        for i in upper:
            if i in k and i + 1 in k and k[i] < k[i + 1]:
                soft.append(f"{levels[i].name} {case[-1]}: K = {k[i] / k[i + 1]:.2f} × storey above")
    out.append(
        Irregularity(
            "Vertical (Table 6)",
            "Stiffness irregularity (soft storey)",
            "Table 6 (i)",
            bool(soft),
            "; ".join(soft[:4]) or "every storey at least as stiff as the storey above",
        )
    )

    # ---- Table 6 (ii) mass irregularity (roof excluded)
    w = [lv.weight for lv in levels]
    mass = []
    top = upper[-1] if upper else None
    for i in upper:
        if i == top or i - 1 <= base:
            continue
        if w[i - 1] > 0 and w[i] > 1.5 * w[i - 1]:
            mass.append(f"{levels[i].name}: {w[i] / w[i - 1]:.2f} × floor below")
    out.append(
        Irregularity(
            "Vertical (Table 6)",
            "Mass irregularity",
            "Table 6 (ii)",
            bool(mass),
            "; ".join(mass) or "no floor heavier than 150 % of the floor below (roof excluded)",
        )
    )

    # ---- Table 6 (iii) vertical geometric irregularity
    geo = []
    dims = {}
    for i in range(1, len(levels)):
        pl = p.plan(levels[i].plan)
        pts = [c.pos for c in pl.columns] + [q for wl in pl.walls for q in (wl.p1, wl.p2)] if pl else []
        if pts:
            x0, y0, x1, y1 = G.bbox(pts)
            dims[i] = (x1 - x0, y1 - y0)
    for i in upper:
        if i in dims and i - 1 in dims:
            for k_, ax in ((0, "X"), (1, "Y")):
                if dims[i - 1][k_] > 1e-6 and dims[i][k_] > 1.25 * dims[i - 1][k_]:
                    geo.append(f"{levels[i].name} {ax}: {dims[i][k_] / dims[i - 1][k_]:.2f} × storey below")
    out.append(
        Irregularity(
            "Vertical (Table 6)",
            "Vertical geometric irregularity",
            "Table 6 (iii)",
            bool(geo),
            "; ".join(geo) or "no storey wider than 125 % of the storey below",
        )
    )

    # ---- Table 6 (iv) in-plane discontinuity, (vi) floating columns
    disc = [i.message for i in m.issues if i.level == "error" and ("no column" in i.message or "no wall" in i.message)]
    flt = [i.message for i in m.issues if "floats" in i.message]
    out.append(
        Irregularity(
            "Vertical (Table 6)",
            "In-plane discontinuity in vertical elements",
            "Table 6 (iv)",
            bool(disc),
            "; ".join(disc[:3]) or "none found",
        )
    )
    out.append(
        Irregularity(
            "Vertical (Table 6)",
            "Floating or stub columns",
            "Table 6 (vi)",
            bool(flt),
            "; ".join(flt[:3]) or "none",
            severe=bool(flt),
        )
    )
    out.append(
        Irregularity(
            "Vertical (Table 6)",
            "Strength irregularity (weak storey)",
            "Table 6 (v)",
            None,
            "needs storey shear capacities – check manually",
        )
    )
    return out


def is_regular(checks: list[Irregularity]) -> bool:
    return not any(c.irregular for c in checks)
