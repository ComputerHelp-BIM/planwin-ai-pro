"""1.1.0 features: rigid diaphragms, shear walls, torsion, response spectrum, irregularity,
combined footings, wall design, floor-wise BOQ, units, wizards and the export registry."""

import copy
import math

import numpy as np
import pytest

from planwin_ai.ai.templates import build_template, generate_template
from planwin_ai.core.dynamics import cqc_matrix, sa_by_g_rsa
from planwin_ai.core.frame import FrameModel
from planwin_ai.core.model import Project, Wall
from planwin_ai.core.solver import Diaphragm, FMember, FNode, FrameSolver, MTorque, section_forces
from planwin_ai.design import is456
from planwin_ai.design.quantities import compare_revisions, revision_snapshot
from planwin_ai.design.runner import run_full
from planwin_ai.design.wizards import Staircase, WaterTank, apply_staircase, apply_water_tank, design_staircase
from planwin_ai.io import project_io
from planwin_ai.units import MKS, SI, T_TO_KN


def _g4(diaphragm=True, method="static"):
    p = build_template("residential_g4")
    p.seismic.rigid_diaphragm = diaphragm
    p.seismic.method = method
    return p


# ------------------------------------------------------------------ solver
def _portal(diaphragm: bool):
    nodes, mem = {}, {}
    for k, (x, y) in enumerate(((0, 0), (5, 0), (5, 5), (0, 5)), start=1):
        nodes[k] = FNode(k, x, y, 0, support="fixed")
        nodes[k + 10] = FNode(k + 10, x, y, 3.0, 1)
        mem[k] = FMember(k, k, k + 10, "column", 0.4, 0.4, 2.5e7)
    nodes[99] = FNode(99, 2.5, 2.5, 3.0, 1)
    dg = [Diaphragm(99, [11, 12, 13, 14])] if diaphragm else []
    return nodes, mem, dg


def test_rigid_diaphragm_lateral_stiffness_equals_sum_of_cantilever_columns():
    nodes, mem, dg = _portal(True)
    s = FrameSolver(nodes, mem, {}, dg)
    s.solve(["DL"])
    F = np.zeros((len(nodes) * 6, 1))
    F[6 * s.idx[99], 0] = 1.0
    u = s.displacements(F)
    k_expected = 4 * 3 * 2.5e7 * (0.4 * 0.4**3 / 12) / 3.0**3  # column tops free to rotate
    assert 1 / u[6 * s.idx[99], 0] == pytest.approx(k_expected, rel=1e-6)
    # every slave moves with the master (rigid body)
    for n in (11, 12, 13, 14):
        assert u[6 * s.idx[n], 0] == pytest.approx(u[6 * s.idx[99], 0], rel=1e-9)


def test_rigid_diaphragm_rotation_kinematics():
    nodes, mem, dg = _portal(True)
    s = FrameSolver(nodes, mem, {}, dg)
    s.solve(["DL"])
    F = np.zeros((len(nodes) * 6, 1))
    F[6 * s.idx[99] + 5, 0] = 10.0  # torque at the master
    u = s.displacements(F)
    rz = u[6 * s.idx[99] + 5, 0]
    for n in (11, 12, 13, 14):
        nd = nodes[n]
        assert u[6 * s.idx[n], 0] == pytest.approx(-(nd.y - 2.5) * rz, abs=1e-12)
        assert u[6 * s.idx[n] + 1, 0] == pytest.approx((nd.x - 2.5) * rz, abs=1e-12)


def test_distributed_torque_cantilever():
    nodes = {1: FNode(1, 0, 0, 3, support="fixed"), 2: FNode(2, 4, 0, 3)}
    m = FMember(1, 1, 2, "beam", 0.3, 0.6, 2.5e7, torsion_factor=1.0)
    m.loads["DL"] = [MTorque(0, 4, 2.0, 2.0)]
    res = FrameSolver(nodes, {1: m}).solve(["DL"])
    assert res.reactions["DL"][1][3] == pytest.approx(-8.0, rel=1e-6)
    sf = section_forces(m, nodes, res.end_forces["DL"][1], m.loads["DL"], np.array([0.0, 2.0, 4.0]))
    assert sf["T"] == pytest.approx([8.0, 4.0, 0.0], abs=1e-6)


