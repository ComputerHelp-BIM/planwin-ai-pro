"""DXF export (2-D plan, 3-D frame) and import (slabs / columns / beams).

Export layers follow legacy PlanWin so existing CAD standards keep working:
SLAB, SLAB_TEXT, SLAB_LOAD, BEAM, BEAM_TEXT, BEAM_LOAD, COLUMN, COLUMN_TEXT,
COLUMN_LOAD, plus WALL and WALL_TEXT for shear walls.  Import reads closed
polylines on layer SLAB, circles or closed polylines on layer COLUMN and lines
on layer BEAM (all optional).
"""

from __future__ import annotations

import math

import ezdxf
from ezdxf.enums import TextEntityAlignment

from ..core import geometry as G
from ..core.frame import FrameModel
from ..core.model import Beam, Column, Plan, Slab
from ..core.plan_engine import PlanResult

LAYERS = {
    "SLAB": 8,
    "SLAB_TEXT": 3,
    "SLAB_LOAD": 3,
    "BEAM": 1,
    "BEAM_TEXT": 2,
    "BEAM_LOAD": 6,
    "COLUMN": 4,
    "COLUMN_TEXT": 4,
    "COLUMN_LOAD": 5,
    "WALL": 6,
    "WALL_TEXT": 6,
    "GRID": 9,
    "TITLE": 7,
    "FRAME_BEAM": 1,
    "FRAME_COLUMN": 4,
}


def _doc():
    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.M
    for name, color in LAYERS.items():
        if name not in doc.layers:
            doc.layers.add(name, color=color)
    return doc


def export_plan_dxf(
    plan: Plan, path: str, result: PlanResult | None = None, text_h: float = 0.15, title: str = "", watermark: str = ""
) -> str:
    doc = _doc()
    msp = doc.modelspace()
    for s in plan.slabs:
        msp.add_lwpolyline(s.pts, close=True, dxfattribs={"layer": "SLAB"})
        cx, cy = G.polygon_centroid(s.pts)
        msp.add_text(s.mark, height=text_h * 1.4, dxfattribs={"layer": "SLAB_TEXT"}).set_placement(
            (cx, cy + text_h), align=TextEntityAlignment.MIDDLE_CENTER
        )
        msp.add_text(
            f"t={s.thickness * 1000:.0f} D={s.dead:.2f} L={s.live_load:.2f} kN/m2",
            height=text_h * 0.8,
            dxfattribs={"layer": "SLAB_LOAD"},
        ).set_placement((cx, cy - text_h), align=TextEntityAlignment.MIDDLE_CENTER)
    for b in plan.beams:
        msp.add_line(b.p1, b.p2, dxfattribs={"layer": "BEAM"})
        # beam outline (width)
        L = b.length
        if L > 0:
            nx, ny = -(b.y2 - b.y1) / L * b.b / 2, (b.x2 - b.x1) / L * b.b / 2
            msp.add_lwpolyline(
                [(b.x1 + nx, b.y1 + ny), (b.x2 + nx, b.y2 + ny), (b.x2 - nx, b.y2 - ny), (b.x1 - nx, b.y1 - ny)],
                close=True,
                dxfattribs={"layer": "BEAM", "color": 8},
            )
        ang = math.degrees(math.atan2(b.y2 - b.y1, b.x2 - b.x1))
        if ang > 90 or ang < -90:
            ang += 180
        mx, my = (b.x1 + b.x2) / 2, (b.y1 + b.y2) / 2
        label = f"{b.mark} ({b.b * 1000:.0f}x{b.d * 1000:.0f})"
        t = msp.add_text(label, height=text_h, rotation=ang, dxfattribs={"layer": "BEAM_TEXT"})
        t.set_placement((mx, my), align=TextEntityAlignment.BOTTOM_CENTER)
        if result and b.id in result.beams:
            br = result.beams[b.id]
            t2 = msp.add_text(
                f"UDL {br.equivalent_udl():.2f} kN/m",
                height=text_h * 0.8,
                rotation=ang,
                dxfattribs={"layer": "BEAM_LOAD"},
            )
            t2.set_placement((mx, my), align=TextEntityAlignment.TOP_CENTER)
    for c in plan.columns:
        msp.add_lwpolyline(c.corners(), close=True, dxfattribs={"layer": "COLUMN"})
        hatch = msp.add_hatch(color=4, dxfattribs={"layer": "COLUMN"})
        hatch.paths.add_polyline_path(c.corners(), is_closed=True)
        msp.add_text(c.mark, height=text_h, dxfattribs={"layer": "COLUMN_TEXT"}).set_placement(
            (c.x + max(c.b, c.d) / 2 + 0.05, c.y + 0.05), align=TextEntityAlignment.BOTTOM_LEFT
        )
        if result:
            cl = next((v for v in result.columns.values() if v.column_id == c.id), None)
            if cl:
                msp.add_text(
                    f"D {cl.dead:.1f} L {cl.live:.1f} kN", height=text_h * 0.8, dxfattribs={"layer": "COLUMN_LOAD"}
                ).set_placement((c.x + max(c.b, c.d) / 2 + 0.05, c.y - 0.05), align=TextEntityAlignment.TOP_LEFT)
    for w in plan.walls:  # shear walls: footprint and mark (not read back by import_dxf)
        msp.add_lwpolyline(w.corners(), close=True, dxfattribs={"layer": "WALL"})
        cx, cy = w.centre
        ang = w.angle % 180
        if ang > 90:
            ang -= 180
        msp.add_text(w.mark, height=text_h, rotation=ang, dxfattribs={"layer": "WALL_TEXT"}).set_placement(
            (cx, cy), align=TextEntityAlignment.MIDDLE_CENTER
        )
    x0, y0, x1, y1 = plan.extents()
    tt = f"{title or plan.name}" + (f"  [{watermark}]" if watermark else "")
    msp.add_text(tt, height=text_h * 2.5, dxfattribs={"layer": "TITLE"}).set_placement(
        (x0, y0 - 1.0), align=TextEntityAlignment.TOP_LEFT
    )
    doc.saveas(path)
    return path


