"""Geometry, PlanWin load transfer and 3-D solver tests (closed-form checks)."""

import math

import numpy as np
import pytest

from planwin_ai.core import geometry as G
from planwin_ai.core.beamcalc import LinLoad, PtLoad, continuous_beam, diagrams, simple_span_reactions
from planwin_ai.core.model import Beam, Column, Plan, Slab
from planwin_ai.core.plan_engine import PlanEngine, auto_beams, auto_columns, slab_edge_profiles, profile_integral
from planwin_ai.core.solver import FMember, FNode, FrameSolver, MLoad, MPoint, section_forces


# ----------------------------------------------------------------- geometry
def test_polygon_area_centroid():
    sq = [(0, 0), (4, 0), (4, 3), (0, 3)]
    assert G.polygon_area(sq) == pytest.approx(12)
    assert G.polygon_centroid(sq) == pytest.approx((2, 1.5))
    assert G.is_rectangle(sq)
    assert not G.is_rectangle([(0, 0), (4, 0), (5, 3), (0, 3)])


def test_segment_tools():
    hit = G.segment_intersection((0, 0), (4, 0), (2, -1), (2, 1))
    assert hit[0] == pytest.approx((2, 0))
    assert G.collinear_overlap((0, 0), (10, 0), (2, 0), (5, 0)) == pytest.approx((0.2, 0.5))
    assert G.collinear_overlap((0, 0), (10, 0), (2, 1), (5, 1)) is None


# ----------------------------------------------------------------- slab distribution
@pytest.mark.parametrize("lx,ly", [(4, 4), (4, 5), (3, 7), (5, 3)])
def test_rectangular_profiles_integrate_to_area(lx, ly):
    s = Slab(points=[[0, 0], [lx, 0], [lx, ly], [0, ly]])
    prof, method, _ = slab_edge_profiles(s)
    assert sum(profile_integral(p) for p in prof) == pytest.approx(lx * ly, rel=1e-9)


def test_two_way_trapezoid_shape():
    s = Slab(points=[[0, 0], [4, 0], [4, 6], [0, 6]])
    prof, method, _ = slab_edge_profiles(s)
    assert "two-way" in method
    # long edge (index 1, length 6): trapezoid with plateau lx/2 = 2
    assert max(w for _, w in prof[1]) == pytest.approx(2.0)
    assert profile_integral(prof[1]) == pytest.approx(2 * (6 - 2))
    assert profile_integral(prof[0]) == pytest.approx(4 * 4 / 4)


def test_one_way_and_cantilever():
    s = Slab(points=[[0, 0], [9, 0], [9, 3], [0, 3]])  # ratio 3 -> one way
    prof, method, _ = slab_edge_profiles(s)
    assert "one-way" in method
    assert profile_integral(prof[0]) == pytest.approx(9 * 1.5)
    assert profile_integral(prof[1]) == pytest.approx(0)
    c = Slab(points=[[0, 0], [4, 0], [4, 1.5], [0, 1.5]], distribution="cantilever", cant_edge=2)
    prof, _, _ = slab_edge_profiles(c)
    assert profile_integral(prof[2]) == pytest.approx(6.0)


def test_irregular_slab_conserves_load():
    s = Slab(points=[[0, 0], [5, 0], [6, 3], [2, 5], [-1, 3]])
    prof, method, _ = slab_edge_profiles(s)
    assert "CG" in method
    assert sum(profile_integral(p) for p in prof) == pytest.approx(s.area, rel=1e-9)


# ----------------------------------------------------------------- beam statics
def test_simple_span_reactions_with_overhang():
    loads = [LinLoad(0, 6, 10, 10, "D"), PtLoad(7, 20, "D")]
    R = simple_span_reactions(7.0, [0.0, 6.0], loads, "D")
    assert sum(R) == pytest.approx(80)
    # moments about left support: 60*3 + 20*7 = 320 -> Rb = 53.33
    assert R[1] == pytest.approx(320 / 6)


def test_continuous_two_span_udl():
    # classic: 2 equal spans, UDL -> middle reaction 1.25 wL
    R = continuous_beam(8.0, [0, 4, 8], [LinLoad(0, 8, 10, 10, "D")], "D")
    assert R[1] == pytest.approx(1.25 * 10 * 4, rel=1e-3)
    assert R[0] == pytest.approx(0.375 * 10 * 4, rel=1e-3)


def test_diagram_simply_supported():
    d = diagrams(6.0, [LinLoad(0, 6, 10, 10)], [(0, 30), (6, 30)])
    assert float(np.interp(3.0, d["x"], d["M"])) == pytest.approx(45.0, rel=1e-3)


# ----------------------------------------------------------------- plan engine
def _two_by_one_plan():
    p = Plan(name="T", floor_height_above=3.0)
    p.slabs = [Slab(mark="S1", points=[[0, 0], [5, 0], [5, 4], [0, 4]], live=3, floor_finish=1, other=0),
               Slab(mark="S2", points=[[5, 0], [10, 0], [10, 4], [5, 4]], live=3, floor_finish=1, other=0)]
    return p


