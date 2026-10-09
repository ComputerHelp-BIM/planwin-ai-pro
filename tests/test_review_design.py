"""Design review (v1.2.0): regression tests for defects found in ``planwin_ai.design``.

Every test states the IS 456:2000 / IS 13920:2016 clause it checks and is written as an
independent hand calculation, not by re-running the code under test.
"""

import math
import re

import pytest

from planwin_ai.ai.templates import build_template
from planwin_ai.design import is456
from planwin_ai.design.runner import run_full
from planwin_ai.design.wizards import Staircase, apply_staircase


# ------------------------------------------------------------------ torsion (cl 41)
def test_torsion_stirrups_use_corner_bar_centres():
    """cl 41.4.3: Asv = Tu sv/(b1 d1 0.87 fy) + Vu sv/(2.5 d1 0.87 fy), with b1/d1 the
    centre-to-centre distances of the corner bars.  Clear cover 25 to T8 stirrups, T16 bars:
    b1 = 300 - 2(25 + 8) - 16 = 218 mm, d1 = 600 - 2(25 + 8) - 16 = 518 mm.

    Tu = 29 kN·m, Vu = 60 kN, fy 415:
      Tu/(b1 d1 0.87fy) = 29e6/(218·518·361.05) = 0.7113 mm²/mm
      Vu/(2.5 d1 0.87fy) = 60e3/(2.5·518·361.05) = 0.1283 mm²/mm      -> 0.8396 mm²/mm
      (τve − τc) b/(0.87fy) with τve = (60 + 1.6·29/0.3)·1e3/(300·559) = 1.280, τc(1 %) = 0.64
        -> 0.532 mm²/mm (does not govern)
      2-legged T8 (100.5 mm²) -> sv = 119.7 mm -> 100 mm (25 mm steps, rounded down).
    Using b1 = x1 − 8 − 16 (one stirrup diameter too few) gave 125 mm (unsafe)."""
    b1, d1 = 300 - 2 * (25 + 8) - 16, 600 - 2 * (25 + 8) - 16
    per_mm = 29e6 / (b1 * d1 * 0.87 * 415) + 60e3 / (2.5 * d1 * 0.87 * 415)
    s = 2 * math.pi * 8**2 / 4 / per_mm
    assert s == pytest.approx(119.7, abs=0.2)
    r = is456.torsion_design(29, 60, 100, 150, 300, 600, 25, 25, 415, 415, 1.0, 16)
    assert r.ok and r.dia == 8
    assert r.spacing == 100.0


def test_torsion_side_face_bars_provide_the_required_area():
    """cl 26.5.1.7(b) / 26.5.1.3: side-face bars of 0.1 % of the web area, half on each face.
    1000 x 600 (cover 25): 0.001·1000·550/2 = 275 mm² per face; with 2 bars per face each bar
    needs 137.5 mm² – a T12 (113 mm²) is not enough."""
    r = is456.torsion_design(20, 50, 100, 100, 1000, 600, 25, 25, 500, 500, 0.5, 16)
    m = re.match(r"(\d+)-T(\d+) each face", r.side_face)
    assert m, r.side_face
    n, dia = int(m.group(1)), int(m.group(2))
    assert n * math.pi * dia**2 / 4 >= 0.001 * 1000 * (600 - 2 * 25) / 2


# ------------------------------------------------------------------ combined footing
def _combined(P, Pu, s, a, fck=25, fy=500, cover=0.05, sbc=200.0):
    r = is456.design_combined_footing(P, P, Pu, Pu, s, (a, a), (a, a), sbc, fck, fy, cover)
    return r, r.D - cover - 0.016


