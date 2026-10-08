"""Step-by-step calculation sheets: the numbers on the sheets equal the design report."""

import dataclasses
import re

import pytest

from planwin_ai.ai.templates import build_template
from planwin_ai.design.runner import run_full
from planwin_ai.io import calc_sheets as cs


@pytest.fixture(scope="module")
def bungalow():
    prj = build_template("bungalow")
    fm, fa, rep = run_full(prj)
    return prj, fm, fa, rep


@pytest.fixture(scope="module")
def all_pdf(bungalow, tmp_path_factory):
    prj, fm, fa, rep = bungalow
    path = str(tmp_path_factory.mktemp("calc") / "all.pdf")
    assert cs.write_calc_sheets(path, prj, fm, fa, rep, watermark="TRIAL") == path
    return path


def _pages(path) -> int:
    with open(path, "rb") as f:
        return len(re.findall(rb"/Type\s*/Page(?![s\w])", f.read()))


def test_all_sheets_written(bungalow, all_pdf):
    prj, fm, fa, rep = bungalow
    with open(all_pdf, "rb") as f:
        data = f.read()
    assert data.startswith(b"%PDF") and len(data) > 100_000
    n = len(rep.beams) + len(rep.columns) + len(rep.footings) + len(rep.slabs)
    assert n > 50
    assert _pages(all_pdf) >= n + 2  # cover + contents + one page or more per sheet
    sheets = cs.build_sheets(prj, fm, fa, rep)
    assert [s.kind for s in sheets].count("Beam") == len(rep.beams)
    assert {s.kind for s in sheets} == {"Beam", "Column", "Footing", "Slab"}
    for s in sheets:
        assert s.steps and s.checks and all(st.clause for st in s.steps), s.ref


def test_selection_gives_fewer_pages(bungalow, all_pdf, tmp_path):
    prj, fm, fa, rep = bungalow
    path = str(tmp_path / "sel.pdf")
    ids = [rep.beams[0].member_id, rep.columns[0].member_id]
    cs.write_calc_sheets(path, prj, fm, fa, rep, member_ids=ids, footing_marks=[], slabs=[])
    sel = cs.build_sheets(prj, fm, fa, rep, member_ids=ids, footing_marks=[], slabs=[])
    assert [s.kind for s in sel] == ["Beam", "Column"]
    assert 4 <= _pages(path) < _pages(all_pdf)
    # footings / slabs selected by mark, nothing else
    pn, slab = rep.slabs[0]
    sel = cs.build_sheets(
        prj, fm, fa, rep, member_ids=[], footing_marks=[rep.footings[0].mark], slabs=[(pn, slab.mark)]
    )
    assert [s.kind for s in sel] == ["Footing", "Slab"]
    # an empty selection still gives a valid cover + contents
    empty = str(tmp_path / "empty.pdf")
    cs.write_calc_sheets(empty, prj, fm, fa, rep, member_ids=[], footing_marks=[], slabs=[])
    assert _pages(empty) == 2
    with pytest.raises(ValueError):
        cs.build_sheets(prj, fm, fa, rep, member_ids=[999_999])
    with pytest.raises(ValueError):
        cs.build_sheets(prj, fm, fa, rep, footing_marks=["NOPE"])