def export_frame_dxf(fm: FrameModel, path: str) -> str:
    doc = _doc()
    msp = doc.modelspace()
    for m in fm.members.values():
        a, b = fm.nodes[m.n1], fm.nodes[m.n2]
        msp.add_line(
            (a.x, a.y, a.z),
            (b.x, b.y, b.z),
            dxfattribs={"layer": "FRAME_COLUMN" if m.kind in ("column", "wall") else "FRAME_BEAM"},
        )
    doc.saveas(path)
    return path


def import_dxf(path: str, unit: str = "m", plan_name: str = "Imported") -> tuple[Plan, list[str]]:
    """Read SLAB / COLUMN / BEAM layers into a new plan.

    ``unit`` – "m" or "mm" (drawing units).  Returns (plan, notes).
    """
    scale = 0.001 if unit == "mm" else 1.0
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    plan = Plan(name=plan_name)
    notes: list[str] = []

    def layer(e):
        return e.dxf.layer.upper()

    for e in msp:
        t = e.dxftype()
        lay = layer(e)
        if lay.startswith("SLAB") and t in ("LWPOLYLINE", "POLYLINE"):
            pts = [
                (p[0] * scale, p[1] * scale)
                for p in (
                    e.get_points("xy")
                    if t == "LWPOLYLINE"
                    else [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                )
            ]
            if len(pts) > 2 and G.same_point(pts[0], pts[-1]):
                pts = pts[:-1]
            closed = e.closed if t == "LWPOLYLINE" else e.is_closed
            if len(pts) >= 3 and (closed or len(pts) >= 3):
                pts = G.ensure_ccw(pts)
                plan.slabs.append(Slab(mark=plan.next_mark("S"), points=[[round(x, 4), round(y, 4)] for x, y in pts]))
        elif lay.startswith("COLUMN"):
            if t == "CIRCLE":
                c = e.dxf.center
                plan.columns.append(Column(mark=plan.next_mark("C"), x=round(c.x * scale, 4), y=round(c.y * scale, 4)))
            elif t in ("LWPOLYLINE", "POLYLINE") and lay == "COLUMN":
                pts = [
                    (p[0] * scale, p[1] * scale)
                    for p in (
                        e.get_points("xy")
                        if t == "LWPOLYLINE"
                        else [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                    )
                ]
                if len(pts) >= 4:
                    pts = pts[:4]
                    cx = sum(p[0] for p in pts) / 4
                    cy = sum(p[1] for p in pts) / 4
                    e1, e2 = G.dist(pts[0], pts[1]), G.dist(pts[1], pts[2])
                    ang = math.degrees(math.atan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0]))
                    plan.columns.append(
                        Column(
                            mark=plan.next_mark("C"),
                            x=round(cx, 4),
                            y=round(cy, 4),
                            b=round(e1, 3),
                            d=round(e2, 3),
                            angle=round(ang % 180, 2),
                        )
                    )
            elif t == "INSERT":
                ins = e.dxf.insert
                plan.columns.append(
                    Column(mark=plan.next_mark("C"), x=round(ins.x * scale, 4), y=round(ins.y * scale, 4))
                )
        elif lay.startswith("BEAM") and t == "LINE":
            a, b = e.dxf.start, e.dxf.end
            plan.beams.append(
                Beam(
                    mark=plan.next_mark("B"),
                    x1=round(a.x * scale, 4),
                    y1=round(a.y * scale, 4),
                    x2=round(b.x * scale, 4),
                    y2=round(b.y * scale, 4),
                )
            )
    if not plan.slabs:
        notes.append("No closed polylines found on layer SLAB")
    if not plan.columns:
        notes.append("No circles/rectangles found on layer COLUMN")
    plan.renumber("slab")
    return plan, notes
