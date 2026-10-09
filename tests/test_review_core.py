"""Analysis-core review for 1.2.0: each test reproduces a defect in ``planwin_ai.core`` (it failed
before the fix) and checks the result against statics or the IS 1893 / IS 875 clause."""

import math

import numpy as np
import pytest

from planwin_ai.core.dynamics import G_ACC
from planwin_ai.core.frame import FrameModel
from planwin_ai.core.irregularity import check_irregularities
from planwin_ai.core.model import Beam, Column, Level, PartLoad, Plan, Project, Slab, Wall
from planwin_ai.core.plan_engine import PlanEngine, auto_beams


def _wall_box(levels=(3.0,), diaphragm=True, floor_type="roof", method="static", thickness=(0.2,)):
    """4 x 4 m floors carried only by two shear walls (x = 0 and x = 4) and two edge beams.

    ``thickness[k]`` is the wall thickness of storey k+1 (one plan per storey)."""
    p = Project()
    for k, h in enumerate(levels):
        t = thickness[min(k, len(thickness) - 1)]
        top = k == len(levels) - 1
        pl = Plan(name=f"P{k + 1}", floor_type=floor_type if top else "typical")
        pl.slabs.append(Slab(points=[[0, 0], [4, 0], [4, 4], [0, 4]], thickness=0.15, live=2.0, floor_finish=1.0))
        pl.walls += [
            Wall(mark="W1", x1=0, y1=0, x2=0, y2=4, thickness=t),
            Wall(mark="W2", x1=4, y1=0, x2=4, y2=4, thickness=t),
        ]
        pl.beams += [Beam(mark="B1", x1=0, y1=0, x2=4, y2=0), Beam(mark="B2", x1=0, y1=4, x2=4, y2=4)]
        p.plans.append(pl)
        p.levels.append(Level(f"L{k + 1}", pl.name, h))
    p.seismic.base_level = 0
    p.seismic.rigid_diaphragm = diaphragm
    p.seismic.method = method
    return p


def _vertical_weight(fm, level):
    """Self weight of the columns and walls of the storey below ``level`` (kN)."""
    return sum(
        m.b * m.d * 25.0 * abs(fm.nodes[m.n2].z - fm.nodes[m.n1].z)
        for m in fm.members.values()
        if m.kind in ("column", "wall") and m.level == level
    )


def _base_torque(fa, case):
    """Moment of the support reactions about the global Z axis through the origin."""
    mz = 0.0
    for nid, r in fa.res.reactions[case].items():
        nd = fa.model.nodes[nid]
        mz += r[5] + nd.x * r[1] - nd.y * r[0]
    return mz


# ------------------------------------------------------------------ seismic weight (cl 7.4)
def test_seismic_weight_counts_slab_load_on_walls_once():
    """Slab edges bearing on a wall are in the plan's applied load *and* were added again as the
    wall's nodal load, so the seismic weight (and Vb, masses) of wall buildings was too large."""
    fm = FrameModel(_wall_box()).build()
    lv = fm.levels[1]
    assert lv.result.walls and any(w.slab_loads for w in lv.result.walls.values())
    # roof: no imposed load (cl 7.3.1); half of the walls of the storey below lumps at the roof
    assert lv.weight == pytest.approx(lv.result.applied["D"] + 0.5 * _vertical_weight(fm, 1), rel=1e-9)


def test_seismic_weight_conserves_the_dead_load():
    """Σ floor weights = total dead load − lower half of the ground storey (no imposed load here).
    Masonry on the beams of a top floor that is not a roof has no floor above to go to; half of it
    was dropped from the seismic weight."""
    p = Project()
    pl = Plan(name="T", floor_type="typical")
    for k, (x, y) in enumerate(((0, 0), (5, 0), (5, 4), (0, 4)), 1):
        pl.columns.append(Column(mark=f"C{k}", x=x, y=y, b=0.3, d=0.3))
    pl.slabs.append(Slab(points=[[0, 0], [5, 0], [5, 4], [0, 4]], live=0.0))
    for k, (a, b) in enumerate((((0, 0), (5, 0)), ((5, 0), (5, 4)), ((5, 4), (0, 4)), ((0, 4), (0, 0))), 1):
        pl.beams.append(Beam(mark=f"B{k}", x1=a[0], y1=a[1], x2=b[0], y2=b[1]))
    p.plans.append(pl)
    p.levels += [Level("L1", "T", 3.0), Level("L2", "T", 3.0)]
    p.joint_loads.append({"level": 2, "mark": "C1", "fz": 40.0})  # e.g. a water tank (dead load)
    p.seismic.base_level = 0
    p.seismic.method = "static"
    p.wind.enabled = False
    fm = FrameModel(p).build()
    fa = fm.analyze()
    total_dl = fa.equilibrium()["DL"]
    assert sum(lv.weight for lv in fm.levels) == pytest.approx(total_dl - 0.5 * _vertical_weight(fm, 1), rel=1e-9)


