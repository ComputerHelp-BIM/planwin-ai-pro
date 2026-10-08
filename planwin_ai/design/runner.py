"""Design orchestration: frame analysis results -> member designs -> BOQ.

The runner keeps the engineering routines (is456.py) independent of the
model so they can be tested in isolation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..core import geometry as G
from ..core.frame import FrameAnalysis, FrameModel
from ..core.model import Project, grade_fck
from ..core.plan_engine import PlanEngine
from . import is456

STEEL_DENSITY = 7850.0  # kg/m^3


@dataclass
class BeamDesign:
    member_id: int
    mark: str
    level: str
    b: float
    d: float
    span: float
    M_sag: float
    M_hog_l: float
    M_hog_r: float
    V_max: float
    bottom: str
    top_l: str
    top_r: str
    stirrups: str
    ast_bot: float
    ast_top_l: float
    ast_top_r: float
    deflection_ok: bool
    ok: bool
    utilisation: float
    notes: list[str] = field(default_factory=list)


@dataclass
class ColumnDesign:
    member_id: int
    mark: str
    level: str
    b: float
    d: float
    Pu: float
    Mux: float
    Muy: float
    steel_pct: float
    bars: str
    ties: str
    governing: str
    ok: bool
    utilisation: float
    As: float
    notes: list[str] = field(default_factory=list)


@dataclass
class FootingDesign:
    mark: str
    P_service: float
    L: float
    B: float
    D: float
    bars_L: str
    bars_B: str
    q: float
    ok: bool
    ast_L: float
    ast_B: float
    notes: list[str] = field(default_factory=list)


@dataclass
class DesignReport:
    beams: list[BeamDesign] = field(default_factory=list)
    columns: list[ColumnDesign] = field(default_factory=list)
    footings: list[FootingDesign] = field(default_factory=list)
    slabs: list[tuple[str, is456.SlabResult]] = field(default_factory=list)  # (plan, result)
    boq: dict = field(default_factory=dict)
    drifts: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def failures(self) -> int:
        return (sum(not b.ok for b in self.beams) + sum(not c.ok for c in self.columns)
                + sum(not f.ok for f in self.footings) + sum(not s.ok for _, s in self.slabs))


def design_all(fa: FrameAnalysis, project: Project) -> DesignReport:
    rep = DesignReport()
    m = fa.model
    ds = project.design
    fy = ds.fy_main
    ult = fa.ultimate
    levels = {lv.index: lv.name for lv in m.levels}
    # ------------------------------------------------------------- beams
    for mid, mem in m.members.items():
        if mem.kind != "beam":
            continue
        fck = grade_fck(mem.grade)
        sag = np.zeros(9)
        hog = np.zeros(9)
        vmax = 0.0
        for c in ult:
            f = fa.forces(mid, c.factors, 9)
            M = -f.My  # sagging +
            sag = np.maximum(sag, M)
            hog = np.minimum(hog, M)
            vmax = max(vmax, float(np.max(np.abs(f.Vz))))
        L = float(f.x[-1])
        b_mm, D_mm = mem.b * 1000, mem.d * 1000
        cover = ds.beam_cover * 1000
        fs = is456.flexure(float(sag.max()), fck, fy, b_mm, D_mm, cover)
        fl = is456.flexure(float(-hog[0]), fck, fy, b_mm, D_mm, cover)
        fr = is456.flexure(float(-hog[-1]), fck, fy, b_mm, D_mm, cover)
        d_eff = D_mm - cover - 18
        ast_min = 0.85 * b_mm * d_eff / fy
        # bottom bars also act as compression steel for hogging (and vice versa)
        n, dia, prov_b = is456.select_bars(max(fs.ast, fl.asc, fr.asc, ast_min), b_mm, cover)
        nl, dl, prov_l = is456.select_bars(max(fl.ast, fs.asc, ast_min), b_mm, cover)
        nr, dr, prov_r = is456.select_bars(max(fr.ast, fs.asc, ast_min), b_mm, cover)
        pt = 100 * max(prov_l, prov_r) / (b_mm * d_eff)
        sh = is456.shear(vmax, fck, ds.fy_shear, b_mm, d_eff, pt)
        notes = [x for x in (fs.note, fl.note, fr.note, sh.note) if x]
        span, basic = _deflection_span(m, mem)
        mf = is456.deflection_mf(100 * prov_b / (b_mm * d_eff), 0.58 * fy * fs.ast / max(prov_b, 1))
        allowed = basic * mf * (10.0 / span if span > 10.0 and basic != 7 else 1.0)  # cl 23.2.1 (b)
        dok = (span * 1000 / d_eff) <= allowed if span > 0 else True
        if not dok:
            notes.append(f"deflection span/d {span * 1000 / d_eff:.1f} > {allowed:.1f}")
        mul = is456.mu_lim(fck, fy, b_mm, d_eff) / 1e6
        util = max(sag.max(), -hog.min()) / mul if mul else 0
        rep.beams.append(BeamDesign(mid, mem.mark, levels.get(mem.level, str(mem.level)), mem.b, mem.d, L,
                                    float(sag.max()), float(hog[0]), float(hog[-1]), vmax,
                                    f"{n}-T{dia}", f"{nl}-T{dl}", f"{nr}-T{dr}",
                                    f"{sh.legs}L-T{sh.dia} @ {int(sh.spacing)} c/c" if sh.ok else "FAIL",
                                    prov_b, prov_l, prov_r, dok, fs.ok and fl.ok and fr.ok and sh.ok and dok,
                                    float(util), notes))
    # ------------------------------------------------------------- columns
    for mid, mem in m.members.items():
        if mem.kind != "column":
            continue
        fck = grade_fck(mem.grade)
        demands = []
        Pmax = Mx = My = 0.0
        for c in ult:
            f = fa.forces(mid, c.factors, 3)
            for k in (0, -1):
                Pu = float(-f.N[k])
                dx, dy = float(f.My[k]), float(f.Mz[k])
                demands.append((c.name, Pu, dx, dy))
                if Pu > Pmax:
                    Pmax, Mx, My = Pu, abs(dx), abs(dy)
        # unsupported length = clear height below the deepest beam framing in at the top (cl 25.1.3)
        top = mem.n2
        dmax = max((bm.d for bm in m.members.values() if bm.kind == "beam" and top in (bm.n1, bm.n2)), default=0.0)
        L = max(abs(m.nodes[mem.n2].z - m.nodes[mem.n1].z) - dmax, 0.5)
        chk = is456.design_column(demands, mem.b, mem.d, L, fck, fy, ds.column_cover, ds.min_column_steel_pct,
                                  ds.max_column_steel_pct, ds.effective_length_factor)
        rep.columns.append(ColumnDesign(mid, mem.mark, levels.get(mem.level, str(mem.level)), mem.b, mem.d,
                                        Pmax, Mx, My, chk.steel_pct, chk.bars, chk.ties, chk.governing, bool(chk.ok),
                                        float(chk.ratio), chk.As_req, chk.notes))
    # ------------------------------------------------------------- footings
    serv = next(c for c in fa.combos if c.kind == "service" and set(c.factors) == {"DL", "LL"})
    lat_serv = [c for c in fa.combos if c.kind == "service" and c is not serv]
    base_cols = {mem.n1: mem for mem in m.members.values() if mem.kind == "column" and m.nodes[mem.n1].support}
    rects = []
    for nid, mem in base_cols.items():
        R = fa.reaction(nid, serv.factors)
        P = float(R[2])
        if P <= 0:
            continue
        swap = abs(math.sin(math.radians(mem.angle))) > 0.7
        lat = []
        for c in lat_serv:
            r = fa.reaction(nid, c.factors)
            mx, my = abs(float(r[3])), abs(float(r[4]))
            lat.append((float(r[2]), my if swap else mx, mx if swap else my))
        fck = grade_fck(mem.grade)
        fr = is456.design_footing(P, mem.b, mem.d, ds.sbc, fck, fy, ds.footing_cover, ds.footing_self_weight_pct, lat)
        nd = m.nodes[nid]
        rects.append((mem.mark, nd.x, nd.y, fr.B if not swap else fr.L, fr.L if not swap else fr.B))
        rep.footings.append(FootingDesign(mem.mark, P, fr.L, fr.B, fr.D, fr.bars_L, fr.bars_B, fr.q_max, fr.ok,
                                          fr.ast_L, fr.ast_B, fr.notes))
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            if abs(a[1] - b[1]) < (a[3] + b[3]) / 2 and abs(a[2] - b[2]) < (a[4] + b[4]) / 2:
                rep.warnings.append(f"Footings {a[0]} and {b[0]} overlap – design a combined footing")
    # ------------------------------------------------------------- slabs
    done = set()
    for lv in project.levels:
        plan = project.plan(lv.plan)
        if plan is None or plan.name in done:
            continue
        done.add(plan.name)
        fck = grade_fck(lv.grade)
        for s in plan.slabs:
            if s.distribution == "on_grade" or len(s.points) < 3:
                continue
            pts = s.pts
            edges = s.edges()
            lens = [G.dist(a, b) for a, b in edges]
            if G.is_rectangle(pts):
                lx, ly = sorted(lens[:2])
            else:
                x0, y0, x1, y1 = G.bbox(pts)
                lx, ly = sorted((x1 - x0, y1 - y0))
            cont = 0
            for a, b in edges:
                for o in plan.slabs:
                    if o is s:
                        continue
                    if any(G.collinear_overlap(a, b, c, d) for c, d in o.edges()):
                        cont += 1
                        break
            kind = "cantilever" if s.distribution == "cantilever" else (
                "one_way" if s.distribution in ("one_way", "one_way_long") or ly / max(lx, 1e-6) > ds.two_way_ratio_limit else "two_way")
            res = is456.design_slab(s.mark, lx, ly, s.dead, s.live_load, s.thickness, fck, fy, kind, cont,
                                    ds.slab_cover)
            rep.slabs.append((plan.name, res))
    rep.drifts = fa.storey_drifts()
    for d in rep.drifts:
        if not d["ok"]:
            rep.warnings.append(f"Storey drift {d['ratio']:.4f} > 0.004 at {d['level']} ({d['case']})")
    rep.boq = quantities(fa, project, rep)
    return rep


def _deflection_span(m: FrameModel, mem) -> tuple[float, int]:
    """Span between real supports of the original beam containing ``mem`` and the
    IS 456 cl 23.2.1 basic span/depth ratio (7 cantilever, 20 simply supported,
    26 continuous / framed into columns at both ends)."""
    a, b = m.nodes[mem.n1], m.nodes[mem.n2]
    seg = math.dist((a.x, a.y), (b.x, b.y))
    lv = m.levels[mem.level] if mem.level < len(m.levels) else None
    res = lv.result if lv else None
    br = res.beams.get(mem.group) if res else None
    plan = m.p.plan(lv.plan) if lv else None
    beam = next((x for x in plan.beams if x.id == mem.group), None) if plan else None
    if not br or not beam or not br.supports:
        return seg, 20
    xm = (math.dist((beam.x1, beam.y1), (a.x, a.y)) + math.dist((beam.x1, beam.y1), (b.x, b.y))) / 2
    xs = [s.x for s in br.supports]
    if xm < xs[0] - 1e-6:
        return xs[0], 7  # overhang before first support
    if xm > xs[-1] + 1e-6:
        return br.length - xs[-1], 7
    for i in range(len(xs) - 1):
        if xs[i] - 1e-6 <= xm <= xs[i + 1] + 1e-6:
            s0, s1 = br.supports[i], br.supports[i + 1]
            continuous = len(xs) >= 3 or (s0.kind == "column" and s1.kind == "column")
            return xs[i + 1] - xs[i], 26 if continuous else 20
    return seg, 20


def quantities(fa: FrameAnalysis, project: Project, rep: DesignReport) -> dict:
    """Concrete, steel, formwork and cost estimate."""
    m = fa.model
    conc: dict[str, float] = {}
    form = 0.0
    steel = {"columns": 0.0, "beams": 0.0, "slabs": 0.0, "footings": 0.0}

    def add_c(grade, v):
        conc[grade] = conc.get(grade, 0.0) + v

    cd = {c.member_id: c for c in rep.columns}
    bd = {b.member_id: b for b in rep.beams}
    for mid, mem in m.members.items():
        a, b = m.nodes[mem.n1], m.nodes[mem.n2]
        L = math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))
        add_c(mem.grade, mem.b * mem.d * L)
        if mem.kind == "column":
            form += 2 * (mem.b + mem.d) * L
            c = cd.get(mid)
            if c:
                n, dia = _parse_bars(c.bars)
                steel["columns"] += n * math.pi * dia * dia / 4e6 * L * 1.1 * STEEL_DENSITY
                tie_n = L / 0.15
                steel["columns"] += tie_n * 2 * (mem.b + mem.d) * math.pi * 0.008 ** 2 / 4 * STEEL_DENSITY
        else:
            form += (mem.b + 2 * mem.d) * L
            d = bd.get(mid)
            if d:
                area = d.ast_bot * L + (d.ast_top_l + d.ast_top_r) * L / 3 + 2 * 113 * L
                steel["beams"] += area / 1e6 * STEEL_DENSITY
                steel["beams"] += (L / 0.15) * 2 * (mem.b + mem.d) * math.pi * 0.008 ** 2 / 4 * STEEL_DENSITY
    for lv in project.levels:
        plan = project.plan(lv.plan)
        if not plan:
            continue
        for s in plan.slabs:
            if s.distribution == "on_grade":
                continue
            add_c(lv.grade, s.area * s.thickness)
            form += s.area
            steel["slabs"] += s.area * s.thickness * 80.0  # typical 80 kg/m^3 when not detailed
    for f in rep.footings:
        g = project.levels[0].grade if project.levels else "M25"
        add_c(g, f.L * f.B * f.D)
        add_c("PCC M10", (f.L + 0.3) * (f.B + 0.3) * 0.1)
        form += 2 * (f.L + f.B) * f.D
        steel["footings"] += (f.ast_L * f.B + f.ast_B * f.L) / 1e6 * max(f.L, f.B) * STEEL_DENSITY
    total_c = sum(v for k, v in conc.items() if not k.startswith("PCC"))
    total_s = sum(steel.values())
    rates = project.design.rates
    cost = 0.0
    lines = []
    for g, v in conc.items():
        r = rates.get(f"concrete_{g.lower().replace(' ', '_')}", rates.get("concrete_m25", 7000.0) if not g.startswith("PCC") else 5000.0)
        lines.append((f"Concrete {g}", "m³", v, r, v * r))
        cost += v * r
    lines.append(("Reinforcement steel", "kg", total_s, rates.get("steel_kg", 75.0), total_s * rates.get("steel_kg", 75.0)))
    cost += total_s * rates.get("steel_kg", 75.0)
    lines.append(("Formwork", "m²", form, rates.get("formwork_m2", 550.0), form * rates.get("formwork_m2", 550.0)))
    cost += form * rates.get("formwork_m2", 550.0)
    return {"concrete": conc, "steel": steel, "formwork": form, "total_concrete": total_c, "total_steel": total_s,
            "steel_per_m3": total_s / total_c if total_c else 0.0, "lines": lines, "cost": cost}


def _parse_bars(s: str) -> tuple[int, int]:
    try:
        n, rest = s.split("-T", 1)
        return int(n), int(rest.split()[0])
    except (ValueError, IndexError):
        return 0, 0


# ===================================================================== autosize
def default_moment_factor(project: Project) -> float:
    """Allowance for frame moments in axial-load sizing: larger in higher seismic zones."""
    from ..core.lateral import ZONE_FACTOR

    if not project.seismic.enabled:
        return 1.25
    return 1.25 + 2.5 * ZONE_FACTOR.get(project.seismic.zone, 0.16)


def autosize_columns(project: Project, breadth: Optional[float] = None, steel_pct: float = 0.8,
                     same_size: bool = True, max_step: float = 0.05, moment_factor: Optional[float] = None,
                     below_ground_increase: float = 0.0) -> dict[str, list[float]]:
    """FrameWin AUTOSIZE: size columns from cumulative factored axial load.

    Returns {mark: [depth per level index 1..n]} and writes overrides into
    ``project.column_sizes``.
    """
    n = len(project.levels)
    if moment_factor is None:
        moment_factor = default_moment_factor(project)
    seismic_min = 0.3 if project.seismic.enabled and project.seismic.zone != "II" else 0.0
    loads: dict[str, list[float]] = {}
    info: dict[str, dict] = {}
    for i, lv in enumerate(project.levels, start=1):
        plan = project.plan(lv.plan)
        fha = project.levels[i].height if i < n else plan.floor_height_above
        res = PlanEngine(plan, fha, project.design.two_way_ratio_limit).run()
        for c in plan.columns:
            cl = next((v for v in res.columns.values() if v.column_id == c.id), None)
            loads.setdefault(c.mark, [0.0] * (n + 1))
            red = 1 - lv.live_reduction / 100.0
            loads[c.mark][i] = (cl.dead + cl.live * red) if cl else 0.0
            info.setdefault(c.mark, {})[i] = c
    out = {}
    for mark, per in loads.items():
        present = sorted(info[mark])
        sizes = {}
        cum = 0.0
        for i in sorted(present, reverse=True):
            c = info[mark][i]
            b = max(breadth or c.b, seismic_min)  # IS 13920: min 300 mm in ductile frames
            h = project.levels[i - 1].height
            cum += per[i] + b * max(c.d, b) * h * 25.0
            fck = grade_fck(project.levels[i - 1].grade)
            d = is456.autosize_depth(1.5 * cum, b, fck, project.design.fy_main, steel_pct, moment_factor, min_d=b)
            if seismic_min and d > 2.0 * b:
                # ductile frames: keep d/b <= 2 so both directions have lateral stiffness
                area = b * d
                b = max(b, math.ceil(math.sqrt(area / 2.0) / 0.05 - 1e-9) * 0.05)
                d = max(math.ceil(area / b / 0.05 - 1e-9) * 0.05, b)
            sizes[i] = [round(b, 3), round(d, 3)]
        if same_size:
            bmax = max(v[0] for v in sizes.values())
            dmax = max(v[1] for v in sizes.values())
            for i in sizes:
                sizes[i] = [bmax, dmax]
        else:  # limit reduction between consecutive levels
            prev = None
            for i in sorted(sizes):
                if prev is not None and sizes[prev][1] - sizes[i][1] > max_step:
                    sizes[i][1] = round(sizes[prev][1] - max_step, 3)
                prev = i
        for i, (b, d) in sizes.items():
            if i == 1 and below_ground_increase:
                b, d = b + 2 * below_ground_increase, d + 2 * below_ground_increase
            project.set_column_size(mark, i, b, d, info[mark][i].angle)
        out[mark] = [sizes[i][1] for i in sorted(sizes)]
    return out


def optimize_sizes(project: Project, max_iter: int = 8, target_col_pct: float = 3.0,
                   step: float = 0.05, progress=None) -> tuple[list[str], "DesignReport"]:
    """Iteratively enlarge failing members (FrameWin "change size, re-run" loop).

    Columns: depth +step (breadth when depth/breadth >= 2.5) for segments that
    fail or need more than ``target_col_pct`` steel; lower storeys are kept at
    least as large as upper ones.  Beams: depth +step (breadth +step when the
    shear stress limit governs).  Returns (change log, final design report).
    """
    log: list[str] = []
    rep = None
    for it in range(1, max_iter + 1):
        fm, fa, rep = run_full(project)
        bad_cols = [c for c in rep.columns if not c.ok or c.steel_pct > target_col_pct]
        bad_beams = [b for b in rep.beams if not b.ok]
        bad_drift = [d for d in rep.drifts if not d["ok"]]
        if progress:
            progress(it, len(bad_cols), len(bad_beams))
        if not bad_cols and not bad_beams and not bad_drift:
            log.append(f"Iteration {it}: all members and storey drifts pass")
            return log, rep
        drift_lv: dict[int, set[str]] = {}
        if bad_drift:  # stiffen columns (in the drift direction) and beams up to the highest drifting storey
            lvl_idx = {lv.name: i for i, lv in enumerate(project.levels, start=1)}
            for d in bad_drift:
                top = lvl_idx.get(d["level"], 0)
                for k in range(1, top + 1):
                    drift_lv.setdefault(k, set()).add(d["case"][-1])  # "X" / "Y"
        lvl_index = {lv.name: i for i, lv in enumerate(project.levels, start=1)}
        changed = set()
        for c in bad_cols:
            i = lvl_index.get(c.level)
            if i is None or (c.mark, i) in changed:
                continue
            plan = project.plan(project.levels[i - 1].plan)
            col = next((x for x in plan.columns if x.mark == c.mark), None)
            if col is None:
                continue
            b, d, ang = project.column_size(c.mark, i, col)
            if d / b >= 2.5:
                b += step
            else:
                d += step
            for k in range(1, i + 1):  # this level and all below
                plan_k = project.plan(project.levels[k - 1].plan)
                col_k = next((x for x in plan_k.columns if x.mark == c.mark), None)
                if col_k is None:
                    continue
                bk, dk, ak = project.column_size(c.mark, k, col_k)
                project.set_column_size(c.mark, k, max(bk, b), max(dk, d), ak)
                changed.add((c.mark, k))
        stiffened_plans = set()
        for i, dirs in drift_lv.items():
            plan = project.plan(project.levels[i - 1].plan)
            for col in plan.columns:
                if (col.mark, i) in changed:
                    continue
                b, d, ang = project.column_size(col.mark, i, col)
                b_along_x = abs(math.cos(math.radians(ang))) >= 0.7
                grow_b = ("X" in dirs and b_along_x) or ("Y" in dirs and not b_along_x)
                grow_d = ("Y" in dirs and b_along_x) or ("X" in dirs and not b_along_x)
                project.set_column_size(col.mark, i, b + step * grow_b, d + step * grow_d, ang)
                changed.add((col.mark, i))
            if plan.name not in stiffened_plans:
                stiffened_plans.add(plan.name)
                for bm in plan.beams:
                    if bm.d < bm.length / 8:
                        bm.d = round(bm.d + step, 3)
        beam_ids = set()
        for bd in bad_beams:
            mem = fm.members.get(bd.member_id)
            if mem is None or mem.group in beam_ids:
                continue
            beam_ids.add(mem.group)
            for plan in project.plans:
                bm = next((x for x in plan.beams if x.id == mem.group), None)
                if bm is None:
                    continue
                if any("τv" in n for n in bd.notes) and bm.d / bm.b >= 2.5:
                    bm.b = round(bm.b + step, 3)
                else:
                    bm.d = round(bm.d + step, 3)
        log.append(f"Iteration {it}: enlarged {len(changed)} column segments and {len(beam_ids)} beams"
                   + (f" (storey drift at {len(bad_drift)} level/case)" if bad_drift else ""))
    fm, fa, rep = run_full(project)
    log.append(f"Stopped after {max_iter} iterations – {rep.failures} member(s) still need attention")
    return log, rep


def run_full(project: Project) -> tuple[FrameModel, FrameAnalysis, DesignReport]:
    fm = FrameModel(project).build()
    fa = fm.analyze()
    rep = design_all(fa, project)
    return fm, fa, rep
