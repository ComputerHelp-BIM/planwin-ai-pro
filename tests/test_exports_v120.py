"""BOQ & cost estimate workbook and member schedules (Excel) – writers, export registry, AI phrases and CLI."""

import os
import re

import pytest

from planwin_ai import __version__, units
from planwin_ai.ai.actions import ACTION_SCHEMA, Session, execute
from planwin_ai.ai.offline import parse
from planwin_ai.ai.templates import build_template
from planwin_ai.cli import main as cli
from planwin_ai.core.model import Wall
from planwin_ai.design.quantities import revision_snapshot
from planwin_ai.design.report import DesignReport
from planwin_ai.design.runner import run_full
from planwin_ai.io import project_io
from planwin_ai.io.boq_excel import write_boq_excel
from planwin_ai.io.schedules import write_schedules_excel
from planwin_ai.services import exports
from planwin_ai.units import T_TO_KN

openpyxl = pytest.importorskip("openpyxl")
ITEM = re.compile(r"^[A-Z]\.\d+$")


@pytest.fixture(autouse=True)
def _si_units():
    units.set_system("SI")
    yield
    units.set_system("SI")


@pytest.fixture(scope="module")
def bungalow():
    p = build_template("bungalow")
    fm, fa, rep = run_full(p)
    return p, rep


@pytest.fixture(scope="module")
def boq_wb(bungalow, tmp_path_factory):
    p, rep = bungalow
    return openpyxl.load_workbook(write_boq_excel(str(tmp_path_factory.mktemp("boq") / "b.xlsx"), p, rep, "TRIAL"))


@pytest.fixture(scope="module")
def sched_wb(bungalow, tmp_path_factory):
    p, rep = bungalow
    path = str(tmp_path_factory.mktemp("sch") / "s.xlsx")
    return openpyxl.load_workbook(write_schedules_excel(path, p, rep, "TRIAL"))


# ----------------------------------------------------------------- tiny formula evaluator (+ and * of refs)
def _value(wb, ws, ref: str) -> float:
    if "!" in ref:
        sheet, ref = ref.split("!")
        ws = wb[sheet]
    v = ws[ref.replace("$", "")].value
    return _eval(wb, ws, v[1:]) if isinstance(v, str) and v.startswith("=") else float(v or 0.0)


def _eval(wb, ws, expr: str) -> float:
    total = 0.0
    for term in expr.split("+"):
        prod = 1.0
        for f in term.split("*"):
            prod *= float(f) if re.fullmatch(r"[\d.]+", f) else _value(wb, ws, f)
        total += prod
    return total


def _table(ws, first_header: str):
    rows = list(ws.iter_rows())
    i = next(k for k, r in enumerate(rows) if r[0].value == first_header)
    return rows[i], i + 1, rows[i + 1 :]


# ----------------------------------------------------------------- registry
def test_registry_keys_and_aliases():
    boq, sch = exports.get("boq"), exports.get("schedules")
    assert (boq.label, boq.suffix, boq.group) == ("BOQ & cost estimate (Excel)", "_BOQ.xlsx", "Reports")
    assert (sch.label, sch.suffix, sch.group) == ("Member schedules (Excel)", "_schedules.xlsx", "Reports")
    for alias in ("bill of quantities", "estimate", "cost", "BOQ"):
        assert exports.get(alias) is boq
    for alias in ("schedule", "column schedule", "beam schedule"):
        assert exports.get(alias) is sch
    assert exports.get("bar bending schedule").key == "bbs"
    names = [k for f in exports.formats() for k in (f.key, *f.aliases)]
    assert len(names) == len(set(names))
    assert {"boq", "schedules"} <= set(ACTION_SCHEMA["export"]["format"].split("|"))


# ----------------------------------------------------------------- BOQ workbook
def test_boq_sheets_header_and_print_setup(boq_wb):
    assert boq_wb.sheetnames == ["BOQ", "Rates", "By floor", "By type"]  # no saved revisions
    ws = boq_wb["BOQ"]
    assert ws["A1"].value == "Bill of quantities & cost estimate"
    info = {r[0]: r[1] for r in ws.iter_rows(max_row=8, values_only=True) if r[0]}
    assert info["Project"] == "Bungalow G+1" and info["Licence"] == "TRIAL"
    assert info["Prepared with"] == f"PlanWin AI Pro {__version__}"
    head, hrow, _ = _table(ws, "Item No.")
    assert [c.value for c in head] == ["Item No.", "Description", "Unit", "Quantity", "Rate (₹)", "Amount (₹)"]
    assert ws.freeze_panes == f"A{hrow + 1}" and ws.print_title_rows == f"${hrow}:${hrow}"
    assert ws.page_setup.orientation == "portrait" and int(ws.page_setup.paperSize) == 9  # A4
    assert ws.page_setup.fitToWidth == 1 and ws.sheet_properties.pageSetUpPr.fitToPage