# ------------------------------------------------------------------ frame
def test_diaphragm_frame_equilibrium_and_masters():
    fm = FrameModel(_g4(True)).build()
    fa = fm.analyze()
    assert sum(lv.master is not None for lv in fm.levels) >= 5
    for case in ("EQX", "WLX", "EQY"):
        applied = sum(v[:3] for v in fm.nodal[case].values())
        react = sum(r[:3] for r in fa.res.reactions[case].values())
        # residual from the 1e-10 stabilising springs only
        assert np.abs(react + applied).max() <= 1e-5 * np.abs(applied).max()
    ref = FrameModel(_g4(False)).build().analyze().equilibrium()["DL"]
    assert fa.equilibrium()["DL"] == pytest.approx(ref, rel=1e-6)


def test_shear_wall_takes_the_storey_shear_in_its_plane():
    p = _g4(True)
    for pl in p.plans:
        pl.walls.append(Wall(mark="W1", x1=4.5, y1=4.0, x2=8.5, y2=4.0, thickness=0.23))
    fm = FrameModel(p).build()
    assert not [i for i in fm.issues if i.level == "error"]
    fa = fm.analyze()
    sup = {n: nd for n, nd in fm.nodes.items() if nd.support}
    wall_v = sum(abs(fa.res.reactions["EQX"][n][0]) for n, nd in sup.items() if nd.tag.startswith("wall"))
    tot = sum(abs(fa.res.reactions["EQX"][n][0]) for n in sup)
    assert wall_v / tot > 0.8
    # wall self weight is in the vertical equilibrium
    extra = fa.equilibrium()["DL"] - FrameModel(_g4(True)).build().analyze().equilibrium()["DL"]
    H = fm.levels[-1].z
    assert extra == pytest.approx(0.23 * 4.0 * 25.0 * H, rel=0.01)
    _, _, rep = run_full(p)
    assert rep.walls and all(w.t == 0.23 for w in rep.walls)


def test_wall_supports_beams_and_slab_in_plan():
    from planwin_ai.core.model import Beam, Column, Plan, Slab
    from planwin_ai.core.plan_engine import PlanEngine

    pl = Plan(name="P")
    pl.slabs.append(Slab(points=[[0, 0], [4, 0], [4, 4], [0, 4]], thickness=0.15, live=2.0, floor_finish=1.0))
    pl.walls.append(Wall(mark="W1", x1=0, y1=0, x2=0, y2=4, thickness=0.2))
    for k, (x, y) in enumerate(((4, 0), (4, 4)), 1):
        pl.columns.append(Column(mark=f"C{k}", x=x, y=y))
    pl.beams += [
        Beam(mark="B1", x1=0, y1=0, x2=4, y2=0),
        Beam(mark="B2", x1=4, y1=0, x2=4, y2=4),
        Beam(mark="B3", x1=4, y1=4, x2=0, y2=4),
    ]
    res = PlanEngine(pl).run()
    assert not res.errors, [i.message for i in res.errors]
    assert abs(res.imbalance_pct) < 1e-6
    wl = next(iter(res.walls.values()))
    q = 0.15 * 25 + 1.0
    assert wl.slab_loads and wl.dead > 0.2 * q * 16  # the wall edge takes its yield-line share directly


def test_cantilever_balcony_produces_equilibrium_torsion():
    fm = FrameModel(build_template("bungalow")).build()
    torques = [ld for m in fm.members.values() for ld in m.loads.get("DL", []) if isinstance(ld, MTorque)]
    assert torques
    _, _, rep = run_full(build_template("bungalow"))
    assert max(b.T_max for b in rep.beams) > 1.0


