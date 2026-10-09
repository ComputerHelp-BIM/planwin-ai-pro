"""Import legacy PlanWin ``.plw`` files (VB6 versions 1.x, 2.x, 3.x and 5.7.x).

Legacy files store loads in tonnes (T, T/m, T/m^2) and grades as kg/cm^2
(e.g. 200 -> M20).  Everything is converted to kN / MPa.  Beam UDLs are
preserved exactly as a "legacy UDL" wedge load so imported projects
reproduce the original load take-down; wall data is re-derived only when
the file carries it (v5.7).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..core import geometry as G
from ..core.model import Beam, Column, PartLoad, Plan, PointLoad, Slab

T_TO_KN = 9.80665


class LegacyFormatError(ValueError):
    pass


@dataclass
class ImportReport:
    version: str = ""
    slabs: int = 0
    columns: int = 0
    beams: int = 0
    notes: list[str] = field(default_factory=list)


def _grade(v) -> str:
    try:
        g = float(v)
    except (TypeError, ValueError):
        return "M25"
    if g > 100:  # kg/cm^2
        g = g / 10.0
    g = int(round(g / 5.0) * 5) or 25
    return f"M{min(max(g, 15), 60)}"


def _num(v, default=0.0) -> float:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


_CANT_EDGE = {"Cant- 1 2": 0, "Cant- 2 3": 1, "Cant- 3 4": 2, "Cant- 1 4": 3}


def _direction(slab: Slab, direction: str, report: ImportReport):
    d = (direction or "").strip()
    if d in _CANT_EDGE:
        slab.distribution = "cantilever"
        slab.cant_edge = _CANT_EDGE[d] if _CANT_EDGE[d] < len(slab.points) else None
    elif d in ("Both", ""):
        slab.distribution = "auto"
    elif d in ("Side1", "Side2", "Long Side", "Small Side"):
        slab.distribution = "one_way" if d in ("Small Side", "Side1") else "one_way_long"
    else:
        report.notes.append(f"Slab {slab.mark}: unknown distribution '{d}' – automatic used")


def _slab_loads(slab: Slab, total_t: float, live_t: float, thk: float):
    total = total_t * T_TO_KN
    live = max(live_t * T_TO_KN, 0.0)
    dead = max(total - live, 0.0)
    slab.live = round(live, 4)
    slab.thickness = thk if thk > 0 else 0.125
    slab.floor_finish = 0.0
    self_w = slab.thickness * slab.density
    if dead >= self_w:
        slab.other = round(dead - self_w, 4)
    else:  # preserve the total exactly
        slab.density = round(dead / slab.thickness, 4) if slab.thickness else 25.0
        slab.other = 0.0


class _Tokens:
    """VB6 ``Input #`` style token stream: comma or newline separated."""

    def __init__(self, lines: list[str]):
        self.toks: list[str] = []
        for ln in lines:
            self.toks.extend(t.strip().strip('"') for t in ln.rstrip("\r\n").split(","))
        self.i = 0

    def next(self) -> str:
        if self.i >= len(self.toks):
            raise LegacyFormatError("Unexpected end of file")
        t = self.toks[self.i]
        self.i += 1
        return t

    def take(self, n: int) -> list[str]:
        return [self.next() for _ in range(n)]


def read_plw(path: str) -> tuple[Plan, ImportReport]:
    with open(path, encoding="latin-1") as f:
        lines = f.readlines()
    if not lines or not lines[0].startswith("Ver "):
        raise LegacyFormatError("Not a PlanWin .plw file (missing version header)")
    version = lines[0].split("#")[0].replace("Ver", "").strip()
    rep = ImportReport(version=version)
    name = os.path.splitext(os.path.basename(path))[0]
    major = int(version.split(".")[0]) if version[:1].isdigit() else 3
    try:
        plan = _read_v5(lines, name, rep) if major >= 5 else _read_v1_3(lines, name, rep)
    except (IndexError, ValueError) as exc:  # truncated / malformed record
        if isinstance(exc, LegacyFormatError):
            raise
        raise LegacyFormatError(f"Damaged PlanWin {version} file: {exc}") from exc
    rep.slabs, rep.columns, rep.beams = len(plan.slabs), len(plan.columns), len(plan.beams)
    return plan, rep


