"""Regression tests for the bugs fixed in 1.0.1 (each test failed on 1.0.0)."""

import copy
import json

import pytest

from planwin_ai.ai.actions import Session, _spec_from, execute, safe_filename
from planwin_ai.ai.templates import build_template
from planwin_ai.core.frame import FrameModel
from planwin_ai.core.generator import SPEC_IS_INPUT, GridSpec, auto_slab_thickness, grid_building
from planwin_ai.core.model import Plan, Slab
from planwin_ai.design import is456
from planwin_ai.design.runner import run_full
from planwin_ai.io.cities import lookup_city


# ------------------------------------------------------------------ design
def test_column_tension_check_uses_the_moment_of_each_end():
    """Both column ends carry the same combination name; the large moment at one end
    must not be replaced by the small moment at the other end."""
    one_end = is456.design_column([("0.9DL+1.5EQX", -200.0, 80.0, 0.0)], 0.3, 0.45, 3.0, 25, 500)
    both = is456.design_column(
        [("0.9DL+1.5EQX", -200.0, 80.0, 0.0), ("0.9DL+1.5EQX", -200.0, 0.0, 0.0)], 0.3, 0.45, 3.0, 25, 500
    )
    assert both.steel_pct == one_end.steel_pct
    assert both.ratio == pytest.approx(one_end.ratio)


def test_minimum_eccentricity_about_one_axis_at_a_time():
    """IS 456 cl 25.4: with biaxial bending e_min need only be met about one axis at a time."""
    b, D, L, fck, fy = 300.0, 450.0, 2700.0, 25, 500
    Pu = 1500e3
    chk = is456.design_column([("1.5(DL+LL)", 1500.0, 1.0, 1.0)], b / 1000, D / 1000, L / 1000, fck, fy)
    As = chk.steel_pct / 100 * b * D
    lex = L
    ex, ey = max(lex / 500 + D / 30, 20), max(lex / 500 + b / 30, 20)
    both_at_once = is456.biaxial_ratio(Pu, Pu * ex, Pu * ey, b, D, As, fck, fy, 40)
    one_x = is456.biaxial_ratio(Pu, Pu * ex, 1e6, b, D, As, fck, fy, 40)
    one_y = is456.biaxial_ratio(Pu, 1e6, Pu * ey, b, D, As, fck, fy, 40)
    assert chk.ratio == pytest.approx(max(one_x, one_y), rel=1e-6)
    assert chk.ratio < both_at_once


def test_footing_structural_design_uses_ultimate_reactions():
    """1.5(DL±EL) / 0.9DL±1.5EL may govern; scaling service cases by 1.2 is unconservative."""
    base = is456.design_footing(1000.0, 0.3, 0.45, 200, 25, 500, lateral=[(1000.0, 60.0, 0.0)])
    ult = is456.design_footing(
        1000.0,
        0.3,
        0.45,
        200,
        25,
        500,
        lateral=[(1000.0, 60.0, 0.0)],
        ultimate=[(1500.0, 0.0, 0.0), (1500.0, 135.0, 0.0)],
    )
    assert (ult.L, ult.B) == (base.L, base.B)  # plan size is a service check
    assert ult.ast_L > base.ast_L or ult.D > base.D


def test_design_all_passes_ultimate_reactions_to_footings(monkeypatch):
    seen = []
    real = is456.design_footing

    def spy(*a, **k):
        seen.append(k.get("ultimate"))
        return real(*a, **k)

    monkeypatch.setattr(is456, "design_footing", spy)
    run_full(build_template("bungalow"))
    assert seen and all(u and len(u) >= 25 for u in seen)


def test_boq_beam_concrete_excludes_slab_and_column_overlap():
    prj = grid_building(
        GridSpec(
            bays_x=[5.0],
            bays_y=[5.0],
            upper_floors=0,
            auto_size=False,
            column=(0.3, 0.3),
            beam_int=(0.3, 0.5),
            beam_ext=(0.3, 0.5),
            slab_thickness=0.15,
        )
    )
    prj.seismic.enabled = prj.wind.enabled = False
    fm, fa, rep = run_full(prj)
    roof_t = 0.15
    cols = sum(m.b * m.d * abs(fm.nodes[m.n2].z - fm.nodes[m.n1].z) for m in fm.members.values() if m.kind == "column")
    roof = prj.plan("Roof")
    slabs = sum(s.area * s.thickness for s in roof.slabs)
    # 4 roof beams of 5 m c/c between 0.3 m columns, below a 0.15 m slab; plinth beams have no slab on them
    beams = 4 * 0.3 * (0.5 - roof_t) * (5.0 - 0.3) + 4 * 0.3 * 0.5 * (5.0 - 0.3)
    footings = sum(f.L * f.B * f.D for f in rep.footings)
    expected = cols + slabs + beams + footings
    assert rep.boq["total_concrete"] == pytest.approx(expected, rel=1e-6)


# ------------------------------------------------------------------ frame
def test_unused_plan_does_not_change_seismic_period():
    p = build_template("residential_g4")
    t0 = {k: v.T for k, v in FrameModel(p).build().seismic.items()}
    p2 = copy.deepcopy(p)
    p2.plans.append(Plan(name="Scratch", slabs=[Slab(points=[[100, 100], [104, 100], [104, 104], [100, 104]])]))
    t1 = {k: v.T for k, v in FrameModel(p2).build().seismic.items()}
    assert t1 == pytest.approx(t0)


