"""Staircase and overhead water-tank load wizards.

Both wizards keep their input in the project (``project.stairs`` / ``project.water_tanks``)
and (re)write the loads they produce, tagged with their name, so editing a definition and
applying it again never double-counts:

* **Staircase** (dog-legged / straight flight, IS 456:2000 cl 33): load per m² of plan from
  the waist slab (25·t·√(R²+T²)/T), steps (25·R/2), finishes and live load (IS 875-2:
  3 kN/m² residential, 5 kN/m² public); the flight spans horizontally between its support
  beams with an effective span of going + half of each landing (≤ 1 m each, cl 33.1 b).
  Each support beam receives w·span/2 per metre over the flight width as a part load.  The
  waist slab is designed for w·L²/8 per metre width (main steel along the flight, 0.12 %
  distribution steel) with the span/depth check of cl 23.2.1 (basic ratio 20).
* **Water tank**: RCC tank sized from its capacity and water depth (base 200 mm, walls
  200 mm, cover slab 150 mm, free board 0.3 m); tank + water weight are shared equally by
  the supporting columns as joint loads at the given level.  The full water weight is
  treated as dead load, which also puts it in the seismic weight (IS 1893-1 cl 7.3).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from ..core.model import PartLoad, Project
from . import is456


# --------------------------------------------------------------------------- staircase
@dataclass
class Staircase:
    name: str = "ST1"
    plan: str = "Typical"
    support_beams: list[str] = field(default_factory=lambda: ["B1", "B2"])  # beam marks carrying the flight
    start: float = 0.0  # m along each support beam where the flight starts
    width: float = 1.2  # flight width (m)
    going: float = 3.0  # horizontal going of the flight (m)
    landing: float = 1.2  # landing width at each end (m)
    riser: float = 0.15
    tread: float = 0.30
    waist: float = 0.20  # waist slab thickness (m) – about span/20
    live: float = 3.0  # kN/m² (IS 875-2: 3 residential, 5 public/assembly)
    finish: float = 1.0  # kN/m²
    grade: str = "M25"


@dataclass
class StairDesign:
    w_dead: float  # kN/m² of plan
    w_live: float
    span: float  # m
    reaction_dead: float  # kN/m on each support beam
    reaction_live: float
    Mu: float  # kN·m per m width
    main: str
    distribution: str
    deflection_ok: bool
    ok: bool
    notes: list[str] = field(default_factory=list)


def design_staircase(st: Staircase, fy: float = 500.0, cover: float = 0.02) -> StairDesign:
    notes = []
    if not (0.10 <= st.riser <= 0.20) or not (0.22 <= st.tread <= 0.35):
        notes.append("riser 100–200 mm and tread 220–350 mm are usual (NBC 2016 Part 4)")
    if not 0.55 <= 2 * st.riser + st.tread <= 0.70:
        notes.append(f"2R + T = {2 * st.riser + st.tread:.2f} m – keep between 0.55 and 0.70 m for comfort")
    slope = math.sqrt(st.riser**2 + st.tread**2) / st.tread
    w_dead = 25.0 * st.waist * slope + 25.0 * st.riser / 2 + st.finish
    w_live = st.live
    span = st.going + min(st.landing / 2, 1.0) * 2  # cl 33.1 (b)
    rd, rl = w_dead * span / 2, w_live * span / 2
    wu = 1.5 * (w_dead + w_live)
    Mu = wu * span**2 / 8
    fck = float(st.grade.upper().replace("M", "") or 25)
    D = st.waist * 1000
    d = D - cover * 1000 - 6
    ast = is456.ast_singly(Mu * 1e6, fck, fy, 1000, d)
    amin = 0.0012 * 1000 * D
    ok = math.isfinite(ast)
    if not ok:
        notes.append("waist too thin for the bending moment – increase it")
    main = is456.mesh_for(max(ast, amin) if ok else amin, (10, 12, 16), min(3 * d, 300.0))
    dist = is456.mesh_for(amin, (8, 10), min(5 * d, 450.0))
    pt = 100 * (main.area_per_m if main else amin) / (1000 * d)
    allowed = 20 * is456.deflection_mf(pt, 0.58 * fy * max(ast, amin) / max(main.area_per_m if main else 1, 1))
    dok = span * 1000 / d <= allowed
    if not dok:
        notes.append(f"span/d = {span * 1000 / d:.1f} > {allowed:.1f} – increase the waist")
    return StairDesign(
        w_dead, w_live, span, rd, rl, Mu, str(main) if main else "-", str(dist) if dist else "-", dok, ok and dok, notes
    )


def apply_staircase(project: Project, st: Staircase) -> StairDesign:
    """Store the definition and (re)write its part loads on the support beams."""
    plan = project.plan(st.plan)
    if plan is None:
        raise ValueError(f"plan '{st.plan}' not found")
    beams = {b.mark: b for b in plan.beams}
    missing = [m for m in st.support_beams if m not in beams]
    if missing:
        raise ValueError(f"support beam(s) {', '.join(missing)} not found in plan '{st.plan}'")
    des = design_staircase(st, project.design.fy_main)
    tag = f"Stair {st.name}"
    for b in plan.beams:  # remove this staircase's previous loads everywhere on the plan
        b.part_loads = [pl for pl in b.part_loads if pl.desc != tag]
    for mark in st.support_beams:
        b = beams[mark]
        a0 = min(max(st.start, 0.0), b.length)
        ln = min(st.width, b.length - a0)
        if ln <= 0:
            raise ValueError(f"flight does not fit on beam {mark} (length {b.length:.2f} m)")
        b.part_loads.append(PartLoad(a0, ln, des.reaction_dead, des.reaction_dead, "D", tag))
        b.part_loads.append(PartLoad(a0, ln, des.reaction_live, des.reaction_live, "L", tag))
    project.stairs = [s for s in project.stairs if s.get("name") != st.name] + [asdict(st)]
    return des


# --------------------------------------------------------------------------- water tank
@dataclass
class WaterTank:
    name: str = "T1"
    capacity_l: float = 10000.0
    water_depth: float = 1.5  # m
    level: int = 0  # level index (1-based) whose column tops carry the tank; 0 = top level
    columns: list[str] = field(default_factory=lambda: ["C1", "C2", "C3", "C4"])


@dataclass
class TankLoads:
    side: float  # m, square tank plan side (inside)
    water: float  # kN
    tank: float  # kN, RCC self weight
    per_column: float  # kN
    level: int


def tank_loads(t: WaterTank, n_levels: int) -> TankLoads:
    if t.capacity_l <= 0 or t.water_depth <= 0 or not t.columns:
        raise ValueError("capacity, water depth and supporting columns are required")
    vol = t.capacity_l / 1000.0
    area = vol / t.water_depth
    side = math.sqrt(area)
    wall_h = t.water_depth + 0.3  # free board
    outer = side + 2 * 0.2
    concrete = outer**2 * 0.2 + outer**2 * 0.15 + 4 * (side + 0.2) * wall_h * 0.2
    water = vol * 9.81
    tank = concrete * 25.0
    lvl = t.level or n_levels
    return TankLoads(side, water, tank, (water + tank) / len(t.columns), lvl)


def apply_water_tank(project: Project, t: WaterTank) -> TankLoads:
    loads = tank_loads(t, len(project.levels))
    if not 1 <= loads.level <= len(project.levels):
        raise ValueError(f"level {loads.level} does not exist")
    plan = project.plan(project.levels[loads.level - 1].plan)
    marks = {c.mark for c in plan.columns} if plan else set()
    missing = [c for c in t.columns if c not in marks]
    if missing:
        raise ValueError(f"column(s) {', '.join(missing)} not found at level {project.levels[loads.level - 1].name}")
    tag = f"Tank {t.name}"
    project.joint_loads = [j for j in project.joint_loads if j.get("source") != tag]
    for mark in t.columns:
        project.joint_loads.append(
            {"level": loads.level, "mark": mark, "fz": round(loads.per_column, 2), "case": "D", "source": tag}
        )
    project.water_tanks = [w for w in project.water_tanks if w.get("name") != t.name] + [asdict(t)]
    return loads