def test_plan_equilibrium_with_auto_tools():
    p = _two_by_one_plan()
    auto_columns(p)
    assert len(p.columns) == 6
    auto_beams(p)
    assert len(p.beams) == 7
    r = PlanEngine(p).run()
    assert not r.errors
    assert r.imbalance_pct == pytest.approx(0, abs=1e-9)
    assert r.applied["L"] == pytest.approx(3 * 40)


def test_secondary_beam_transfers_to_main_beams():
    p = _two_by_one_plan()
    p.columns = [Column(mark=f"C{i}", x=x, y=y) for i, (x, y) in enumerate([(0, 0), (10, 0), (0, 4), (10, 4)], 1)]
    auto_beams(p)
    r = PlanEngine(p).run()
    assert not r.errors, [e.message for e in r.errors]
    mid = next(b for b in p.beams if abs(b.x1 - 5) < 1e-6 and abs(b.x2 - 5) < 1e-6)
    sup = r.beams[mid.id].supports
    assert all(s.kind == "beam" for s in sup) and len(sup) == 2
    assert r.imbalance_pct == pytest.approx(0, abs=1e-9)


def test_unsupported_beam_end_is_reported():
    p = Plan()
    p.columns = [Column(mark="C1", x=0, y=0)]
    p.beams = [Beam(mark="B1", x1=0, y1=0, x2=3, y2=0)]
    r = PlanEngine(p).run()
    assert any("not supported" in e.message for e in r.errors)
    p.beams[0].cantilever = True
    r = PlanEngine(p).run()
    assert not r.errors
    assert r.columns[p.columns[0].id].dead == pytest.approx(p.beams[0].udl_dead(3.0, "typical") * 3)


def test_face_aligned_column_supports_beam():
    p = Plan()
    p.columns = [Column(mark="C1", x=0, y=0), Column(mark="C2", x=5, y=-0.11, b=0.23, d=0.45)]
    p.beams = [Beam(mark="B1", x1=0, y1=0, x2=5, y2=0)]
    r = PlanEngine(p).run()
    assert not r.errors


# ----------------------------------------------------------------- 3-D solver
E = 25e6


def test_fixed_beam_udl():
    nodes = {1: FNode(1, 0, 0, 3, support="fixed"), 2: FNode(2, 6, 0, 3, support="fixed")}
    m = FMember(1, 1, 2, "beam", 0.3, 0.5, E)
    m.loads = {"D": [MLoad(0, 6, (0, 0, -10), (0, 0, -10))]}
    r = FrameSolver(nodes, {1: m}).solve(["D"])
    sf = section_forces(m, nodes, r.end_forces["D"][1], m.loads["D"], np.array([0, 3, 6.0]))
    assert -sf["My"] == pytest.approx([-30, 15, -30], abs=1e-6)


def test_simply_supported_point_and_udl():
    nodes = {1: FNode(1, 0, 0, 0, support="pinned"), 2: FNode(2, 6, 0, 0, support="pinned")}
    m = FMember(1, 1, 2, "beam", 0.3, 0.5, E)
    m.loads = {"D": [MLoad(0, 6, (0, 0, -10), (0, 0, -10)), MPoint(2, (0, 0, -30))]}
    r = FrameSolver(nodes, {1: m}).solve(["D"])
    sf = section_forces(m, nodes, r.end_forces["D"][1], m.loads["D"], np.array([2.0]))
    assert -sf["My"][0] == pytest.approx(80, abs=1e-6)
    assert r.reactions["D"][1][2] == pytest.approx(50)


@pytest.mark.parametrize("angle,inertia", [(0, 0.6 * 0.3 ** 3 / 12), (90, 0.3 * 0.6 ** 3 / 12)])
def test_cantilever_column_orientation(angle, inertia):
    nodes = {1: FNode(1, 0, 0, 0, support="fixed"), 2: FNode(2, 0, 0, 3)}
    c = FMember(1, 1, 2, "column", 0.3, 0.6, E, angle=angle)
    r = FrameSolver(nodes, {1: c}, {"H": {2: np.array([10.0, 0, 0, 0, 0, 0])}}).solve(["H"])
    assert r.disp["H"][1][0] == pytest.approx(10 * 27 / (3 * E * inertia), rel=1e-6)


def test_portal_frame_lateral_equilibrium():
    nodes = {1: FNode(1, 0, 0, 0, support="fixed"), 2: FNode(2, 6, 0, 0, support="fixed"),
             3: FNode(3, 0, 0, 3), 4: FNode(4, 6, 0, 3)}
    mem = {1: FMember(1, 1, 3, "column", 0.3, 0.3, E), 2: FMember(2, 2, 4, "column", 0.3, 0.3, E),
           3: FMember(3, 3, 4, "beam", 0.3, 0.5, E)}
    r = FrameSolver(nodes, mem, {"H": {3: np.array([50.0, 0, 0, 0, 0, 0])}}).solve(["H"])
    assert sum(v[0] for v in r.reactions["H"].values()) == pytest.approx(-50)
    # symmetric columns share the shear almost equally
    assert r.reactions["H"][1][0] == pytest.approx(r.reactions["H"][2][0], rel=0.05)