# ------------------------------------------------------------------ floors without a rigid diaphragm
def test_lateral_loads_reach_walls_without_rigid_diaphragm():
    """Storey forces were shared by column joints only: a floor carried by walls lost its
    seismic, wind and accidental-torsion loads (sum of reactions 0 instead of −Vb)."""
    fm = FrameModel(_wall_box(diaphragm=False)).build()
    fa = fm.analyze()
    # tolerances: the 1e-10 stabilising springs pick up ~1e-5 of the load next to the stiff rigid links
    vb = fm.seismic["EQX"].Vb
    assert vb > 0
    assert sum(r[0] for r in fa.res.reactions["EQX"].values()) == pytest.approx(-vb, rel=1e-4)
    assert sum(r[1] for r in fa.res.reactions["EQY"].values()) == pytest.approx(-fm.seismic["EQY"].Vb, rel=1e-4)
    assert sum(r[0] for r in fa.res.reactions["WLX"].values()) == pytest.approx(-sum(fm.wind["WLX"].forces), rel=1e-4)
    # cl 7.8.2: Mt = F · 0.05 · b with b = 4 m perpendicular to EQY (walls 4 m apart in X)
    mt = fm.seismic["EQY"].forces[1] * 0.05 * 4.0
    assert abs(_base_torque(fa, "ETY")) == pytest.approx(mt, rel=1e-4)


def test_modal_masses_include_walls_without_rigid_diaphragm():
    """Without a diaphragm the mass was lumped at column joints only – a wall floor had no mass
    and the response spectrum analysis fell back to the static method."""
    fm = FrameModel(_wall_box(diaphragm=False, method="response_spectrum")).build()
    fa = fm.analyze()
    assert fa.modal is not None and fa.rs
    assert fa.modal.total_mass == pytest.approx(fm.levels[1].weight / G_ACC, rel=1e-9)
    assert fa.rs["RSX"].storey_shear[0] >= fm.seismic["EQX"].Vb * (1 - 1e-9)  # cl 7.7.3


# ------------------------------------------------------------------ drifts and irregularity with walls
def test_storey_drift_includes_wall_joints():
    """Drifts were measured at column joints only: a wall building reported 0 mm (always 'ok')."""
    fm = FrameModel(_wall_box()).build()
    fa = fm.analyze()
    d = next(x for x in fa.storey_drifts() if x["case"] == "EQX")
    top, bot = fm.levels[1].wall_nodes["W1"], fm.levels[0].wall_nodes["W1"]
    expect = abs(fa.displacement(top, {"EQX": 1})[0] - fa.displacement(bot, {"EQX": 1})[0])
    assert expect > 0
    assert d["drift_mm"] == pytest.approx(expect * 1000, rel=1e-6)


def test_soft_storey_check_includes_walls():
    """Table 6 (i) took the storey drift at column joints only and never flagged a wall storey.
    Here the ground storey (0.12 m walls, 4.5 m high) is far softer than the one above
    (0.30 m walls, 3 m high)."""
    fm = FrameModel(_wall_box(levels=(4.5, 3.0), thickness=(0.12, 0.3))).build()
    fa = fm.analyze()
    soft = next(c for c in check_irregularities(fa) if c.clause == "Table 6 (i)")
    assert soft.irregular, soft.detail


