"""IS 456 design routines, IS 1893 / IS 875 loads and full-building integration."""

import pytest

from planwin_ai.ai.templates import TEMPLATES, build_template
from planwin_ai.core.frame import FrameModel, is_combinations
from planwin_ai.core.lateral import design_pressure, k2, sa_by_g, seismic_static
from planwin_ai.design import is456
from planwin_ai.design.runner import optimize_sizes, run_full


# ----------------------------------------------------------------- IS 456
def test_singly_reinforced_textbook():
    # 230 x 450 effective, M20 Fe415, Mu = 100 kNm -> Ast ~ 720 mm2 (SP-16)
    assert is456.ast_singly(100e6, 20, 415, 230, 450) == pytest.approx(719.6, abs=1.0)


def test_mu_lim():
    assert is456.mu_lim(20, 415, 230, 450) / 1e6 == pytest.approx(0.138 * 20 * 230 * 450 ** 2 / 1e6, rel=0.01)


def test_tau_c_table():
    assert is456.tau_c(1.0, 20) == pytest.approx(0.62)
    assert is456.tau_c(0.15, 25) == pytest.approx(0.29)
    assert is456.tau_c(5.0, 40) == pytest.approx(1.01)


def test_doubly_reinforced_kicks_in():
    r = is456.flexure(400, 25, 500, 300, 500, 25)
    assert r.doubly and r.asc > 0


def test_interaction_curve_shape():
    b = D = 400
    m0 = is456.section_capacity(0.0, b, D, 0.02 * b * D, 25, 415, 40)
    m4 = is456.section_capacity(0.4 * 25 * b * D, b, D, 0.02 * b * D, 25, 415, 40)
    puz = 0.45 * 25 * b * D + 0.75 * 415 * 0.02 * b * D
    assert m0 > 0 and m4 > 0
    assert is456.section_capacity(puz * 1.01, b, D, 0.02 * b * D, 25, 415, 40) == 0.0
    # SP-16 chart region: ~0.09-0.12 for p/fck = 0.08, d'/D ~0.15
    assert 0.07 < m0 / (25 * b * D * D) < 0.13


def test_column_design_monotonic():
    light = is456.design_column([("c", 800, 30, 10)], 0.3, 0.45, 3.0, 25, 500)
    heavy = is456.design_column([("c", 2000, 120, 60)], 0.3, 0.45, 3.0, 25, 500)
    assert light.ok and light.steel_pct == pytest.approx(0.8)
    assert heavy.steel_pct > light.steel_pct


def test_footing_area_and_depth():
    f = is456.design_footing(1000, 0.3, 0.45, 200, 25, 500)
    assert f.L * f.B >= 1100 / 200 - 1e-6
    assert f.D >= 0.3 and f.ok


def test_slab_design_two_way():
    s = is456.design_slab("S1", 4, 5, 4.6, 2, 0.15, 25, 500, "two_way", 2)
    assert s.ok and s.Mx_pos > s.My_pos


# ----------------------------------------------------------------- loads
def test_sa_by_g_regions():
    assert sa_by_g(0.3, "medium") == 2.5
    assert sa_by_g(1.0, "medium") == pytest.approx(1.36)
    assert sa_by_g(1.0, "soft") == pytest.approx(1.67)
    assert sa_by_g(5.0, "hard") == pytest.approx(0.25)


def test_seismic_coefficient_hand_calc():
    r = seismic_static([0, 1000, 1000, 1000], [0, 2, 5, 8], 1, "III", 1.2, 5.0, "medium", 0.05, False, 10, "X")
    assert r.Ah == pytest.approx(0.16 / 2 * 1.2 / 5 * 2.5)
    assert r.Vb == pytest.approx(r.Ah * 2000)  # levels above base level 1
    assert sum(r.forces) == pytest.approx(r.Vb)


def test_wind_k2_and_pressure():
    assert k2(10, 3) == pytest.approx(0.91)
    assert k2(25, 2) == pytest.approx((1.07 + 1.12) / 2)
    pd = design_pressure(10, 44, 3)
    vz = 44 * 0.91
    assert pd == pytest.approx(max(0.9 * 0.9 * 0.6 * vz ** 2 / 1000, 0.7 * 0.6 * vz ** 2 / 1000))