def test_boq_rates_are_inputs(boq_wb, bungalow):
    p, rep = bungalow
    ws = boq_wb["Rates"]
    _, _, rows = _table(ws, "Code")
    rates = {r[0].value: r[3] for r in rows if r[0].value and r[3].value is not None}
    for k, v in p.design.rates.items():
        assert rates[k].value == v
    assert rates["concrete_pcc"].value == 5000  # PCC under the footings priced at the ledger's default
    for c in rates.values():
        assert isinstance(c.value, (int, float)) and c.fill.fgColor.rgb.endswith("FFF2CC")
    # every BOQ rate is a reference to one of these input cells
    refs = {f"=Rates!$D${c.row}" for c in rates.values()}
    items = [r for r in boq_wb["BOQ"].iter_rows() if r[0].value and ITEM.match(str(r[0].value))]
    assert items and all(r[4].value in refs for r in items)


def test_boq_quantities_and_grand_total_match_design(boq_wb, bungalow):
    _, rep = bungalow
    boq = rep.boq
    ws = boq_wb["BOQ"]
    items = [r for r in ws.iter_rows() if r[0].value and ITEM.match(str(r[0].value))]
    sums: dict[str, float] = {}
    amount = 0.0
    for r in items:
        row = r[0].row
        assert r[5].value == f"=D{row}*E{row}"
        kind = "pcc" if r[1].value.startswith("PCC") else "rcc" if r[1].value.startswith("RCC") else r[2].value
        sums[kind] = sums.get(kind, 0.0) + r[3].value
        amount += _value(boq_wb, ws, f"F{row}")
    assert sums["rcc"] == pytest.approx(boq["total_concrete"], abs=0.01)
    assert sums["pcc"] == pytest.approx(boq["concrete"]["PCC M10"], abs=0.01)
    assert sums["kg"] == pytest.approx(boq["total_steel"], abs=1.0)
    assert sums["m²"] == pytest.approx(boq["formwork"], abs=0.01)
    assert amount == pytest.approx(boq["cost"], rel=5e-3)
    # sub-totals per member type and a SUBTOTAL grand total over every item row
    labels = [r[1].value for r in ws.iter_rows() if r[1].value]
    assert [x for x in labels if str(x).startswith("Sub-total")] == [
        f"Sub-total – {k}" for k in ("Columns", "Beams", "Slabs", "Footings")
    ]
    total = next(r for r in ws.iter_rows() if r[1].value == "GRAND TOTAL")
    m = re.fullmatch(r"=SUBTOTAL\(9,F(\d+):F(\d+)\)", total[5].value)
    assert m and int(m.group(1)) <= items[0][0].row and int(m.group(2)) >= items[-1][0].row
    ratio = next(r for r in ws.iter_rows() if r[1].value == "Steel / RCC concrete ratio")
    assert ratio[3].value.startswith("=IF(")


@pytest.mark.parametrize("sheet,key,head", [("By floor", "by_level", "Floor"), ("By type", "by_type", "Member type")])
def test_boq_breakdown_cost_formulas(boq_wb, bungalow, sheet, key, head):
    _, rep = bungalow
    ws = boq_wb[sheet]
    header, _, rows = _table(ws, head)
    names = [c.value for c in header]
    assert names[-3:] == ["RCC total m³", "Steel kg/m³", "Cost ₹"] and "Steel kg" in names
    body = rows[: next(i for i, r in enumerate(rows) if r[0].value == "TOTAL")]
    groups = {k: v for k, v in rep.boq[key].items() if v["by_grade"] or v["steel"]}
    assert [r[0].value for r in body] == list(groups)  # e.g. no walls row in the bungalow
    cost = 0.0
    for r in body:
        f = r[-1].value
        assert f.startswith("=") and "Rates!" in f
        c = _eval(boq_wb, ws, f[1:])
        assert c == pytest.approx(groups[r[0].value]["cost"], rel=1e-3)
        assert r[names.index("Steel kg")].value == pytest.approx(groups[r[0].value]["steel"], abs=0.01)
        cost += c
    assert cost == pytest.approx(rep.boq["cost"], rel=1e-3)
    tot = next(r for r in rows if r[0].value == "TOTAL")
    assert tot[-1].value.startswith("=SUM(") and tot[names.index("Steel kg")].value.startswith("=SUM(")