@pytest.mark.parametrize("P,s", [(400, 3.0), (400, 5.0), (800, 4.0), (1500, 5.0)])
def test_combined_footing_one_way_shear_uses_the_steel_provided(P, s):
    """cl 34.2.4.1(a) / Table 19: one-way shear at d from the column faces with τc for the
    tension steel actually provided at that section (bottom in the cantilevers, top between
    the columns) – not a fixed pt = 0.25 % (unconservative when only 0.12 % is provided).
    Symmetric footing: w = 2Pu/L per metre; V(x) = w x − Pu (x past column 1)."""
    a, Pu = 0.4, 1.5 * P
    r, d = _combined(P, Pu, s, a)
    L, B, x1 = r.L, r.B, r.x_start
    w = 2 * Pu / L
    for x in (x1 - a / 2 - d, x1 + a / 2 + d):
        if not 0 < x < L:
            continue
        V = w * x - (Pu if x > x1 else 0.0)
        M = w * x * x / 2 - (Pu * (x - x1) if x > x1 else 0.0)
        mesh = r.bottom if M >= 0 else r.top
        pt = 100 * mesh.area_per_m / (1000 * d * 1000)
        tv = abs(V) / (B * d) / 1e3
        assert tv <= is456.tau_c(pt, 25) + 1e-9, (x, tv, pt)


@pytest.mark.parametrize("P,s,a", [(1200, 2.5, 0.3), (2000, 2.5, 0.23), (2000, 2.5, 0.3), (2000, 3.5, 0.4)])
def test_combined_footing_transverse_steel_is_adequate_or_reported(P, s, a):
    """SP 34 band method: each column load is spread across the width B over a band of
    width c + 2d; moment per metre of band = Pu/(B·band)·((B − c)/2)²/2.  The bar mesh must
    provide that steel (and 0.12 %), otherwise the footing must not be reported as OK."""
    Pu = 1.5 * P
    r, d = _combined(P, Pu, s, a)
    band = a + 2 * d
    m = Pu / (r.B * band) * ((r.B - a) / 2) ** 2 / 2
    req = max(is456.ast_singly(m * 1e6, 25, 500, 1000, d * 1000), 1.2 * r.D * 1000)
    for t in r.transverse:
        mm = re.search(r"T(\d+) @ (\d+) c/c(?! \(check\))", t)
        prov = 1000 * math.pi * int(mm.group(1)) ** 2 / 4 / int(mm.group(2)) if mm else 0.0
        assert prov >= req - 1e-6 or not r.ok, (t, req, r)


def test_combined_footing_close_columns_never_ok_with_an_impossible_section():
    """Columns 0.9 m apart: the 7.9 m wide strip cannot carry the transverse cantilever
    moment at the depth that suits the longitudinal design – the result must say so."""
    r = is456.design_combined_footing(1500, 1500, 2250, 2250, 0.9, (0.6, 0.6), (0.6, 0.6), 200, 25, 500)
    assert all("None" not in t for t in r.transverse) or not r.ok


@pytest.mark.parametrize("P,s,a", [(2000, 2.5, 0.3), (2000, 3.5, 0.4), (2500, 4.0, 0.5), (1500, 3.0, 0.6)])
def test_combined_footing_punching_perimeter_stops_at_the_free_edge(P, s, a):
    """cl 31.6.1 (Fig 13, free edge): the critical perimeter at d/2 from the column only
    counts where there is concrete.  With the minimum 0.3 m overhang and d/2 > 0.3 m the
    outer side of column 1 lies beyond the footing edge: three sides remain and the
    upward pressure outside the footing must not be deducted.  Overlapping perimeters of
    close columns are checked as one section around both."""
    Pu = 1.5 * P
    r, d = _combined(P, Pu, s, a)
    L, B, x1, x2 = r.L, r.B, r.x_start, r.x_start + s
    qu = 2 * Pu / (L * B)
    tp = min(0.5 + 1.0, 1.0) * 0.25 * math.sqrt(25)
    rects = [(x1 - (a + d) / 2, x1 + (a + d) / 2, Pu), (x2 - (a + d) / 2, x2 + (a + d) / 2, Pu)]
    if rects[0][1] >= rects[1][0]:  # perimeters overlap -> one section round both columns
        rects = [(rects[0][0], rects[1][1], 2 * Pu)]
    w = min(a + d, B)
    for lo, hi, P_ in rects:
        lo_c, hi_c = max(lo, 0.0), min(hi, L)
        bo = (2 * (hi_c - lo_c) if a + d < B else 0.0) + w * ((lo > 0) + (hi < L))
        Vp = P_ - qu * (hi_c - lo_c) * w
        if bo > 0:
            assert Vp * 1e3 / (bo * 1e3 * d * 1e3) <= tp + 1e-9, (lo, hi, bo, Vp, r)


