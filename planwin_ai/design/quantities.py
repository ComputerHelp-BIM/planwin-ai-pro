"""Quantities (BOQ) and cost estimate from the frame model and the design report."""

from __future__ import annotations

import math

from ..core import geometry as G
from ..core.frame import FrameAnalysis, FrameModel
from ..core.model import Project
from .report import DesignReport

STEEL_DENSITY = 7850.0  # kg/m^3


def quantities(fa: FrameAnalysis, project: Project, rep: DesignReport) -> dict:
    """Concrete, steel, formwork and cost estimate."""
    m = fa.model
    conc: dict[str, float] = {}
    form = 0.0
    steel = {"columns": 0.0, "walls": 0.0, "beams": 0.0, "slabs": 0.0, "footings": 0.0}

    def add_c(grade, v):
        conc[grade] = conc.get(grade, 0.0) + v

    def link_kg(b: float, d: float, length: float, links, cover: float = 0.04) -> float:
        """Closed links over ``length`` (m) for a b × d section (m)."""
        dia = links.dia if links else 8
        sp = (links.spacing / 1000) if links and links.spacing > 0 else 0.15
        per = 2 * ((b - 2 * cover) + (d - 2 * cover)) + 0.24  # hooks
        return (length / sp + 1) * per * math.pi * (dia / 1000) ** 2 / 4 * STEEL_DENSITY

    cd = {c.member_id: c for c in rep.columns}
    bd = {b.member_id: b for b in rep.beams}
    wd = {w.member_id: w for w in getattr(rep, "walls", [])}
    slab_t = _beam_slab_thickness(m)
    col_at = {mem.n2: mem for mem in m.members.values() if mem.kind in ("column", "wall")}  # support below a node
    for mid, mem in m.members.items():
        if mem.kind == "link":  # rigid links of the wall model are not real concrete
            continue
        a, b = m.nodes[mem.n1], m.nodes[mem.n2]
        L = math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))
        if mem.kind == "wall":
            add_c(mem.grade, mem.b * mem.d * L)
            form += 2 * (mem.b + mem.d) * L
            w = wd.get(mid)
            rho = (w.rho_v + w.rho_h) if w else 0.005  # vertical + horizontal steel ratios
            steel["walls"] += rho * mem.b * mem.d * L * 1.1 * STEEL_DENSITY
        elif mem.kind == "column":
            add_c(mem.grade, mem.b * mem.d * L)
            form += 2 * (mem.b + mem.d) * L
            c = cd.get(mid)
            if c:
                if c.main_bars is not None:
                    n, dia = c.main_bars.count, c.main_bars.dia
                else:
                    n, dia = _parse_bars(c.bars)
                steel["columns"] += n * math.pi * dia * dia / 4e6 * L * 1.1 * STEEL_DENSITY  # +10 % laps
                l0 = min(2 * c.l0, L) if c.tie_confined else 0.0
                steel["columns"] += link_kg(mem.b, mem.d, L - l0, c.tie) + (
                    link_kg(mem.b, mem.d, l0, c.tie_confined) * 1.5 if l0 else 0.0  # cross-ties in l0
                )
        else:
            # concrete below the slab only (the slab volume is counted with the slabs) and
            # clear length between column faces (the joint is counted with the column)
            t = min(slab_t.get(mem.group, 0.0), mem.d)
            Lc = max(L - sum(_half_in_column(col_at.get(n), a, b) for n in (mem.n1, mem.n2)), 0.0)
            add_c(mem.grade, mem.b * (mem.d - t) * Lc)
            form += (mem.b + 2 * (mem.d - t)) * Lc
            d = bd.get(mid)
            if d:
                area = d.ast_bot * L + (d.ast_top_l + d.ast_top_r) * L / 3 + 2 * 113 * L
                steel["beams"] += area / 1e6 * STEEL_DENSITY
                zone = min(4 * mem.d, Lc) if d.links_end else 0.0  # 2d at each end
                steel["beams"] += link_kg(mem.b, mem.d, Lc - zone, d.links, 0.025)
                if zone:
                    steel["beams"] += link_kg(mem.b, mem.d, zone, d.links_end, 0.025)
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
        # ast_* are mm²/m: bars along L are spread over the width B and are L long (and vice versa)
        steel["footings"] += (f.ast_L + f.ast_B) * f.L * f.B / 1e6 * STEEL_DENSITY
    total_c = sum(v for k, v in conc.items() if not k.startswith("PCC"))
    total_s = sum(steel.values())
    rates = project.design.rates
    cost = 0.0
    lines = []
    for g, v in conc.items():
        r = rates.get(
            f"concrete_{g.lower().replace(' ', '_')}",
            rates.get("concrete_m25", 7000.0) if not g.startswith("PCC") else 5000.0,
        )
        lines.append((f"Concrete {g}", "m³", v, r, v * r))
        cost += v * r
    lines.append(
        ("Reinforcement steel", "kg", total_s, rates.get("steel_kg", 75.0), total_s * rates.get("steel_kg", 75.0))
    )
    cost += total_s * rates.get("steel_kg", 75.0)
    lines.append(("Formwork", "m²", form, rates.get("formwork_m2", 550.0), form * rates.get("formwork_m2", 550.0)))
    cost += form * rates.get("formwork_m2", 550.0)
    return {
        "concrete": conc,
        "steel": steel,
        "formwork": form,
        "total_concrete": total_c,
        "total_steel": total_s,
        "steel_per_m3": total_s / total_c if total_c else 0.0,
        "lines": lines,
        "cost": cost,
    }


def _beam_slab_thickness(m: FrameModel) -> dict[str, float]:
    """Thickest suspended slab bearing on each plan beam (keyed by beam id)."""
    out: dict[str, float] = {}
    for lv in m.levels[1:]:
        plan = m.p.plan(lv.plan)
        if plan is None:
            continue
        for bm in plan.beams:
            if bm.id in out:
                continue
            t = 0.0
            for s in plan.slabs:
                if s.distribution == "on_grade":
                    continue
                if any(
                    G.collinear_overlap(bm.p1, bm.p2, e0, e1, tol=max(0.02, bm.b / 2 + 0.03)) for e0, e1 in s.edges()
                ):
                    t = max(t, s.thickness)
            out[bm.id] = t
    return out


def _half_in_column(col, a, b) -> float:
    """Length of beam a->b that lies inside column ``col`` (half its extent along the beam)."""
    if col is None:
        return 0.0
    L = math.hypot(b.x - a.x, b.y - a.y) or 1.0
    ux, uy = (b.x - a.x) / L, (b.y - a.y) / L
    ang = math.radians(col.angle)
    return (
        abs(ux * math.cos(ang) + uy * math.sin(ang)) * col.b / 2
        + abs(-ux * math.sin(ang) + uy * math.cos(ang)) * col.d / 2
    )


def _parse_bars(s: str) -> tuple[int, int]:
    try:
        n, rest = s.split("-T", 1)
        return int(n), int(rest.split()[0])
    except (ValueError, IndexError):
        return 0, 0
