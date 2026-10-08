"""IS 13920:2016 ductile detailing checks (hand-calculated values)."""

import dataclasses
import math

import pytest

from planwin_ai.ai.templates import build_template
from planwin_ai.core.model import Project
from planwin_ai.design import is456
from planwin_ai.design.is13920 import (
    DuctileCheck,
    beam_capacity_shear,
    beam_moment_capacity,
    check_ductility,
    column_confinement,
    confining_length,
    required,
    rho_min,
)
from planwin_ai.design.runner import run_full


@pytest.fixture(scope="module")
def bungalow():
    p = build_template("bungalow")
    fm, fa, rep = run_full(p)
    return p, fm, fa, rep, check_ductility(fa, p, rep)


def test_rho_min_m25_fe500():
    # cl 6.2.1: 0.24 * sqrt(25) / 500 = 0.0024 = 0.24 %
    assert rho_min(25, 500) == pytest.approx(0.0024)
    assert rho_min(20, 415) == pytest.approx(0.24 * math.sqrt(20) / 415)


def test_beam_moment_capacity_hand():
    ast, b, d, fck, fy = 3 * math.pi * 16**2 / 4, 230.0, 400.0, 25.0, 500.0  # 3-T16
    expected = 0.87 * fy * ast * d * (1 - ast * fy / (b * d * fck))
    assert beam_moment_capacity(ast, b, d, fck, fy) == pytest.approx(expected)
    # 0.87·500·603.2·400 = 104.96e6;  1 − 603.2·500/(230·400·25) = 0.8689  ->  91.2 kN·m
    assert expected / 1e6 == pytest.approx(91.2, abs=0.1)
    # over-reinforced: capped at Mu,lim = 0.133 fck b d² for Fe500
    assert beam_moment_capacity(5000.0, b, d, fck, fy) == pytest.approx(is456.mu_lim(fck, fy, b, d))
    assert beam_moment_capacity(0.0, b, d, fck, fy) == 0.0


def test_beam_capacity_shear_hand():
    # Vg,A = 50, Vg,B = 60 kN; Ms,A 80, Mh,A 120, Ms,B 90, Mh,B 110 kN·m; L0 = 4 m
    # sway right: 1.4 (80 + 110)/4 = 66.5 -> Vu,a = |50 - 66.5| = 16.5, Vu,b = 60 + 66.5 = 126.5
    # sway left:  1.4 (120 + 90)/4 = 73.5 -> Vu,a = 50 + 73.5 = 123.5, Vu,b = |60 - 73.5| = 13.5
    assert beam_capacity_shear(50, 60, 80, 120, 90, 110, 4.0) == pytest.approx(126.5)
    # symmetric case: 1.4 (80 + 120)/4 = 70 -> 50 + 70
    assert beam_capacity_shear(50, 50, 80, 120, 80, 120, 4.0) == pytest.approx(120.0)


def test_column_confining_length():
    # cl 8.1: l0 = max(larger dimension, clear height / 6, 450 mm)
    assert confining_length(450, 2600) == pytest.approx(450)  # 2600/6 = 433
    assert confining_length(450, 3300) == pytest.approx(550)
    assert confining_length(600, 3000) == pytest.approx(600)


def test_column_ash_300x450():
    """300 x 450, M25, Fe500 hoops, 40 mm clear cover to hoops, 6-T16 (3 bars on each long face)."""
    b, D, c, fck, fy = 300.0, 450.0, 40.0, 25.0, 500.0
    Ag, Ak = b * D, (b - 2 * c) * (D - 2 * c)  # 135000, 220 x 370 = 81400
    # core 370 > 300 mm -> one cross-tie across the depth: legs 2 (across b) x 3 (across D)
    h = max(220 / 1, 370 / 2)  # 220 mm
    per_mm = max(0.18 * h * fck / fy * (Ag / Ak - 1), 0.05 * h * fck / fy)  # 1.304 mm²/mm
    s_limit = min(300 / 4, 6 * 16, 100)  # 75 mm
    # T8 -> 38.5 mm, T10 -> 60.2 mm (< 75, rejected), T12 -> 86.7 mm  => T12 @ 75
    assert math.pi * 10**2 / 4 / per_mm < 75 < math.pi * 12**2 / 4 / per_mm
    conf = column_confinement(b, D, c, fck, fy, 16, n_bars=6, dia_min=8)
    assert conf.ok
    assert (conf.dia, conf.legs_b, conf.legs_d) == (12, 2, 3)
    assert conf.h == pytest.approx(h)
    assert conf.s_limit == pytest.approx(s_limit)
    assert conf.s_ash == pytest.approx(math.pi * 12**2 / 4 / per_mm)
    assert conf.s == pytest.approx(75)
    assert conf.ash_req == pytest.approx(per_mm * 75)
    # and the provided leg satisfies both cl 8.1(b) formulae at that spacing
    assert conf.ash >= 0.18 * conf.s * h * fck / fy * (Ag / Ak - 1)
    assert conf.ash >= 0.05 * conf.s * h * fck / fy


