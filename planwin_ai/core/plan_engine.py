"""PlanWin engine: slab -> beam -> column load transfer for one floor plan.

Pipeline (same philosophy as legacy PlanWin, re-implemented):

1. Topology – which columns sit on each beam and which beams rest on other
   beams (primary / secondary resolution, T-junctions, cantilevers).
2. Slab load distribution to edges (45° yield lines for rectangles, CG
   triangles for convex irregular slabs, one-way, cantilever, uniform).
3. Edge loads mapped onto the beams that lie along each edge.
4. Beams solved in dependency order (secondary before primary); reactions
   become point loads on supporting beams or column loads.
5. Equilibrium check: total applied load must equal total column reaction.

Results are plain data so the GUI, reports and exporters can consume them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from . import geometry as G
from .beamcalc import LinLoad, PtLoad, continuous_beam, diagrams, simple_span_reactions, total
from .model import Beam, Column, Plan, Slab

END_TOL = 0.02  # m – how close a point must be to a beam end to count as "at the end"


@dataclass
class Issue:
    level: str  # "error" | "warning"
    kind: str  # slab | beam | column | topology
    message: str
    obj_id: Optional[str] = None
    at: Optional[tuple[float, float]] = None


@dataclass
class Support:
    x: float  # beam-local position
    kind: str  # "column" | "beam"
    ref: str  # column id or supporting beam id
    point: tuple[float, float]


@dataclass
class BeamResult:
    beam_id: str
    length: float
    supports: list[Support] = field(default_factory=list)
    loads: list = field(default_factory=list)  # LinLoad / PtLoad (D & L)
    reactions: dict[str, list[float]] = field(default_factory=dict)  # case -> per support
    rank: int = 1

    def total(self, case: str) -> float:
        return total(self.loads, case)

    def equivalent_udl(self, case: Optional[str] = None) -> float:
        tot = sum(total(self.loads, c) for c in (("D", "L") if case is None else (case,)))
        return tot / self.length if self.length > 0 else 0.0

    def diagram(self, factored: bool = True, n: int = 81):
        """Combined (1.5 D + 1.5 L when factored) shear/moment diagram."""
        f = 1.5 if factored else 1.0
        reacts = []
        for i, s in enumerate(self.supports):
            r = sum(self.reactions.get(c, [0.0] * len(self.supports))[i] for c in ("D", "L"))
            reacts.append((s.x, f * r))
        scaled = []
        for ld in self.loads:
            if isinstance(ld, LinLoad):
                scaled.append(LinLoad(ld.a, ld.b, ld.w1 * f, ld.w2 * f, ld.case, ld.src))
            else:
                scaled.append(PtLoad(ld.x, ld.P * f, ld.case, ld.src))
        return diagrams(self.length, scaled, reacts, None, n)


@dataclass
class ColumnLoad:
    column_id: str
    mark: str
    dead: float = 0.0
    live: float = 0.0
    parts: list[tuple[str, float, float]] = field(default_factory=list)  # (beam mark, D, L)

    @property
    def total(self) -> float:
        return self.dead + self.live


@dataclass
class PlanResult:
    plan_name: str
    beams: dict[str, BeamResult] = field(default_factory=dict)
    columns: dict[str, ColumnLoad] = field(default_factory=dict)
    slab_edges: dict[str, list[list[tuple[float, float]]]] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    applied: dict[str, float] = field(default_factory=lambda: {"D": 0.0, "L": 0.0})
    reacted: dict[str, float] = field(default_factory=lambda: {"D": 0.0, "L": 0.0})
    breakdown: dict[str, float] = field(default_factory=dict)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def imbalance_pct(self) -> float:
        a = self.applied["D"] + self.applied["L"]
        r = self.reacted["D"] + self.reacted["L"]
        return 0.0 if a <= 1e-9 else 100.0 * (a - r) / a

    @property
    def ok(self) -> bool:
        return not self.errors


def column_on_beam(c: Column, b: Beam, tol: float = 0.05) -> Optional[float]:
    """Parameter t (0..1) where column ``c`` supports beam ``b``, else None.

    The column footprint (rotated b x d rectangle) must touch the beam
    centre-line, which also covers face-aligned (offset) columns.
    """
    L = b.length
    if L < 1e-9:
        return None
    ux, uy = (b.x2 - b.x1) / L, (b.y2 - b.y1) / L
    nx, ny = -uy, ux
    a = math.radians(c.angle)
    cb = (math.cos(a), math.sin(a))  # column local x (dimension b)
    cd = (-math.sin(a), math.cos(a))  # column local y (dimension d)
    half_n = abs(nx * cb[0] + ny * cb[1]) * c.b / 2 + abs(nx * cd[0] + ny * cd[1]) * c.d / 2
    half_u = abs(ux * cb[0] + uy * cb[1]) * c.b / 2 + abs(ux * cd[0] + uy * cd[1]) * c.d / 2
    rx, ry = c.x - b.x1, c.y - b.y1
    along = rx * ux + ry * uy
    perp = abs(rx * nx + ry * ny)
    if perp > half_n + tol or along < -(half_u + tol) or along > L + half_u + tol:
        return None
    return min(max(along / L, 0.0), 1.0)


# =========================================================================
# Slab distribution
# =========================================================================
EdgeProfile = list[tuple[float, float]]  # [(s, w)] piecewise linear along edge, kN/m per kN/m^2


def slab_edge_profiles(slab: Slab, ratio_limit: float = 2.0) -> tuple[list[EdgeProfile], str, list[str]]:
    """Distribution profile per edge for a unit (1 kN/m^2) slab load.

    Returns (profiles, method, notes).  Profiles integrate to the slab area.
    """
    pts = G.ensure_ccw(slab.pts)
    # keep edge indexing consistent with slab.points ordering
    if pts != slab.pts:
        pts = slab.pts
    n = len(pts)
    edges = [(pts[i], pts[(i + 1) % n]) for i in range(n)]
    lens = [G.dist(a, b) for a, b in edges]
    area = abs(G.polygon_area(pts))
    notes: list[str] = []
    dist = slab.distribution
    prof: list[EdgeProfile] = [[(0.0, 0.0), (L, 0.0)] for L in lens]

    if dist == "on_grade":
        return prof, "on grade", notes

    if dist == "cantilever":
        i = slab.cant_edge if slab.cant_edge is not None and 0 <= slab.cant_edge < n else int(max(range(n), key=lambda k: lens[k]))
        if slab.cant_edge is None:
            notes.append("cantilever fixed edge not set – longest edge assumed")
        w = area / lens[i]
        prof[i] = [(0.0, w), (lens[i], w)]
        return prof, "cantilever", notes

    if dist == "uniform":
        per = sum(lens)
        return [[(0.0, area / per), (L, area / per)] for L in lens], "uniform", notes

    if G.is_rectangle(pts):
        lx, ly = sorted((lens[0], lens[1]))
        ratio = ly / lx if lx > 0 else 1.0
        mode = dist
        if dist == "auto":
            mode = "two_way" if ratio <= ratio_limit else "one_way"
        square = abs(lx - ly) <= 1e-3
        if mode == "two_way":
            for k, L in enumerate(lens):
                if square or abs(L - lx) < abs(L - ly):
                    # short edge (or square): triangle of height lx/2
                    prof[k] = [(0.0, 0.0), (L / 2, lx / 2), (L, 0.0)]
                else:
                    prof[k] = [(0.0, 0.0), (lx / 2, lx / 2), (L - lx / 2, lx / 2), (L, 0.0)]
            return prof, f"two-way (ly/lx = {ratio:.2f})", notes
        if mode in ("one_way", "one_way_long"):
            # load goes to the edges perpendicular to the span direction
            span = lx if mode == "one_way" else ly
            for k, L in enumerate(lens):
                is_long = (k % 2 == 0) if square else abs(L - ly) < abs(L - lx)
                carry = is_long if mode == "one_way" else not is_long
                prof[k] = [(0.0, span / 2), (L, span / 2)] if carry else [(0.0, 0.0), (L, 0.0)]
            return prof, f"one-way (span {span:.2f} m)", notes

    # irregular polygon
    convex = _is_convex(pts)
    if not convex:
        notes.append("concave slab – load shared uniformly by perimeter; consider splitting it")
        per = sum(lens)
        return [[(0.0, area / per), (L, area / per)] for L in lens], "uniform (concave)", notes
    if dist in ("one_way", "one_way_long"):
        notes.append("one-way distribution needs a rectangle – CG method used")
    cg = G.polygon_centroid(pts)
    for k, (a, b) in enumerate(edges):
        L = lens[k]
        tri = abs(G.polygon_area([cg, a, b]))
        if L < 1e-9:
            continue
        s_peak = min(max(G.project_param(cg, a, b), 0.0), 1.0) * L
        peak = 2.0 * tri / L
        if s_peak <= 1e-9:
            prof[k] = [(0.0, peak), (L, 0.0)]
        elif s_peak >= L - 1e-9:
            prof[k] = [(0.0, 0.0), (L, peak)]
        else:
            prof[k] = [(0.0, 0.0), (s_peak, peak), (L, 0.0)]
    return prof, "CG triangles (irregular)", notes


def _subtract(s0: float, s1: float, covered: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Parts of [s0, s1] not inside any covered interval."""
    parts = [(s0, s1)]
    for c0, c1 in covered:
        nxt = []
        for a, b in parts:
            if c1 <= a or c0 >= b:
                nxt.append((a, b))
                continue
            if c0 > a:
                nxt.append((a, c0))
            if c1 < b:
                nxt.append((c1, b))
        parts = nxt
    return [(a, b) for a, b in parts if b - a > 1e-6]


