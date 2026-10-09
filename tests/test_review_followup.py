"""Design-review follow-up: the calculation sheets mirror the design fixes, and the ductile
hoop spacings the design report hands to the BBS / drawings / schedules are the governing
IS 456 + IS 13920 values.

Hand calculations are independent of the code under test (IS 456:2000, IS 13920:2016)."""

import math
import re

import pytest

from planwin_ai.ai.templates import TEMPLATES, build_template
from planwin_ai.design import is456
from planwin_ai.design.runner import design_all, run_full
from planwin_ai.io import calc_sheets as cs


def _differs(sheets):
    return [(s.kind, s.ref, w) for s in sheets for w in s.warnings if "differs from the design report" in w]


# ------------------------------------------------------------------ every template
@pytest.mark.parametrize("key", [t.key for t in TEMPLATES])
def test_template_calc_sheets_agree_with_the_design_report(key):
    """Every beam (with torsion where the template has balconies), column (IS 13920 hoops),
    footing and slab sheet recomputes the same numbers as the design report.  The school
    template also has combined footings (they replace the isolated pads and have no sheet)."""
    p = build_template(key)
    fm, fa, rep = run_full(p)
    sheets = cs.build_sheets(p, fm, fa, rep)
    assert len(sheets) == len(rep.beams) + len(rep.columns) + len(rep.footings) + len(rep.slabs)
    assert _differs(sheets) == []


# ------------------------------------------------------------------ constructed members
@pytest.fixture(scope="module")
def modified_bungalow():
    """Bungalow with members the templates do not have: deep edge beams carrying the balcony
    torsion (B11 300 × 600, B12 600 × 600), a balcony projecting 2.0 m from its fixed edge and
    a slab spanning the long way."""
    p = build_template("bungalow")
    plan = p.plan("Typical")
    beams = {b.mark: b for b in plan.beams}
    beams["B11"].b, beams["B11"].d = 0.30, 0.60
    beams["B12"].b, beams["B12"].d = 0.60, 0.60
    slabs = {s.mark: s for s in plan.slabs}
    slabs["S5"].points = [[0.0, -2.0], [1.2, -2.0], [1.2, 0.0], [0.0, 0.0]]
    slabs["S5"].cant_edge = 2  # (1.2, 0) -> (0, 0): along B11
    slabs["S1"].distribution = "one_way_long"
    fm, fa, rep = run_full(p)
    return p, fm, fa, rep


def test_torsion_beam_sheets_use_corner_bar_centres_and_side_face_area(modified_bungalow):
    """cl 41.4.3: b1 = x1 − 2·8 − φ (corner-bar centres inside T8 stirrups on both faces);
    cl 26.5.1.3/26.5.1.7(b): 600 × 600, cover 25: 0.001·600·550/2 = 165 mm² per face, 2 bars
    (≤ 300 mm apart) need 82.5 mm² each – T12, not T10 (78.5 mm²)."""
    p, fm, fa, rep = modified_bungalow
    tors = [b for b in rep.beams if b.T_max > 1.0 and b.d > 0.45]
    assert {b.mark for b in tors} == {"B11", "B12"}
    cover = p.design.beam_cover * 1000
    for bd in tors:
        sh = cs.beam_sheet(p, fa, bd)
        assert _differs([sh]) == [], (bd.mark, sh.warnings)
        dims = next(s for s in sh.steps if s.title == "Closed stirrup dimensions")
        x1 = bd.b * 1000 - 2 * cover
        phi = int(re.search(r"φ = (\d+)", dims.subst).group(1))
        assert f"b1 = {x1 - 16 - phi:.0f}," in dims.result
        if bd.mark == "B12":
            a_face = 0.001 * 600 * (600 - 2 * cover) / 2
            assert a_face / 2 > math.pi * 10**2 / 4
            assert bd.side_face.startswith("2-T12 each face")