# ------------------------------------------------------------------ dynamics
@pytest.mark.parametrize(
    "soil,T,expected",
    [
        ("medium", 0.05, 1.75),
        ("medium", 0.3, 2.5),
        ("medium", 1.0, 1.36),
        ("hard", 1.0, 1.0),
        ("soft", 0.6, 2.5),
        ("soft", 5.0, 0.42),
    ],
)
def test_rsa_spectrum(soil, T, expected):
    assert sa_by_g_rsa(T, soil) == pytest.approx(expected)


def test_cqc_reduces_to_srss_for_well_separated_modes():
    rho = cqc_matrix(np.array([1.0, 10.0]), 0.05)
    assert rho[0, 0] == pytest.approx(1.0) and rho[0, 1] < 1e-3


def test_response_spectrum_analysis_scaled_to_static_base_shear():
    fm = FrameModel(_g4(True, "response_spectrum")).build()
    fa = fm.analyze()
    assert set(fa.rs) == {"RSX", "RSY"}
    assert any("RSX" in c.factors for c in fa.ultimate)
    for case, eq in (("RSX", "EQX"), ("RSY", "EQY")):
        r = fa.rs[case]
        assert fa.modal.cumulative("x" if case == "RSX" else "y")[r.modes_used - 1] >= 0.9 or r.modes_used == len(
            fa.modal.modes
        )
        assert r.storey_shear[0] == pytest.approx(max(r.vb_dynamic, fm.seismic[eq].Vb), rel=1e-6)
    # periods sorted, modal masses sum to 1 over all modes
    T = [m.period for m in fa.modal.modes]
    assert T == sorted(T, reverse=True)
    assert sum(m.mass_x for m in fa.modal.modes) == pytest.approx(1.0, rel=1e-6)
    # design runs on the spectral envelopes
    rep = __import__("planwin_ai.design.runner", fromlist=["design_all"]).design_all(fa, fm.p)
    assert rep.seismic_method == "response spectrum"


def test_auto_method_uses_static_for_small_regular_zone_ii_building():
    from planwin_ai.core.generator import GridSpec, grid_building

    p = grid_building(GridSpec(bays_x=[4, 4], bays_y=[4], upper_floors=1, city="Ajmer"))  # zone II
    p.seismic.zone = "II"
    fa = FrameModel(p).build().analyze()
    assert fa.irregularities
    if all(not i.irregular for i in fa.irregularities):
        assert not fa.rs and not fa.dynamic_required


def test_irregularity_detects_mass_and_reentrant_corner():
    from planwin_ai.core.irregularity import _raster, _reentrant
    from planwin_ai.core.model import Plan, Slab

    pl = Plan()
    pl.slabs.append(Slab(points=[[0, 0], [10, 0], [10, 4], [0, 4]]))
    pl.slabs.append(Slab(points=[[0, 4], [4, 4], [4, 10], [0, 10]]))  # L shape, 60 % projection
    px, py = _reentrant(_raster(pl)[0])
    assert max(px, py) == pytest.approx(0.6, abs=0.02)
    p = _g4(True)
    p.joint_loads.append({"level": 4, "mark": "C1", "fz": 3000.0, "case": "D"})
    fa = FrameModel(p).build().analyze()
    mass = next(i for i in fa.irregularities if i.name == "Mass irregularity")
    assert mass.irregular


# ------------------------------------------------------------------ member design
def test_torsion_design_textbook_values():
    r = is456.torsion_design(45, 100, 115, 0, 300, 600, 40, 25, 415, 415, 0.8, 20)
    assert r.Ve == pytest.approx(100 + 1.6 * 45 / 0.3)
    assert r.Mt == pytest.approx(45 * (1 + 600 / 300) / 1.7)
    assert r.Me1_sag == pytest.approx(115 + r.Mt)
    assert r.ok and r.spacing <= min(300 - 80, (220 + 520) / 4, 300)