def _is_convex(pts) -> bool:
    n = len(pts)
    sign = 0
    for i in range(n):
        a, b, c = pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        cr = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cr) < 1e-9:
            continue
        s = 1 if cr > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return True


def profile_integral(p: EdgeProfile, s0: float = 0.0, s1: float = 1e18) -> float:
    tot = 0.0
    for (sa, wa), (sb, wb) in zip(p, p[1:]):
        a, b = max(sa, s0), min(sb, s1)
        if b <= a:
            continue
        def w(s):
            return wa + (wb - wa) * (s - sa) / (sb - sa) if sb > sa else wa
        tot += 0.5 * (w(a) + w(b)) * (b - a)
    return tot


# =========================================================================
# Engine
# =========================================================================
class PlanEngine:
    """Run load transfer for a :class:`Plan`.

    Parameters
    ----------
    floor_height_above:
        Storey height above this plan used for wall heights.  Defaults to
        ``plan.floor_height_above``; FrameWin passes the real storey height
        for each level the plan is used at.
    """

    def __init__(self, plan: Plan, floor_height_above: Optional[float] = None,
                 ratio_limit: float = 2.0, continuity: bool = False):
        self.plan = plan
        self.fha = plan.floor_height_above if floor_height_above is None else floor_height_above
        self.ratio_limit = ratio_limit
        self.continuity = continuity
        self.res = PlanResult(plan.name)

    # ------------------------------------------------------------------ API
    def run(self) -> PlanResult:
        p = self.plan
        res = self.res
        self._validate()
        self._topology()
        self._slab_loads()
        self._beam_self_loads()
        self._solve_beams()
        res.reacted["D"] = sum(c.dead for c in res.columns.values())
        res.reacted["L"] = sum(c.live for c in res.columns.values())
        imb = res.imbalance_pct
        if abs(imb) > 1.0:
            res.issues.append(Issue("warning", "topology",
                                    f"Load imbalance {imb:.2f}% between applied load and column reactions – check errors above"))
        for c in p.columns:
            res.columns.setdefault(c.id, ColumnLoad(c.id, c.mark))
        return res

    # ------------------------------------------------------------ validation
    def _validate(self):
        p, res = self.plan, self.res
        seen = {}
        for c in p.columns:
            key = c.mark
            if key in seen:
                res.issues.append(Issue("error", "column", f"Duplicate column mark {c.mark}", c.id, c.pos))
            seen[key] = c
        for b in p.beams:
            if b.length < 0.05:
                res.issues.append(Issue("error", "beam", f"Beam {b.mark} has zero length", b.id, b.p1))
        for s in p.slabs:
            if len(s.points) < 3 or s.area < 1e-4:
                res.issues.append(Issue("error", "slab", f"Slab {s.mark} is degenerate", s.id))
        bl = p.beams
        for i in range(len(bl)):
            for j in range(i + 1, len(bl)):
                ov = G.collinear_overlap(bl[i].p1, bl[i].p2, bl[j].p1, bl[j].p2)
                if ov and (ov[1] - ov[0]) * bl[i].length > 0.05:
                    res.issues.append(Issue("error", "beam", f"Beams {bl[i].mark} and {bl[j].mark} overlap", bl[j].id, bl[j].p1))

    # ------------------------------------------------------------ topology
    def _rank(self, b: Beam, col_pts) -> int:
        if b.role == "primary":
            return 2
        if b.role == "secondary":
            return 1
        a = any(G.same_point(b.p1, c, 0.3) for c in col_pts.get(b.id, []))
        z = any(G.same_point(b.p2, c, 0.3) for c in col_pts.get(b.id, []))
        return 2 if (a and z) else 1

    def _topology(self):
        p, res = self.plan, self.res
        col_on: dict[str, list[tuple[float, Column]]] = {}
        col_pts: dict[str, list[tuple[float, float]]] = {}
        for b in p.beams:
            L = b.length
            lst = []
            for c in p.columns:
                t = column_on_beam(c, b)
                if t is not None:
                    lst.append((t * L, c))
            col_on[b.id] = lst
            col_pts[b.id] = [G.lerp(b.p1, b.p2, x / L) for x, _ in lst] if L > 0 else []
            res.beams[b.id] = BeamResult(b.id, L, rank=self._rank(b, col_pts))

        supports: dict[str, list[Support]] = {b.id: [Support(x, "column", c.id, c.pos) for x, c in col_on[b.id]] for b in p.beams}
        self.rests_on: dict[str, set[str]] = {b.id: set() for b in p.beams}

        def has_col_near(bid, pt):
            return any(G.same_point(pt, cp, 0.3) for cp in col_pts[bid])

        bl = p.beams
        for i in range(len(bl)):
            for j in range(i + 1, len(bl)):
                A, B = bl[i], bl[j]
                # a beam end touching the other beam's face counts as resting on it
                hit = G.segment_intersection(A.p1, A.p2, B.p1, B.p2, tol=max(END_TOL, max(A.b, B.b) / 2 + 0.03))
                if not hit:
                    continue
                P, tA, tB = hit
                if has_col_near(A.id, P) or has_col_near(B.id, P):
                    continue
                LA, LB = A.length, B.length
                A_end = tA * LA <= END_TOL or (1 - tA) * LA <= END_TOL
                B_end = tB * LB <= END_TOL or (1 - tB) * LB <= END_TOL
                rA, rB = res.beams[A.id].rank, res.beams[B.id].rank
                carrier = carried = None
                if A_end and not B_end:
                    carried, carrier = A, B
                elif B_end and not A_end:
                    carried, carrier = B, A
                elif A_end and B_end:
                    if A.cantilever != B.cantilever:
                        carrier, carried = (A, B) if A.cantilever else (B, A)
                    elif rA != rB:
                        carrier, carried = (A, B) if rA > rB else (B, A)
                    else:
                        continue  # both ends free here -> reported as unsupported end
                else:  # interior crossing
                    if rA != rB:
                        carrier, carried = (A, B) if rA > rB else (B, A)
                    else:
                        carrier, carried = (A, B) if (A.d, LA) >= (B.d, LB) else (B, A)
                        res.issues.append(Issue("warning", "topology",
                                                f"{carried.mark} assumed to rest on {carrier.mark} at "
                                                f"({P[0]:.2f}, {P[1]:.2f}); set beam role to change",
                                                carried.id, P))
                tC = G.project_param(P, carried.p1, carried.p2)
                supports[carried.id].append(Support(min(max(tC, 0), 1) * carried.length, "beam", carrier.id, P))
                self.rests_on[carried.id].add(carrier.id)

        for b in p.beams:
            sup = sorted(supports[b.id], key=lambda s: s.x)
            # merge duplicates at same position (prefer columns)
            merged: list[Support] = []
            for s in sup:
                if merged and abs(s.x - merged[-1].x) < END_TOL:
                    if merged[-1].kind == "beam" and s.kind == "column":
                        merged[-1] = s
                    continue
                merged.append(s)
            res.beams[b.id].supports = merged
            L = b.length
            if not merged:
                res.issues.append(Issue("error", "beam", f"Beam {b.mark} has no support (no column or beam under it)", b.id, b.p1))
                continue
            for end_x, pt in ((0.0, b.p1), (L, b.p2)):
                if all(abs(s.x - end_x) > END_TOL for s in merged) and not b.cantilever:
                    res.issues.append(Issue("error", "beam",
                                            f"Beam {b.mark}: end at ({pt[0]:.2f}, {pt[1]:.2f}) is not supported – "
                                            f"add a column/beam or mark the beam as cantilever", b.id, pt))
            if len(merged) == 1 and not b.cantilever:
                res.issues.append(Issue("error", "beam", f"Beam {b.mark} has a single support – mark as cantilever", b.id, b.p1))

        self.order = self._topo_order()

    def _topo_order(self) -> list[str]:
        """Beams that rest on others first (Kahn on reversed dependency)."""
        deps = {k: set(v) for k, v in self.rests_on.items()}
        carried_by = {k: set() for k in deps}
        for k, v in deps.items():
            for c in v:
                carried_by[c].add(k)
        # a beam can be solved when all beams resting on it are solved
        pending = {k: len(carried_by[k]) for k in deps}
        ready = [k for k, n in pending.items() if n == 0]
        order = []
        while ready:
            k = ready.pop()
            order.append(k)
            for c in deps[k]:
                pending[c] -= 1
                if pending[c] == 0:
                    ready.append(c)
        if len(order) < len(deps):
            cyc = [k for k in deps if k not in order]
            marks = ", ".join(self.plan.find(k).mark for k in cyc)
            self.res.issues.append(Issue("error", "topology", f"Circular beam support between: {marks}. Set beam roles."))
            order.extend(cyc)
        return order

    # ------------------------------------------------------------ slab loads
    def _slab_loads(self):
        p, res = self.plan, self.res
        for s in p.slabs:
            if len(s.points) < 3:
                continue
            profiles, method, notes = slab_edge_profiles(s, self.ratio_limit)
            for n_ in notes:
                res.issues.append(Issue("warning", "slab", f"Slab {s.mark}: {n_}", s.id))
            res.slab_edges[s.id] = [list(pr) for pr in profiles]
            for case, w in (("D", s.dead), ("L", s.live_load)):
                res.applied[case] += w * s.area
                res.breakdown[f"slab_{case}"] = res.breakdown.get(f"slab_{case}", 0.0) + w * s.area
            pts = s.pts
            for k in range(len(pts)):
                a, b = pts[k], pts[(k + 1) % len(pts)]
                prof = profiles[k]
                if profile_integral(prof) <= 1e-12:
                    continue
                Le = G.dist(a, b)
                covered: list[tuple[float, float]] = []
                for bm in p.beams:
                    # beams drawn face-aligned or digitised from CAD may sit a few cm off the edge
                    ov = G.collinear_overlap(a, b, bm.p1, bm.p2, tol=max(0.02, bm.b / 2 + 0.03))
                    if not ov:
                        continue
                    s0, s1 = ov[0] * Le, ov[1] * Le
                    # only the part not already taken by another beam (no double counting)
                    for u0, u1 in _subtract(s0, s1, covered):
                        self._map_profile(s, bm, a, b, prof, u0, u1)
                        covered.append((u0, u1))
                # unsupported part of the edge (the imbalance check reports the lost load)
                full = profile_integral(prof)
                lost = full - sum(profile_integral(prof, c0, c1) for c0, c1 in covered)
                if lost > max(0.005 * full, 0.01):
                    res.issues.append(Issue("error", "slab",
                                            f"Slab {s.mark}: edge ({a[0]:.2f},{a[1]:.2f})–({b[0]:.2f},{b[1]:.2f}) "
                                            f"is not fully supported by beams ({lost:.2f} m² of load lost)", s.id, a))

    def _map_profile(self, slab: Slab, bm: Beam, a, b, prof: EdgeProfile, s0: float, s1: float):
        """Transfer the edge profile portion [s0, s1] onto beam ``bm``."""
        Le = G.dist(a, b)
        # breakpoints within [s0, s1]
        ss = sorted({s0, s1, *[s for s, _ in prof if s0 < s < s1]})

        def w_at(s):
            for (sa, wa), (sb, wb) in zip(prof, prof[1:]):
                if sa - 1e-9 <= s <= sb + 1e-9:
                    return wa if sb - sa < 1e-12 else wa + (wb - wa) * (s - sa) / (sb - sa)
            return 0.0

        out = self.res.beams[bm.id].loads
        for sa, sb in zip(ss, ss[1:]):
            pa = G.lerp(a, b, sa / Le)
            pb = G.lerp(a, b, sb / Le)
            xa = G.project_param(pa, bm.p1, bm.p2) * bm.length
            xb = G.project_param(pb, bm.p1, bm.p2) * bm.length
            wa, wb = w_at(sa), w_at(sb)
            if xa > xb:
                xa, xb, wa, wb = xb, xa, wb, wa
            for case, q in (("D", slab.dead), ("L", slab.live_load)):
                if q and (wa or wb):
                    out.append(LinLoad(xa, xb, wa * q, wb * q, case, f"slab {slab.mark}"))

    # ------------------------------------------------------------ beam loads
    def _beam_self_loads(self):
        p, res = self.plan, self.res
        for b in p.beams:
            br = res.beams[b.id]
            comps = b.udl_components(self.fha, p.floor_type)
            w = sum(comps.values())
            if w:
                br.loads.append(LinLoad(0.0, b.length, w, w, "D", "self+wall+plaster"))
                res.applied["D"] += w * b.length
                for k, v in comps.items():
                    res.breakdown[f"beam_{k}"] = res.breakdown.get(f"beam_{k}", 0.0) + v * b.length
            for pl in b.point_loads:
                x = min(max(pl.dist, 0.0), b.length)
                for case, P in (("D", pl.dead), ("L", pl.live)):
                    if P:
                        br.loads.append(PtLoad(x, P, case, pl.desc))
                        res.applied[case] += P
            for wl in b.part_loads:
                a0, a1 = max(wl.start, 0.0), min(wl.start + wl.length, b.length)
                if a1 > a0:
                    ld = LinLoad(a0, a1, wl.w1, wl.w2, wl.case, wl.desc)
                    br.loads.append(ld)
                    res.applied[wl.case] += ld.resultant()[0]

    # ------------------------------------------------------------ solve
    def _solve_beams(self):
        p, res = self.plan, self.res
        by_id = {b.id: b for b in p.beams}
        cols = {c.id: c for c in p.columns}
        for bid in self.order:
            b = by_id[bid]
            br = res.beams[bid]
            if not br.supports:
                continue
            xs = [s.x for s in br.supports]
            for case in ("D", "L"):
                if self.continuity and len(xs) >= 2:
                    R = continuous_beam(br.length, xs, br.loads, case)
                else:
                    R = simple_span_reactions(br.length, xs, br.loads, case)
                br.reactions[case] = R
            for i, s in enumerate(br.supports):
                rd, rl = br.reactions["D"][i], br.reactions["L"][i]
                if s.kind == "column":
                    c = cols[s.ref]
                    cl = res.columns.setdefault(c.id, ColumnLoad(c.id, c.mark))
                    cl.dead += rd
                    cl.live += rl
                    cl.parts.append((b.mark, rd, rl))
                else:
                    tgt = by_id[s.ref]
                    x = G.project_param(s.point, tgt.p1, tgt.p2) * tgt.length
                    tr = res.beams[tgt.id]
                    if rd:
                        tr.loads.append(PtLoad(x, rd, "D", f"from {b.mark}"))
                    if rl:
                        tr.loads.append(PtLoad(x, rl, "L", f"from {b.mark}"))


