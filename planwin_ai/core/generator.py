"""Parametric building generator used by templates and the AI assistant.

``grid_building`` creates a complete PlanWin/FrameWin project:
plinth (ground) plan, typical floor plan, roof plan and the level stack.
"""

from __future__ import annotations

import math

from dataclasses import dataclass, field
from typing import Optional

from .model import Beam, Column, Level, Plan, Project, Slab
from .plan_engine import auto_beams, mark_external_beams

#: IS 875 (Part 2) imposed loads (kN/m^2) by occupancy, plus typical finishes
OCCUPANCY = {
    "residential": {"live": 2.0, "ff": 1.0, "other": 0.5, "importance": 1.2},
    "office": {"live": 3.0, "ff": 1.0, "other": 1.0, "importance": 1.2},
    "commercial": {"live": 4.0, "ff": 1.5, "other": 1.0, "importance": 1.2},
    "school": {"live": 3.0, "ff": 1.0, "other": 0.5, "importance": 1.5},
    "hospital": {"live": 3.0, "ff": 1.5, "other": 1.0, "importance": 1.5},
    "industrial": {"live": 5.0, "ff": 1.5, "other": 0.5, "importance": 1.2},
    "it_park": {"live": 4.0, "ff": 1.5, "other": 1.0, "importance": 1.2},
}


@dataclass
class GridSpec:
    name: str = "Grid Building"
    bays_x: list[float] = field(default_factory=lambda: [4.0, 4.0, 4.0])
    bays_y: list[float] = field(default_factory=lambda: [4.0, 4.0])
    upper_floors: int = 3  # G + N
    ground_height: float = 3.2  # plinth to first floor
    floor_height: float = 3.0
    foundation_depth: float = 2.0  # footing base to plinth
    occupancy: str = "residential"
    slab_thickness: float = 0.0  # 0 -> automatic from the short span (deflection)
    roof_live: float = 1.5
    roof_finish: float = 2.0
    parapet: float = 1.0
    column: tuple[float, float] = (0.30, 0.45)
    beam_int: tuple[float, float] = (0.23, 0.45)
    beam_ext: tuple[float, float] = (0.23, 0.60)
    grade: str = "M25"
    balcony: Optional[dict] = None  # {"side": "south", "depth": 1.2}
    mumty: bool = False  # stair cabin over first bay
    city: str = "Mumbai"
    auto_size: bool = True  # size beams from span and columns from axial load


def auto_slab_thickness(bays_x, bays_y, ratio: float = 30.0) -> float:
    """Thickness that satisfies IS 456 span/depth for the largest short span.

    ``ratio`` ~30 suits continuous panels; use ~26 for isolated panels.
    """
    lx = max(min(a, b) for a in (bays_x or [4.0]) for b in (bays_y or [4.0]))
    return round(max(0.125, math.ceil((lx / ratio + 0.025) / 0.005) * 0.005), 3)


def _grid_plan(spec: GridSpec, name: str, floor_type: str, fha: float) -> Plan:
    occ = OCCUPANCY.get(spec.occupancy, OCCUPANCY["residential"])
    if not spec.slab_thickness:
        spec.slab_thickness = auto_slab_thickness(spec.bays_x, spec.bays_y)
    plan = Plan(name=name, floor_type=floor_type, floor_height_above=fha)
    xs = [0.0]
    for b in spec.bays_x:
        xs.append(round(xs[-1] + b, 4))
    ys = [0.0]
    for b in spec.bays_y:
        ys.append(round(ys[-1] + b, 4))
    for j in range(len(ys) - 1):
        for i in range(len(xs) - 1):
            s = Slab(mark="tmp", points=[[xs[i], ys[j]], [xs[i + 1], ys[j]], [xs[i + 1], ys[j + 1]], [xs[i], ys[j + 1]]],
                     thickness=spec.slab_thickness, grade=spec.grade)
            if floor_type == "ground":
                s.distribution = "on_grade"
            elif floor_type == "roof":
                s.live, s.floor_finish, s.other = spec.roof_live, spec.roof_finish, 0.0
            else:
                s.live, s.floor_finish, s.other = occ["live"], occ["ff"], occ["other"]
            plan.slabs.append(s)
    if spec.balcony and floor_type == "typical":
        side, dep = spec.balcony.get("side", "south"), float(spec.balcony.get("depth", 1.2))
        for i in range(len(xs) - 1):
            if side == "south":
                pts = [[xs[i], -dep], [xs[i + 1], -dep], [xs[i + 1], 0.0], [xs[i], 0.0]]
                edge = 2
            else:  # north
                y = ys[-1]
                pts = [[xs[i], y], [xs[i + 1], y], [xs[i + 1], y + dep], [xs[i], y + dep]]
                edge = 0
            t_bal = max(0.125, math.ceil((dep / 9 + 0.025) / 0.005) * 0.005)
            plan.slabs.append(Slab(mark="tmp", points=pts, thickness=t_bal, live=3.0, floor_finish=1.0,
                                   distribution="cantilever", cant_edge=edge, grade=spec.grade, room="Balcony"))
    plan.renumber("slab")
    for y in reversed(ys):
        for x in xs:
            plan.columns.append(Column(mark="tmp", x=x, y=y, b=spec.column[0], d=spec.column[1], grade=spec.grade))
    plan.renumber("column")
    auto_beams(plan, spec.beam_int, spec.beam_ext, grade=spec.grade)
    plan.renumber("beam")
    mark_external_beams(plan)
    open_plan = spec.occupancy in ("office", "commercial", "it_park", "industrial")
    for b in plan.beams:
        if open_plan and not b.external and floor_type != "roof":
            # open-plan floors: partitions are in the slab "other" load, no masonry on internal beams
            b.include_wall = b.include_plaster = False
        if floor_type == "roof":
            b.parapet = spec.parapet if b.external else None
            b.wall_height = None
            if not b.external:
                b.include_wall = b.include_plaster = False
    return plan


