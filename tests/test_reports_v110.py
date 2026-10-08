"""1.1.0 report content: seismic method & modal results, irregularity, IS 13920, walls,
combined footings, floor-wise BOQ, revision compare and display units in Excel / PDF."""

import os

import pytest

from planwin_ai.ai.templates import build_template
from planwin_ai.core.model import Wall
from planwin_ai.core.plan_engine import PlanEngine
from planwin_ai.design.quantities import compare_revisions, revision_snapshot
from planwin_ai.design.runner import run_full
from planwin_ai.io.excel_report import write_excel
from planwin_ai.io.pdf_report import write_pdf
from planwin_ai.units import MKS, SI, T_TO_KN

openpyxl = pytest.importorskip("openpyxl")


def _plans(p):
    return {pl.name: PlanEngine(pl).run() for pl in p.plans}


@pytest.fixture(scope="module")
def bungalow():
    p = build_template("bungalow")
    fm, fa, rep = run_full(p)
    return p, _plans(p), fm, fa, rep


@pytest.fixture(scope="module")
def workbook(bungalow, tmp_path_factory):
    p, prs, fm, fa, rep = bungalow
    path = str(tmp_path_factory.mktemp("xl") / "si.xlsx")
    write_excel(path, p, prs, fm, fa, rep, units=SI)
    return openpyxl.load_workbook(path)


def _rows(ws):
    return list(ws.iter_rows(values_only=True))


def _table(ws, first_header: str):
    """(header, rows) of the table in ``ws`` whose header row starts with ``first_header``."""
    rows = _rows(ws)
    i = next(k for k, r in enumerate(rows) if r and r[0] == first_header)
    out = []
    for r in rows[i + 1 :]:
        if not r or r[0] is None:
            break
        out.append(r)
    return rows[i], out


def _col(header, name):
    return list(header).index(name)


def test_new_sheets_exist(workbook, bungalow):
    rep = bungalow[4]
    assert rep.seismic_method == "response spectrum" and rep.ductile
    for name in ("Seismic method", "Irregularity", "IS 13920", "IS 13920 detailing", "BOQ by floor", "BOQ by type"):
        assert name in workbook.sheetnames, name
    assert "Walls" not in workbook.sheetnames  # no walls in the bungalow
    assert "Revision compare" not in workbook.sheetnames


def test_seismic_method_sheet(workbook, bungalow):
    _, _, fm, fa, rep = bungalow
    ws = workbook["Seismic method"]
    info = {r[0]: r[1] for r in _rows(ws) if r and r[0]}
    assert "Response spectrum" in info["Method used"]
    assert info["Dynamic analysis required (cl 7.7.1)"] == ("YES" if fa.dynamic_required else "NO")
    head, modes = _table(ws, "Mode")
    assert len(modes) == len(rep.modal.modes)
    cum = rep.modal.cumulative("x")
    assert modes[-1][_col(head, "Cumulative X %")] == pytest.approx(100 * cum[-1], abs=1e-3)
    assert [m[1] for m in modes] == pytest.approx([m.period for m in rep.modal.modes], abs=1e-3)
    head, rs = _table(ws, "Direction")
    assert {r[0] for r in rs} == {"RSX", "RSY"}
    for r in rs:
        res = fa.rs[r[0]]
        assert r[_col(head, "Scale factor")] == pytest.approx(res.scale, abs=1e-3)
        assert r[_col(head, "VB static kN")] == pytest.approx(res.vb_static, abs=1e-3)
    head, shears = _table(ws, "Level")
    assert len(shears) == len(fm.levels)
    base = shears[-1]
    assert base[_col(head, "RSX kN")] == pytest.approx(fa.rs["RSX"].storey_shear[0], abs=1e-3)


def test_irregularity_and_is13920_sheets(workbook, bungalow):
    rep = bungalow[4]
    rows = _rows(workbook["Irregularity"])
    assert len(rows) - 1 == len(rep.irregularities)
    assert {r[4] for r in rows[1:]} <= {"regular", "IRREGULAR", "IRREGULAR – reconfigure", "not checked"}
    ws = workbook["IS 13920"]
    info = {r[0]: r[1] for r in _rows(ws) if r and r[0]}
    n_checks = sum(len(d.checks) for d in rep.ductile)
    assert info["Checks"] == n_checks and info["Members checked"] == len(rep.ductile)
    assert info["Passed"] + info["Failed"] == n_checks
    head, checks = _table(ws, "Kind")
    assert len(checks) == n_checks
    assert {r[_col(head, "Result")] for r in checks} <= {"PASS", "FAIL"}
    assert sum(r[_col(head, "Result")] == "FAIL" for r in checks) == info["Failed"]
    assert {r[0] for r in checks} <= {"beam", "column", "joint"}
    det = _rows(workbook["IS 13920 detailing"])
    assert len(det) - 1 == sum(len(d.detailing) for d in rep.ductile)