# =========================================================================
# Generators (Auto Beam / Judge column)
# =========================================================================
def column_candidates(plan: Plan) -> list[tuple[float, float]]:
    """Slab corner points where edges change direction (PlanWin "Judge")."""
    pts: list[tuple[float, float]] = []
    for s in plan.slabs:
        P = s.pts
        n = len(P)
        for i in range(n):
            a, b, c = P[i - 1], P[i], P[(i + 1) % n]
            cr = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            if abs(cr) > 1e-6:
                pts.append(b)
    out: list[tuple[float, float]] = []
    for p in pts:
        if not any(G.same_point(p, q, 0.02) for q in out):
            out.append(p)
    out.sort(key=lambda q: (-round(q[1], 2), round(q[0], 2)))
    return out


def auto_columns(plan: Plan, b: float = 0.23, d: float = 0.45, grade: str = "M25") -> list[Column]:
    created = []
    for p in column_candidates(plan):
        if plan.column_at(p, 0.05):
            continue
        c = Column(mark=plan.next_mark("C"), x=round(p[0], 4), y=round(p[1], 4), b=b, d=d, grade=grade)
        plan.columns.append(c)
        created.append(c)
    return created


def auto_beams(plan: Plan, int_size=(0.23, 0.45), ext_size=(0.23, 0.6), int_wall=0.115, ext_wall=0.23,
               grade: str = "M25") -> list[Beam]:
    """Create beams along all slab edges (PlanWin AUTO BEAM).

    Collinear edges are merged into maximal lines which are then split at
    columns, so main beams run column-to-column and secondary beams end on
    other beams.  Existing beams are kept and not duplicated.
    """
    segs = []
    for s in plan.slabs:
        if s.distribution == "cantilever":
            # only the fixed edge needs a beam for a cantilever slab
            P = s.pts
            i = s.cant_edge if s.cant_edge is not None else None
            if i is not None:
                segs.append((P[i], P[(i + 1) % len(P)]))
            continue
        segs.extend(s.edges())
    lines = _merge_collinear(segs)
    created = []
    for a, b in lines:
        L = G.dist(a, b)
        cuts = sorted({0.0, 1.0, *[G.project_param(c.pos, a, b) for c in plan.columns
                                    if G.point_line_distance(c.pos, a, b) < 0.05 and 0.0 < G.project_param(c.pos, a, b) < 1.0]})
        for t0, t1 in zip(cuts, cuts[1:]):
            if (t1 - t0) * L < 0.05:
                continue
            p, q = G.lerp(a, b, t0), G.lerp(a, b, t1)
            if any(G.collinear_overlap(bm.p1, bm.p2, p, q) for bm in plan.beams):
                continue
            ext = _is_external(plan, p, q)
            bsz = ext_size if ext else int_size
            bm = Beam(mark="tmp", x1=round(p[0], 4), y1=round(p[1], 4), x2=round(q[0], 4), y2=round(q[1], 4),
                      b=bsz[0], d=bsz[1], wall_thk=ext_wall if ext else int_wall, external=ext, grade=grade)
            plan.beams.append(bm)
            created.append(bm)
    for i, bm in enumerate(created):
        bm.mark = plan.next_mark("B")
    return created