def _span_depth(span: float, current: float, ratio: float = 12.0) -> float:
    """Beam depth ~ span/ratio (12 gravity, 10 for seismic frames), rounded up to 25 mm."""
    return round(max(current, math.ceil(span / ratio / 0.025) * 0.025), 3)


def grid_building(spec: GridSpec) -> Project:
    occ = OCCUPANCY.get(spec.occupancy, OCCUPANCY["residential"])
    if spec.auto_size:
        from ..io.cities import lookup_city

        span = max(list(spec.bays_x) + list(spec.bays_y))
        info = lookup_city(spec.city)
        seismic = bool(info) and info["zone"] in ("III", "IV", "V")
        ratio = 10.0 if seismic and spec.upper_floors >= 3 else 12.0
        width = 0.3 if span > 5.0 or spec.upper_floors >= 5 else spec.beam_int[0]
        spec.beam_int = (max(spec.beam_int[0], width), _span_depth(span, spec.beam_int[1], ratio))
        spec.beam_ext = (max(spec.beam_ext[0], width), _span_depth(span, spec.beam_ext[1], ratio))
    prj = Project(name=spec.name, location=spec.city)
    ground = _grid_plan(spec, "Ground", "ground", spec.ground_height)
    typical = _grid_plan(spec, "Typical", "typical", spec.floor_height)
    roof = _grid_plan(spec, "Roof", "roof", spec.floor_height)
    prj.plans.extend([ground, typical, roof])
    prj.levels.append(Level("Plinth", "Ground", spec.foundation_depth, spec.grade))
    for k in range(spec.upper_floors):
        h = spec.ground_height if k == 0 else spec.floor_height
        prj.levels.append(Level(f"Floor {k + 1}", "Typical", h, spec.grade))
    prj.levels.append(Level("Roof", "Roof", spec.ground_height if spec.upper_floors == 0 else spec.floor_height, spec.grade))
    if spec.mumty and len(spec.bays_x) and len(spec.bays_y):
        m = Plan(name="Mumty", floor_type="roof", floor_height_above=3.0)
        x1, y1 = spec.bays_x[0], spec.bays_y[0]
        m.slabs.append(Slab(mark="S1", points=[[0, 0], [x1, 0], [x1, y1], [0, y1]],
                            thickness=auto_slab_thickness([x1], [y1], 26.0),
                            live=0.75, floor_finish=2.0, grade=spec.grade))
        for (x, y) in ((0, y1), (x1, y1), (0, 0), (x1, 0)):
            src = roof.column_at((x, y), 0.05)
            m.columns.append(Column(mark=src.mark if src else "C?", x=x, y=y, b=spec.column[0], d=spec.column[1], grade=spec.grade))
        auto_beams(m, spec.beam_int, spec.beam_ext, grade=spec.grade)
        for b in m.beams:
            b.parapet = 0.6
        prj.plans.append(m)
        prj.levels.append(Level("Mumty", "Mumty", 3.0, spec.grade))
        # walls of the roof beams under the mumty go up to the mumty slab
        for b in roof.beams:
            if all(0 - 1e-6 <= v <= lim + 1e-6 for v, lim in ((b.x1, x1), (b.x2, x1), (b.y1, y1), (b.y2, y1))):
                b.parapet = None
                b.wall_height = 3.0 - b.d
                b.include_wall = b.include_plaster = True
    from dataclasses import asdict

    import json

    prj.meta["grid_spec"] = json.loads(json.dumps(asdict(spec)))  # JSON-normalised (tuples -> lists)
    if spec.auto_size:
        from ..design.runner import autosize_columns

        b = max(spec.column[0], 0.3 if spec.upper_floors >= 4 else 0.23)
        autosize_columns(prj, breadth=b, steel_pct=1.0, same_size=spec.upper_floors <= 8, max_step=0.1)
        if prj.seismic.enabled:  # columns at least as deep as the beams framing in (strong column)
            dmin = max(spec.beam_int[1], spec.beam_ext[1]) * 0.75
            for mark, per in prj.column_sizes.items():
                for k, v in per.items():
                    v[1] = round(max(v[1], dmin), 3)
    prj.seismic.importance = occ["importance"]
    prj.wind.below_ground = spec.foundation_depth  # plinth ~ ground level
    prj.wind.parapet = spec.parapet
    from ..io.cities import lookup_city  # local import to avoid cycles

    info = lookup_city(spec.city)
    if info:
        prj.wind.city = info["city"]
        if info["vb"]:
            prj.wind.basic_speed = info["vb"]
        prj.seismic.zone = info["zone"]
    return prj
