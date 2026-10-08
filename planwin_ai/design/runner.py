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
from . import is456, is13920
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
    ductile = is13920.required(project) and project.seismic.enabled
    col_below = {mem.n2: mem for mem in m.members.values() if mem.kind in ("column", "wall")}
    col_above = {mem.n1: mem for mem in m.members.values() if mem.kind in ("column", "wall")}
    for mid, mem in m.members.items():
        if mem.kind != "beam":
            continue
        rep.beams.append(_design_beam(fa, project, mid, mem, levels, ductile, col_below, col_above))
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
        H = abs(m.nodes[mem.n2].z - m.nodes[mem.n1].z)
        L = max(H - dmax, 0.5)
        conf = l0 = None
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
        tie = chk.tie
        if ductile and chk.main_bars is not None:
            # IS 13920 cl 8: special confining hoops over l0 at both ends; elsewhere ≤ min(b/2, 300) (cl 7.6.1)
            b_mm, D_mm = mem.b * 1000, mem.d * 1000
            conf = is13920.column_confinement(
                b_mm,
                D_mm,
                ds.column_cover * 1000,
                fck,
                min(ds.fy_shear, 415.0),
                chk.main_bars.dia,
                chk.main_bars.count,
                tie.dia if tie else 8,
            )
            l0 = is13920.confining_length(max(b_mm, D_mm), L * 1000) / 1000
            if tie is not None:
                s_out = min(tie.spacing, min(b_mm, D_mm) / 2, 300.0)
                tie = is456.Links(tie.legs, max(tie.dia, conf.dia), float(math.floor(s_out / 25) * 25))
        ties_txt = chk.ties
        if conf is not None and tie is not None:
            ties_txt = (
                f"T{conf.dia} ({conf.legs_b}×{conf.legs_d} legs) @ {int(conf.s)} over l0 = {l0 * 1000:.0f} "
                f"/ T{tie.dia} @ {int(tie.spacing)} c/c"
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
                ties_txt,
                chk.governing,
                bool(chk.ok),
                float(chk.ratio),
                chk.As_req,
                chk.notes,
                main_bars=chk.main_bars,
                tie=tie,
                level_index=mem.level,
                height=H,
                clear_height=L,
                tie_confined=(is456.Links(max(conf.legs_b, conf.legs_d), conf.dia, conf.s) if conf else None),
                l0=l0 or 0.0,
            )
        )
    # ------------------------------------------------------------- footings
    serv = next(c for c in fa.combos if c.kind == "service" and set(c.factors) == {"DL", "LL"})
    lat_serv = [c for c in fa.combos if c.kind == "service" and c is not serv]
    base_cols = {
        mem.n1: mem for mem in m.members.values() if mem.kind in ("column", "wall") and m.nodes[mem.n1].support
    }
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
                mem.mark,
                P,
                fr.L,
                fr.B,
                fr.D,
                fr.bars_L,
                fr.bars_B,
                fr.q_max,
                fr.ok,
                fr.ast_L,
                fr.ast_B,
                fr.notes,
                mesh_L=fr.mesh_L,
                mesh_B=fr.mesh_B,
                col_b=mem.b,
                col_d=mem.d,
                x=nd.x,
                y=nd.y,
                angle=mem.angle,
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


def _end_support(mem, a, b, col_below, col_above, node: int) -> float:
    """Half the width of the column/wall at ``node`` measured along beam a->b (0 if none)."""
    col = col_below.get(node) or col_above.get(node)
    if col is None:
        return 0.0
    from .quantities import _half_in_column

    return _half_in_column(col, a, b)


