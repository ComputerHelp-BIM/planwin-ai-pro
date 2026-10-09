"""Quantities (BOQ) and cost estimate from the frame model and the design report."""

from __future__ import annotations

import math

from ..core import geometry as G
from ..core.frame import FrameAnalysis, FrameModel
from ..core.model import Project
from .report import DesignReport

STEEL_DENSITY = 7850.0  # kg/m^3


class _Ledger:
    """Quantities booked by (level, member type): concrete by grade, steel and formwork."""

    TYPES = ("Columns", "Walls", "Beams", "Slabs", "Footings")

    def __init__(self, rates: dict[str, float]):
        self.rates = rates
        self.rows: dict[tuple[str, str], dict] = {}
        self.order: list[str] = []

    def add(
        self, level: str, kind: str, grade: str = "", concrete: float = 0.0, steel: float = 0.0, formwork: float = 0.0
    ) -> None:
        if level not in self.order:
            self.order.append(level)
        r = self.rows.setdefault((level, kind), {"concrete": {}, "steel": 0.0, "formwork": 0.0})
        if concrete:
            r["concrete"][grade] = r["concrete"].get(grade, 0.0) + concrete
        r["steel"] += steel
        r["formwork"] += formwork

    def rate(self, grade: str) -> float:
        if grade.startswith("PCC"):
            return self.rates.get("concrete_pcc", 5000.0)
        return self.rates.get(f"concrete_{grade.lower().replace(' ', '_')}", self.rates.get("concrete_m25", 7000.0))

    def summarise(self, rows) -> dict:
        conc: dict[str, float] = {}
        steel = formwork = 0.0
        for r in rows:
            for g, v in r["concrete"].items():
                conc[g] = conc.get(g, 0.0) + v
            steel += r["steel"]
            formwork += r["formwork"]
        rc = sum(v for g, v in conc.items() if not g.startswith("PCC"))
        cost = (
            sum(v * self.rate(g) for g, v in conc.items())
            + steel * self.rates.get("steel_kg", 75.0)
            + formwork * self.rates.get("formwork_m2", 550.0)
        )
        return {
            "concrete": rc,
            "pcc": conc.get("PCC M10", 0.0),
            "by_grade": conc,
            "steel": steel,
            "formwork": formwork,
            "cost": cost,
        }


