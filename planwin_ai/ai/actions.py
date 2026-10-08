"""Action schema and executor shared by the offline parser and LLM providers.

Every assistant turn is converted into a list of JSON actions:
``{"action": "<name>", ...params}``.  Actions are validated and executed
against a :class:`Session` so the GUI and the CLI behave identically.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from ..core.generator import OCCUPANCY, SPEC_IS_INPUT, GridSpec, auto_slab_thickness, grid_building
from ..core.model import Project
from ..io.cities import lookup_city
from ..services import exports
from ..services.exports import safe_filename as safe_filename  # re-export (moved in 1.1.0)
from .templates import TEMPLATES, build_template

ACTION_SCHEMA: dict[str, dict[str, str]] = {
    "new_building": {
        "bays_x": "list of bay widths in X (m), e.g. [5,5,4]",
        "bays_y": "list of bay widths in Y (m)",
        "upper_floors": "int N for G+N",
        "floor_height": "typical storey height (m)",
        "ground_height": "ground storey height (m)",
        "foundation_depth": "footing base to plinth (m)",
        "occupancy": "|".join(OCCUPANCY),
        "city": "Indian city name (sets wind speed and seismic zone)",
        "balcony": '{"side": "south|north", "depth": m} or null',
        "mumty": "true/false (stair cabin on roof)",
        "column": "[b, d] m",
        "beam_int": "[b, d] m",
        "beam_ext": "[b, d] m",
        "grade": "concrete grade e.g. M25",
        "name": "project name",
    },
    "load_template": {"key": "|".join(t.key for t in TEMPLATES)},
    "modify_building": {"...": "any new_building field to change; regenerates the parametric model"},
    "set_loads": {
        "plan": "plan name or 'all'",
        "live": "kN/m2",
        "floor_finish": "kN/m2",
        "other": "kN/m2",
        "thickness": "slab thickness m",
    },
    "set_location": {"city": "Indian city"},
    "set_seismic": {
        "zone": "II|III|IV|V",
        "soil": "hard|medium|soft",
        "importance": "1.0|1.2|1.5",
        "response_reduction": "3|5",
        "enabled": "true/false",
        "method": "auto|static|response_spectrum (auto = RSA where IS 1893-1 cl 7.7.1 requires it)",
        "rigid_diaphragm": "true/false (floors act as rigid diaphragms, cl 7.6.4)",
    },
    "set_wind": {"basic_speed": "m/s", "terrain": "1-4", "enabled": "true/false"},
    "set_materials": {"concrete": "M20..M50", "steel": "415|500|550"},
    "set_sbc": {"sbc": "safe bearing capacity kN/m2"},
    "autosize_columns": {"steel_pct": "assumed steel % (0.8-2.0)", "same_size": "true/false"},
    "optimize_sizes": {"max_iter": "iterations (default 5)", "target_col_pct": "max column steel % (default 3)"},
    "add_wall": {
        "plan": "plan name or 'all' (default all)",
        "mark": "wall mark, e.g. W1 (default next free)",
        "x1": "m",
        "y1": "m",
        "x2": "m",
        "y2": "m",
        "thickness": "m (default 0.23, min 0.15)",
        "grade": "concrete grade (default: the plan's level grade)",
    },
    "add_staircase": {
        "name": "e.g. ST1 (same name replaces the earlier definition)",
        "plan": "plan whose beams carry the flight (default Typical)",
        "support_beams": 'list of beam marks, e.g. ["B3", "B4"]',
        "start": "m along each support beam where the flight starts",
        "width": "flight width m (default 1.2)",
        "going": "horizontal going m (default 3.0)",
        "landing": "landing width m (default 1.2)",
        "riser": "m (default 0.15)",
        "tread": "m (default 0.30)",
        "waist": "waist slab thickness m (default 0.20)",
        "live": "kN/m2 (3 residential, 5 public)",
        "finish": "kN/m2 (default 1.0)",
    },
    "add_water_tank": {
        "name": "e.g. T1 (same name replaces the earlier tank)",
        "capacity_l": "litres",
        "water_depth": "m (default 1.5)",
        "level": "level index (1-based) or level name, e.g. Roof (default top level)",
        "columns": "list of supporting column marks",
    },
    "add_grids": {"grids": "'auto' (from the column positions) or list of {name, axis: x|y, pos}"},
    "save_revision": {"label": "revision label, e.g. R1 (runs design if needed)"},
    "compare_revisions": {"a": "older revision label (default second last)", "b": "newer label (default last)"},
    "boq": {"by": "floor|type (default floor) – runs design if needed"},
    "set_units": {"system": "SI (kN) | MKS (tonnes) – display units only"},
    "analyze": {},
    "design": {},
    "export": {"format": "|".join(exports.keys()), "path": "optional file path", "plan": "plan name (dxf)"},
    "answer": {"text": "reply only, no model change"},
}


@dataclass
class Session:
    project: Project
    out_dir: str = "."
    last: dict[str, Any] = field(default_factory=dict)  # fm, fa, rep, plan_results
    watermark: str = ""
    exports_allowed: bool = True
    on_change: Callable[[str], None] | None = None


@dataclass
class ActionResult:
    messages: list[str] = field(default_factory=list)
    changed: bool = False
    analysed: bool = False
    files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _to_bool(v: Any) -> bool:
    """Interpret JSON/LLM booleans: ``"false"``, ``"no"``, ``"0"`` and ``0`` are False."""
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "y", "1", "on")
    return bool(v)


def _spec_from(project: Project) -> GridSpec:
    d = dict(project.meta.get("grid_spec") or {})
    valid = set(asdict(GridSpec()))
    d = {k: v for k, v in d.items() if k in valid}  # tolerate specs written by other versions
    for k in ("column", "beam_int", "beam_ext"):
        if k in d and isinstance(d[k], list):
            d[k] = tuple(d[k])
    spec = GridSpec(**d) if d else GridSpec()
    if not project.meta.get(SPEC_IS_INPUT):
        # files from 1.0.0 stored the auto-sized spec; an automatic slab thickness must be
        # recomputed when the bays change, so turn it back into "automatic"
        if spec.slab_thickness and abs(spec.slab_thickness - auto_slab_thickness(spec.bays_x, spec.bays_y)) < 1e-9:
            spec.slab_thickness = 0.0
    return spec


def _clean_spec(params: dict, base: GridSpec | None = None) -> GridSpec:
    spec = base or GridSpec()
    valid = set(asdict(spec))
    for k, v in params.items():
        if k not in valid or v is None:
            continue
        if k in ("bays_x", "bays_y"):
            v = [float(x) for x in (v if isinstance(v, (list, tuple)) else [v])]
            if not v or any(x <= 0.5 or x > 30 for x in v):
                raise ValueError(f"{k} must be bay widths between 0.5 and 30 m")
        elif k in ("column", "beam_int", "beam_ext"):
            v = tuple(float(x) for x in v)[:2]
        elif k == "upper_floors":
            v = int(v)
            if not 0 <= v <= 60:
                raise ValueError("upper_floors must be 0..60")
        elif k in ("floor_height", "ground_height", "foundation_depth", "slab_thickness", "parapet"):
            v = float(v)
        elif k == "occupancy" and v not in OCCUPANCY:
            v = "residential"
        elif k == "mumty":
            v = _to_bool(v)
        setattr(spec, k, v)
    return spec


def execute(session: Session, actions: list[dict]) -> ActionResult:
    res = ActionResult()
    for act in actions:
        if not isinstance(act, dict):
            continue
        name = str(act.get("action", "")).strip()
        params = {k: v for k, v in act.items() if k != "action"}
        try:
            fn = _HANDLERS.get(name)
            if fn is None:
                res.errors.append(f"Unknown action '{name}'")
                continue
            fn(session, params, res)
        except Exception as exc:  # report and continue with next action
            res.errors.append(f"{name}: {exc}")
    return res


# ------------------------------------------------------------------ handlers
def _new_building(s: Session, p: dict, r: ActionResult):
    spec = _clean_spec(p)
    if not p.get("name"):
        spec.name = f"{spec.occupancy.replace('_', ' ').title()} G+{spec.upper_floors}" + (
            f" – {spec.city}" if p.get("city") else ""
        )
    s.project = grid_building(spec)
    s.last.clear()
    r.changed = True
    nfl = spec.upper_floors
    r.messages.append(
        f"Created {s.project.name}: G+{nfl}, {len(spec.bays_x)}×{len(spec.bays_y)} bays "
        f"({sum(spec.bays_x):.1f} × {sum(spec.bays_y):.1f} m), {spec.occupancy}, "
        f"{s.project.wind.city} (zone {s.project.seismic.zone}, Vb {s.project.wind.basic_speed} m/s)."
    )


def _modify(s: Session, p: dict, r: ActionResult):
    if not s.project.meta.get("grid_spec"):
        raise ValueError("this model was not created parametrically – edit it on the canvas instead")
    keep = s.project
    spec = _clean_spec(p, _spec_from(keep))
    s.project = grid_building(spec)
    s.project.seismic, s.project.wind, s.project.design = keep.seismic, keep.wind, keep.design
    if "occupancy" in p:  # importance factor follows the occupancy (IS 1893-1 Table 8)
        s.project.seismic.importance = OCCUPANCY[spec.occupancy]["importance"]
    if "city" in p:
        _set_location(s, {"city": p["city"]}, r)
    s.last.clear()
    r.changed = True
    r.messages.append("Model regenerated with: " + ", ".join(f"{k}={v}" for k, v in p.items()))


def _template(s: Session, p: dict, r: ActionResult):
    s.project = build_template(str(p.get("key", "")))
    s.last.clear()
    r.changed = True
    r.messages.append(f"Loaded template '{s.project.name}'.")


def _set_loads(s: Session, p: dict, r: ActionResult):
    target = str(p.get("plan", "all"))
    n = 0
    for plan in s.project.plans:
        if target != "all" and plan.name.lower() != target.lower():
            continue
        for sl in plan.slabs:
            if sl.distribution == "on_grade":
                continue
            if "live" in p:
                sl.live = float(p["live"])
            if "floor_finish" in p:
                sl.floor_finish = float(p["floor_finish"])
            if "other" in p:
                sl.other = float(p["other"])
            if "thickness" in p:
                sl.thickness = float(p["thickness"])
            n += 1
    if not n:
        raise ValueError(f"no slabs found for plan '{target}'")
    s.last.clear()
    r.changed = True
    r.messages.append(f"Updated loads on {n} slabs.")


def _set_location(s: Session, p: dict, r: ActionResult):
    info = lookup_city(str(p.get("city", "")))
    if not info:
        raise ValueError(f"city '{p.get('city')}' not in the database – set wind speed and zone manually")
    pr = s.project
    pr.location = info["city"]
    pr.wind.city = info["city"]
    if info["vb"]:
        pr.wind.basic_speed = info["vb"]
    pr.seismic.zone = info["zone"]
    s.last.clear()
    r.changed = True
    r.messages.append(
        f"Location {info['city']}: seismic zone {info['zone']}, Vb {pr.wind.basic_speed} m/s ({info['source']})."
    )


def _set_seismic(s: Session, p: dict, r: ActionResult):
    sm = s.project.seismic
    if "zone" in p:
        z = str(p["zone"]).upper().replace("ZONE", "").strip()
        z = {"2": "II", "3": "III", "4": "IV", "5": "V"}.get(z, z)
        if z not in ("II", "III", "IV", "V"):
            raise ValueError("zone must be II, III, IV or V")
        sm.zone = z
    if "soil" in p:
        soil = str(p["soil"]).lower().replace("soil", "").strip()
        soil = {"rock": "hard", "i": "hard", "ii": "medium", "iii": "soft"}.get(soil, soil)
        if soil not in ("hard", "medium", "soft"):
            raise ValueError("soil must be hard, medium or soft")
        sm.soil = soil
    for k in ("importance", "response_reduction"):
        if k in p:
            setattr(sm, k, float(p[k]))
    if "enabled" in p:
        sm.enabled = _to_bool(p["enabled"])
    if "method" in p:
        sm.method = _seismic_method(p["method"])
    if "rigid_diaphragm" in p:
        sm.rigid_diaphragm = _to_bool(p["rigid_diaphragm"])
    s.last.clear()
    r.changed = True
    r.messages.append(
        f"Seismic: zone {sm.zone}, {sm.soil} soil, I={sm.importance}, R={sm.response_reduction}, "
        f"method {_METHOD_LABEL.get(sm.method, sm.method)}, "
        + ("rigid diaphragm" if sm.rigid_diaphragm else "no rigid diaphragm")
        + ("" if sm.enabled else " (disabled)")
    )


_METHOD_LABEL = {
    "auto": "auto (IS 1893 cl 7.7.1)",
    "static": "equivalent static",
    "response_spectrum": "response spectrum",
}


def _seismic_method(v: Any) -> str:
    key = " ".join(str(v).strip().lower().replace("_", " ").replace("-", " ").split())
    if key in ("auto", "automatic", "default"):
        return "auto"
    if key in ("static", "equivalent static", "esm", "seismic coefficient", "linear static"):
        return "static"
    if key in ("response spectrum", "rsa", "rs", "dynamic", "modal", "response spectrum analysis", "rsm"):
        return "response_spectrum"
    raise ValueError(f"seismic method '{v}' – use auto, static or response_spectrum")


def _set_wind(s: Session, p: dict, r: ActionResult):
    w = s.project.wind
    if "basic_speed" in p:
        w.basic_speed = float(p["basic_speed"])
    if "terrain" in p:
        w.terrain = min(max(int(p["terrain"]), 1), 4)
    if "enabled" in p:
        w.enabled = _to_bool(p["enabled"])
    s.last.clear()
    r.changed = True
    r.messages.append(
        f"Wind: Vb {w.basic_speed} m/s, terrain category {w.terrain}" + ("" if w.enabled else " (disabled)")
    )


def _set_materials(s: Session, p: dict, r: ActionResult):
    pr = s.project
    if "concrete" in p:
        g = str(p["concrete"]).upper()
        g = g if g.startswith("M") else f"M{g}"
        for lv in pr.levels:
            lv.grade = g
        for plan in pr.plans:
            for o in plan.slabs + plan.beams + plan.columns:
                o.grade = g
    if "steel" in p:
        fy = float(str(p["steel"]).upper().replace("FE", ""))
        pr.design.fy_main = pr.design.fy_shear = fy
    s.last.clear()
    r.changed = True
    r.messages.append(
        f"Materials: concrete {pr.levels[0].grade if pr.levels else '-'}, steel Fe{int(pr.design.fy_main)}"
    )


def _set_sbc(s: Session, p: dict, r: ActionResult):
    s.project.design.sbc = float(p["sbc"])
    s.last.clear()
    r.changed = True
    r.messages.append(f"Safe bearing capacity set to {s.project.design.sbc:.0f} kN/m².")


def _autosize(s: Session, p: dict, r: ActionResult):
    from ..design.runner import autosize_columns

    out = autosize_columns(
        s.project, steel_pct=float(p.get("steel_pct", 1.0)), same_size=_to_bool(p.get("same_size", True))
    )
    s.last.clear()
    r.changed = True
    big = sorted(out.items(), key=lambda kv: -max(kv[1]))[:3]
    r.messages.append(
        "Columns auto-sized from axial load. Largest: " + ", ".join(f"{k} depth {max(v):.2f} m" for k, v in big)
    )


def _optimize(s: Session, p: dict, r: ActionResult):
    from ..design.runner import optimize_sizes

    log, rep = optimize_sizes(s.project, int(p.get("max_iter", 5)), float(p.get("target_col_pct", 3.0)))
    s.last.clear()
    r.changed = True
    r.messages.extend(log)


def _analyze(s: Session, p: dict, r: ActionResult):
    from .. import units
    from ..core.frame import FrameModel
    from ..core.plan_engine import PlanEngine

    pr = s.project
    s.last["plan_results"] = {
        pl.name: PlanEngine(pl, None, pr.design.two_way_ratio_limit, pr.design.continuity_in_load_transfer).run()
        for pl in pr.plans
    }
    fm = FrameModel(pr).build()
    errs = [i for i in fm.issues if i.level == "error"]
    if errs:
        s.last["fm"] = fm
        raise ValueError(f"{len(errs)} model errors, e.g. {errs[0].message}")
    fa = fm.analyze()
    s.last.update(fm=fm, fa=fa)
    r.analysed = True
    eq = fa.equilibrium()
    U = units.current
    msg = (
        f"Analysis done: {len(fm.nodes)} joints, {len(fm.members)} members. "
        f"Vertical reaction DL {U.fmt(eq['DL'], 'force', 0)}, LL {U.fmt(eq['LL'], 'force', 0)}."
    )
    for k, v in fm.seismic.items():
        msg += f" {k}: T={v.T:.2f}s Ah={v.Ah:.4f} VB={U.fmt(v.Vb, 'force', 0)}."
    r.messages.append(msg)


def _design(s: Session, p: dict, r: ActionResult):
    from ..design.runner import design_all

    if "fa" not in s.last:
        _analyze(s, {}, r)
    rep = design_all(s.last["fa"], s.project)
    s.last["rep"] = rep
    r.analysed = True
    b = rep.boq
    r.messages.append(
        f"Design done: {len(rep.columns)} column segments, {len(rep.beams)} beams, {len(rep.footings)} footings, "
        f"{len(rep.slabs)} slabs – {rep.failures} need attention. Concrete {b['total_concrete']:.1f} m³, "
        f"steel {b['total_steel'] / 1000:.1f} t ({b['steel_per_m3']:.0f} kg/m³), est. cost ₹{b['cost'] / 1e5:.1f} lakh."
    )
    for w in rep.warnings[:3]:
        r.messages.append("⚠ " + w)
    if rep.failures or any(not d["ok"] for d in rep.drifts):
        r.messages.append(
            "Tip: say 'optimise sizes' (or Design ▸ Optimise sizes) to enlarge failing members automatically."
        )


def _export(s: Session, p: dict, r: ActionResult):
    if not s.exports_allowed:
        raise ValueError("exports are disabled – the trial has expired. Please activate a licence")
    fmt = exports.get(p.get("format", ""))

    def ensure_frame():
        if "fm" not in s.last or "fa" not in s.last:
            _analyze(s, {}, r)
        return s.last["fm"], s.last["fa"]

    def ensure_design():
        if "rep" not in s.last:
            _design(s, {}, r)
        return s.last["rep"]

    params = {k: v for k, v in p.items() if k not in ("format", "path")}
    ctx = exports.ExportContext(
        s.project, s.out_dir, s.watermark, params, ensure_frame, ensure_design, lambda: s.last.get("plan_results", {})
    )
    out = exports.run(fmt, ctx, p.get("path"))
    r.files.append(out)
    r.messages.append(f"Exported {fmt.label} → {out}")


# ------------------------------------------------------------ v1.1 handlers
def _num(p: dict, key: str, default: float | None = None) -> float:
    v = p.get(key, default)
    if v is None:
        raise ValueError(f"'{key}' is required")
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError(f"'{key}' must be a number, got {v!r}") from None


def _marks(v: Any, what: str) -> list[str]:
    """``["B3", "b4"]`` or ``"B3 B4"`` / ``"B3,B4"`` → ``["B3", "B4"]``."""
    items = v if isinstance(v, (list, tuple)) else str(v or "").replace(",", " ").split()
    out = [str(x).strip().upper() for x in items if str(x).strip()]
    if not out:
        raise ValueError(f"list the {what}")
    return list(dict.fromkeys(out))


def _plans(s: Session, target: Any) -> list:
    pr = s.project
    if not pr.plans:
        raise ValueError("the project has no plans – create a building first")
    t = str(target or "all").strip()
    if t.lower() == "all":
        return list(pr.plans)
    hit = [pl for pl in pr.plans if pl.name.lower() == t.lower()]
    if not hit:
        raise ValueError(f"plan '{t}' not found – plans: {', '.join(pl.name for pl in pr.plans)}")
    return hit


def _plan_grade(s: Session, plan_name: str) -> str:
    return next((lv.grade for lv in s.project.levels if lv.plan == plan_name), "M25")


def _metres(v: float, limit: float) -> float:
    """Values above ``limit`` are taken as millimetres (``230`` → 0.23 m)."""
    return v / 1000.0 if v > limit else v


def _add_wall(s: Session, p: dict, r: ActionResult):
    from ..core.model import Wall

    plans = _plans(s, p.get("plan", "all"))
    x1, y1, x2, y2 = (_num(p, k) for k in ("x1", "y1", "x2", "y2"))
    t = _metres(_num(p, "thickness", 0.23), 2.0)
    length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    if length < 1e-3:
        raise ValueError("the wall has zero length – give two different end points")
    if t < 0.15:
        raise ValueError(f"wall thickness {t * 1000:.0f} mm is below the 150 mm minimum (IS 13920 cl 9.1.1)")
    mark = str(p.get("mark") or plans[0].next_mark("W")).strip().upper()
    for pl in plans:
        pl.walls = [w for w in pl.walls if w.mark != mark]  # the same mark replaces (walls stack by mark)
        g = str(p.get("grade") or _plan_grade(s, pl.name)).upper()
        g = g if g.startswith("M") else f"M{g}"
        pl.walls.append(Wall(mark=mark, x1=x1, y1=y1, x2=x2, y2=y2, thickness=round(t, 4), grade=g))
    s.last.clear()
    r.changed = True
    r.messages.append(
        f"Shear wall {mark} ({x1:g}, {y1:g}) → ({x2:g}, {y2:g}), {length:.2f} m long, {t * 1000:.0f} mm thick "
        f"on {', '.join(pl.name for pl in plans)}."
    )


def _add_staircase(s: Session, p: dict, r: ActionResult):
    from .. import units
    from ..design.wizards import Staircase, apply_staircase

    pr = s.project
    if p.get("plan") not in (None, ""):
        plan = _plans(s, p["plan"])[0]
    else:
        plan = pr.plan("Typical") or next((pl for pl in pr.plans if pl.beams), None)
        if plan is None:
            raise ValueError("no plan with beams to carry the staircase")
    st = Staircase(
        name=str(p.get("name") or "ST1").strip().upper(),
        plan=plan.name,
        support_beams=_marks(p.get("support_beams"), "support beams, e.g. ['B3', 'B4']"),
        grade=str(p.get("grade") or _plan_grade(s, plan.name)).upper(),
    )
    for k, limit in (("width", 20), ("going", 20), ("landing", 20), ("riser", 2), ("tread", 2), ("waist", 2)):
        if p.get(k) is not None:
            v = _metres(_num(p, k), limit)
            if v <= 0:
                raise ValueError(f"{k} must be positive")
            setattr(st, k, v)
    for k in ("start", "live", "finish"):
        if p.get(k) is not None:
            v = _num(p, k)
            if v < 0:
                raise ValueError(f"{k} cannot be negative")
            setattr(st, k, v)
    des = apply_staircase(pr, st)
    s.last.clear()
    r.changed = True
    U = units.current
    r.messages.append(
        f"Staircase {st.name} on {plan.name} beams {', '.join(st.support_beams)} ({st.width:.2f} m wide): "
        f"dead {U.fmt(des.w_dead, 'area')}, live {U.fmt(des.w_live, 'area')} of plan, effective span {des.span:.2f} m; "
        f"each beam carries DL {U.fmt(des.reaction_dead, 'line')} + LL {U.fmt(des.reaction_live, 'line')}. "
        f"Waist {st.waist * 1000:.0f} mm: Mu {U.fmt(des.Mu, 'moment', 1)}/m, main {des.main}, "
        f"distribution {des.distribution}, deflection {'OK' if des.deflection_ok else 'NOT OK'}"
        + ("." if des.ok else " – revise the waist.")
    )
    r.messages.extend("⚠ " + n for n in des.notes)


def _level_index(s: Session, v: Any) -> int:
    lv = s.project.levels
    if not lv:
        raise ValueError("the project has no levels")
    if v is None or str(v).strip().lower() in ("", "0", "top"):
        return len(lv)
    if isinstance(v, (int, float)) or str(v).strip().isdigit():
        i = int(float(v))
        if not 1 <= i <= len(lv):
            raise ValueError(f"level {i} does not exist (1..{len(lv)})")
        return i
    name = str(v).strip().lower()
    for i, x in enumerate(lv, 1):
        if x.name.lower() == name:
            return i
    for i in range(len(lv), 0, -1):  # a plan name: the highest level that uses it
        if lv[i - 1].plan.lower() == name:
            return i
    raise ValueError(f"level '{v}' not found – levels: {', '.join(x.name for x in lv)}")


def _add_water_tank(s: Session, p: dict, r: ActionResult):
    from .. import units
    from ..design.wizards import WaterTank, apply_water_tank

    pr = s.project
    cap = _num(p, "capacity_l", p.get("capacity"))
    depth = _metres(_num(p, "water_depth", 1.5), 20)
    if cap <= 0 or depth <= 0:
        raise ValueError("capacity and water depth must be positive")
    lvl = _level_index(s, p.get("level"))
    t = WaterTank(
        name=str(p.get("name") or "T1").strip().upper(),
        capacity_l=cap,
        water_depth=depth,
        level=lvl,
        columns=_marks(p.get("columns"), "supporting columns, e.g. ['C5', 'C6', 'C9', 'C10']"),
    )
    loads = apply_water_tank(pr, t)
    s.last.clear()
    r.changed = True
    U = units.current
    r.messages.append(
        f"Water tank {t.name}: {cap:,.0f} L ({loads.side:.2f} m square × {depth:.2f} m water) at "
        f"{pr.levels[lvl - 1].name}: water {U.fmt(loads.water, 'force', 1)}, tank {U.fmt(loads.tank, 'force', 1)} "
        f"→ {U.fmt(loads.per_column, 'force', 1)} on each of {', '.join(t.columns)} (dead load)."
    )


def _grid_names(n: int, letters: bool) -> list[str]:
    if not letters:
        return [str(i + 1) for i in range(n)]
    out = []
    for i in range(n):  # A..Z, AA, AB, …
        name, k = "", i
        while k >= 0:
            name = chr(ord("A") + k % 26) + name
            k = k // 26 - 1
        out.append(name)
    return out


def _distinct(values: list[float], tol: float = 0.05) -> list[float]:
    out: list[float] = []
    for v in sorted(values):
        if not out or v - out[-1] > tol:
            out.append(round(v, 3))
    return out


def _add_grids(s: Session, p: dict, r: ActionResult):
    spec = p.get("grids", p.get("mode", "auto"))
    if spec is None or (isinstance(spec, str) and spec.strip().lower() in ("", "auto", "automatic")):
        if not s.project.plans or not s.project.plans[0].columns:
            raise ValueError("auto grids need columns on the first plan")
        cols = s.project.plans[0].columns
        xs, ys = _distinct([c.x for c in cols]), _distinct([c.y for c in cols])
        grids = [{"name": n, "axis": "x", "pos": x} for n, x in zip(_grid_names(len(xs), True), xs)]
        grids += [{"name": n, "axis": "y", "pos": y} for n, y in zip(_grid_names(len(ys), False), ys)]
    elif isinstance(spec, (list, tuple)):
        grids = []
        for g in spec:
            if not isinstance(g, dict):
                raise ValueError("each grid must be {name, axis, pos}")
            axis = str(g.get("axis", "")).strip().lower()
            if axis not in ("x", "y"):
                raise ValueError(f"grid axis must be 'x' or 'y', got {g.get('axis')!r}")
            name = str(g.get("name", "")).strip()
            if not name:
                raise ValueError("every grid needs a name")
            grids.append({"name": name, "axis": axis, "pos": round(_num(g, "pos"), 4)})
        if not grids:
            raise ValueError("no grids given")
        names = [g["name"] for g in grids]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"duplicate grid names: {', '.join(dup)}")
    else:
        raise ValueError("grids must be 'auto' or a list of {name, axis, pos}")
    s.project.grids = grids
    r.changed = True  # drawings only – analysis and design results stay valid
    gx = [g["name"] for g in grids if g["axis"] == "x"]
    gy = [g["name"] for g in grids if g["axis"] == "y"]
    r.messages.append(f"Grids: {len(gx)} along X ({', '.join(gx) or '-'}), {len(gy)} along Y ({', '.join(gy) or '-'}).")


def _ensure_design(s: Session, r: ActionResult):
    if "rep" not in s.last:
        _design(s, {}, r)
    return s.last["rep"]


def _save_revision(s: Session, p: dict, r: ActionResult):
    import datetime

    from ..design.quantities import revision_snapshot

    revs = list(s.project.meta.get("revisions") or [])
    label = str(p.get("label") or f"R{len(revs) + 1}").strip()
    rep = _ensure_design(s, r)
    snap = revision_snapshot(rep.boq, label, datetime.date.today().isoformat())
    replaced = any(x.get("label") == label for x in revs)
    s.project.meta["revisions"] = [x for x in revs if x.get("label") != label] + [snap]
    r.changed = True
    tot = snap["total"]
    r.messages.append(
        f"Revision {label} {'updated' if replaced else 'saved'}: concrete {tot['concrete']:.1f} m³, "
        f"steel {tot['steel'] / 1000:.2f} t, cost ₹{tot['cost'] / 1e5:.2f} lakh."
    )


def _pct(x: float) -> str:
    return "new" if x == float("inf") else f"{x:+.1f} %"


def _compare_revisions(s: Session, p: dict, r: ActionResult):
    from ..design.quantities import compare_revisions

    revs = s.project.meta.get("revisions") or []
    if len(revs) < 2:
        raise ValueError(f"need two saved revisions to compare ({len(revs)} saved) – say 'save revision R1'")

    def pick(key: str, default: dict) -> dict:
        lab = p.get(key)
        if lab in (None, ""):
            return default
        hit = next((x for x in revs if str(x.get("label")) == str(lab)), None) or next(
            (x for x in revs if str(x.get("label")).lower() == str(lab).lower()), None
        )
        if hit is None:
            raise ValueError(f"revision '{lab}' not found – saved: {', '.join(str(x.get('label')) for x in revs)}")
        return hit

    a, b = pick("a", revs[-2]), pick("b", revs[-1])
    if a is b:
        raise ValueError("pick two different revisions")
    names = {"concrete": "concrete m³", "steel": "steel t", "cost": "cost ₹ lakh"}
    scale = {"concrete": 1.0, "steel": 1e-3, "cost": 1e-5}
    la, lb = str(a["label"]), str(b["label"])
    lines = [f"Revision {la} → {lb}:", f"{'Item':<28}{la:>12}{lb:>12}{'Change':>10}"]
    for group, item, va, vb, _d, pct in compare_revisions(a, b):
        if group not in ("Total", "Floor"):
            continue
        key, q = item.split(" – ", 1)
        q = q.split(" ", 1)[0]
        if q not in names:
            continue
        name = f"{'Total' if group == 'Total' else key} {names[q]}"
        lines.append(f"{name:<28}{va * scale[q]:>12.2f}{vb * scale[q]:>12.2f}{_pct(pct):>10}")
    r.messages.append("\n".join(lines))


def boq_table(boq: dict, by: str = "floor") -> str:
    """Text BOQ table per floor (``by="floor"``) or per member type (``by="type"``)."""
    key = "by_type" if str(by).lower().startswith(("type", "member")) else "by_level"
    head = "Floor" if key == "by_level" else "Member type"
    lines = [f"{head:<14}{'Concrete m³':>13}{'Steel kg':>11}{'Formwork m²':>13}{'Cost ₹ lakh':>13}"]
    for name, v in (boq.get(key) or {}).items():
        if key == "by_type" and not (v["concrete"] or v["steel"] or v.get("pcc")):
            continue  # e.g. no walls in the model
        lines.append(
            f"{name:<14}{v['concrete']:>13.2f}{v['steel']:>11.0f}{v['formwork']:>13.1f}{v['cost'] / 1e5:>13.2f}"
        )
    lines.append(
        f"{'Total':<14}{boq['total_concrete']:>13.2f}{boq['total_steel']:>11.0f}{boq['formwork']:>13.1f}"
        f"{boq['cost'] / 1e5:>13.2f}"
    )
    return "\n".join(lines)


def _boq(s: Session, p: dict, r: ActionResult):
    by = str(p.get("by") or "floor").strip().lower()
    if by.startswith(("type", "member")):
        by = "type"
    elif by.startswith(("floor", "level", "storey", "story")):
        by = "floor"
    else:
        raise ValueError("by must be 'floor' or 'type'")
    rep = _ensure_design(s, r)
    r.messages.append(f"Bill of quantities by {by} (concrete excl. PCC):\n" + boq_table(rep.boq, by))


def _set_units(s: Session, p: dict, r: ActionResult):
    from .. import units

    v = str(p.get("system", "")).strip().lower().replace(" ", "")
    if v in ("si", "kn", "kilonewton", "kilonewtons"):
        name = "SI"
    elif v in ("mks", "t", "tf", "tonne", "tonnes", "ton", "tons", "metric"):
        name = "MKS"
    else:
        raise ValueError(f"units '{p.get('system')}' – use SI (kN) or MKS (tonnes)")
    u = units.set_system(name)
    r.messages.append(
        f"Display units: {u.system} – forces in {u.label('force')}, moments in {u.label('moment')} "
        "(the engine always works in kN)."
    )


def _answer(s: Session, p: dict, r: ActionResult):
    if p.get("text"):
        r.messages.append(str(p["text"]))


_HANDLERS: dict[str, Callable[[Session, dict, ActionResult], None]] = {
    "new_building": _new_building,
    "modify_building": _modify,
    "load_template": _template,
    "set_loads": _set_loads,
    "set_location": _set_location,
    "set_seismic": _set_seismic,
    "set_wind": _set_wind,
    "set_materials": _set_materials,
    "set_sbc": _set_sbc,
    "autosize_columns": _autosize,
    "optimize_sizes": _optimize,
    "add_wall": _add_wall,
    "add_staircase": _add_staircase,
    "add_water_tank": _add_water_tank,
    "add_grids": _add_grids,
    "save_revision": _save_revision,
    "compare_revisions": _compare_revisions,
    "boq": _boq,
    "set_units": _set_units,
    "analyze": _analyze,
    "design": _design,
    "export": _export,
    "answer": _answer,
}