def _design_beam(fa: FrameAnalysis, project: Project, mid: int, mem, levels, ductile: bool, col_below, col_above):
    """IS 456 flexure/shear/deflection, cl 41 torsion and – where IS 13920 applies – ductile
    detailing (cl 6.2 longitudinal steel, cl 6.3.3 capacity shear, cl 6.3.5 hoop spacing)."""
    m = fa.model
    ds = project.design
    fy = ds.fy_main
    fck = grade_fck(mem.grade)
    sag = np.zeros(9)
    hog = np.zeros(9)
    vmax = tmax = 0.0
    for c in fa.ultimate:
        f = fa.forces(mid, c.factors, 9)
        M = -f.My  # sagging +
        sag = np.maximum(sag, M)
        hog = np.minimum(hog, M)
        vmax = max(vmax, float(np.max(np.abs(f.Vz))))
        tmax = max(tmax, float(np.max(np.abs(f.T))))
    L = float(f.x[-1])
    b_mm, D_mm = mem.b * 1000, mem.d * 1000
    cover = ds.beam_cover * 1000
    d_eff = D_mm - cover - 18
    notes: list[str] = []
    m_sag, m_hl, m_hr = float(sag.max()), float(-hog[0]), float(-hog[-1])
    # ---- torsion (cl 41): equilibrium torsion from cantilevers; compatibility torsion is released
    tors = None
    if tmax > 1.0:
        pt0 = 100 * 0.85 * b_mm * d_eff / fy / (b_mm * d_eff)
        tors = is456.torsion_design(tmax, vmax, m_sag, max(m_hl, m_hr), b_mm, D_mm, cover, fck, fy, ds.fy_shear, pt0)
        # longitudinal steel for the equivalent moments (cl 41.4.2)
        m_sag = max(tors.Me1_sag, tors.Me2_hog)
        m_hl = max(m_hl + tors.Mt, tors.Me2_sag)
        m_hr = max(m_hr + tors.Mt, tors.Me2_sag)
        if tors.note:
            notes.append(tors.note)
    fs = is456.flexure(m_sag, fck, fy, b_mm, D_mm, cover)
    fl = is456.flexure(m_hl, fck, fy, b_mm, D_mm, cover)
    fr = is456.flexure(m_hr, fck, fy, b_mm, D_mm, cover)
    ast_min = 0.85 * b_mm * d_eff / fy  # cl 26.5.1.1
    if ductile:  # IS 13920 cl 6.2.1: ρmin = 0.24 √fck / fy on both faces
        ast_min = max(ast_min, is13920.rho_min(fck, fy) * b_mm * d_eff)
    # top bars at the supports; hogging compression steel comes from the bottom bars
    nl, dl, prov_l = is456.select_bars(max(fl.ast, fs.asc, ast_min), b_mm, cover)
    nr, dr, prov_r = is456.select_bars(max(fr.ast, fs.asc, ast_min), b_mm, cover)
    bot_req = max(fs.ast, fl.asc, fr.asc, ast_min)
    if ductile:  # cl 6.2.3: bottom steel at a joint face ≥ ½ the top steel there (bottom bars run through)
        bot_req = max(bot_req, 0.5 * max(prov_l, prov_r))
    n, dia, prov_b = is456.select_bars(bot_req, b_mm, cover)
    if ductile and 100 * max(prov_b, prov_l, prov_r) / (b_mm * d_eff) > 2.5:
        notes.append("steel > 2.5 % (IS 13920 cl 6.2.2) – increase section")
    pt = 100 * max(prov_l, prov_r) / (b_mm * d_eff)
    # ---- shear: analysis envelope, or capacity shear for ductile frames (cl 6.3.3)
    v_design = vmax
    a_nd, b_nd = m.nodes[mem.n1], m.nodes[mem.n2]
    if ductile and (mem.n1 in col_below or mem.n1 in col_above) and (mem.n2 in col_below or mem.n2 in col_above):
        lc = max(
            L
            - _end_support(mem, a_nd, b_nd, col_below, col_above, mem.n1)
            - _end_support(mem, a_nd, b_nd, col_below, col_above, mem.n2),
            0.3 * L,
        )
        g = fa.forces(mid, {"DL": 1.2, "LL": 1.2}, 3)
        ms = is13920.beam_moment_capacity(prov_b, b_mm, d_eff, fck, fy) / 1e6
        v_cap = is13920.beam_capacity_shear(
            abs(float(g.Vz[0])),
            abs(float(g.Vz[-1])),
            ms,
            is13920.beam_moment_capacity(prov_l, b_mm, d_eff, fck, fy) / 1e6,
            ms,
            is13920.beam_moment_capacity(prov_r, b_mm, d_eff, fck, fy) / 1e6,
            lc,
        )
        v_design = max(vmax, v_cap)
    sh = is456.shear(v_design, fck, ds.fy_shear, b_mm, d_eff, pt)
    if tors is not None:  # torsion governs the closed stirrups (cl 41.4.3)
        tors = is456.torsion_design(
            tmax, v_design, m_sag, max(m_hl, m_hr), b_mm, D_mm, cover, fck, fy, ds.fy_shear, pt, max(dl, dr)
        )
        if tors.ok and (not sh.ok or tors.spacing * sh.dia**2 <= sh.spacing * tors.dia**2):
            sh = is456.ShearResult(v_design, tors.tau_ve, sh.tau_c, 2, tors.dia, tors.spacing, True)
        elif not tors.ok:
            sh = is456.ShearResult(v_design, tors.tau_ve, sh.tau_c, 2, 12, 0.0, False, tors.note)
    links = links_end = None
    if sh.ok:
        s_mid = min(sh.spacing, math.floor(d_eff / 2 / 25) * 25) if ductile else sh.spacing  # cl 6.3.5
        links = is456.Links(sh.legs, sh.dia, float(s_mid))
        if ductile:  # within 2d of each column face: ≤ min(d/4, 8 db, 100) (cl 6.3.5)
            s_end = min(sh.spacing, d_eff / 4, 8 * min(dia, dl, dr), 100.0)
            links_end = is456.Links(sh.legs, max(sh.dia, 8), float(max(math.floor(s_end / 5) * 5, 50)))
    notes += [x for x in (fs.note, fl.note, fr.note, sh.note) if x]
    span, basic = _deflection_span(m, mem)
    mf = is456.deflection_mf(100 * prov_b / (b_mm * d_eff), 0.58 * fy * fs.ast / max(prov_b, 1))
    allowed = basic * mf * (10.0 / span if span > 10.0 and basic != 7 else 1.0)  # cl 23.2.1 (b)
    dok = (span * 1000 / d_eff) <= allowed if span > 0 else True
    if not dok:
        notes.append(f"deflection span/d {span * 1000 / d_eff:.1f} > {allowed:.1f}")
    mul = is456.mu_lim(fck, fy, b_mm, d_eff) / 1e6
    util = max(sag.max(), -hog.min()) / mul if mul else 0
    if links is None:
        stirrups = "FAIL"
    elif links_end is not None:
        stirrups = f"{links_end} (2d from faces) / {int(links.spacing)} c/c"
    else:
        stirrups = str(links)
    return BeamDesign(
        mid,
        mem.mark,
        levels.get(mem.level, str(mem.level)),
        mem.b,
        mem.d,
        L,
        float(sag.max()),
        float(hog[0]),
        float(hog[-1]),
        v_design,
        f"{n}-T{dia}",
        f"{nl}-T{dl}",
        f"{nr}-T{dr}",
        stirrups,
        prov_b,
        prov_l,
        prov_r,
        dok,
        fs.ok and fl.ok and fr.ok and sh.ok and dok and (tors is None or tors.ok),
        float(util),
        notes,
        bottom_bars=is456.BarSet(n, dia),
        top_l_bars=is456.BarSet(nl, dl),
        top_r_bars=is456.BarSet(nr, dr),
        links=links,
        group=mem.group,
        level_index=mem.level,
        links_end=links_end,
        T_max=tmax,
        side_face=(tors.side_face if tors else _side_face(b_mm, D_mm, cover)),
    )


def _side_face(b_mm: float, D_mm: float, cover: float) -> str:
    """IS 456 cl 26.5.1.3: beams deeper than 750 mm need side-face bars of 0.1 % of the web area
    spread equally on both faces, at most 300 mm apart."""
    if D_mm <= 750:
        return ""
    a_face = 0.001 * b_mm * D_mm / 2
    n = max(2, math.ceil((D_mm - 2 * cover - 100) / 300))
    dia = next((d for d in (10, 12, 16) if n * math.pi * d * d / 4 >= a_face), 16)
    return f"{n}-T{dia} each face (side-face, cl 26.5.1.3)"


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