def test_boq_revisions_sheet(bungalow, tmp_path):
    p, rep = bungalow
    a = revision_snapshot(rep.boq, "R1", "2026-10-01")
    b = revision_snapshot(rep.boq, "R2", "2026-10-05")
    b["total"]["steel"] *= 1.1
    old = dict(p.meta)
    p.meta["revisions"] = [a, b]
    try:
        wb = openpyxl.load_workbook(write_boq_excel(str(tmp_path / "r.xlsx"), p, rep))
    finally:
        p.meta.clear()
        p.meta.update(old)
    ws = wb["Revisions"]
    _, _, rows = _table(ws, "Revision")
    assert [r[0].value for r in rows[:2]] == ["R1", "R2"]
    head, _, rows = _table(ws, "Group")
    assert [c.value for c in head][2:] == ["A: R1", "B: R2", "Change", "Change %"]
    steel = next(r for r in rows if r[0].value == "Total" and "steel" in r[1].value)
    n = steel[0].row
    assert steel[4].value == f"=D{n}-C{n}" and steel[5].value == f'=IF(C{n}=0,"",E{n}/C{n})'
    assert (steel[3].value - steel[2].value) / steel[2].value == pytest.approx(0.1)
    assert "Licence" not in {r[0] for r in ws.iter_rows(max_row=8, values_only=True)}  # no watermark given


def test_writers_need_a_design(bungalow, tmp_path):
    p, _ = bungalow
    with pytest.raises(ValueError, match="design"):
        write_boq_excel(str(tmp_path / "x.xlsx"), p, DesignReport())
    with pytest.raises(ValueError, match="design"):
        write_schedules_excel(str(tmp_path / "y.xlsx"), p, DesignReport())


# ----------------------------------------------------------------- schedules
def test_schedule_sheets(sched_wb):
    assert sched_wb.sheetnames == [
        "Column schedule",
        "Column list",
        "Beam schedule",
        "Footing schedule",
        "Slab schedule",
    ]  # no walls or combined footings in the bungalow
    for ws in sched_wb.worksheets:
        assert ws.page_setup.orientation == "landscape" and ws.freeze_panes == "A9"
        assert ws.print_title_rows == "$8:$8"


def test_column_schedule_groups_and_lists_every_column(sched_wb, bungalow):
    p, rep = bungalow
    head, _, rows = _table(sched_wb["Column schedule"], "Column marks")
    assert [c.value for c in head][1:] == ["Plinth", "Floor 1", "Roof"]
    rows = [r for r in rows if r[1].value]
    marks = [m for r in rows for m in r[0].value.split(", ")]
    assert sorted(marks) == sorted({c.mark for c in rep.columns})
    assert len(rows) < len(marks)  # identical columns share a row (C1, C3 ...)
    cell = re.compile(r"^\d+×\d+ / \d+-\d+Ø / \d+Ø@\d+( \(conf\. (\d+Ø@)?\d+, l0 \d+\))?$")
    assert all(cell.match(c.value) for r in rows for c in r[1:] if c.value != "–")
    head, _, rows = _table(sched_wb["Column list"], "Level")
    rows = [r for r in rows if r[1].value]
    assert len(rows) == len(rep.columns)
    assert [c.value for c in head][-3:] == ["Pu kN", "Utilisation", "OK"]


def test_beam_schedule_groups_identical_beams_per_level(sched_wb, bungalow):
    _, rep = bungalow
    _, _, rows = _table(sched_wb["Beam schedule"], "Level")
    rows = [r for r in rows if r[2].value]
    got, level = set(), None
    for r in rows:
        level = r[0].value or level
        if r[1].value:
            got |= {(level, m) for m in r[1].value.split(", ")}
    assert got == {(b.level, b.mark) for b in rep.beams}
    assert any(", " in (r[1].value or "") for r in rows) and len(rows) < len(rep.beams)


def test_footing_and_slab_schedules(sched_wb, bungalow):
    _, rep = bungalow
    head, _, rows = _table(sched_wb["Footing schedule"], "Type")
    rows = [r for r in rows if r[1].value]
    assert [r[0].value for r in rows] == [f"F{i}" for i in range(1, len(rows) + 1)]
    served = [m for r in rows for m in r[1].value.split(", ")]
    assert sorted(served) == sorted(f.mark for f in rep.footings) and len(rows) < len(served)
    assert sum(r[2].value for r in rows) == len(rep.footings)
    assert {r[[c.value for c in head].index("SBC check")].value for r in rows} == {"OK"}
    _, _, rows = _table(sched_wb["Slab schedule"], "Plan")
    rows = [r for r in rows if r[1].value]
    assert len(rows) == len(rep.slabs)
    assert {r[3].value for r in rows} <= {"Two-way", "One-way", "Cantilever"}