# ------------------------------------------------------------------ isolated footing
def test_footing_uplift_case_does_not_inflate_the_plan_size():
    """A service case with net uplift (P < 0) can never give full contact however large the
    footing is.  It must be reported (uplift note, not OK) without growing the footing by the
    80 enlargement steps (10 m x 10 m pads)."""
    base = is456.design_footing(1000, 0.3, 0.45, 200, 25, 500, lateral=[(1200, 50, 20)])
    up = is456.design_footing(1000, 0.3, 0.45, 200, 25, 500, lateral=[(-50, 100, 0), (1200, 50, 20)])
    assert (up.L, up.B) == (base.L, base.B)
    assert not up.ok
    assert any("uplift" in n for n in up.notes)


# ------------------------------------------------------------------ walls (IS 13920 cl 10)
def test_wall_vertical_steel_is_provided_or_the_wall_fails():
    """150 mm wall, single curtain, bars ≤ t/10 = 15 mm (cl 10.1.8) at ≥ 100 mm: at most
    T12 @ 100 = 1131 mm²/m = 0.75 %.  When P–M needs ρv ≈ 1.5 % the wall cannot be
    reported OK with T12 @ 100 c/c."""
    r = is456.design_wall([("c", 500.0, 1500.0, 100.0)], 0.15, 2.0, 25, 500, 500, False)
    m = re.match(r"T(\d+) @ (\d+) c/c", r.vertical)
    prov = 1000 * math.pi * int(m.group(1)) ** 2 / 4 / int(m.group(2)) * r.curtains / (150 * 1000)
    assert prov >= r.rho_v - 1e-9 or not r.ok


# ------------------------------------------------------------------ ductile schedules
@pytest.fixture(scope="module")
def bungalow_rep():
    p = build_template("bungalow")
    fm, fa, rep = run_full(p)
    return p, rep


@pytest.mark.xfail(
    strict=True,
    reason="known issue: ColumnDesign/BeamDesign hoop spacings ignore the IS 13920 capacity-shear "
    "spacing computed later by is13920.check_ductility; fixing it needs the calc sheets (io/) to "
    "mirror the change – reported, not fixed in this review",
)
def test_ductile_schedules_use_the_is13920_check_spacing(bungalow_rep):
    """The column/beam schedules (ColumnDesign.tie_confined/tie, BeamDesign.links_end/links)
    feed the BBS, drawings and BOQ; they must not show a wider hoop spacing than the
    IS 13920 check requires (cl 7.5 capacity shear within l0 and cl 6.3.5 end zones)."""
    _, rep = bungalow_rep
    cols = {c.member_id: c for c in rep.columns}
    beams = {b.member_id: b for b in rep.beams}
    n = 0
    for dc in rep.ductile:
        txt = " | ".join(dc.detailing)
        if dc.kind == "column":
            c = cols[dc.member_id]
            s_l0 = float(re.search(r"@ (\d+) c/c within l0", txt).group(1))
            s_out = float(re.search(r"@ (\d+) c/c over the rest", txt).group(1))
            assert c.tie_confined.spacing <= s_l0 + 1e-6, (c.mark, c.level, c.tie_confined, s_l0)
            assert c.tie.spacing <= s_out + 1e-6, (c.mark, c.level, c.tie, s_out)
            assert f"@ {int(c.tie_confined.spacing)} over l0" in c.ties
            n += 1
        elif dc.kind == "beam" and dc.checks and dc.checks[0][0] != "IS 456 design":
            b = beams[dc.member_id]
            me = re.search(r"@ (\d+) c/c over 2d", txt)
            if me and b.links_end is not None:
                assert b.links_end.spacing <= float(me.group(1)) + 1e-6, (b.mark, b.level, b.links_end, txt)
                assert b.stirrups.startswith(str(b.links_end))
            mm = re.search(r"@ (\d+) c/c elsewhere", txt)
            if mm:
                assert b.links.spacing <= float(mm.group(1)) + 1e-6, (b.mark, b.level, b.links, txt)
            n += 1
    assert n > 0