def test_one_way_long_and_cantilever_slab_sheets_use_the_design_span(modified_bungalow):
    """S1 (4.0 × 3.5 m, 'one_way_long') spans 4.0 m; balcony S5 projects 2.0 m from B11:
    Mu = wu l²/2 = 1.5 (gk + qk) 2.0²/2."""
    p, fm, fa, rep = modified_bungalow
    sheets = {s.ref: s for s in cs.build_sheets(p, fm, fa, rep, member_ids=[], footing_marks=[])}
    s1, s5 = sheets["S1 (Typical)"], sheets["S5 (Typical)"]
    assert _differs([s1, s5]) == []
    assert s1.value("span") == pytest.approx(4.0)
    assert s5.value("span") == pytest.approx(2.0)
    slab5 = next(s for s in p.plan("Typical").slabs if s.mark == "S5")
    assert s5.value("Mx") == pytest.approx(1.5 * (slab5.dead + slab5.live_load) * 2.0**2 / 2)
    assert _differs(list(sheets.values())) == []


# ------------------------------------------------------------------ footings
def _footing_with_wind(dP, M):
    """Bungalow with the WLX reaction of the first footing's column replaced by an uplift
    ``dP`` (kN) and moment ``M`` (kN·m about the footing's L direction) – the analysis is not
    rerun, the design is."""
    p = build_template("bungalow")
    fm, fa, rep = run_full(p)
    m = fa.model
    mark = rep.footings[0].mark
    col = next(mem for mem in m.members.values() if mem.mark == mark and m.nodes[mem.n1].support)
    assert abs(math.sin(math.radians(col.angle))) < 0.7  # Mz-about-y varies the pressure along L
    r = fa.res.reactions["WLX"][col.n1]
    r[2], r[4] = dP, M
    rep = design_all(fa, p)
    return p, fa, rep, next(f for f in rep.footings if f.mark == mark), col.n1


def test_footing_sheet_uses_the_no_tension_peak_pressure():
    """0.9DL + 1.5WLX puts the factored resultant outside the kern and governs: the sheet's
    qu is the triangular-block value 2P/(3B(L/2 − e)) (soil takes no tension), as the design."""
    p, fa, rep, fd, nid = _footing_with_wind(-60.0, -60.0)
    gov = max(fa.ultimate, key=lambda c: is456.peak_pressure(*_pm(fa, nid, c), fd.L, fd.B))
    P, ML, MB = _pm(fa, nid, gov)
    e = ML / P
    assert 6 * e / fd.L + 6 * MB / P / fd.B > 1  # outside the kern
    sh = cs.footing_sheet(p, fa, fd)
    assert _differs([sh]) == [], sh.warnings
    if MB < 1e-9 * P * fd.B:
        assert sh.value("qu") == pytest.approx(2 * P / (3 * fd.B * (fd.L / 2 - e)), rel=1e-6)
    assert sh.value("qu") > P / (fd.L * fd.B) * (1 + 6 * e / fd.L + 6 * MB / P / fd.B) - 1e-6
    assert "outside the kern" in sh.step("qu").formula


def _pm(fa, nid, combo):
    r = fa.reaction(nid, combo.factors)
    return float(r[2]), abs(float(r[4])), abs(float(r[3]))


@pytest.mark.parametrize("dP,M,what", [(-80.0, -40.0, "overturning"), (-300.0, -10.0, "uplift")])
def test_footing_sheet_reports_overturning_and_uplift_like_the_design(dP, M, what):
    """A factored resultant outside the base (overturning) or a service case with net uplift
    fails the footing in the design; the sheet shows the failing check, never a PASS."""
    p, fa, rep, fd, _ = _footing_with_wind(dP, M)
    assert not fd.ok and any(what in n for n in fd.notes)
    sh = cs.footing_sheet(p, fa, fd)
    assert _differs([sh]) == [], sh.warnings
    assert not sh.ok
    label = {"overturning": "Overturning", "uplift": "No net uplift"}[what]
    assert any(c.label == label and not c.ok for c in sh.checks)