def test_required_zone_and_frame_type():
    p = Project()
    p.seismic.zone, p.seismic.response_reduction = "II", 3.0  # OMRF in zone II
    assert required(p) is False
    p.seismic.zone, p.seismic.response_reduction = "IV", 5.0  # SMRF in zone IV
    assert required(p) is True
    p.seismic.zone, p.seismic.response_reduction = "II", 5.0  # SMRF anywhere
    assert required(p) is True


def test_every_member_and_joint_checked(bungalow):
    p, fm, fa, rep, res = bungalow
    assert all(isinstance(r, DuctileCheck) for r in res)
    beams = {r.member_id for r in res if r.kind == "beam"}
    cols = {r.member_id for r in res if r.kind == "column"}
    assert beams == {b.member_id for b in rep.beams}
    assert cols == {c.member_id for c in rep.columns}
    for r in res:
        assert r.checks, r
        assert all(isinstance(n, str) and isinstance(ok, bool) and isinstance(t, str) for n, ok, t in r.checks)
    for r in res:
        if r.kind in ("beam", "column"):
            assert r.detailing
    # every beam-column joint above the base (a column below it and a beam framing in) has a result
    col_top = {m.n2: mid for mid, m in fm.members.items() if m.kind == "column"}
    beam_nodes = {n for m in fm.members.values() if m.kind == "beam" for n in (m.n1, m.n2)}
    expected = {col_top[n] for n in col_top if n in beam_nodes}
    joints = [r for r in res if r.kind == "joint"]
    assert {r.member_id for r in joints} == expected
    col_bottom = {m.n1 for m in fm.members.values() if m.kind == "column"}
    for r in joints:
        node = fm.members[r.member_id].n2
        if node not in col_bottom:  # roof: exempt
            assert r.ok and "exempt" in r.checks[0][2]
        else:
            assert any(n.startswith("7.2.1") and "ΣMc" in t for n, _, t in r.checks)


def test_weak_column_fails_scwb(bungalow):
    p, fm, fa, rep, res = bungalow
    # a joint above the base that passes, with columns of the same mark above and below
    col_bottom = {m.n1: mid for mid, m in fm.members.items() if m.kind == "column"}
    passing = [r for r in res if r.kind == "joint" and r.ok and fm.members[r.member_id].n2 in col_bottom]
    assert passing
    j = passing[0]
    below = fm.members[j.member_id]
    above = fm.members[col_bottom[below.n2]]
    p2 = p.clone()
    for mem in (below, above):
        p2.set_column_size(mem.mark, mem.level, 0.23, 0.23, mem.angle)
    fm2, fa2, rep2 = run_full(p2)
    res2 = check_ductility(fa2, p2, rep2)
    col_top2 = {m.n2: mid for mid, m in fm2.members.items() if m.kind == "column"}
    node2 = next(
        n
        for n, mid in col_top2.items()
        if fm2.members[mid].mark == below.mark and fm2.members[mid].level == below.level
    )
    j2 = next(r for r in res2 if r.kind == "joint" and r.member_id == col_top2[node2])
    scwb = [c for c in j2.checks if c[0].startswith("7.2.1")]
    assert scwb and not all(ok for _, ok, _ in scwb), j2.checks
    # the shrunk column also violates the minimum dimension of cl 7.1.1
    c2 = next(r for r in res2 if r.kind == "column" and r.member_id == col_top2[node2])
    assert not dict((n, ok) for n, ok, _ in c2.checks)["7.1.1 Min. dimension ≥ 300 mm"]


def test_failed_design_is_reported_not_raised(bungalow):
    p, fm, fa, rep, res = bungalow
    rep2 = dataclasses.replace(rep, beams=list(rep.beams), columns=list(rep.columns))
    rep2.beams[0] = dataclasses.replace(rep.beams[0], links=None)
    rep2.columns[0] = dataclasses.replace(rep.columns[0], main_bars=None)
    out = check_ductility(fa, p, rep2)
    b = next(r for r in out if r.member_id == rep.beams[0].member_id and r.kind == "beam")
    c = next(r for r in out if r.member_id == rep.columns[0].member_id and r.kind == "column")
    assert not b.ok and "failed" in b.checks[0][2]
    assert not c.ok and "failed" in c.checks[0][2]