def test_wall_design_minimum_steel_and_shear():
    chk = is456.design_wall([("c", 500, 100, 50)], 0.2, 3.0, 25, 500, 415, True)
    assert chk.rho_v == pytest.approx(0.0025) and chk.rho_h == pytest.approx(0.0025) and chk.ok
    thin = is456.design_wall([("c", 500, 100, 50)], 0.14, 3.0, 25, 500, 415, True)
    assert not thin.ok
    big_v = is456.design_wall([("c", 500, 100, 1600)], 0.2, 3.0, 25, 500, 415, True)
    assert big_v.rho_h > 0.0025  # τv > τc needs horizontal steel


def test_combined_footing_hand_calculation():
    cf = is456.design_combined_footing(800, 1200, 1200, 1800, 3.0, (0.45, 0.3), (0.45, 0.3), 200, 25, 500)
    assert cf.L == pytest.approx(4.65) and cf.B == pytest.approx(2.4) and cf.x_start == pytest.approx(0.525)
    w = (1200 + 1800) / (cf.L * cf.B) * cf.B
    x = 1200 / w  # zero shear between the columns
    assert cf.M_hog == pytest.approx(1200 * (x - 0.525) - w * x * x / 2, rel=0.01)
    assert cf.M_sag == pytest.approx(w * 1.125**2 / 2, rel=0.01)


def test_overlapping_footings_become_combined():
    from planwin_ai.core.generator import GridSpec, grid_building

    # two column lines only 2 m apart: their footings overlap in pairs
    p = grid_building(GridSpec(bays_x=[2.0], bays_y=[6.0], upper_floors=4, city="Pune"))
    p.design.sbc = 120.0
    _, _, rep = run_full(p)
    assert len(rep.combined_footings) == 2
    marks = {m for c in rep.combined_footings for m in c.marks}
    assert not marks & {f.mark for f in rep.footings}  # replaced, not duplicated
    for c in rep.combined_footings:
        assert c.q <= 120.0 * 1.0001 and c.L > 2.0 and c.ok


def test_many_overlapping_footings_advise_raft():
    p = build_template("bungalow")
    p.design.sbc = 25.0
    _, _, rep = run_full(p)
    assert any("raft" in w for w in rep.warnings)


def test_ductile_design_meets_is13920_on_optimised_bungalow():
    from planwin_ai.design.runner import optimize_sizes

    log, rep = optimize_sizes(build_template("bungalow"), 10)
    assert rep.ductile and rep.ductile_failures == 0 and rep.failures == 0
    assert all(c.tie_confined is not None and c.l0 >= 0.45 for c in rep.columns)
    assert all(c.main_bars.dia >= 16 for c in rep.columns)


# ------------------------------------------------------------------ BOQ / units / wizards / io
def test_floor_wise_boq_reconciles_with_totals_and_compares():
    _, _, rep = run_full(build_template("residential_g4"))
    b = rep.boq
    for key, tot in (("concrete", b["total_concrete"]), ("steel", b["total_steel"]), ("cost", b["cost"])):
        assert sum(v[key] for v in b["by_level"].values()) == pytest.approx(tot)
        assert sum(v[key] for v in b["by_type"].values()) == pytest.approx(tot)
    a = revision_snapshot(b, "R0", "2026-10-01")
    b2 = copy.deepcopy(a)
    b2["total"]["steel"] *= 1.1
    rows = compare_revisions(a, b2)
    steel = next(r for r in rows if r[0] == "Total" and "steel" in r[1])
    assert steel[5] == pytest.approx(10.0)


def test_units_round_trip_and_labels():
    assert MKS.show(T_TO_KN, "force") == pytest.approx(1.0)
    assert MKS.parse(2.0, "area") == pytest.approx(2 * T_TO_KN)
    assert MKS.show(5.0, "length") == 5.0 and SI.show(5.0, "force") == 5.0
    assert MKS.text("Pu kN, Mu kN·m, w kN/m²") == "Pu t, Mu t·m, w t/m²"