# ------------------------------------------------------------------ IS 13920 hoops
def test_column_confinement_uses_the_hoop_yield_strength_without_the_shear_cap():
    """IS 13920:2016 cl 8.1(b): Ash = max(0.18 s h fck/fy (Ag/Ak − 1), 0.05 s h fck/fy) with fy
    of the hoop steel (Fe 500 here) – the 415 MPa cap of IS 456 cl 40.4 is a shear rule.  The
    design report, the calculation sheet and the IS 13920 check use the same spacing."""
    p = build_template("bungalow")
    fm, fa, rep = run_full(p)
    fy_h = p.design.fy_shear
    assert fy_h == 500.0
    checks = {d.member_id: d for d in rep.ductile if d.kind == "column"}
    for cd in rep.columns:
        sh = cs.column_sheet(p, fa, cd)
        assert _differs([sh]) == [], sh.warnings
        b, D = cd.b * 1000, cd.d * 1000
        c = p.design.column_cover * 1000
        Ag, Ak = b * D, (b - 2 * c) * (D - 2 * c)
        h = sh.value("h")
        dia = int(re.match(r"T(\d+)", sh.step("h").result).group(1))
        fck = float(re.search(r"fck = (\d+)", dict(sh.header)["Concrete"]).group(1))
        s_ash = math.pi * dia**2 / 4 / (h * fck / fy_h * max(0.18 * (Ag / Ak - 1), 0.05))
        assert sh.value("s_ash") == pytest.approx(s_ash, rel=1e-9)
        txt = " ".join(det for name, _ok, det in checks[cd.member_id].checks if name.startswith("8.1"))
        assert f"allows s ≤ {s_ash:.0f} mm" in txt
        assert cd.tie_confined.spacing <= min(s_ash, sh.value("s_limit")) + 1e-9


def test_reported_hoops_are_the_governing_spacing_and_pass_the_check(modified_bungalow):
    """The reported links/ties never exceed the IS 13920 frame-check spacing nor the IS 456
    design, are on a 5 mm grid and not below the practical minimum (50 mm beams, 75 mm
    columns); the check passes for those spacings and the sheets show the same values."""
    p, fm, fa, rep = modified_bungalow
    beams = {b.member_id: b for b in rep.beams}
    cols = {c.member_id: c for c in rep.columns}
    n = 0
    for dc in rep.ductile:
        if dc.kind == "beam" and beams[dc.member_id].links is not None:
            bd = beams[dc.member_id]
            assert bd.links.spacing <= dc.spacing["mid"] + 1e-9 or bd.links.spacing == 50
            assert bd.links.spacing % 5 == 0 and bd.links.spacing >= 50
            if "end" in dc.spacing and bd.links_end is not None:
                assert bd.links_end.spacing <= dc.spacing["end"] + 1e-9 or bd.links_end.spacing == 50
                assert bd.links_end.spacing % 5 == 0
            sh = cs.beam_sheet(p, fa, bd)
            assert sh.value("sv") == bd.links.spacing
            n += 1
        elif dc.kind == "column":
            cd = cols[dc.member_id]
            assert cd.tie_confined.spacing <= dc.spacing["l0"] + 1e-9 or cd.tie_confined.spacing == 75
            assert cd.tie.spacing <= dc.spacing["out"] + 1e-9
            assert cd.tie_confined.spacing % 5 == 0 and cd.tie_confined.spacing >= 75
            assert cd.tie.legs >= max(dc.confinement.legs_b, dc.confinement.legs_d)
            sh = cs.column_sheet(p, fa, cd)
            assert sh.value("s_confined") == cd.tie_confined.spacing
            assert sh.value("tie_spacing_out") == cd.tie.spacing
            n += 1
        if dc.kind in ("beam", "column"):
            for name, ok, det in dc.checks:
                if name.startswith(("6.3.5", "8.2", "7.6.1")):
                    assert ok, (dc.mark, dc.level, name, det)
    assert n > 0