# ------------------------------------------------------------------ staircase wizard
def _stair_loads(project):
    return {
        (plan.name, b.mark): [pl for pl in b.part_loads if pl.desc.startswith("Stair ")]
        for plan in project.plans
        for b in plan.beams
    }


def test_staircase_moved_to_another_plan_leaves_no_loads_behind():
    """Re-applying a staircase (same name) on a different plan must replace – not add to –
    its previous loads (wizard contract: editing and re-applying never double-counts)."""
    p = build_template("bungalow")
    st = Staircase(name="ST1", plan="Typical", support_beams=["B11", "B12"])
    apply_staircase(p, st)
    st2 = Staircase(name="ST1", plan="Roof", support_beams=["B11", "B12"])
    apply_staircase(p, st2)
    loads = _stair_loads(p)
    assert not any(v for (plan, _), v in loads.items() if plan == "Typical")
    assert sum(len(v) for (plan, _), v in loads.items() if plan == "Roof") == 4


def test_staircase_that_does_not_fit_leaves_the_project_unchanged():
    """A ValueError on the second support beam must not leave half-applied loads (or remove
    the previous ones) – the caller's project is unchanged."""
    p = build_template("bungalow")
    apply_staircase(p, Staircase(name="ST1", plan="Typical", support_beams=["B11", "B12"]))
    before = {k: list(v) for k, v in _stair_loads(p).items()}
    stairs_before = list(p.stairs)
    # B11 is 4.0 m long (flight fits from 3.8 m), B4 only 3.5 m -> fails on the second beam
    lengths = {b.mark: b.length for b in p.plan("Typical").beams}
    assert lengths["B11"] == pytest.approx(4.0) and lengths["B4"] == pytest.approx(3.5)
    bad = Staircase(name="ST1", plan="Typical", support_beams=["B11", "B4"], start=3.8)
    with pytest.raises(ValueError):
        apply_staircase(p, bad)
    assert _stair_loads(p) == before
    assert p.stairs == stairs_before


# ------------------------------------------------------------------ slabs (cl 22 / Table 12)
def _slab_result(p, rep, plan, mark):
    return next(r for pn, r in rep.slabs if pn == plan and r.mark == mark)


def test_one_way_long_slab_is_designed_for_its_long_span():
    """A slab set to 'one_way_long' spans the long way (PlanEngine sends its load to the short
    edges, span = ly).  Its bending moment must use ly, not lx: S1 is 4.0 x 3.5 m, so the
    one-way moment coefficients (Table 12) apply to 4.0² – (4.0/3.5)² = 1.306 times the
    'one_way' (short span) value."""
    p = build_template("bungalow")
    s1 = next(s for s in p.plan("Typical").slabs if s.mark == "S1")
    s1.distribution = "one_way"
    short = _slab_result(p, run_full(p)[2], "Typical", "S1")
    s1.distribution = "one_way_long"
    long_ = _slab_result(p, run_full(p)[2], "Typical", "S1")
    assert (short.lx, short.ly) == (pytest.approx(3.5), pytest.approx(4.0))
    assert long_.Mx_pos == pytest.approx(short.Mx_pos * (4.0 / 3.5) ** 2, rel=1e-9)
    assert long_.M_neg == pytest.approx(short.M_neg * (4.0 / 3.5) ** 2, rel=1e-9)


def test_cantilever_slab_span_is_its_projection_from_the_fixed_edge():
    """Cantilever slab 1.2 m wide projecting 2.0 m from its fixed edge (cant_edge on the
    1.2 m side): Mu = wu l²/2 with l = 2.0 m (projection = area / fixed-edge length, as the
    PlanEngine loads the beam), not the smaller plan dimension 1.2 m."""
    p = build_template("bungalow")
    s5 = next(s for s in p.plan("Typical").slabs if s.mark == "S5")
    s5.points = [[0.0, -2.0], [1.2, -2.0], [1.2, 0.0], [0.0, 0.0]]
    s5.cant_edge = 2  # (1.2, 0) -> (0, 0): along the beam at y = 0
    res = _slab_result(p, run_full(p)[2], "Typical", "S5")
    wu = 1.5 * (s5.dead + s5.live_load)
    assert res.kind == "cantilever"
    assert res.Mx_pos == pytest.approx(wu * 2.0**2 / 2, rel=1e-9)
