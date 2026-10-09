"""ETABS text model (.e2k) writer.

ETABS uses Z-up with plan X/Y, so coordinates map directly.  Points are
plan locations shared by all stories; lines are assigned per story.
Column sections: D (local-2 direction) = column depth ``d``; the local-2
axis is rotated by ``angle + 90°`` so ``d`` lies perpendicular to ``b``.

Lateral loads are exported as the user point loads PlanWin AI Pro computed
(load pattern type "Other" for wind/quake) so the model imports without
needing ETABS auto-lateral parameters; switch to ETABS auto loads if
preferred.  Please verify the import in your ETABS version.
"""

from __future__ import annotations

import datetime as _dt
import math

from .. import APP_NAME, __version__
from ..core.frame import FrameModel, is_combinations
from ..core.model import grade_fck
from ..core.solver import MLoad, MPoint, MTorque

_PAT = {
    "DL": ("Dead", "Dead"),
    "LL": ("Live", "Live"),
    "WLX": ("WLX", "Wind"),
    "WLY": ("WLY", "Wind"),
    "EQX": ("EQX", "Seismic"),
    "EQY": ("EQY", "Seismic"),
    "ETX": ("ETX", "Seismic"),
    "ETY": ("ETY", "Seismic"),
}


def _f(v: float) -> str:
    s = f"{v:.5f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def write_etabs(fm: FrameModel, path: str, watermark: str = "") -> str:
    p = fm.p
    title = p.name.replace('"', "'")[:40]  # a double quote would end the e2k string
    story_names = ["Base"] + [lv.name.replace('"', "'") for lv in p.levels]
    # ETABS story names must be unique
    seen = {}
    for i, nm in enumerate(story_names):
        if nm in seen:
            story_names[i] = f"{nm}_{i}"
        seen[story_names[i]] = i
    out = [
        f"$ File {path} saved {_dt.datetime.now():%m/%d/%Y %H:%M:%S} by {APP_NAME} {__version__} {watermark}".rstrip(),
        "",
        "$ PROGRAM INFORMATION",
        '  PROGRAM  "ETABS"  VERSION "18.0.0"',
        "",
        "$ CONTROLS",
        '  UNITS  "KN"  "M"  "C"',
        f'  TITLE2  "{title}"',
        "",
        "$ STORIES - IN SEQUENCE FROM TOP",
    ]
    for i in range(len(p.levels), 0, -1):
        out.append(f'  STORY "{story_names[i]}"  HEIGHT {_f(p.levels[i - 1].height)}')
    out.append(f'  STORY "{story_names[0]}"  ELEV 0')
    out += ["", "$ MATERIAL PROPERTIES"]
    grades = sorted({m.grade for m in fm.members.values()}, key=grade_fck)
    for g in grades:
        fck = grade_fck(g)
        out.append(f'  MATERIAL  "{g}"  TYPE "Concrete"  WEIGHTPERVOLUME 25')
        out.append(f'  MATERIAL  "{g}"  SYMTYPE "Isotropic"  E {_f(5000 * math.sqrt(fck) * 1000)}  U 0.2  A 0.0000055')
        out.append(f'  MATERIAL  "{g}"  FC {_f(fck * 1000)}')
    fy = p.design.fy_main
    out.append(f'  MATERIAL  "Fe{int(fy)}"  TYPE "Rebar"  WEIGHTPERVOLUME 76.9729')
    out.append(f'  MATERIAL  "Fe{int(fy)}"  SYMTYPE "Uniaxial"  E 200000000  A 0.0000117')
    out.append(f'  MATERIAL  "Fe{int(fy)}"  FY {_f(fy * 1000)}  FU {_f(fy * 1100)}')
    out += ["", "$ FRAME SECTIONS"]
    secs = {}
    for m in fm.members.values():
        key = (m.kind, round(m.b, 3), round(m.d, 3), m.grade)
        if key not in secs:
            pre = {"column": "C", "wall": "W", "link": "RIGID"}.get(m.kind, "B")
            secs[key] = f"{pre}{int(round(m.b * 1000))}X{int(round(m.d * 1000))}{m.grade}"
            out.append(
                f'  FRAMESECTION  "{secs[key]}"  MATERIAL "{m.grade}"  SHAPE "Concrete Rectangular"  '
                f"D {_f(m.d)}  B {_f(m.b)}"
            )
            # cracked-section property modifiers (IS 1893-1:2016 cl 6.4.3.1) and reduced torsion
            out.append(
                f'  FRAMESECTION  "{secs[key]}"  JMOD {_f(m.torsion_factor)}  I2MOD {_f(m.i_factor)}  '
                f"I3MOD {_f(m.i_factor)}"
            )
    out += ["", "$ CONCRETE SECTIONS"]
    for (kind, _b, _d, _g), name in secs.items():
        if kind == "link":
            continue  # rigid links of the wide-column wall model are not designed
        if kind in ("column", "wall"):
            out.append(
                f'  CONCRETESECTION  "{name}"  LONGBARMATERIAL "Fe{int(fy)}"  CONFINEBARMATERIAL "Fe{int(fy)}"  '
                f'TYPE "COLUMN"  PATTERN "RECTANGULAR"  CONFINEMENT "TIES"  COVER {_f(p.design.column_cover)}'
            )
        else:
            out.append(
                f'  CONCRETESECTION  "{name}"  LONGBARMATERIAL "Fe{int(fy)}"  CONFINEBARMATERIAL "Fe{int(fy)}"  '
                f'TYPE "BEAM"  COVERTOP {_f(p.design.beam_cover + 0.02)}  COVERBOTTOM {_f(p.design.beam_cover + 0.02)}'
            )
    # points (unique XY)
    pts: dict[tuple[float, float], str] = {}

    def pid(x, y):
        k = (round(x, 4), round(y, 4))
        if k not in pts:
            pts[k] = str(len(pts) + 1)
        return pts[k]

    nodal, diaphragms = fm.export_model()
    used = {n for m in fm.members.values() for n in (m.n1, m.n2)} | {n.id for n in fm.nodes.values() if n.support}
    node_pt = {nid: pid(n.x, n.y) for nid, n in fm.nodes.items() if nid in used}
    out += ["", "$ POINT COORDINATES"]
    for (x, y), name in pts.items():
        out.append(f'  POINT "{name}"  {_f(x)} {_f(y)}')
    out += ["", "$ LINE CONNECTIVITIES"]
    names = {}
    nb = nc = 0
    for mid in sorted(fm.members):
        m = fm.members[mid]
        a, b = fm.nodes[m.n1], fm.nodes[m.n2]
        if m.kind in ("column", "wall"):
            nc += 1
            names[mid] = f"C{nc}"
            span = max(1, b.level - a.level)
            out.append(f'  LINE  "{names[mid]}"  COLUMN  "{node_pt[m.n2]}"  "{node_pt[m.n1]}"  {span}')
        else:
            nb += 1
            names[mid] = f"B{nb}"
            out.append(f'  LINE  "{names[mid]}"  BEAM  "{node_pt[m.n1]}"  "{node_pt[m.n2]}"  0')
    out += ["", "$ POINT ASSIGNS"]
    for nid, n in fm.nodes.items():
        if n.support:
            r = "UX UY UZ RX RY RZ" if n.support == "fixed" else "UX UY UZ"
            out.append(f'  POINTASSIGN  "{node_pt[nid]}"  "{story_names[n.level]}"  RESTRAINT "{r}"')
    if diaphragms:
        out += ["", "$ DIAPHRAGM NAMES", '  DIAPHRAGM "D1"  TYPE RIGID', "", "$ DIAPHRAGM ASSIGNS"]
        for lvl, (master, slaves) in sorted(diaphragms.items()):
            for n in sorted({node_pt[k] for k in [master, *slaves] if k in node_pt}, key=int):
                out.append(f'  POINTASSIGN  "{n}"  "{story_names[lvl]}"  DIAPH "D1"')
    out += ["", "$ LINE ASSIGNS"]
    for mid in sorted(fm.members):
        m = fm.members[mid]
        top = fm.nodes[m.n2].level
        sec = secs[(m.kind, round(m.b, 3), round(m.d, 3), m.grade)]
        extra = f"  ANG {_f((m.angle + 90.0) % 360)}" if m.kind in ("column", "wall") else ""
        out.append(f'  LINEASSIGN  "{names[mid]}"  "{story_names[top]}"  SECTION "{sec}"{extra}')
    out += ["", "$ LOAD PATTERNS"]
    cases = fm.cases()
    for c in cases:
        nm, typ = _PAT[c]
        etyp = typ if typ in ("Dead", "Live") else "Other"
        out.append(f'  LOADPATTERN "{nm}"  TYPE  "{etyp}"  SELFWEIGHT  0')
    out += ["", "$ POINT OBJECT LOADS"]
    for c in cases:
        nm = _PAT[c][0]
        for nid, vec in nodal.get(c, {}).items():
            if nid not in node_pt:
                continue
            comps = " ".join(
                f"{k} {_f(v)}"
                for k, v in (
                    ("FX", vec[0]),
                    ("FY", vec[1]),
                    ("FZ", vec[2]),
                    ("MX", vec[3]),
                    ("MY", vec[4]),
                    ("MZ", vec[5]),
                )
                if abs(v) > 1e-9
            )
            if comps:
                out.append(
                    f'  POINTLOAD  "{node_pt[nid]}"  "{story_names[fm.nodes[nid].level]}"  '
                    f'TYPE "FORCE"  LC "{nm}"  {comps}'
                )
    out += ["", "$ FRAME OBJECT LOADS"]
    for c in cases:
        nm = _PAT[c][0]
        for mid in sorted(fm.members):
            m = fm.members[mid]
            top = story_names[fm.nodes[m.n2].level]
            a, b = fm.nodes[m.n1], fm.nodes[m.n2]
            Lm = math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))
            for ld in m.loads.get(c, []):
                if isinstance(ld, MLoad):
                    w1, w2 = -ld.w1[2], -ld.w2[2]
                    if abs(w1) < 1e-9 and abs(w2) < 1e-9:
                        continue
                    out.append(
                        f'  LINELOAD  "{names[mid]}"  "{top}"  TYPE "TRAPF"  DIR "GRAV"  LC "{nm}"  '
                        f"FSTART {_f(w1)}  FEND {_f(w2)}  RDSTART {_f(ld.a / Lm)}  RDEND {_f(ld.b / Lm)}"
                    )
                elif isinstance(ld, MPoint) and abs(ld.P[2]) > 1e-9:
                    out.append(
                        f'  LINELOAD  "{names[mid]}"  "{top}"  TYPE "POINTF"  DIR "GRAV"  LC "{nm}"  '
                        f"FVAL {_f(-ld.P[2])}  RDIST {_f(ld.x / Lm)}"
                    )
    n_torque = sum(isinstance(ld, MTorque) for m in fm.members.values() for lds in m.loads.values() for ld in lds)
    if n_torque:
        out.append(f"$ NOTE: {n_torque} distributed torsion loads (cantilever slabs on edge beams) are not exported –")
        out.append("$ model the cantilever slabs as shell/membrane areas in ETABS or add frame moments manually.")
    if fm.rs:
        out.append("$ NOTE: seismic method was response spectrum – EQX/EQY carry the equivalent storey forces of the")
        out.append(
            "$ scaled spectral storey shears (IS 1893-1 cl 7.7.3); define a response spectrum case to re-run RSA."
        )
    out += ["", "$ LOAD CASES"]
    for c in cases:
        nm = _PAT[c][0]
        out.append(f'  LOADCASE "{nm}"  TYPE  "Linear Static"  INITCOND  "PRESET"')
        out.append(f'  LOADCASE "{nm}"  LOADPAT  "{nm}"  SF  1')
    out += ["", "$ LOAD COMBINATIONS"]
    for j, cb in enumerate(
        [c for c in is_combinations(p.seismic.enabled, p.wind.enabled, "ETX" in cases) if c.kind == "ultimate"], 1
    ):
        cname = f"PW{j:02d}"
        out.append(f'  COMBO "{cname}"  TYPE "Linear Add"')
        for k, v in cb.factors.items():
            if k in cases:
                out.append(f'  COMBO "{cname}"  LOADCASE "{_PAT[k][0]}"  SF {_f(v)}')
    out += ["", "  END", "$ END OF MODEL FILE", ""]
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(out))
    return path
