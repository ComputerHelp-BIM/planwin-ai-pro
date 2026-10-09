"""FrameWin AUTOSIZE (columns from axial load) and the size optimiser (enlarge failing members)."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ..core.model import Project, grade_fck
from ..core.plan_engine import PlanEngine
from . import is456

if TYPE_CHECKING:
    from .report import DesignReport


def default_moment_factor(project: Project) -> float:
    """Allowance for frame moments in axial-load sizing: larger in higher seismic zones."""
    from ..core.lateral import ZONE_FACTOR

    if not project.seismic.enabled:
        return 1.25
    return 1.25 + 2.5 * ZONE_FACTOR.get(project.seismic.zone, 0.16)


def autosize_columns(
    project: Project,
    breadth: float | None = None,
    steel_pct: float = 0.8,
    same_size: bool = True,
    max_step: float = 0.05,
    moment_factor: float | None = None,
    below_ground_increase: float = 0.0,
) -> dict[str, list[float]]:
    """FrameWin AUTOSIZE: size columns from cumulative factored axial load.

    Returns {mark: [depth per level index 1..n]} and writes overrides into
    ``project.column_sizes``.
    """
    n = len(project.levels)
    if moment_factor is None:
        moment_factor = default_moment_factor(project)
    seismic_min = 0.3 if project.seismic.enabled and project.seismic.zone != "II" else 0.0
    loads: dict[str, list[float]] = {}
    info: dict[str, dict] = {}
    for i, lv in enumerate(project.levels, start=1):
        plan = project.plan(lv.plan)
        fha = project.levels[i].height if i < n else plan.floor_height_above
        res = PlanEngine(plan, fha, project.design.two_way_ratio_limit).run()
        for c in plan.columns:
            cl = next((v for v in res.columns.values() if v.column_id == c.id), None)
            loads.setdefault(c.mark, [0.0] * (n + 1))
            red = 1 - lv.live_reduction / 100.0
            loads[c.mark][i] = (cl.dead + cl.live * red) if cl else 0.0
            info.setdefault(c.mark, {})[i] = c
    out = {}
    for mark, per in loads.items():
        present = sorted(info[mark])
        sizes = {}
        cum = 0.0
        for i in sorted(present, reverse=True):
            c = info[mark][i]
            b = max(breadth or c.b, seismic_min)  # IS 13920: min 300 mm in ductile frames
            h = project.levels[i - 1].height
            cum += per[i] + b * max(c.d, b) * h * 25.0
            fck = grade_fck(project.levels[i - 1].grade)
            d = is456.autosize_depth(1.5 * cum, b, fck, project.design.fy_main, steel_pct, moment_factor, min_d=b)
            if seismic_min and d > 2.0 * b:
                # ductile frames: keep d/b <= 2 so both directions have lateral stiffness
                area = b * d
                b = max(b, math.ceil(math.sqrt(area / 2.0) / 0.05 - 1e-9) * 0.05)
                d = max(math.ceil(area / b / 0.05 - 1e-9) * 0.05, b)
            sizes[i] = [round(b, 3), round(d, 3)]
        if same_size:
            bmax = max(v[0] for v in sizes.values())
            dmax = max(v[1] for v in sizes.values())
            for i in sizes:
                sizes[i] = [bmax, dmax]
        else:  # limit reduction between consecutive levels
            prev = None
            for i in sorted(sizes):
                if prev is not None and sizes[prev][1] - sizes[i][1] > max_step:
                    sizes[i][1] = round(sizes[prev][1] - max_step, 3)
                prev = i
        for i, (b, d) in sizes.items():
            if i == 1 and below_ground_increase:
                b, d = b + 2 * below_ground_increase, d + 2 * below_ground_increase
            project.set_column_size(mark, i, b, d, info[mark][i].angle)
        out[mark] = [sizes[i][1] for i in sorted(sizes)]
    return out


def optimize_sizes(
    project: Project, max_iter: int = 8, target_col_pct: float = 3.0, step: float = 0.05, progress=None
) -> tuple[list[str], DesignReport]:
    """Iteratively enlarge failing members (FrameWin "change size, re-run" loop).

    Columns: depth +step (breadth when depth/breadth >= 2.5) for segments that
    fail or need more than ``target_col_pct`` steel; lower storeys are kept at
    least as large as upper ones.  Beams: depth +step (breadth +step when the
    shear stress limit governs).  Returns (change log, final design report).
    """
    from .runner import run_full  # runner imports this module at load time

    log: list[str] = []
    rep = None
    for it in range(1, max_iter + 1):
        fm, fa, rep = run_full(project)
        bad_cols = [c for c in rep.columns if not c.ok or c.steel_pct > target_col_pct]
        bad_beams = [b for b in rep.beams if not b.ok]
        bad_drift = [d for d in rep.drifts if not d["ok"]]
        bad_duct = [d for d in rep.ductile if not d.ok]
        bad_walls = [w for w in rep.walls if not w.ok]
        if progress:
            progress(it, len(bad_cols), len(bad_beams))
        if not bad_cols and not bad_beams and not bad_drift and not bad_duct and not bad_walls:
            log.append(f"Iteration {it}: all members and storey drifts pass")
            return log, rep
        drift_lv: dict[int, set[str]] = {}
        if bad_drift:  # stiffen columns (in the drift direction) and beams up to the highest drifting storey
            lvl_idx = {lv.name: i for i, lv in enumerate(project.levels, start=1)}
            for d in bad_drift:
                top = lvl_idx.get(d["level"], 0)
                for k in range(1, top + 1):
                    drift_lv.setdefault(k, set()).add(d["case"][-1])  # "X" / "Y"
        lvl_index = {lv.name: i for i, lv in enumerate(project.levels, start=1)}
        changed = set()
        for c in bad_cols:
            i = lvl_index.get(c.level)
            if i is None or (c.mark, i) in changed:
                continue
            plan = project.plan(project.levels[i - 1].plan)
            col = next((x for x in plan.columns if x.mark == c.mark), None)
            if col is None:
                continue
            b, d, ang = project.column_size(c.mark, i, col)
            if d / b >= 2.5:
                b += step
            else:
                d += step
            for k in range(1, i + 1):  # this level and all below
                plan_k = project.plan(project.levels[k - 1].plan)
                col_k = next((x for x in plan_k.columns if x.mark == c.mark), None)
                if col_k is None:
                    continue
                bk, dk, ak = project.column_size(c.mark, k, col_k)
                project.set_column_size(c.mark, k, max(bk, b), max(dk, d), ak)
                changed.add((c.mark, k))
        n_duct = _fix_ductile(project, fm, bad_duct, changed, step)
        n_wall = _thicken_walls(project, bad_walls)
        stiffened_plans = set()
        for i, dirs in drift_lv.items():
            plan = project.plan(project.levels[i - 1].plan)
            for col in plan.columns:
                if (col.mark, i) in changed:
                    continue
                b, d, ang = project.column_size(col.mark, i, col)
                b_along_x = abs(math.cos(math.radians(ang))) >= 0.7
                grow_b = ("X" in dirs and b_along_x) or ("Y" in dirs and not b_along_x)
                grow_d = ("Y" in dirs and b_along_x) or ("X" in dirs and not b_along_x)
                project.set_column_size(col.mark, i, b + step * grow_b, d + step * grow_d, ang)
                changed.add((col.mark, i))
            if plan.name not in stiffened_plans:
                stiffened_plans.add(plan.name)
                for bm in plan.beams:
                    if bm.d < bm.length / 8:
                        bm.d = round(bm.d + step, 3)
        beam_ids = set()
        for bd in bad_beams:
            mem = fm.members.get(bd.member_id)
            if mem is None or mem.group in beam_ids:
                continue
            beam_ids.add(mem.group)
            for plan in project.plans:
                bm = next((x for x in plan.beams if x.id == mem.group), None)
                if bm is None:
                    continue
                if any("τv" in n for n in bd.notes) and bm.d / bm.b >= 2.5:
                    bm.b = round(bm.b + step, 3)
                else:
                    bm.d = round(bm.d + step, 3)
        log.append(
            f"Iteration {it}: enlarged {len(changed)} column segments and {len(beam_ids)} beams"
            + (f" (storey drift at {len(bad_drift)} level/case)" if bad_drift else "")
            + (f"; {n_duct} IS 13920 fixes" if n_duct else "")
            + (f"; thickened {n_wall} walls" if n_wall else "")
        )
    fm, fa, rep = run_full(project)
    log.append(f"Stopped after {max_iter} iterations – {rep.failures} member(s) still need attention")
    return log, rep


def _grow_column(project: Project, mark: str, level: int, grow_b: float, grow_d: float, changed: set) -> None:
    """Enlarge column ``mark`` at ``level`` and keep every level below at least as large."""
    target = None
    for k in range(level, 0, -1):
        plan_k = project.plan(project.levels[k - 1].plan)
        col_k = next((x for x in plan_k.columns if x.mark == mark), None) if plan_k else None
        if col_k is None:
            continue
        bk, dk, ak = project.column_size(mark, k, col_k)
        if target is None:
            target = (bk + grow_b, dk + grow_d)
        project.set_column_size(mark, k, max(bk, target[0]), max(dk, target[1]), ak)
        changed.add((mark, k))


def _fix_ductile(project: Project, fm, bad: list, changed: set, step: float) -> int:
    """Size changes for IS 13920 failures: strong column–weak beam (enlarge the columns at the joint
    in the failing direction), column proportions (cl 7.1) and beam proportions (cl 6.1)."""
    n = 0
    for dc in bad:
        names = [name for name, ok, _ in dc.checks if not ok]
        mem = fm.members.get(dc.member_id) if dc.member_id is not None else None
        if mem is None:
            continue
        if dc.kind == "joint":
            for name in names:
                if "7.2.1" not in name:
                    continue
                along_x = "along X" in name
                for lvl in (mem.level, mem.level + 1):
                    if lvl > len(project.levels):
                        continue
                    plan = project.plan(project.levels[lvl - 1].plan)
                    col = next((x for x in plan.columns if x.mark == dc.mark), None) if plan else None
                    if col is None or (dc.mark, lvl) in changed:
                        continue
                    b, d, ang = project.column_size(dc.mark, lvl, col)
                    b_along_x = abs(math.cos(math.radians(ang))) >= 0.7
                    grow_b = along_x == b_along_x
                    _grow_column(project, dc.mark, lvl, step if grow_b else 0.0, 0.0 if grow_b else step, changed)
                    n += 1
        elif dc.kind == "column" and any(x.startswith(("7.1.1", "7.1.2")) for x in names):
            plan = project.plan(project.levels[mem.level - 1].plan)
            col = next((x for x in plan.columns if x.mark == dc.mark), None) if plan else None
            if col is not None and (dc.mark, mem.level) not in changed:
                b, d, ang = project.column_size(dc.mark, mem.level, col)
                nb = max(b, 0.3, math.ceil(0.4 * max(b, d) / 0.025) * 0.025)
                nd = max(d, 0.3, math.ceil(0.4 * max(b, d) / 0.025) * 0.025)
                _grow_column(project, dc.mark, mem.level, nb - b, nd - d, changed)
                n += 1
        elif dc.kind == "beam" and any(x.startswith("6.1.3") for x in names):
            # cl 6.1.3: depth ≤ clear span / 4 – make short beams shallower (not deeper)
            for plan in project.plans:
                bm = next((x for x in plan.beams if x.id == mem.group), None)
                if bm is None:
                    continue
                clear = _clear_span(project, plan, bm)
                cap = math.floor(clear / 4 / 0.025) * 0.025
                if 0.3 <= cap < bm.d:
                    bm.d = round(cap, 3)
                    bm.b = round(max(bm.b, math.ceil(0.3 * bm.d / 0.025) * 0.025), 3)
                    n += 1
        elif dc.kind == "beam" and any(x.startswith(("6.1.1", "6.1.2")) for x in names):
            for plan in project.plans:
                bm = next((x for x in plan.beams if x.id == mem.group), None)
                if bm is not None:
                    bm.b = round(max(bm.b, 0.2, math.ceil(0.3 * bm.d / 0.025) * 0.025), 3)
                    n += 1
    return n


def _thicken_walls(project: Project, bad: list) -> int:
    marks = {w.mark for w in bad}
    for plan in project.plans:
        for w in plan.walls:
            if w.mark in marks:
                w.thickness = round(w.thickness + 0.025, 3)
    return len(marks)


def _clear_span(project: Project, plan, bm) -> float:
    """Beam length between the faces of the columns/walls at its ends (m), using the largest
    column size (per-level overrides included) of every level that uses this plan."""
    import copy

    from ..core.frame import _half_along
    from ..core.plan_engine import column_on_beam

    levels = [i for i, lv in enumerate(project.levels, start=1) if lv.plan == plan.name]
    clear = bm.length
    for c in plan.columns:
        t = column_on_beam(c, bm)
        if t is None or not (t < 1e-3 or t > 1 - 1e-3):
            continue
        half = 0.0
        for i in levels or [0]:
            b, d, ang = project.column_size(c.mark, i, c) if i else (c.b, c.d, c.angle)
            cc = copy.copy(c)
            cc.b, cc.d, cc.angle = b, d, ang
            half = max(half, _half_along(cc, bm))
        clear -= half
    for w in plan.walls:
        for p in (bm.p1, bm.p2):
            if w.contains(p):
                clear -= w.thickness / 2
    return max(clear, 0.1)