# ---------------------------------------------------------------- v1-v3
def _read_v1_3(lines: list[str], name: str, rep: ImportReport) -> Plan:
    tk = _Tokens(lines[1:])
    tk.take(5)  # scale, xmin, ymin, xmax, ymax
    ns, nc, nb = (int(_num(x)) for x in tk.take(3))
    plan = Plan(name=name)
    for _ in range(ns):
        _, cnt, _ang, mark, load, tempt, grade, direction, live, _stype = tk.take(10)
        cnt = int(_num(cnt))
        pts = [[_num(tk.next()), _num(tk.next())] for _ in range(cnt)]
        if cnt == 2:  # legacy degenerate triangle
            pts.append(pts[1][:])
        s = Slab(mark=mark, points=[[round(x, 4), round(y, 4)] for x, y in pts], grade=_grade(grade))
        thk = _num(tempt) if ":" not in tempt else 0.0
        _slab_loads(s, _num(load), _num(live), thk)
        _direction(s, direction, rep)
        plan.slabs.append(s)
    for _ in range(nc):
        _, cnt, ang, mark, length, breadth, _xc, _yc, *_ = tk.take(11)
        cnt = int(_num(cnt))
        pts = [(_num(tk.next()), _num(tk.next())) for _ in range(cnt)]
        cx = sum(p[0] for p in pts) / max(cnt, 1)
        cy = sum(p[1] for p in pts) / max(cnt, 1)
        plan.columns.append(
            Column(
                mark=mark,
                x=round(cx, 4),
                y=round(cy, 4),
                b=_num(breadth, 0.23) or 0.23,
                d=_num(length, 0.45) or 0.45,
                angle=_num(ang),
            )
        )
    for _ in range(nb):
        (_, width, depth, mark, grade, udl, btype, nload, *_rest) = tk.take(14)
        pts = [(_num(tk.next()), _num(tk.next())) for _ in range(4)]
        p1 = ((pts[0][0] + pts[3][0]) / 2, (pts[0][1] + pts[3][1]) / 2)
        p2 = ((pts[1][0] + pts[2][0]) / 2, (pts[1][1] + pts[2][1]) / 2)
        b = Beam(
            mark=mark,
            x1=round(p1[0], 4),
            y1=round(p1[1], 4),
            x2=round(p2[0], 4),
            y2=round(p2[1], 4),
            b=_num(width, 0.23),
            d=_num(depth, 0.45),
            grade=_grade(grade),
            cantilever=btype.lower().startswith("cant"),
        )
        _legacy_udl(b, _num(udl))
        for _ in range(int(_num(nload))):
            _, ltype, desc, st, ln, w1, w2 = tk.take(7)
            _beam_load(b, ltype, desc, _num(st), _num(ln), _num(w1), _num(w2))
        plan.beams.append(b)
    return plan


def _legacy_udl(b: Beam, udl_t: float):
    b.include_self = b.include_wall = b.include_plaster = False
    if udl_t > 0 and b.length > 0:
        w = round(udl_t * T_TO_KN, 4)
        b.part_loads.append(PartLoad(0.0, b.length, w, w, "D", "Legacy UDL"))


def _beam_load(b: Beam, ltype: str, desc: str, st: float, ln: float, w1: float, w2: float):
    lt = ltype.strip().lower()
    if lt.startswith("p"):
        b.point_loads.append(PointLoad(st, dead=round(w1 * T_TO_KN, 4), desc=desc or "P"))
    else:
        b.part_loads.append(PartLoad(st, ln, round(w1 * T_TO_KN, 4), round(w2 * T_TO_KN, 4), "D", desc or "W"))


