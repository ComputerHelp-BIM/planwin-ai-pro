"""FrameWin engine: stack PlanWin plans into a 3-D space frame and analyse it.

* Columns are linked between levels by their **mark** (as legacy FrameWin);
  a column missing on the level below becomes a floating column that lands
  on the beam underneath (an automatic joint is created there).
* Beam-beam junctions get automatic joints (FrameWin "Auto Joint").
* Gravity loads come from the PlanWin engine (slab yield-line loads, beam
  self/wall/plaster loads, user point & wedge loads).  Reactions of
  secondary beams are *not* applied – in 3-D those beams are real members.
* Lateral loads: IS 1893-1:2016 equivalent static and IS 875-3:2015 wind.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import geometry as G
from .beamcalc import LinLoad, PtLoad
from .lateral import SeismicResult, WindResult, seismic_static, wind_storey_forces
from .model import Project, concrete_E, grade_fck
from .plan_engine import Issue, PlanEngine, PlanResult, column_on_beam
from .solver import FMember, FNode, FrameResults, FrameSolveError, FrameSolver, MLoad, MPoint, section_forces

NODE_TOL = 0.02


@dataclass
class Combo:
    name: str
    factors: dict[str, float]
    kind: str = "ultimate"  # ultimate | service


def is_combinations(seismic: bool, wind: bool, torsion: bool = False) -> list[Combo]:
    """IS 456 Table 18 / IS 1893-1:2016 cl 6.3.4 / IS 875-5 combinations.

    25 combinations without accidental torsion (legacy PlanWin set); with
    torsion each seismic combination is taken with ± accidental torsion
    (ETX / ETY cases), giving 37.
    """
    out = [Combo("1.5(DL+LL)", {"DL": 1.5, "LL": 1.5})]
    lat: list[tuple[str, str | None]] = []
    if wind:
        lat += [("WLX", None), ("WLY", None)]
    if seismic:
        lat += [("EQX", "ETX" if torsion else None), ("EQY", "ETY" if torsion else None)]

    def variants(L, T, f):
        for s in (1, -1):
            sg = "+" if s > 0 else "-"
            if T is None:
                yield f"{sg}{L}", {L: f * s}
            else:
                for t in (1, -1):
                    yield f"{sg}{L}{'+' if t > 0 else '-'}{T}", {L: f * s, T: f * t}

    for L, T in lat:
        for tag, fac in variants(L, T, 1.2):
            out.append(Combo(f"1.2(DL+LL{tag})", {"DL": 1.2, "LL": 1.2, **fac}))
    for L, T in lat:
        for tag, fac in variants(L, T, 1.5):
            out.append(Combo(f"1.5(DL{tag})", {"DL": 1.5, **fac}))
    for L, T in lat:
        for tag, fac in variants(L, T, 1.5):
            out.append(Combo(f"0.9DL{tag.replace(L, '1.5' + L, 1)}", {"DL": 0.9, **fac}))
    out.append(Combo("DL+LL (service)", {"DL": 1.0, "LL": 1.0}, "service"))
    for L, T in lat:
        for tag, fac in variants(L, T, 1.0):
            out.append(Combo(f"DL+LL{tag} (service)", {"DL": 1.0, "LL": 1.0, **fac}, "service"))
    return out


@dataclass
class LevelInfo:
    index: int
    name: str
    z: float
    plan: str
    result: PlanResult | None = None
    weight: float = 0.0
    nodes: list[int] = field(default_factory=list)
    column_nodes: dict[str, int] = field(default_factory=dict)  # mark -> node id


class FrameModel:
    def __init__(self, project: Project):
        self.p = project
        self.nodes: dict[int, FNode] = {}
        self.members: dict[int, FMember] = {}
        self.nodal: dict[str, dict[int, np.ndarray]] = {}
        self.issues: list[Issue] = []
        self.levels: list[LevelInfo] = []
        self.seismic: dict[str, SeismicResult] = {}
        self.wind: dict[str, WindResult] = {}
        self._reg: dict[int, list[tuple[float, float, int]]] = {}
        self._nid = 0
        self._mid = 0

    # ------------------------------------------------------------ nodes
    def _node(self, lvl: int, x: float, y: float, z: float, create: bool = True, tag: str = "") -> int | None:
        for px, py, nid in self._reg.setdefault(lvl, []):
            if abs(px - x) <= NODE_TOL and abs(py - y) <= NODE_TOL:
                return nid
        if not create:
            return None
        self._nid += 1
        self.nodes[self._nid] = FNode(self._nid, x, y, z, lvl, tag=tag)
        self._reg[lvl].append((x, y, self._nid))
        return self._nid

    def _err(self, msg, at=None, level="error"):
        self.issues.append(Issue(level, "frame", msg, None, at))

    # ------------------------------------------------------------ build
    def build(self) -> FrameModel:
        p = self.p
        if not p.levels:
            raise FrameSolveError("No levels defined – add levels in the Levels table")
        elev = p.elevations()
        fs = p.design
        # ---- pass 0: plan analysis per level
        self.levels = [LevelInfo(0, "Base", 0.0, "")]
        for i, lv in enumerate(p.levels, start=1):
            plan = p.plan(lv.plan)
            if plan is None:
                raise FrameSolveError(f"Level {lv.name}: plan '{lv.plan}' not found")
            fha = p.levels[i].height if i < len(p.levels) else plan.floor_height_above
            res = PlanEngine(plan, fha, fs.two_way_ratio_limit, fs.continuity_in_load_transfer).run()
            for iss in res.errors:
                self.issues.append(Issue("error", iss.kind, f"[{lv.name}] {iss.message}", iss.obj_id, iss.at))
            self.levels.append(LevelInfo(i, lv.name, elev[i], lv.plan, res))

        # ---- pass 1: node points per level
        for i in range(1, len(self.levels)):
            plan = p.plan(p.levels[i - 1].plan)
            z = elev[i]
            for c in plan.columns:
                nid = self._node(i, c.x, c.y, z, tag=c.mark)
                self.levels[i].column_nodes[c.mark] = nid
            for b in plan.beams:
                for x, y in (b.p1, b.p2):
                    if _footprint_column(plan, x, y) is None:
                        self._node(i, x, y, z)
            bl = plan.beams
            for a in range(len(bl)):
                for c_ in range(a + 1, len(bl)):
                    hit = G.segment_intersection(bl[a].p1, bl[a].p2, bl[c_].p1, bl[c_].p2)
                    if hit and _footprint_column(plan, *hit[0]) is None:
                        self._node(i, hit[0][0], hit[0][1], z)
            # floating columns from the level above land on this level's beams
            if i + 1 < len(self.levels):
                up = p.plan(p.levels[i].plan)
                marks_here = {c.mark for c in plan.columns}
                for c in up.columns:
                    if c.mark in marks_here or plan.column_at(c.pos, 0.05):
                        continue
                    on = [b for b in plan.beams if G.is_point_on_segment(c.pos, b.p1, b.p2, 0.05)]
                    if on:
                        self._node(i, c.x, c.y, z, tag=f"float {c.mark}")
                        self._err(
                            f"Column {c.mark} floats on beam {on[0].mark} at level {self.levels[i].name}",
                            c.pos,
                            "warning",
                        )
                    else:
                        self._err(
                            f"Column {c.mark} at level {self.levels[i + 1].name} has no column or beam below", c.pos
                        )

        # ---- pass 2: members
        for i in range(1, len(self.levels)):
            lv = p.levels[i - 1]
            plan = p.plan(lv.plan)
            res = self.levels[i].result
            z, zb = elev[i], elev[i - 1]
            E = concrete_E(grade_fck(lv.grade))
            tf = 0.1 if fs.torsion_release else 1.0
            cb_f, cc_f = fs.crack_beam, fs.crack_column
            # beams
            for b in plan.beams:
                L = b.length
                if L < 1e-6:
                    continue
                ts = {}
                for t_end, (x, y) in ((0.0, b.p1), (1.0, b.p2)):
                    col = _footprint_column(plan, x, y)
                    ts[t_end] = self.levels[i].column_nodes[col.mark] if col else self._node(i, x, y, z)
                for px, py, nid in self._reg[i]:
                    t = G.project_param((px, py), b.p1, b.p2)
                    if 1e-6 < t < 1 - 1e-6 and G.point_line_distance((px, py), b.p1, b.p2) <= NODE_TOL:
                        ts[t] = nid
                for c in plan.columns:  # columns touching the beam (incl. face-aligned)
                    tt = column_on_beam(c, b)
                    if tt is not None:
                        nid = self.levels[i].column_nodes[c.mark]
                        # points inside the column footprint (projected on the beam) merge into the column node;
                        # beam ends and junctions there were already mapped to this node in pass 1
                        tol = _half_along(c, b) + 0.05
                        near = [k for k in ts if abs(k - tt) * L <= max(tol, NODE_TOL)]
                        for k in near:
                            del ts[k]
                        ts[tt] = nid
                pts = sorted(ts.items())
                loads = [
                    ld
                    for ld in (res.beams[b.id].loads if res and b.id in res.beams else [])
                    if not (isinstance(ld, PtLoad) and ld.src.startswith("from "))
                ]
                for (t0, n0), (t1, n1) in zip(pts, pts[1:]):
                    if n0 == n1:
                        continue
                    x0, x1 = t0 * L, t1 * L
                    self._mid += 1
                    m = FMember(self._mid, n0, n1, "beam", b.b, b.d, E, 0.0, i, b.mark, b.id, lv.grade, tf, cb_f)
                    for case, cname in (("D", "DL"), ("L", "LL")):
                        lst = []
                        for ld in loads:
                            if ld.case != case:
                                continue
                            if isinstance(ld, LinLoad):
                                a, bb = max(ld.a, x0), min(ld.b, x1)
                                if bb - a > 1e-9:
                                    lst.append(MLoad(a - x0, bb - x0, (0, 0, -ld.w_at(a)), (0, 0, -ld.w_at(bb))))
                            elif x0 - 1e-9 <= ld.x < x1 - 1e-9 or (abs(ld.x - x1) < 1e-9 and t1 >= 1 - 1e-9):
                                lst.append(MPoint(ld.x - x0, (0, 0, -ld.P)))
                        m.loads[cname] = lst
                    self.members[m.id] = m
            # columns (storey below level i)
            below = p.plan(p.levels[i - 2].plan) if i >= 2 else None
            for c in plan.columns:
                top = self.levels[i].column_nodes[c.mark]
                if i == 1:
                    bot = self._node(0, c.x, c.y, 0.0, tag=c.mark)
                    self.nodes[bot].support = self.p.supports.get(c.mark, "fixed")
                    self.levels[0].column_nodes[c.mark] = bot
                else:
                    bot = self.levels[i - 1].column_nodes.get(c.mark)
                    if bot is None:
                        same = below.column_at(c.pos, 0.05) if below else None
                        if same is not None:
                            bot = self.levels[i - 1].column_nodes.get(same.mark)
                            self._err(
                                f"Column {c.mark} sits on {same.mark} below – marks should match", c.pos, "warning"
                            )
                        else:
                            bot = self._node(i - 1, c.x, c.y, zb, create=False)
                    if bot is None:
                        continue  # already reported
                    nb = self.nodes[bot]
                    if math.hypot(nb.x - c.x, nb.y - c.y) > 0.05:
                        self._err(
                            f"Column {c.mark} is offset {math.hypot(nb.x - c.x, nb.y - c.y):.2f} m "
                            "from the column below",
                            c.pos,
                            "warning",
                        )
                cb, cd, ca = p.column_size(c.mark, i, c)
                self._mid += 1
                m = FMember(self._mid, bot, top, "column", cb, cd, E, ca, i, c.mark, c.mark, lv.grade, tf, cc_f)
                w = cb * cd * 25.0
                m.loads["DL"] = [
                    MLoad(
                        0,
                        math.dist(
                            (self.nodes[bot].x, self.nodes[bot].y, self.nodes[bot].z),
                            (self.nodes[top].x, self.nodes[top].y, self.nodes[top].z),
                        ),
                        (0, 0, -w),
                        (0, 0, -w),
                    )
                ]
                m.loads["LL"] = []
                self.members[m.id] = m
        for nid, nd in self.nodes.items():
            self.levels[nd.level].nodes.append(nid)
        self._joint_loads()
        self._lateral()
        return self

    # ------------------------------------------------------------ loads
    def _joint_loads(self):
        for jl in self.p.joint_loads:
            lvl = int(jl.get("level", 0))
            mark = jl.get("mark")
            if not (0 < lvl < len(self.levels)) or mark not in self.levels[lvl].column_nodes:
                self._err(f"Joint load: column {mark} not found at level {lvl}", level="warning")
                continue
            nid = self.levels[lvl].column_nodes[mark]
            case = "LL" if str(jl.get("case", "DL")).upper().startswith("L") else "DL"
            vec = self.nodal.setdefault(case, {}).setdefault(nid, np.zeros(6))
            vec[2] -= float(jl.get("fz", 0.0))  # input downward positive

    def _level_weights(self) -> list[float]:
        weights = [0.0] * len(self.levels)
        col_w = {}
        for m in self.members.values():
            if m.kind == "column":
                h = abs(self.nodes[m.n2].z - self.nodes[m.n1].z)
                w = m.b * m.d * 25.0 * h
                col_w.setdefault(m.level, 0.0)
                col_w[m.level] += w
        for i in range(1, len(self.levels)):
            res = self.levels[i].result
            plan = self.p.plan(self.levels[i].plan)
            live_frac = 0.0
            if plan.floor_type != "roof":
                area = sum(s.area for s in plan.slabs if s.distribution != "on_grade") or 1.0
                live_frac = 0.25 if (res.applied["L"] / area) <= 3.0 else 0.5
            weights[i] = res.applied["D"] + live_frac * res.applied["L"]
            weights[i] += 0.5 * col_w.get(i, 0.0) + 0.5 * col_w.get(i + 1, 0.0)
            # walls standing on this level's beams belong half to this floor, half to the floor above
            weights[i] -= 0.5 * self._wall_weight(i)
            weights[i] += 0.5 * self._wall_weight(i - 1)
            self.levels[i].weight = weights[i]
        for nid, vec in self.nodal.get("DL", {}).items():
            weights[self.nodes[nid].level] += -vec[2]
            self.levels[self.nodes[nid].level].weight = weights[self.nodes[nid].level]
        return weights

    def _wall_weight(self, i: int) -> float:
        if i < 1 or i >= len(self.levels):
            return 0.0
        plan = self.p.plan(self.levels[i].plan)
        if plan is None or plan.floor_type == "roof":
            return 0.0  # parapets stay with the roof
        bd = self.levels[i].result.breakdown
        return bd.get("beam_wall", 0.0) + bd.get("beam_plaster", 0.0)

    def _torsion(self, case: str, level: int, force: float, axis: int, width: float):
        """Accidental torsion Mt = F·0.05·b as a force couple on the column nodes (cl 7.8.2)."""
        lv = self.levels[level]
        nids = list(lv.column_nodes.values())
        if len(nids) < 2 or abs(force) < 1e-12:
            return
        mt = force * 0.05 * width
        other = 1 if axis == 0 else 0  # lever-arm coordinate
        cs = [(self.nodes[n].y if other == 1 else self.nodes[n].x) for n in nids]
        cm = sum(cs) / len(cs)
        den = sum((c - cm) ** 2 for c in cs)
        if den < 1e-9:
            return
        for n, c in zip(nids, cs):
            vec = self.nodal.setdefault(case, {}).setdefault(n, np.zeros(6))
            vec[axis] += mt * (c - cm) / den

    def _distribute(self, case: str, level: int, force: float, axis: int):
        lv = self.levels[level]
        cols = list(lv.column_nodes.items())
        if not cols or abs(force) < 1e-12:
            return
        res = lv.result
        wts = []
        for mark, _nid in cols:
            cl = next((v for v in res.columns.values() if v.mark == mark), None) if res else None
            wts.append(max(cl.dead + 0.25 * cl.live, 0.0) if cl else 0.0)
        tot = sum(wts)
        if tot <= 1e-9:
            wts, tot = [1.0] * len(cols), float(len(cols))
        for (_mark, nid), w in zip(cols, wts):
            vec = self.nodal.setdefault(case, {}).setdefault(nid, np.zeros(6))
            vec[axis] += force * w / tot

    def _lateral(self):
        p = self.p
        elev = p.elevations()
        # building dimensions come only from plans that are actually used by a level;
        # spare/scratch plans in the project must not change T or the torsion lever arm
        used = [p.plan(lv.plan) for lv in p.levels]
        pts: list = []
        for pl in {id(x): x for x in used if x is not None}.values():
            pts.extend(pl.all_points())
        x0, y0, x1, y1 = G.bbox(pts)
        dx, dy = max(x1 - x0, 1.0), max(y1 - y0, 1.0)

        def level_dims(i: int) -> tuple[float, float]:
            """Plan dimensions of level i (cl 7.8.2 uses the floor plan dimension at that level)."""
            pl = used[i - 1] if 1 <= i <= len(used) else None
            if pl is None or not pl.all_points():
                return dx, dy
            a0, b0, a1, b1 = G.bbox(pl.all_points())
            return max(a1 - a0, 1.0), max(b1 - b0, 1.0)

        weights = self._level_weights()
        s = p.seismic
        if s.enabled:
            for dname, axis, dim in (("EQX", 0, dx), ("EQY", 1, dy)):
                r = seismic_static(
                    weights,
                    elev,
                    s.base_level,
                    s.zone,
                    s.importance,
                    s.response_reduction,
                    s.soil,
                    s.damping,
                    s.infill,
                    dim,
                    dname,
                )
                self.seismic[dname] = r
                for i, f in enumerate(r.forces):
                    self._distribute(dname, i, f, axis)
                    if s.accidental_torsion:
                        lx, ly = level_dims(i)
                        self._torsion("ET" + dname[-1], i, f, axis, ly if axis == 0 else lx)
        w = p.wind
        if w.enabled:
            for dname, axis, width in (("WLX", 0, dy), ("WLY", 1, dx)):
                r = wind_storey_forces(
                    elev,
                    width,
                    w.basic_speed,
                    w.terrain,
                    w.force_coeff,
                    w.parapet,
                    w.below_ground,
                    dname,
                    k1=w.k1,
                    k3=w.k3,
                    k4=w.k4,
                    kd=w.kd,
                    ka=w.ka,
                    kc=w.kc,
                )
                self.wind[dname] = r
                for i, f in enumerate(r.forces):
                    lv_w = self.levels[i]
                    cols = list(lv_w.column_nodes.values())
                    for nid in cols:
                        vec = self.nodal.setdefault(dname, {}).setdefault(nid, np.zeros(6))
                        vec[axis] += f / len(cols)

    # ------------------------------------------------------------ analyse
    def cases(self) -> list[str]:
        c = ["DL", "LL"]
        if self.p.wind.enabled:
            c += ["WLX", "WLY"]
        if self.p.seismic.enabled:
            c += ["EQX", "EQY"]
            if self.p.seismic.accidental_torsion:
                c += ["ETX", "ETY"]
        return c

    def analyze(self) -> FrameAnalysis:
        if not self.members:
            raise FrameSolveError("Frame has no members – build plans and levels first")
        res = FrameSolver(self.nodes, self.members, self.nodal).solve(self.cases())
        return FrameAnalysis(
            self,
            res,
            is_combinations(
                self.p.seismic.enabled,
                self.p.wind.enabled,
                self.p.seismic.enabled and self.p.seismic.accidental_torsion,
            ),
        )


def _footprint_column(plan, x: float, y: float, tol: float = 0.05):
    """Column whose (rotated) footprint, grown by ``tol``, contains the point."""
    best, bd = None, 1e9
    for c in plan.columns:
        a = math.radians(c.angle)
        dx, dy = x - c.x, y - c.y
        u = dx * math.cos(a) + dy * math.sin(a)
        v = -dx * math.sin(a) + dy * math.cos(a)
        if abs(u) <= c.b / 2 + tol and abs(v) <= c.d / 2 + tol:
            d = math.hypot(dx, dy)
            if d < bd:
                best, bd = c, d
    return best


def _half_along(c, b) -> float:
    """Half extent of a column footprint measured along beam ``b``."""
    L = b.length or 1.0
    ux, uy = (b.x2 - b.x1) / L, (b.y2 - b.y1) / L
    a = math.radians(c.angle)
    return abs(ux * math.cos(a) + uy * math.sin(a)) * c.b / 2 + abs(-ux * math.sin(a) + uy * math.cos(a)) * c.d / 2


@dataclass
class MemberForces:
    x: np.ndarray
    N: np.ndarray
    Vy: np.ndarray
    Vz: np.ndarray
    T: np.ndarray
    My: np.ndarray
    Mz: np.ndarray


class FrameAnalysis:
    def __init__(self, model: FrameModel, res: FrameResults, combos: list[Combo]):
        self.model = model
        self.res = res
        self.combos = combos

    @property
    def ultimate(self) -> list[Combo]:
        return [c for c in self.combos if c.kind == "ultimate"]

    def forces(self, mid: int, factors: dict[str, float], n: int = 9) -> MemberForces:
        m = self.model.members[mid]
        L = math.dist(*[(self.model.nodes[k].x, self.model.nodes[k].y, self.model.nodes[k].z) for k in (m.n1, m.n2)])
        f = np.zeros(12)
        loads = []
        for case, a in factors.items():
            if case not in self.res.end_forces or a == 0:
                continue
            f += a * self.res.end_forces[case][mid]
            for ld in m.loads.get(case, []):
                if isinstance(ld, MLoad):
                    loads.append(MLoad(ld.a, ld.b, tuple(a * v for v in ld.w1), tuple(a * v for v in ld.w2)))
                else:
                    loads.append(MPoint(ld.x, tuple(a * v for v in ld.P)))
        xs = np.linspace(0.0, L, n)
        sf = section_forces(m, self.model.nodes, f, loads, xs)
        return MemberForces(sf["x"], sf["N"], sf["Vy"], sf["Vz"], sf["T"], sf["My"], sf["Mz"])

    def reaction(self, nid: int, factors: dict[str, float]) -> np.ndarray:
        out = np.zeros(6)
        for case, a in factors.items():
            r = self.res.reactions.get(case, {}).get(nid)
            if r is not None:
                out += a * r
        return out

    def displacement(self, nid: int, factors: dict[str, float]) -> np.ndarray:
        i = self.res.node_index[nid]
        out = np.zeros(6)
        for case, a in factors.items():
            if case in self.res.disp:
                out += a * self.res.disp[case][i]
        return out

    def storey_drifts(self) -> list[dict]:
        """Max inter-storey drift ratio per level for unfactored lateral cases (limit 0.004)."""
        out = []
        mdl = self.model
        for case in [c for c in self.res.cases if c[:2] in ("EQ", "WL")]:  # drift without accidental torsion
            for i in range(1, len(mdl.levels)):
                h = mdl.levels[i].z - mdl.levels[i - 1].z
                worst = 0.0
                for mark, nid in mdl.levels[i].column_nodes.items():
                    below = mdl.levels[i - 1].column_nodes.get(mark)
                    if below is None:
                        continue
                    axis = 0 if case.endswith("X") else 1
                    d = abs(self.displacement(nid, {case: 1})[axis] - self.displacement(below, {case: 1})[axis])
                    worst = max(worst, d)
                if h > 0:
                    out.append(
                        {
                            "case": case,
                            "level": mdl.levels[i].name,
                            "drift_mm": worst * 1000,
                            "ratio": worst / h,
                            "ok": worst / h <= 0.004,
                        }
                    )
        return out

    def equilibrium(self) -> dict[str, float]:
        """Sum of vertical reactions per primary case (for verification)."""
        return {c: float(sum(r[2] for r in self.res.reactions[c].values())) for c in self.res.cases}
