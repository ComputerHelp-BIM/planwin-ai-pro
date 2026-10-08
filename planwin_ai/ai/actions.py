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
    },
    "set_wind": {"basic_speed": "m/s", "terrain": "1-4", "enabled": "true/false"},
    "set_materials": {"concrete": "M20..M50", "steel": "415|500|550"},
    "set_sbc": {"sbc": "safe bearing capacity kN/m2"},
    "autosize_columns": {"steel_pct": "assumed steel % (0.8-2.0)", "same_size": "true/false"},
    "optimize_sizes": {"max_iter": "iterations (default 5)", "target_col_pct": "max column steel % (default 3)"},
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
    s.last.clear()
    r.changed = True
    r.messages.append(
        f"Seismic: zone {sm.zone}, {sm.soil} soil, I={sm.importance}, R={sm.response_reduction}"
        + ("" if sm.enabled else " (disabled)")
    )


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
    msg = (
        f"Analysis done: {len(fm.nodes)} joints, {len(fm.members)} members. "
        f"Vertical reaction DL {eq['DL']:.0f} kN, LL {eq['LL']:.0f} kN."
    )
    for k, v in fm.seismic.items():
        msg += f" {k}: T={v.T:.2f}s Ah={v.Ah:.4f} VB={v.Vb:.0f} kN."
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
            "Tip: say 'optimise sizes' (or FrameWin ▸ Optimise sizes) to enlarge failing members automatically."
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
    "analyze": _analyze,
    "design": _design,
    "export": _export,
    "answer": _answer,
}