# ------------------------------------------------------------------ AI actions
@pytest.mark.parametrize("text", ["false", "False", "no", "0", 0, False])
def test_string_false_disables_loads(text):
    s = Session(build_template("bungalow"))
    r = execute(s, [{"action": "set_seismic", "enabled": text}, {"action": "set_wind", "enabled": text}])
    assert not r.errors
    assert s.project.seismic.enabled is False and s.project.wind.enabled is False


def test_invalid_soil_is_rejected_without_changing_the_model():
    s = Session(build_template("bungalow"))
    r = execute(s, [{"action": "set_seismic", "soil": "clay"}])
    assert r.errors and s.project.seismic.soil == "medium"
    execute(s, [{"action": "set_seismic", "soil": "Rock"}])
    assert s.project.seismic.soil == "hard"


def test_modify_occupancy_updates_importance_factor():
    s = Session(build_template("bungalow"))
    execute(s, [{"action": "new_building", "bays_x": [4, 4], "bays_y": [4], "upper_floors": 2}])
    assert s.project.seismic.importance == 1.2
    execute(s, [{"action": "modify_building", "occupancy": "hospital"}])
    assert s.project.seismic.importance == 1.5


def test_modify_bays_resizes_slab_and_beams_from_scratch():
    s = Session(build_template("bungalow"))
    execute(s, [{"action": "new_building", "bays_x": [3, 3], "bays_y": [3], "upper_floors": 3}])
    small = (s.project.plan("Typical").slabs[0].thickness, s.project.plan("Typical").beams[0].d)
    execute(s, [{"action": "modify_building", "bays_x": [6.5, 6.5], "bays_y": [6.5]}])
    fresh = grid_building(GridSpec(bays_x=[6.5, 6.5], bays_y=[6.5], upper_floors=3))
    assert s.project.plan("Typical").slabs[0].thickness == fresh.plan("Typical").slabs[0].thickness > small[0]
    execute(s, [{"action": "modify_building", "bays_x": [3, 3], "bays_y": [3]}])
    assert (s.project.plan("Typical").slabs[0].thickness, s.project.plan("Typical").beams[0].d) == small


def test_grid_building_does_not_modify_the_callers_spec():
    spec = GridSpec(bays_x=[6.0, 6.0], bays_y=[6.0], upper_floors=4)
    before = json.dumps(spec.__dict__, default=list)
    prj = grid_building(spec)
    assert json.dumps(spec.__dict__, default=list) == before
    assert prj.meta[SPEC_IS_INPUT] and prj.meta["grid_spec"]["slab_thickness"] == 0.0


def test_files_from_100_get_automatic_slab_thickness_back():
    prj = grid_building(GridSpec(bays_x=[3.0], bays_y=[3.0]))
    prj.meta.pop(SPEC_IS_INPUT)
    prj.meta["grid_spec"]["slab_thickness"] = auto_slab_thickness([3.0], [3.0])  # what 1.0.0 stored
    prj.meta["grid_spec"]["future_field"] = 1  # unknown keys are tolerated
    assert _spec_from(prj).slab_thickness == 0.0


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Block A/B", "Block_A_B"),
        ("a:b*c?", "a_b_c"),
        ("CON", "_CON"),
        ("  ..", "project"),
        ("Shah & Sons <Tower>", "Shah_&_Sons__Tower"),
    ],
)
def test_safe_filename(name, expected):
    assert safe_filename(name) == expected


def test_export_with_illegal_characters_in_project_name(tmp_path):
    s = Session(build_template("bungalow"), out_dir=str(tmp_path))
    s.project.name = 'Block A/B: "east"'
    r = execute(s, [{"action": "export", "format": "staad"}, {"action": "export", "format": "dxf"}])
    assert not r.errors, r.errors
    assert all(f.startswith(str(tmp_path)) for f in r.files)


def test_pdf_escapes_markup_in_project_text(tmp_path, monkeypatch):
    import reportlab.platypus as rp

    texts = []
    real = rp.Paragraph

    def capture(text, *a, **k):
        texts.append(text)
        return real(text, *a, **k)

    monkeypatch.setattr(rp, "Paragraph", capture)
    s = Session(build_template("bungalow"), out_dir=str(tmp_path))
    s.project.name = "Shah & Sons <Tower>"
    r = execute(s, [{"action": "export", "format": "pdf"}])
    assert not r.errors
    assert any("Shah &amp; Sons &lt;Tower&gt;" in t for t in texts)


# ------------------------------------------------------------------ misc
@pytest.mark.parametrize(
    "query,city",
    [
        ("a", None),
        ("pun", "Pune"),
        ("Thane West", "Thane"),
        ("Delhi NCR", "Delhi"),
        ("  mumbai ", "Mumbai"),
        ("xyz", None),
    ],
)
def test_city_lookup(query, city):
    info = lookup_city(query)
    assert (info["city"] if info else None) == city


def test_cli_reports_missing_file_without_traceback(capsys):
    from planwin_ai.cli import main

    assert main(["run", "does-not-exist.pwai"]) == 2
    assert "error:" in capsys.readouterr().err
    assert main(["new", "--template", "nope", "--out", "x.pwai"]) == 2


def test_assistant_plan_uses_context_captured_by_caller():
    from planwin_ai.ai.assistant import Assistant

    a = Assistant(Session(build_template("bungalow")))
    ctx = a.context()
    a.session.project = None  # a worker thread must not need the project at all
    reply, actions = a.plan("zone IV", ctx)
    assert actions == [{"action": "set_seismic", "zone": "IV"}]