def _merge_collinear(segs):
    segs = [(a, b) for a, b in segs if G.dist(a, b) > 1e-6]
    used = [False] * len(segs)
    lines = []
    for i, (a, b) in enumerate(segs):
        if used[i]:
            continue
        used[i] = True
        # parameterise along direction of a->b
        ux, uy = (b[0] - a[0]) / G.dist(a, b), (b[1] - a[1]) / G.dist(a, b)
        intervals = [(0.0, G.dist(a, b))]
        changed = True
        while changed:
            changed = False
            for j, (c, d) in enumerate(segs):
                if used[j]:
                    continue
                if G.point_line_distance(c, a, b) > 1e-3 or G.point_line_distance(d, a, b) > 1e-3:
                    continue
                tc = (c[0] - a[0]) * ux + (c[1] - a[1]) * uy
                td = (d[0] - a[0]) * ux + (d[1] - a[1]) * uy
                lo, hi = min(tc, td), max(tc, td)
                if any(lo <= i1 + 1e-6 and hi >= i0 - 1e-6 for i0, i1 in intervals):
                    intervals.append((lo, hi))
                    used[j] = True
                    changed = True
            if changed:
                intervals.sort()
                m = [intervals[0]]
                for lo, hi in intervals[1:]:
                    if lo <= m[-1][1] + 1e-6:
                        m[-1] = (m[-1][0], max(m[-1][1], hi))
                    else:
                        m.append((lo, hi))
                intervals = m
        for lo, hi in intervals:
            lines.append(((a[0] + ux * lo, a[1] + uy * lo), (a[0] + ux * hi, a[1] + uy * hi)))
    return lines


def _is_external(plan: Plan, p, q) -> bool:
    mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
    L = G.dist(p, q)
    nx, ny = -(q[1] - p[1]) / L * 0.05, (q[0] - p[0]) / L * 0.05
    left = any(G.point_in_polygon((mx + nx, my + ny), s.pts) for s in plan.slabs if s.distribution != "on_grade" or True)
    right = any(G.point_in_polygon((mx - nx, my - ny), s.pts) for s in plan.slabs)
    return not (left and right)


def mark_external_beams(plan: Plan) -> None:
    for b in plan.beams:
        b.external = _is_external(plan, b.p1, b.p2)