def test_column_list_in_mks_units(bungalow, tmp_path):
    p, rep = bungalow
    units.set_system("MKS")
    wb = openpyxl.load_workbook(write_schedules_excel(str(tmp_path / "t.xlsx"), p, rep))
    head, _, rows = _table(wb["Column list"], "Level")
    i = [c.value for c in head].index("Pu t")
    assert rows[0][i].value == pytest.approx(rep.columns[0].Pu / T_TO_KN, abs=0.06)


def test_wall_and_combined_footing_schedules(tmp_path):
    from planwin_ai.core.generator import GridSpec, grid_building

    p = build_template("residential_g4")
    for pl in p.plans:
        pl.walls.append(Wall(mark="W1", x1=4.5, y1=4.0, x2=8.5, y2=4.0, thickness=0.23))
    fm, fa, rep = run_full(p)
    assert rep.walls
    wb = openpyxl.load_workbook(write_schedules_excel(str(tmp_path / "w.xlsx"), p, rep))
    head, _, rows = _table(wb["Wall schedule"], "Mark")
    rows = [r for r in rows if r[1].value]
    assert len(rows) == len(rep.walls) and {r[2].value for r in rows} == {"230×4000"}
    assert "Boundary elements" in [c.value for c in head]
    boq = openpyxl.load_workbook(write_boq_excel(str(tmp_path / "w_boq.xlsx"), p, rep))
    assert "WALLS" in [r[1] for r in boq["BOQ"].iter_rows(values_only=True)]
    assert "Walls" in [r[0] for r in boq["By type"].iter_rows(values_only=True)]

    p = grid_building(GridSpec(bays_x=[2.0], bays_y=[6.0], upper_floors=4, city="Pune"))
    p.design.sbc = 120.0
    fm, fa, rep = run_full(p)
    assert rep.combined_footings
    wb = openpyxl.load_workbook(write_schedules_excel(str(tmp_path / "c.xlsx"), p, rep))
    _, _, rows = _table(wb["Combined footings"], "Columns")
    assert len([r for r in rows if r[1].value]) == len(rep.combined_footings)


# ----------------------------------------------------------------- AI actions, offline phrases, CLI
def test_export_actions_write_both_workbooks(tmp_path):
    s = Session(build_template("bungalow"), out_dir=str(tmp_path))
    path = str(tmp_path / "custom_boq.xlsx")
    r = execute(s, [{"action": "export", "format": "boq", "path": path}, {"action": "export", "format": "schedules"}])
    assert not r.errors, r.errors
    assert r.files[0] == path and r.files[1].endswith("_schedules.xlsx")
    assert all(os.path.getsize(f) > 5000 for f in r.files)
    assert "BOQ & cost estimate (Excel)" in r.messages[-2]
    r = execute(s, [{"action": "export", "format": "estimate"}])  # alias, default file name
    assert not r.errors and r.files[0].endswith("_BOQ.xlsx")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("export boq", [{"action": "export", "format": "boq"}]),
        ("download BOQ", [{"action": "export", "format": "boq"}]),
        ("boq excel", [{"action": "export", "format": "boq"}]),
        ("export the BOQ to excel", [{"action": "export", "format": "boq"}]),
        ("cost estimate", [{"action": "export", "format": "boq"}]),
        ("export schedules", [{"action": "export", "format": "schedules"}]),
        ("column schedule", [{"action": "export", "format": "schedules"}]),
        ("beam schedule", [{"action": "export", "format": "schedules"}]),
        (
            "export boq and schedules",
            [{"action": "export", "format": "boq"}, {"action": "export", "format": "schedules"}],
        ),
        ("design and export boq", [{"action": "design"}, {"action": "export", "format": "boq"}]),
        # existing phrases keep their meaning
        ("show BOQ by floor", [{"action": "boq", "by": "floor"}]),
        ("boq by member type", [{"action": "boq", "by": "type"}]),
        ("show cost estimate", [{"action": "boq", "by": "floor"}]),
        ("export bar bending schedule", [{"action": "export", "format": "bbs"}]),
        ("export excel", [{"action": "export", "format": "excel"}]),
        ("export staad", [{"action": "export", "format": "staad"}]),
    ],
)
def test_offline_export_phrases(text, expected):
    assert parse(text)[1] == expected


def test_cli_exports_boq_and_schedules(tmp_path, capsys):
    prj = str(tmp_path / "b.pwai")
    project_io.save_project(build_template("bungalow"), prj)
    out = tmp_path / "out"
    rc = cli(["run", prj, "--design", "--export", "boq", "schedules", "--out-dir", str(out)])
    text = capsys.readouterr().out
    assert rc == 0, text
    names = sorted(os.listdir(out))
    assert any(n.endswith("_BOQ.xlsx") for n in names) and any(n.endswith("_schedules.xlsx") for n in names), names
    assert "Member schedules (Excel)" in text