def test_boq_by_floor_and_type_sum_to_totals(workbook, bungalow):
    boq = bungalow[4].boq
    for name, groups in (("BOQ by floor", boq["by_level"]), ("BOQ by type", boq["by_type"])):
        rows = _rows(workbook[name])
        head, body = rows[0], rows[1:]
        assert body[-1][0] == "TOTAL" and len(body) - 1 == len(groups)
        for col, key in (("Concrete m³", "total_concrete"), ("Steel kg", "total_steel"), ("Cost ₹", "cost")):
            i = _col(head, col)
            assert sum(r[i] for r in body[:-1]) == pytest.approx(boq[key], rel=1e-5, abs=0.01)
            assert body[-1][i] == pytest.approx(boq[key], rel=1e-6, abs=1e-3)
        i, c = _col(head, "Steel kg/m³"), _col(head, "Concrete m³")
        s = _col(head, "Steel kg")
        assert all(r[i] == pytest.approx(r[s] / r[c], rel=1e-3) for r in body if r[c])


def test_beam_and_column_design_new_columns(workbook, bungalow):
    rep = bungalow[4]
    head, *rows = _rows(workbook["Beam design"])
    for h in ("End-zone links (2d)", "Tu kNm", "Side face"):
        assert h in head
    assert len(rows) == len(rep.beams)
    assert any(r[_col(head, "End-zone links (2d)")] for r in rows)  # IS 13920 applies (zone III)
    head, *rows = _rows(workbook["Column design"])
    assert "Confining hoops (l0)" in head and "l0 m" in head
    assert all(r[_col(head, "l0 m")] >= 0.45 for r in rows)


def test_mks_units_convert_force_columns(bungalow, workbook, tmp_path):
    p, prs, fm, fa, rep = bungalow
    path = write_excel(str(tmp_path / "mks.xlsx"), p, prs, fm, fa, rep, units=MKS)
    wb = openpyxl.load_workbook(path)
    si_head, *si_rows = _rows(workbook["Column design"])
    mk_head, *mk_rows = _rows(wb["Column design"])
    assert "Pu kN" in si_head and "Pu t" in mk_head and "Mux t·m" in mk_head
    assert not any("kN" in str(h) for h in mk_head)
    i, j = _col(si_head, "Pu kN"), _col(mk_head, "Pu t")
    for a, b in zip(si_rows, mk_rows):
        assert b[j] == pytest.approx(a[i] / T_TO_KN, abs=2e-3)
        assert b[_col(mk_head, "b m")] == a[_col(si_head, "b m")]  # lengths unchanged
        assert b[_col(mk_head, "Steel %")] == a[_col(si_head, "Steel %")]
    _, si_rs = _table(workbook["Seismic method"], "Direction")
    mk_head, mk_rs = _table(wb["Seismic method"], "Direction")
    k = _col(mk_head, "VB static t")
    assert mk_rs[0][k] == pytest.approx(si_rs[0][k] / T_TO_KN, abs=2e-3)
    rows = _rows(wb["Plan checks"])
    assert rows[0][1] == "Applied DL t"
    assert "Units" in {r[0] for r in _rows(wb["Summary"])}


def test_revision_compare_sheet(bungalow, tmp_path):
    p, prs, fm, fa, rep = bungalow
    a = revision_snapshot(rep.boq, "R0", "2026-10-01")
    b = revision_snapshot(rep.boq, "R1", "2026-10-05")
    b["total"]["steel"] *= 1.1
    old = dict(p.meta)
    p.meta["revisions"] = [revision_snapshot(rep.boq, "R-1", "2026-09-01"), a, b]
    try:
        path = write_excel(str(tmp_path / "rev.xlsx"), p, prs, fm, fa, rep)
    finally:
        p.meta.clear()
        p.meta.update(old)
    head, *rows = _rows(openpyxl.load_workbook(path)["Revision compare"])
    assert "R0" in head[2] and "R1" in head[3]  # the last two revisions
    assert len(rows) == len(compare_revisions(a, b))
    steel = next(r for r in rows if r[0] == "Total" and "steel" in r[1])
    assert steel[5] == pytest.approx(10.0, abs=1e-3)