def quantities(fa: FrameAnalysis, project: Project, rep: DesignReport) -> dict:
    """Concrete, steel, formwork and cost – totals, per member type and per floor.

    Steel is taken from the detailed design where available (bars, links/ties incl. the
    IS 13920 confined zones, slab meshes, footing meshes, wall ratios) with +10 % for laps
    on column and wall bars.  Beam concrete excludes the slab depth and the beam–column
    joint (counted with the slab and the column).
    """
    m = fa.model
    led = _Ledger(project.design.rates)
    lvl_name = {lv.index: lv.name for lv in m.levels}

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
        level = lvl_name.get(mem.level, str(mem.level))
        if mem.kind == "wall":
            w = wd.get(mid)
            rho = (w.rho_v + w.rho_h) if w else 0.005  # vertical + horizontal steel ratios
            led.add(
                level,
                "Walls",
                mem.grade,
                mem.b * mem.d * L,
                rho * mem.b * mem.d * L * 1.1 * STEEL_DENSITY,
                2 * (mem.b + mem.d) * L,
            )
        elif mem.kind == "column":
            kg = 0.0
            c = cd.get(mid)
            if c:
                n, dia = (c.main_bars.count, c.main_bars.dia) if c.main_bars is not None else _parse_bars(c.bars)
                kg += n * math.pi * dia * dia / 4e6 * L * 1.1 * STEEL_DENSITY  # +10 % laps
                l0 = min(2 * c.l0, L) if c.tie_confined else 0.0
                kg += link_kg(mem.b, mem.d, L - l0, c.tie)
                if l0:
                    kg += link_kg(mem.b, mem.d, l0, c.tie_confined) * 1.5  # cross-ties in l0
            led.add(level, "Columns", mem.grade, mem.b * mem.d * L, kg, 2 * (mem.b + mem.d) * L)
        else:
            # concrete below the slab only (the slab volume is counted with the slabs) and
            # clear length between column faces (the joint is counted with the column)
            t = min(slab_t.get(mem.group, 0.0), mem.d)
            Lc = max(L - sum(_half_in_column(col_at.get(n), a, b) for n in (mem.n1, mem.n2)), 0.0)
            kg = 0.0
            d = bd.get(mid)
            if d:
                area = d.ast_bot * L + (d.ast_top_l + d.ast_top_r) * L / 3 + 2 * 113 * L
                kg += area / 1e6 * STEEL_DENSITY
                zone = min(4 * mem.d, Lc) if d.links_end else 0.0  # 2d at each end
                kg += link_kg(mem.b, mem.d, Lc - zone, d.links, 0.025)
                if zone:
                    kg += link_kg(mem.b, mem.d, zone, d.links_end, 0.025)
            led.add(level, "Beams", mem.grade, mem.b * (mem.d - t) * Lc, kg, (mem.b + 2 * (mem.d - t)) * Lc)
    slab_res = {(pn, r.mark): r for pn, r in rep.slabs}
    for i, lv in enumerate(project.levels, start=1):
        plan = project.plan(lv.plan)
        if not plan:
            continue
        for s in plan.slabs:
            if s.distribution == "on_grade":
                continue
            r = slab_res.get((plan.name, s.mark))
            if r is not None and r.mesh_x is not None:
                per_m2 = r.mesh_x.area_per_m + (r.mesh_y.area_per_m if r.mesh_y else 0.0)
                per_m2 += 0.5 * r.mesh_neg.area_per_m if r.mesh_neg else 0.0  # top bars over ~half the panel
                kg = s.area * per_m2 / 1e6 * STEEL_DENSITY * 1.05  # laps / chairs
            else:
                kg = s.area * s.thickness * 80.0  # typical 80 kg/m³ when not detailed
            led.add(lvl_name.get(i, lv.name), "Slabs", lv.grade, s.area * s.thickness, kg, s.area)
    g = project.levels[0].grade if project.levels else "M25"
    for f in rep.footings:
        # ast_* are mm²/m: bars along L are spread over the width B and are L long (and vice versa)
        led.add(
            "Foundation",
            "Footings",
            g,
            f.L * f.B * f.D,
            (f.ast_L + f.ast_B) * f.L * f.B / 1e6 * STEEL_DENSITY,
            2 * (f.L + f.B) * f.D,
        )
        led.add("Foundation", "Footings", "PCC M10", (f.L + 0.3) * (f.B + 0.3) * 0.1)
    for c in getattr(rep, "combined_footings", []):
        t_area = max(c.L * c.B, 0.0)
        led.add("Foundation", "Footings", g, t_area * c.D, t_area * c.D * 90.0, 2 * (c.L + c.B) * c.D)
        led.add("Foundation", "Footings", "PCC M10", (c.L + 0.3) * (c.B + 0.3) * 0.1)
    total = led.summarise(led.rows.values())
    by_type = {k: led.summarise(r for (lv_, kind), r in led.rows.items() if kind == k) for k in led.TYPES}
    levels_order = (["Foundation"] if "Foundation" in led.order else []) + [x for x in led.order if x != "Foundation"]
    by_level = {lv_: led.summarise(r for (lv2, _k), r in led.rows.items() if lv2 == lv_) for lv_ in levels_order}
    rates = project.design.rates
    lines = []
    for grade, v in total["by_grade"].items():
        lines.append((f"Concrete {grade}", "m³", v, led.rate(grade), v * led.rate(grade)))
    st = rates.get("steel_kg", 75.0)
    fw = rates.get("formwork_m2", 550.0)
    lines.append(("Reinforcement steel", "kg", total["steel"], st, total["steel"] * st))
    lines.append(("Formwork", "m²", total["formwork"], fw, total["formwork"] * fw))
    steel = {k.lower(): v["steel"] for k, v in by_type.items()}
    return {
        "concrete": total["by_grade"],
        "steel": steel,
        "formwork": total["formwork"],
        "total_concrete": total["concrete"],
        "total_steel": total["steel"],
        "steel_per_m3": total["steel"] / total["concrete"] if total["concrete"] else 0.0,
        "lines": lines,
        "cost": total["cost"],
        "by_type": by_type,
        "by_level": by_level,
    }


def revision_snapshot(boq: dict, label: str, date: str) -> dict:
    """Compact BOQ record kept in ``project.meta["revisions"]`` for later comparison."""
    keep = ("concrete", "steel", "formwork", "cost")
    return {
        "label": label,
        "date": date,
        "total": {
            "concrete": boq["total_concrete"],
            "steel": boq["total_steel"],
            "formwork": boq["formwork"],
            "cost": boq["cost"],
        },
        "by_level": {k: {q: v[q] for q in keep} for k, v in boq.get("by_level", {}).items()},
        "by_type": {k: {q: v[q] for q in keep} for k, v in boq.get("by_type", {}).items()},
    }


def compare_revisions(a: dict, b: dict) -> list[tuple[str, str, float, float, float, float]]:
    """Rows (group, item, value A, value B, change, change %) comparing two revision snapshots."""
    rows = []
    units = {"concrete": "m³", "steel": "kg", "formwork": "m²", "cost": "₹"}

    def add(group, key, qa, qb):
        for q, unit in units.items():
            va, vb = qa.get(q, 0.0), qb.get(q, 0.0)
            pct = (vb - va) / va * 100 if abs(va) > 1e-9 else (0.0 if abs(vb) < 1e-9 else float("inf"))
            rows.append((group, f"{key} – {q} ({unit})", va, vb, vb - va, pct))

    add("Total", "Building", a["total"], b["total"])
    for section in ("by_type", "by_level"):
        keys = list(dict.fromkeys(list(a.get(section, {})) + list(b.get(section, {}))))
        for k in keys:
            add(
                "Member type" if section == "by_type" else "Floor",
                k,
                a.get(section, {}).get(k, {}),
                b.get(section, {}).get(k, {}),
            )
    return rows


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