def test_sheet_numbers_equal_design_report(bungalow):
    prj, fm, fa, rep = bungalow
    for b in rep.beams:
        sh = cs.beam_sheet(prj, fa, b)
        assert sh.warnings == [], (b.mark, sh.warnings)
        assert sh.ok == b.ok
        assert sh.value("ast_bot") == pytest.approx(b.ast_bot, rel=1e-9)
        assert sh.value("ast_top_l") == pytest.approx(b.ast_top_l, rel=1e-9)
        assert sh.value("ast_top_r") == pytest.approx(b.ast_top_r, rel=1e-9)
        assert sh.value("Mu_pos") == pytest.approx(b.M_sag, rel=1e-9)
        assert sh.value("V_design") == pytest.approx(b.V_max, rel=1e-9)  # capacity shear when ductile
        assert sh.value("Tu") == pytest.approx(b.T_max, rel=1e-9)
        if b.links:
            assert sh.value("sv") == b.links.spacing
        if b.links_end:
            assert sh.value("sv_end") == b.links_end.spacing
        steps = cs.beam_steps(prj, fa, b)
        assert all(isinstance(s, cs.Step) for s in steps)
        assert any("√" in s.formula for s in steps)  # Ast formula of Annex G-1.1(b)
    # the bungalow is a ductile frame (zone III, R = 5) with two beams carrying torsion
    sheets = [cs.beam_sheet(prj, fa, b) for b in rep.beams]
    clauses = " ".join(s.clause for sh in sheets for s in sh.steps)
    for clause in ("IS 13920 cl 6.2.1", "IS 13920 cl 6.2.3", "IS 13920 cl 6.3.3", "IS 13920 cl 6.3.5", "cl 41.4.3"):
        assert clause in clauses
    tors = [sh for sh, b in zip(sheets, rep.beams) if b.T_max > 1.0]
    assert tors and all(sh.value("Mt") and sh.value("Ve") for sh in tors)
    for c in rep.columns:
        sh = cs.column_sheet(prj, fa, c)
        assert sh.warnings == [], (c.mark, sh.warnings)
        assert sh.ok == c.ok
        assert sh.value("steel_pct") == pytest.approx(c.steel_pct, abs=1e-9)
        assert sh.value("As_req") == pytest.approx(c.As, rel=1e-9)
        assert sh.value("ratio") == pytest.approx(c.utilisation, rel=1e-6)
        assert sh.value("l_unsupported") == pytest.approx(c.clear_height, rel=1e-9)
        if c.tie_confined is not None:
            assert sh.value("s_confined") == c.tie_confined.spacing
            assert sh.value("l0") == pytest.approx(c.l0 * 1000, rel=1e-9)
            assert sh.value("tie_spacing_out") == c.tie.spacing
    for f in rep.footings:
        sh = cs.footing_sheet(prj, fa, f)
        assert sh.warnings == [], (f.mark, sh.warnings)
        assert sh.ok == f.ok
        assert sh.value("ast_L") == pytest.approx(f.ast_L, rel=1e-9)
        assert sh.value("ast_B") == pytest.approx(f.ast_B, rel=1e-9)
        assert sh.value("D") == pytest.approx(f.D * 1000, rel=1e-9)
    for s in cs.build_sheets(prj, fm, fa, rep, member_ids=[], footing_marks=[]):
        assert s.kind == "Slab" and s.warnings == [], (s.ref, s.warnings)


def test_mismatch_is_flagged_on_the_sheet(bungalow, tmp_path):
    prj, fm, fa, rep = bungalow
    b = dataclasses.replace(rep.beams[0], ast_bot=rep.beams[0].ast_bot * 1.1)
    sh = cs.beam_sheet(prj, fa, b)
    assert sh.warnings and "differs from the design report" in sh.step("ast_bot").warning
    c = dataclasses.replace(rep.columns[0], steel_pct=rep.columns[0].steel_pct + 0.5)
    assert cs.column_sheet(prj, fa, c).step("steel_pct").warning
    # within tolerance (< 0.5 %) is not flagged
    b2 = dataclasses.replace(rep.beams[0], ast_bot=rep.beams[0].ast_bot * 1.004)
    assert cs.beam_sheet(prj, fa, b2).warnings == []


def test_user_text_is_escaped(bungalow, tmp_path):
    prj, fm, fa, rep = bungalow
    nasty = dataclasses.replace(prj, name='A & B <Tower> "x"', engineer="R&D <eng>", client="<b>")
    path = str(tmp_path / "esc.pdf")
    cs.write_calc_sheets(
        path, nasty, fm, fa, rep, member_ids=[rep.beams[0].member_id], footing_marks=[], slabs=[], watermark="<T & C>"
    )
    assert _pages(path) >= 3


def test_ascii_fallback_without_unicode_font(bungalow, tmp_path, monkeypatch):
    prj, fm, fa, rep = bungalow
    monkeypatch.setattr(cs, "_FONT_CACHE", ("Helvetica", "Helvetica-Bold", False))
    assert cs._plain("τv ≤ √x − 1", False) == "tauv <= sqrtx - 1"
    path = str(tmp_path / "ascii.pdf")
    cs.write_calc_sheets(path, prj, fm, fa, rep, member_ids=[rep.columns[0].member_id], footing_marks=[], slabs=[])
    assert _pages(path) >= 3
