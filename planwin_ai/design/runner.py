"""Design orchestration: frame analysis results -> member designs -> BOQ.

The runner keeps the engineering routines (``is456``) independent of the model so they
can be tested in isolation.  Result containers live in :mod:`.report`, quantities in
:mod:`.quantities` and sizing in :mod:`.sizing`; they are re-exported here so existing
``from planwin_ai.design.runner import ...`` imports keep working.
"""

from __future__ import annotations

import math

import numpy as np

from ..core import geometry as G
from ..core.frame import FrameAnalysis, FrameModel
from ..core.model import Project, grade_fck
from . import is456
from .quantities import STEEL_DENSITY, quantities
from .report import BeamDesign, ColumnDesign, DesignReport, FootingDesign
from .sizing import autosize_columns, default_moment_factor, optimize_sizes

__all__ = [
    "BeamDesign",
    "ColumnDesign",
    "DesignReport",
    "FootingDesign",
    "STEEL_DENSITY",
    "autosize_columns",
    "default_moment_factor",
    "design_all",
    "optimize_sizes",
    "quantities",
    "run_full",
]


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
        rep.beams.append(
            BeamDesign(
                mid,
                mem.mark,
                levels.get(mem.level, str(mem.level)),
                mem.b,
                mem.d,
                L,
                float(sag.max()),
                float(hog[0]),
                float(hog[-1]),
                vmax,
                f"{n}-T{dia}",
                f"{nl}-T{dl}",
                f"{nr}-T{dr}",
                f"{sh.legs}L-T{sh.dia} @ {int(sh.spacing)} c/c" if sh.ok else "FAIL",
                prov_b,
                prov_l,
                prov_r,
                dok,
                fs.ok and fl.ok and fr.ok and sh.ok and dok,
                float(util),
                notes,
            )
        )
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
        chk = is456.design_column(
            demands,
            mem.b,
            mem.d,
            L,
            fck,
            fy,
            ds.column_cover,
            ds.min_column_steel_pct,
            ds.max_column_steel_pct,
            ds.effective_length_factor,
        )
        rep.columns.append(
            ColumnDesign(
                mid,
                mem.mark,
                levels.get(mem.level, str(mem.level)),
                mem.b,
                mem.d,
                Pmax,
                Mx,
                My,
                chk.steel_pct,
                chk.bars,
                chk.ties,
                chk.governing,
                bool(chk.ok),
                float(chk.ratio),
                chk.As_req,
                chk.notes,
            )
        )
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
        ult = []
        for c in fa.ultimate:
            r = fa.reaction(nid, c.factors)
            mx, my = abs(float(r[3])), abs(float(r[4]))
            ult.append((float(r[2]), my if swap else mx, mx if swap else my))
        fck = grade_fck(mem.grade)
        fr = is456.design_footing(
            P, mem.b, mem.d, ds.sbc, fck, fy, ds.footing_cover, ds.footing_self_weight_pct, lat, ultimate=ult
        )
        nd = m.nodes[nid]
        rects.append((mem.mark, nd.x, nd.y, fr.B if not swap else fr.L, fr.L if not swap else fr.B))
        rep.footings.append(
            FootingDesign(
                mem.mark, P, fr.L, fr.B, fr.D, fr.bars_L, fr.bars_B, fr.q_max, fr.ok, fr.ast_L, fr.ast_B, fr.notes
            )
        )
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
            kind = (
                "cantilever"
                if s.distribution == "cantilever"
                else (
                    "one_way"
                    if s.distribution in ("one_way", "one_way_long") or ly / max(lx, 1e-6) > ds.two_way_ratio_limit
                    else "two_way"
                )
            )
            res = is456.design_slab(
                s.mark, lx, ly, s.dead, s.live_load, s.thickness, fck, fy, kind, cont, ds.slab_cover
            )
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


def run_full(project: Project) -> tuple[FrameModel, FrameAnalysis, DesignReport]:
    fm = FrameModel(project).build()
    fa = fm.analyze()
    rep = design_all(fa, project)
    return fm, fa, rep