# ---------------------------------------------------------------- v5.x
def _read_v5(lines: list[str], name: str, rep: ImportReport) -> Plan:
    L = [ln.rstrip("\r\n") for ln in lines]
    i = 1
    while i < len(L) and not L[i].strip():
        i += 1
    i += 1  # scale line
    counts = L[i].split(",")
    i += 1
    ns, nc, nb = int(_num(counts[0])), int(_num(counts[1])), int(_num(counts[2]))
    fha = _num(counts[8], 3.0) if len(counts) > 8 else 3.0
    defaults = L[i].split(",")
    i += 1
    floor_type = (defaults[6].strip().lower() if len(defaults) > 6 else "typical") or "typical"
    plan = Plan(
        name=name,
        floor_type=floor_type if floor_type in ("typical", "ground", "roof") else "typical",
        floor_height_above=fha or 3.0,
    )
    for _ in range(ns):
        a = L[i].split(",")
        i += 1
        bline = L[i].split(",")
        i += 1
        cnt = int(_num(a[1]))
        pts = []
        for _ in range(cnt):
            x, y = L[i].split(",")[:2]
            i += 1
            pts.append([round(_num(x), 4), round(_num(y), 4)])
        nobj = int(_num(bline[1])) if len(bline) > 1 else 0
        i += nobj
        s = Slab(mark=a[0], points=pts, grade=_grade(a[7]), room=bline[0])
        s.density = _num(a[4], 2.5) * T_TO_KN or 25.0
        s.thickness = _num(a[3], 0.125) or 0.125
        s.floor_finish = _num(a[5]) * T_TO_KN
        s.other = _num(a[6]) * T_TO_KN
        s.live = _num(a[9]) * T_TO_KN
        _direction(s, a[8], rep)
        if (a[12] if len(a) > 12 else "").strip().lower() in ("graded", "ground"):
            s.distribution = "on_grade"
        plan.slabs.append(s)
    for _ in range(nc):
        a = L[i].split(",")
        i += 1
        L[i].split(",")
        i += 1
        plan.columns.append(
            Column(
                mark=a[0],
                angle=_num(a[1]),
                d=_num(a[2], 0.45) or 0.45,
                b=_num(a[3], 0.23) or 0.23,
                x=round(_num(a[4]), 4),
                y=round(_num(a[5]), 4),
            )
        )
    for _ in range(nb):
        a = L[i].split(",")
        i += 1
        bline = L[i].split(",")
        i += 1
        p = [_num(v) for v in L[i].split(",")[:8]]
        i += 1
        nload = int(_num(a[12])) if len(a) > 12 else 0
        nobj = int(_num(a[16])) if len(a) > 16 else 0
        ndw = int(_num(bline[6])) if len(bline) > 6 else 0
        p1 = ((p[0] + p[6]) / 2, (p[1] + p[7]) / 2)
        p2 = ((p[2] + p[4]) / 2, (p[3] + p[5]) / 2)
        b = Beam(
            mark=a[0],
            b=_num(a[1], 0.23),
            d=_num(a[2], 0.45),
            grade=_grade(a[3]),
            x1=round(p1[0], 4),
            y1=round(p1[1], 4),
            x2=round(p2[0], 4),
            y2=round(p2[1], 4),
            cantilever=a[5].lower().startswith("cant"),
            external=(bline[0].strip().lower().startswith("e")),
        )
        info = (a[14] if len(a) > 14 else "").split(":")
        if len(info) >= 8 and info[2]:
            b.wall_thk = _num(info[2], 0.23)
            b.wall_density = _num(info[3], 2.0) * T_TO_KN
            b.plaster_thk = _num(info[4], 0.03)
            b.include_self, b.include_wall, b.include_plaster = (info[5] == "1", info[6] == "1", info[7] == "1")
            wall_ht = _num(bline[1]) if len(bline) > 1 else 0.0
            if wall_ht > 0:
                b.wall_height = wall_ht
        else:
            _legacy_udl(b, _num(a[4]))
        for _ in range(nload):
            t = L[i].split(",")
            i += 1
            _beam_load(b, t[0], t[1], _num(t[2]), _num(t[3]), _num(t[4]), _num(t[5]))
        i += ndw + nobj
        plan.beams.append(b)
    rep.notes.append("PlanWin 5.x import is in beta – verify wall heights and loads")
    return plan


def plan_bbox_ok(plan: Plan) -> bool:
    x0, y0, x1, y1 = G.bbox(plan.all_points())
    return (x1 - x0) < 2000 and (y1 - y0) < 2000