def test_staircase_loads_and_reapply_is_idempotent():
    st = Staircase(riser=0.15, tread=0.30, waist=0.20, going=3.0, landing=1.2, live=3.0, finish=1.0)
    d = design_staircase(st)
    assert d.w_dead == pytest.approx(25 * 0.2 * math.hypot(0.15, 0.3) / 0.3 + 25 * 0.075 + 1.0)
    assert d.span == pytest.approx(4.2) and d.reaction_live == pytest.approx(3.0 * 4.2 / 2)
    p = build_template("residential_g4")
    beams = [b.mark for b in p.plan("Typical").beams[:2]]
    st = Staircase(name="ST1", plan="Typical", support_beams=beams, width=1.0)
    apply_staircase(p, st)
    apply_staircase(p, st)
    pl = [x for b in p.plan("Typical").beams for x in b.part_loads if x.desc == "Stair ST1"]
    assert len(pl) == 4 and len(p.stairs) == 1


def test_water_tank_joint_loads_reapply_is_idempotent():
    p = build_template("residential_g4")
    roof = next(i for i, lv in enumerate(p.levels, start=1) if lv.name == "Roof")
    t = WaterTank(name="T1", capacity_l=10000, level=roof, columns=["C1", "C2"])
    apply_water_tank(p, t)
    loads = apply_water_tank(p, t)
    mine = [j for j in p.joint_loads if j.get("source") == "Tank T1"]
    assert len(mine) == 2 and sum(j["fz"] for j in mine) == pytest.approx(loads.water + loads.tank, rel=1e-3)
    assert loads.water == pytest.approx(98.1)


def test_schema_2_round_trip_and_1_0_migration(tmp_path):
    p = generate_template("bungalow")
    p.plans[1].walls.append(Wall(mark="W9", x1=0, y1=0, x2=2, y2=0))
    p.grids.append({"name": "A", "axis": "x", "pos": 0.0})
    path = project_io.save_project(p, str(tmp_path / "x.pwai"))
    q = project_io.load_project(path)
    assert q.plans[1].walls[0].mark == "W9" and q.grids[0]["name"] == "A" and q.schema == 2
    old = p.to_dict()
    old["schema"] = 1
    old["seismic"].pop("method")
    old["seismic"].pop("rigid_diaphragm")
    q1 = project_io.project_from_json(__import__("json").dumps(old))
    assert q1.seismic.method == "static" and q1.seismic.rigid_diaphragm is False
    assert Project().seismic.method == "auto" and Project().seismic.rigid_diaphragm is True


def test_export_registry_lists_every_format(tmp_path):
    from planwin_ai.ai.actions import Session, execute
    from planwin_ai.services import exports

    assert {"staad", "etabs", "dxf", "dxf3d", "excel", "pdf", "calc", "bbs", "details", "project"} <= set(
        exports.keys()
    )
    s = Session(build_template("bungalow"), out_dir=str(tmp_path))
    r = execute(s, [{"action": "export", "format": f} for f in ("bbs", "details", "staad", "etabs")])
    assert not r.errors, r.errors
    assert len(r.files) == 4


def test_staad_and_etabs_export_diaphragms_walls_and_rs(tmp_path):
    from planwin_ai.io.etabs import write_etabs
    from planwin_ai.io.staad import write_staad

    p = _g4(True, "response_spectrum")
    for pl in p.plans:
        pl.walls.append(Wall(mark="W1", x1=4.5, y1=4.0, x2=8.5, y2=4.0, thickness=0.23))
    fm = FrameModel(p).build()
    fa = fm.analyze()
    nodal, groups = fm.export_model()
    assert sum(v[0] for v in nodal["EQX"].values()) == pytest.approx(fa.rs["RSX"].storey_shear[0], rel=1e-6)
    std = open(write_staad(fm, str(tmp_path / "a.std")), encoding="ascii").read()
    e2k = open(write_etabs(fm, str(tmp_path / "a.e2k")), encoding="utf-8").read()
    assert std.count("SLAVE ZX MASTER") == len(groups) and "response spectrum" in std
    assert 'DIAPHRAGM "D1"' in e2k and "response spectrum" in e2k
    # masters (virtual centre-of-mass joints) are never exported as points
    masters = {lv.master for lv in fm.levels if lv.master}
    assert all(f"CM {lv.name}" not in std for lv in fm.levels) and masters