def test_combinations_count():
    assert len([c for c in is_combinations(True, True) if c.kind == "ultimate"]) == 25
    assert len([c for c in is_combinations(True, True, True) if c.kind == "ultimate"]) == 37


def test_short_period_rule():
    r = seismic_static([0, 500, 500], [0, 0.5, 3.5], 1, "III", 1.0, 5.0, "medium", 0.05, True, 20, "X")
    assert r.T <= 0.1 and r.Ah == pytest.approx(0.08)


def test_shear_caps_and_fails():
    r = is456.shear(150, 25, 500, 230, 550, 1.0)
    assert r.ok and r.spacing <= 300
    bad = is456.shear(700, 25, 500, 300, 560, 2.0)  # tau_v 4.2 > tau_c,max
    assert not bad.ok


def test_tension_with_moment_needs_more_steel():
    t = is456.design_column([("t", -200, 150, 0)], 0.3, 0.45, 3.0, 25, 500)
    c = is456.design_column([("c", 1, 150, 0)], 0.3, 0.45, 3.0, 25, 500)
    assert t.steel_pct >= c.steel_pct


def test_footing_with_moment_is_larger():
    a = is456.design_footing(1000, 0.3, 0.45, 200, 25, 500)
    b = is456.design_footing(1000, 0.3, 0.45, 200, 25, 500, lateral=[(1100, 150, 30)])
    assert b.L * b.B >= a.L * a.B


# ----------------------------------------------------------------- integration
@pytest.mark.parametrize("key", [t.key for t in TEMPLATES])
def test_templates_analyse_in_equilibrium(key):
    prj = build_template(key)
    fm = FrameModel(prj).build()
    assert not [i for i in fm.issues if i.level == "error"]
    fa = fm.analyze()
    applied_D = sum(lv.result.applied["D"] for lv in fm.levels[1:])
    applied_D += sum(m.b * m.d * 25 * abs(fm.nodes[m.n2].z - fm.nodes[m.n1].z) for m in fm.members.values() if m.kind == "column")
    applied_D += sum(jl.get("fz", 0) for jl in prj.joint_loads)
    eq = fa.equilibrium()
    assert eq["DL"] == pytest.approx(applied_D, rel=1e-6)
    for c in ("EQX", "EQY", "WLX", "WLY"):
        assert abs(eq.get(c, 0.0)) < 1e-3  # no net vertical reaction from lateral cases


@pytest.mark.parametrize("key", ["bungalow", "residential_g4", "office_g5", "school", "industrial", "tutorial"])
def test_templates_design_passes(key):
    _, _, rep = run_full(build_template(key))
    assert rep.failures == 0
    assert all(d["ok"] for d in rep.drifts)
    assert 50 < rep.boq["steel_per_m3"] < 160


def test_beam_framing_into_offset_column_stays_connected():
    from planwin_ai.core.model import Beam, Column, Level, Plan, Project

    plan = Plan(name="P")
    plan.columns = [Column(mark="C1", x=0, y=0, b=0.23, d=0.6, angle=90), Column(mark="C2", x=6, y=0),
                    Column(mark="C3", x=0.3, y=5), Column(mark="C4", x=6, y=5)]
    plan.beams = [Beam(mark="B1", x1=0, y1=0, x2=6, y2=0), Beam(mark="B2", x1=0.3, y1=0, x2=0.3, y2=5),
                  Beam(mark="B3", x1=0.3, y1=5, x2=6, y2=5), Beam(mark="B4", x1=6, y1=0, x2=6, y2=5)]
    prj = Project(plans=[plan], levels=[Level("L1", "P", 3.0)])
    fm = FrameModel(prj).build()
    c1 = fm.levels[1].column_nodes["C1"]
    b2 = next(m for m in fm.members.values() if m.mark == "B2")
    assert c1 in (b2.n1, b2.n2)
    used = {n for m in fm.members.values() for n in (m.n1, m.n2)}
    assert set(fm.nodes) <= used  # no orphan nodes


def test_optimizer_reduces_failures():
    prj = build_template("bungalow")
    for lv in prj.levels:
        pass
    for mark in list({c.mark for c in prj.plans[1].columns}):
        for i in range(1, len(prj.levels) + 1):
            prj.set_column_size(mark, i, 0.23, 0.23, 0)
    _, _, rep0 = run_full(prj)
    log, rep = optimize_sizes(prj, max_iter=6)
    assert rep.failures <= rep0.failures