@pytest.mark.parametrize("diaphragm", [True, False])
def test_torsional_irregularity_takes_the_worse_accidental_eccentricity(diaphragm):
    """Table 5 (i) used EQ + ET only, so a building and its mirror image got different
    Δmax/Δmin ratios; the eccentricity on either side of the centre of mass must be checked."""

    def building(mirror: bool) -> Project:
        def X(x):
            return 12.0 - x if mirror else x

        pl = Plan(name="P", floor_type="roof")
        for i in range(4):
            for j in range(3):
                pl.columns.append(Column(mark=f"C{i}{j}", x=X(4.0 * i), y=3.0 * j, b=0.3, d=0.3))
        for i in range(3):
            x0, x1 = sorted((X(4.0 * i), X(4.0 * (i + 1))))
            for j in range(2):
                y0, y1 = 3.0 * j, 3.0 * (j + 1)
                pl.slabs.append(Slab(points=[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]))
        auto_beams(pl)
        pl.walls.append(Wall(mark="W1", x1=X(0.0), y1=1.0, x2=X(0.0), y2=5.0, thickness=0.2))  # stiff side
        p = Project(plans=[pl], levels=[Level("L1", "P", 3.0)])
        p.seismic.base_level = 0
        p.seismic.method = "static"
        p.seismic.rigid_diaphragm = diaphragm
        return p

    ratios = []
    for mirror in (False, True):
        fa = FrameModel(building(mirror)).build().analyze()
        check = next(c for c in check_irregularities(fa) if c.clause == "Table 5 (i)")
        ratios.append(float(check.detail.split("=")[1].split()[0]))
    assert ratios[0] > 1.5
    assert ratios[1] == pytest.approx(ratios[0], rel=1e-3)


# ------------------------------------------------------------------ plan engine
def test_partial_load_running_past_the_beam_end_keeps_its_intensities():
    """A wedge load longer than the beam was clipped to the beam but kept its end intensities,
    i.e. the clipped part was squeezed onto the beam (load 8 kN instead of 4 kN here)."""
    pl = Plan(name="P")
    pl.columns += [Column(mark="C1", x=0, y=0), Column(mark="C2", x=4, y=0)]
    b = Beam(mark="B1", x1=0, y1=0, x2=4, y2=0, include_self=False, include_wall=False, include_plaster=False)
    b.part_loads.append(PartLoad(start=2.0, length=4.0, w1=0.0, w2=8.0, case="D"))  # 2 kN/m per m
    pl.beams.append(b)
    res = PlanEngine(pl).run()
    # on the beam (x = 2 … 4) the load rises from 0 to 4 kN/m: 4 kN, centroid at x = 3.333
    assert res.applied["D"] == pytest.approx(4.0)
    assert res.beams[b.id].total("D") == pytest.approx(4.0)
    r1, r2 = res.beams[b.id].reactions["D"]
    assert r2 == pytest.approx(4.0 * (10.0 / 3.0) / 4.0)
    assert r1 + r2 == pytest.approx(4.0)


# ------------------------------------------------------------------ seismic base level
def test_no_seismic_weight_above_the_base_level_is_reported():
    """A one-storey building with the default seismic base at level 1 got no earthquake load at
    all and no message (Vb = 0 silently)."""
    p = _wall_box()
    p.seismic.base_level = 1
    fm = FrameModel(p).build()
    assert fm.seismic["EQX"].Vb == 0.0
    assert any("seismic base" in i.message for i in fm.issues if i.level == "warning")


# ------------------------------------------------------------------ guards (closed-form checks)
def test_wall_frame_equilibrium_in_every_case():
    """Σ reactions + Σ applied = 0 (forces and moments) for every primary case, with walls,
    diaphragms, accidental torsion and wind."""
    fm = FrameModel(_wall_box(levels=(3.0, 3.0))).build()
    fa = fm.analyze()
    for case in fa.res.cases:
        F = np.zeros(3)
        for v in fm.nodal.get(case, {}).values():
            F += v[:3]
        for m in fm.members.values():
            for ld in m.loads.get(case, []):
                F += 0.5 * (np.array(ld.w1) + np.array(ld.w2)) * (ld.b - ld.a)
        R = sum(r[:3] for r in fa.res.reactions[case].values())
        # residual from the 1e-10 stabilising springs only (scaled by the stiff rigid links)
        assert np.abs(R + F).max() <= 1e-5 * max(np.abs(F).max(), 1.0), case
    mt = sum(f * 0.05 * 4.0 for f in fm.seismic["EQX"].forces)
    assert abs(_base_torque(fa, "ETX")) == pytest.approx(mt, rel=1e-6)
    # symmetric building: EQX acts at the centre of mass (2, 2) with a torque −2 · Vb about Z,
    # which the reactions balance
    assert _base_torque(fa, "EQX") == pytest.approx(2.0 * fm.seismic["EQX"].Vb, rel=1e-5)
    # … and both walls take the same share
    react = fa.res.reactions["EQX"]
    w1, w2 = (react[fm.levels[0].wall_nodes[m]][0] for m in ("W1", "W2"))
    assert math.isclose(w1, w2, rel_tol=1e-6)