def test_walls_sheet_and_pdf_with_shear_wall(tmp_path):
    p = build_template("residential_g4")
    for pl in p.plans:
        pl.walls.append(Wall(mark="W1", x1=4.5, y1=4.0, x2=8.5, y2=4.0, thickness=0.23))
    fm, fa, rep = run_full(p)
    assert rep.walls
    path = write_excel(str(tmp_path / "w.xlsx"), p, {}, fm, fa, rep, units=SI)
    wb = openpyxl.load_workbook(path)
    head, *rows = _rows(wb["Walls"])
    assert len(rows) == len(rep.walls)
    assert {r[_col(head, "t m")] for r in rows} == {0.23}
    assert {r[_col(head, "OK")] for r in rows} <= {"YES", "NO"}
    assert os.path.getsize(write_pdf(str(tmp_path / "w.pdf"), p, {}, fm, rep, fa=fa)) > 5000


def test_combined_footings_sheet(tmp_path):
    from planwin_ai.core.generator import GridSpec, grid_building

    p = grid_building(GridSpec(bays_x=[2.0], bays_y=[6.0], upper_floors=4, city="Pune"))
    p.design.sbc = 120.0
    fm, fa, rep = run_full(p)
    assert rep.combined_footings
    wb = openpyxl.load_workbook(write_excel(str(tmp_path / "c.xlsx"), p, {}, fm, fa, rep))
    head, *rows = _rows(wb["Combined footings"])
    assert len(rows) == len(rep.combined_footings)
    assert all(" + " in r[0] for r in rows)
    assert os.path.getsize(write_pdf(str(tmp_path / "c.pdf"), p, {}, fm, rep, fa=fa)) > 5000


def _pdf_text(path):
    pypdf = pytest.importorskip("pypdf")
    return "\n".join(pg.extract_text() or "" for pg in pypdf.PdfReader(path).pages)


def test_pdf_with_response_spectrum_and_special_characters(bungalow, tmp_path):
    p, prs, fm, fa, rep = bungalow
    name, client = p.name, p.client
    p.name, p.client = 'Tower "A" & <Annex>', "R&D <client>"
    try:
        path = write_pdf(str(tmp_path / "rs.pdf"), p, prs, fm, rep, "TRIAL & <demo>", fa=fa)
        legacy = write_pdf(str(tmp_path / "legacy.pdf"), p, prs, fm, rep, "TRIAL")  # old positional call
        mks = write_pdf(str(tmp_path / "mks.pdf"), p, prs, fm, rep, fa=fa, units=MKS)
    finally:
        p.name, p.client = name, client
    for f in (path, legacy, mks):
        assert os.path.getsize(f) > 5000
    txt = _pdf_text(path)
    assert "Tower" in txt and "&" in txt and "<Annex>" in txt
    for heading in (
        "Seismic analysis method",
        "Modal periods",
        "Response spectrum scaled",
        "Irregularity",
        "IS 13920",
        "Quantities by floor",
    ):
        assert heading in txt, heading
    assert "Response spectrum scaled" in _pdf_text(legacy)  # RS results taken from fm.rs
    assert "VB static t" in _pdf_text(mks)


def test_pdf_and_excel_static_method(tmp_path):
    p = build_template("bungalow")
    p.seismic.method = "static"
    p.name = "Static & <plain>"
    for pl in p.plans:
        for c in pl.columns[:1]:
            c.mark = "C<1>&"
    fm, fa, rep = run_full(p)
    assert not fa.rs and rep.seismic_method == "static"
    path = write_pdf(str(tmp_path / "st.pdf"), p, _plans(p), fm, rep, fa=fa)
    txt = _pdf_text(path)
    assert "Equivalent static" in txt and "Response spectrum scaled" not in txt
    wb = openpyxl.load_workbook(write_excel(str(tmp_path / "st.xlsx"), p, {}, fm, fa, rep))
    ws = wb["Seismic method"]
    info = {r[0]: r[1] for r in _rows(ws) if r and r[0]}
    assert info["Method used"].startswith("Equivalent static")
    if fa.dynamic_required:
        assert "WARNING" in info["Why"]
    assert _table(ws, "Mode")[1] == []
    assert not any(r and r[0] == "Direction" for r in _rows(ws))
