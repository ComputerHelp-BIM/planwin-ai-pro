"""Domain model for PlanWin AI Pro.

Concepts map 1:1 to the legacy software so existing users feel at home:

* :class:`Plan`   – a PlanWin floor plan (slabs, columns, beams) that can be
  re-used at several levels (e.g. one "Typical" plan for floors 1-10).
* :class:`Level`  – a FrameWin level: which plan sits at the top of the
  storey, the storey height below it and its concrete grade.
* :class:`Project` – everything that is saved in a ``.pwai`` file.

Units: metres, kN, kN/m, kN/m^2, kN/m^3, MPa (N/mm^2) for material strengths.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import itertools
import math
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from . import geometry as G

#: 2 (1.1.0): shear walls, grid lines, staircases, water tanks, rigid diaphragm and seismic method
SCHEMA_VERSION = 2


_id_seed: str | None = None
_id_counter = itertools.count()


def new_id() -> str:
    """Random 10-hex-digit object id (deterministic inside :func:`deterministic_ids`)."""
    if _id_seed is None:
        return uuid.uuid4().hex[:10]
    return hashlib.sha1(f"{_id_seed}:{next(_id_counter)}".encode()).hexdigest()[:10]


@contextlib.contextmanager
def deterministic_ids(seed: str) -> Iterator[None]:
    """Reproducible ids, e.g. for the bundled template library (stable files under git)."""
    global _id_seed, _id_counter
    saved = _id_seed, _id_counter
    _id_seed, _id_counter = seed, itertools.count()
    try:
        yield
    finally:
        _id_seed, _id_counter = saved


# --------------------------------------------------------------------- loads
@dataclass
class PointLoad:
    """Concentrated load on a beam at ``dist`` metres from the beam start."""

    dist: float
    dead: float = 0.0  # kN (downwards positive)
    live: float = 0.0
    desc: str = "P"


@dataclass
class PartLoad:
    """Linearly varying (wedge) load from ``start`` over ``length`` metres."""

    start: float
    length: float
    w1: float  # kN/m at start
    w2: float  # kN/m at end
    case: str = "D"  # "D" dead or "L" live
    desc: str = "W"


# ------------------------------------------------------------------ elements
@dataclass
class Slab:
    id: str = field(default_factory=new_id)
    mark: str = "S1"
    points: list[list[float]] = field(default_factory=list)  # [[x, y], ...] CCW
    thickness: float = 0.125
    density: float = 25.0
    live: float = 2.0
    floor_finish: float = 1.0
    other: float = 0.0  # partitions, waterproofing, services
    #: auto | two_way | one_way (spans short way) | one_way_long | cantilever | on_grade
    #: "uniform" distributes the load evenly to all supporting edges.
    distribution: str = "auto"
    cant_edge: int | None = None  # index i of fixed edge (points[i] -> points[i+1])
    grade: str = "M25"
    room: str = ""

    @property
    def dead(self) -> float:
        """Total dead load intensity (kN/m^2)."""
        if self.distribution == "on_grade":
            return 0.0
        return self.thickness * self.density + self.floor_finish + self.other

    @property
    def live_load(self) -> float:
        return 0.0 if self.distribution == "on_grade" else self.live

    @property
    def pts(self) -> list[G.Point]:
        return [(float(p[0]), float(p[1])) for p in self.points]

    @property
    def area(self) -> float:
        return abs(G.polygon_area(self.pts))

    def edges(self) -> list[tuple[G.Point, G.Point]]:
        p = self.pts
        return [(p[i], p[(i + 1) % len(p)]) for i in range(len(p))]


@dataclass
class Column:
    id: str = field(default_factory=new_id)
    mark: str = "C1"
    x: float = 0.0
    y: float = 0.0
    b: float = 0.23  # dimension along local x (m)
    d: float = 0.45  # dimension along local y (m)
    angle: float = 0.0  # degrees, rotation of local x from global X
    grade: str = "M25"

    @property
    def pos(self) -> G.Point:
        return (self.x, self.y)

    def corners(self) -> list[G.Point]:
        return G.rect_corners(self.x, self.y, self.b, self.d, self.angle)


@dataclass
class Beam:
    id: str = field(default_factory=new_id)
    mark: str = "B1"
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 1.0
    y2: float = 0.0
    b: float = 0.23
    d: float = 0.45
    grade: str = "M25"
    #: auto | primary | secondary (which beam supports which at crossings)
    role: str = "auto"
    cantilever: bool = False
    external: bool = False
    # wall / plaster above the beam (PlanWin "double arrow" dialog)
    wall_thk: float = 0.23
    wall_height: float | None = None  # None -> floor height above - beam depth
    wall_density: float = 20.0
    plaster_thk: float = 0.03  # both faces combined
    plaster_density: float = 20.0
    include_self: bool = True
    include_wall: bool = True
    include_plaster: bool = True
    parapet: float | None = None  # roof parapet height (m) overrides wall height
    point_loads: list[PointLoad] = field(default_factory=list)
    part_loads: list[PartLoad] = field(default_factory=list)

    @property
    def p1(self) -> G.Point:
        return (self.x1, self.y1)

    @property
    def p2(self) -> G.Point:
        return (self.x2, self.y2)

    @property
    def length(self) -> float:
        return G.dist(self.p1, self.p2)

    def wall_h(self, floor_height_above: float, floor_type: str) -> float:
        if self.parapet is not None:
            return self.parapet
        if self.wall_height is not None:
            return self.wall_height
        if floor_type == "roof":
            return 0.0
        return max(floor_height_above - self.d, 0.0)

    def udl_components(self, floor_height_above: float, floor_type: str) -> dict[str, float]:
        """Self weight, wall and plaster loads per metre run (kN/m)."""
        h = self.wall_h(floor_height_above, floor_type)
        return {
            "self": self.b * self.d * 25.0 if self.include_self else 0.0,
            "wall": self.wall_thk * h * self.wall_density if self.include_wall else 0.0,
            "plaster": self.plaster_thk * h * self.plaster_density if self.include_plaster else 0.0,
        }

    def udl_dead(self, floor_height_above: float, floor_type: str) -> float:
        return sum(self.udl_components(floor_height_above, floor_type).values())


@dataclass
class Wall:
    """RC shear wall (structural wall) from (x1, y1) to (x2, y2), centred on that line.

    Walls are stacked between levels by ``mark`` exactly like columns.  In the 3-D
    model a wall is a wide column at its centre joined to its ends (and to any beam
    framing into it) by rigid links – the classic "wide column frame" idealisation.
    """

    id: str = field(default_factory=new_id)
    mark: str = "W1"
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 3.0
    y2: float = 0.0
    thickness: float = 0.2
    grade: str = "M25"

    @property
    def p1(self) -> G.Point:
        return (self.x1, self.y1)

    @property
    def p2(self) -> G.Point:
        return (self.x2, self.y2)

    @property
    def length(self) -> float:
        return G.dist(self.p1, self.p2)

    @property
    def centre(self) -> G.Point:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)

    @property
    def angle(self) -> float:
        """Direction of the wall in plan (degrees from global X)."""
        return math.degrees(math.atan2(self.y2 - self.y1, self.x2 - self.x1))

    def corners(self) -> list[G.Point]:
        cx, cy = self.centre
        return G.rect_corners(cx, cy, self.length, self.thickness, self.angle)

    def contains(self, p: G.Point, tol: float = 0.02) -> bool:
        """True when ``p`` lies inside the wall footprint grown by ``tol``."""
        L = self.length
        if L < 1e-9:
            return G.dist(p, self.p1) <= self.thickness / 2 + tol
        t = G.project_param(p, self.p1, self.p2)
        return -tol / L <= t <= 1 + tol / L and G.point_line_distance(p, self.p1, self.p2) <= self.thickness / 2 + tol


@dataclass
class Plan:
    """A PlanWin floor plan."""

    name: str = "Typical"
    #: typical | ground | roof
    floor_type: str = "typical"
    floor_height_above: float = 3.0
    slabs: list[Slab] = field(default_factory=list)
    columns: list[Column] = field(default_factory=list)
    beams: list[Beam] = field(default_factory=list)
    walls: list[Wall] = field(default_factory=list)
    #: drawing dimensions [{"x1", "y1", "x2", "y2", "offset"}] in m; offset = perpendicular distance of the
    #: dimension line from the measured points, positive to the left of (x1, y1) → (x2, y2)
    dimensions: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""

    # ------------------------------------------------------------ lookups
    def find(self, obj_id: str):
        for coll in (self.slabs, self.columns, self.beams, self.walls):
            for o in coll:
                if o.id == obj_id:
                    return o
        return None

    def remove(self, obj_ids) -> int:
        ids = set(obj_ids)
        n0 = len(self.slabs) + len(self.columns) + len(self.beams) + len(self.walls)
        self.slabs = [s for s in self.slabs if s.id not in ids]
        self.columns = [c for c in self.columns if c.id not in ids]
        self.beams = [b for b in self.beams if b.id not in ids]
        self.walls = [w for w in self.walls if w.id not in ids]
        return n0 - (len(self.slabs) + len(self.columns) + len(self.beams) + len(self.walls))

    def wall_at(self, p: G.Point, tol: float = 0.02) -> Wall | None:
        return next((w for w in self.walls if w.contains(p, tol)), None)

    def column_at(self, p: G.Point, tol: float = 0.05) -> Column | None:
        best, bd = None, tol
        for c in self.columns:
            d = G.dist(c.pos, p)
            if d <= bd:
                best, bd = c, d
        return best

    def all_points(self) -> list[G.Point]:
        pts: list[G.Point] = []
        for s in self.slabs:
            pts.extend(s.pts)
        pts.extend(c.pos for c in self.columns)
        for b in self.beams:
            pts.extend((b.p1, b.p2))
        for w in self.walls:
            pts.extend((w.p1, w.p2))
        return pts

    def extents(self):
        return G.bbox(self.all_points())

    # ------------------------------------------------------------ numbering
    def next_mark(self, prefix: str) -> str:
        coll = {"S": self.slabs, "C": self.columns, "B": self.beams, "W": self.walls}[prefix]
        used = {o.mark for o in coll}
        for i in itertools.count(1):
            m = f"{prefix}{i}"
            if m not in used:
                return m
        raise RuntimeError("unreachable")

    def renumber(self, kind: str, order: str = "lr_tb") -> None:
        """Auto-renumber slabs/columns/beams left-to-right, top-to-bottom.

        Columns must keep the same mark from floor to floor (FrameWin links
        columns by mark), so renumbering columns is only advised on the first
        plan; the GUI warns about this like legacy PlanWin did.
        """

        def key_xy(p):
            x, y = p
            return (-round(y, 2), round(x, 2)) if order == "lr_tb" else (round(x, 2), -round(y, 2))

        if kind == "slab":
            items = sorted(self.slabs, key=lambda s: key_xy(G.polygon_centroid(s.pts)))
            prefix = "S"
        elif kind == "column":
            items = sorted(self.columns, key=lambda c: key_xy(c.pos))
            prefix = "C"
        else:
            items = sorted(self.beams, key=lambda b: key_xy(((b.x1 + b.x2) / 2, (b.y1 + b.y2) / 2)))
            prefix = "B"
        for i, o in enumerate(items, 1):
            o.mark = f"{prefix}{i}"


@dataclass
class Level:
    """A FrameWin level (top of storey)."""

    name: str
    plan: str  # Plan.name used at this level
    height: float  # storey height below this level (m)
    grade: str = "M25"
    live_reduction: float = 0.0  # % reduction (IS 875-2) applied in column design


@dataclass
class SeismicParams:
    enabled: bool = True
    zone: str = "III"
    importance: float = 1.2
    response_reduction: float = 5.0  # SMRF
    soil: str = "medium"  # hard | medium | soft
    damping: float = 0.05
    infill: bool = True  # Ta = 0.09h/sqrt(d) when True
    base_level: int = 1  # level index from which height is measured (plinth)
    accidental_torsion: bool = True  # IS 1893-1:2016 cl 7.8.2 (±0.05 b)
    #: "auto" – response spectrum where IS 1893-1:2016 cl 7.7.1 requires it, else equivalent static;
    #: "static" – equivalent static only; "response_spectrum" – always (scaled to the static base shear)
    method: str = "auto"
    #: floors act as rigid diaphragms (cl 7.6.4): lateral forces at the centre of mass,
    #: one translation pair + rotation per floor
    rigid_diaphragm: bool = True


@dataclass
class WindParams:
    enabled: bool = True
    city: str = "Mumbai"
    basic_speed: float = 44.0  # Vb m/s
    terrain: int = 3  # IS 875-3:2015 category 1..4
    k1: float = 1.0
    k3: float = 1.0
    k4: float = 1.0
    kd: float = 0.9
    ka: float = 1.0
    kc: float = 0.9
    force_coeff: float = 1.2
    parapet: float = 1.0
    below_ground: float = 0.0  # height where wind does not act


@dataclass
class DesignSettings:
    code: str = "IS456:2000"
    fy_main: float = 500.0
    fy_shear: float = 500.0
    beam_cover: float = 0.025
    column_cover: float = 0.040
    slab_cover: float = 0.020
    footing_cover: float = 0.050
    sbc: float = 200.0  # kN/m^2 safe bearing capacity
    footing_self_weight_pct: float = 10.0
    min_column_steel_pct: float = 0.8
    max_column_steel_pct: float = 4.0
    two_way_ratio_limit: float = 2.0
    continuity_in_load_transfer: bool = False
    torsion_release: bool = True  # J reduced to 10 % (cracked torsion)
    effective_length_factor: float = 1.2  # sway frame (IS 456 Annex E / Table 28)
    crack_beam: float = 0.35  # IS 1893-1:2016 cl 6.4.3.1 cracked section, beams
    crack_column: float = 0.70  # columns
    rates: dict[str, float] = field(
        default_factory=lambda: {
            "concrete_m20": 6500.0,
            "concrete_m25": 7000.0,
            "concrete_m30": 7600.0,
            "concrete_m35": 8200.0,
            "concrete_m40": 8800.0,
            "steel_kg": 75.0,
            "formwork_m2": 550.0,
        }
    )


@dataclass
class Project:
    name: str = "Untitled"
    client: str = ""
    engineer: str = ""
    location: str = ""
    plans: list[Plan] = field(default_factory=list)
    levels: list[Level] = field(default_factory=list)  # bottom (level 1) to top
    seismic: SeismicParams = field(default_factory=SeismicParams)
    wind: WindParams = field(default_factory=WindParams)
    design: DesignSettings = field(default_factory=DesignSettings)
    #: column size overrides: {mark: {level_index(str): [b, d, angle]}}
    column_sizes: dict[str, dict[str, list[float]]] = field(default_factory=dict)
    #: extra joint loads e.g. water tank: [{"level": i, "mark": "C4", "fz": 150}]
    joint_loads: list[dict[str, Any]] = field(default_factory=list)
    #: support conditions per column mark: "fixed" | "pinned"
    supports: dict[str, str] = field(default_factory=dict)
    #: grid lines shown on every plan: [{"name": "A", "axis": "x", "pos": 0.0}, …] (axis "x" = line x = pos)
    grids: list[dict[str, Any]] = field(default_factory=list)
    #: staircase definitions from the stair wizard (recomputed loads are applied to beams)
    stairs: list[dict[str, Any]] = field(default_factory=list)
    #: overhead water tanks from the tank wizard (applied as joint loads)
    water_tanks: list[dict[str, Any]] = field(default_factory=list)
    #: free-form metadata, e.g. {"grid_spec": {...}} for AI regeneration
    meta: dict[str, Any] = field(default_factory=dict)
    schema: int = SCHEMA_VERSION

    # ------------------------------------------------------------ helpers
    def plan(self, name: str) -> Plan | None:
        for p in self.plans:
            if p.name == name:
                return p
        return None

    def add_plan(self, plan: Plan) -> Plan:
        base, i = plan.name, 2
        while self.plan(plan.name):
            plan.name = f"{base} ({i})"
            i += 1
        self.plans.append(plan)
        return plan

    def elevations(self) -> list[float]:
        """Elevation of each level (index 0 = base / footing level = 0.0)."""
        z = [0.0]
        for lv in self.levels:
            z.append(z[-1] + lv.height)
        return z

    def column_size(self, mark: str, level_index: int, default: Column) -> tuple[float, float, float]:
        ov = self.column_sizes.get(mark, {}).get(str(level_index))
        if ov:
            return float(ov[0]), float(ov[1]), float(ov[2]) if len(ov) > 2 else default.angle
        return default.b, default.d, default.angle

    def set_column_size(self, mark: str, level_index: int, b: float, d: float, angle: float) -> None:
        self.column_sizes.setdefault(mark, {})[str(level_index)] = [round(b, 3), round(d, 3), angle]

    def clone(self) -> Project:
        return copy.deepcopy(self)

    # ------------------------------------------------------------ serialisation
    def to_dict(self) -> dict:
        d = asdict(self)
        d["app"] = "PlanWin AI Pro"
        return d

    @staticmethod
    def from_dict(d: dict) -> Project:
        return _build(Project, d)

    def summary(self) -> dict:
        return {
            "name": self.name,
            "plans": {
                p.name: {"slabs": len(p.slabs), "columns": len(p.columns), "beams": len(p.beams), "type": p.floor_type}
                for p in self.plans
            },
            "levels": [(lv.name, lv.plan, lv.height, lv.grade) for lv in self.levels],
            "height_m": round(self.elevations()[-1], 3),
            "seismic_zone": self.seismic.zone,
            "wind_city": self.wind.city,
        }


# ---------------------------------------------------------------------------
_NESTED = {
    ("Project", "plans"): "Plan",
    ("Project", "levels"): "Level",
    ("Plan", "slabs"): "Slab",
    ("Plan", "columns"): "Column",
    ("Plan", "beams"): "Beam",
    ("Plan", "walls"): "Wall",
    ("Beam", "point_loads"): "PointLoad",
    ("Beam", "part_loads"): "PartLoad",
}
_SINGLE = {
    ("Project", "seismic"): "SeismicParams",
    ("Project", "wind"): "WindParams",
    ("Project", "design"): "DesignSettings",
}


def _build(cls, data: dict):
    """Tolerant dataclass factory: ignores unknown keys (forward compatibility)."""
    reg = globals()
    kwargs = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        v = data[f.name]
        key = (cls.__name__, f.name)
        if key in _NESTED and isinstance(v, list):
            sub = reg[_NESTED[key]]
            v = [_build(sub, x) for x in v]
        elif key in _SINGLE and isinstance(v, dict):
            v = _build(reg[_SINGLE[key]], v)
        kwargs[f.name] = v
    return cls(**kwargs)


def grade_fck(grade: str) -> float:
    """'M25' -> 25.0"""
    try:
        return float(str(grade).upper().replace("M", "").strip())
    except ValueError:
        return 25.0


def concrete_E(fck: float) -> float:
    """Short-term modulus per IS 456 cl 6.2.3.1, returned in kN/m^2."""
    return 5000.0 * math.sqrt(fck) * 1000.0
