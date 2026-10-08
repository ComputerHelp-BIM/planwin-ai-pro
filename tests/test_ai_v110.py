"""AI actions, offline phrases and CLI options added in 1.1.0 (walls, stairs, tanks, grids, revisions, BOQ, units)."""

import os

import pytest

from planwin_ai import units
from planwin_ai.ai.actions import ACTION_SCHEMA, Session, execute
from planwin_ai.ai.assistant import Assistant
from planwin_ai.ai.offline import parse
from planwin_ai.ai.templates import build_template
from planwin_ai.cli import main as cli
from planwin_ai.io import project_io

NEW = (
    "add_wall",
    "add_staircase",
    "add_water_tank",
    "add_grids",
    "save_revision",
    "compare_revisions",
    "boq",
    "set_units",
)


@pytest.fixture(autouse=True)
def _si_units():
    units.set_system("SI")
    yield
    units.set_system("SI")


@pytest.fixture(scope="module")
def designed_bungalow():
    """One design of the bungalow shared by the read-only BOQ checks."""
    s = Session(build_template("bungalow"))
    r = execute(s, [{"action": "design"}])
    assert not r.errors, r.errors
    return s


def _run(s, *acts):
    return execute(s, list(acts))


def test_schema_lists_new_actions():
    for name in NEW:
        assert name in ACTION_SCHEMA
    assert {"method", "rigid_diaphragm"} <= set(ACTION_SCHEMA["set_seismic"])
    assert all(k in ACTION_SCHEMA["export"]["format"] for k in ("calc", "bbs", "details"))


# ----------------------------------------------------------------- seismic method / diaphragm
@pytest.mark.parametrize(
    "given,expected",
    [
        ("rsa", "response_spectrum"),
        ("dynamic", "response_spectrum"),
        ("response spectrum", "response_spectrum"),
        ("Response_Spectrum", "response_spectrum"),
        ("static", "static"),
        ("equivalent static", "static"),
        ("auto", "auto"),
    ],
)
def test_set_seismic_method_aliases(given, expected):
    s = Session(build_template("bungalow"))
    s.last["rep"] = object()
    r = _run(s, {"action": "set_seismic", "method": given})
    assert not r.errors and r.changed and not s.last
    assert s.project.seismic.method == expected


def test_set_seismic_diaphragm_and_bad_method():
    s = Session(build_template("bungalow"))
    r = _run(s, {"action": "set_seismic", "rigid_diaphragm": "off"})
    assert not r.errors and s.project.seismic.rigid_diaphragm is False
    assert "no rigid diaphragm" in r.messages[0]
    _run(s, {"action": "set_seismic", "rigid_diaphragm": "true"})
    assert s.project.seismic.rigid_diaphragm is True
    r = _run(s, {"action": "set_seismic", "method": "pushover"})
    assert r.errors and "auto, static or response_spectrum" in r.errors[0]
    assert s.project.seismic.method == "auto"


# ----------------------------------------------------------------- walls
def test_add_wall_all_plans_and_named_plan():
    s = Session(build_template("bungalow"))
    r = _run(s, {"action": "add_wall", "mark": "W1", "x1": 0, "y1": 0, "x2": 3, "y2": 0, "thickness": 230})
    assert not r.errors and r.changed
    for pl in s.project.plans:
        assert [(w.mark, w.thickness, w.length) for w in pl.walls] == [("W1", 0.23, 3.0)]
    # same mark again replaces instead of duplicating; a named plan only touches that plan
    _run(s, {"action": "add_wall", "mark": "W1", "x1": 0, "y1": 0, "x2": 4, "y2": 0})
    assert [w.length for pl in s.project.plans for w in pl.walls] == [4.0] * len(s.project.plans)
    r = _run(s, {"action": "add_wall", "plan": "typical", "x1": 8, "y1": 0, "x2": 8, "y2": 4, "grade": "30"})
    assert not r.errors
    typ = s.project.plan("Typical")
    assert [w.mark for w in typ.walls] == ["W1", "W2"] and typ.walls[1].grade == "M30"
    assert typ.walls[1].thickness == pytest.approx(0.23)
    assert len(s.project.plan("Roof").walls) == 1


@pytest.mark.parametrize(
    "params,msg",
    [
        ({"x1": 1, "y1": 1, "x2": 1, "y2": 1}, "zero length"),
        ({"x1": 0, "y1": 0, "x2": 3, "y2": 0, "thickness": 0.1}, "150 mm"),
        ({"x1": 0, "y1": 0, "x2": 3, "y2": 0, "plan": "Basement"}, "plan 'Basement' not found"),
        ({"x1": 0, "y1": 0, "x2": "three", "y2": 0}, "must be a number"),
        ({"x1": 0, "y1": 0, "y2": 0}, "'x2' is required"),
    ],
)
def test_add_wall_rejects_bad_input(params, msg):
    s = Session(build_template("bungalow"))
    r = _run(s, {"action": "add_wall", **params})
    assert r.errors and msg in r.errors[0], r.errors
    assert not any(pl.walls for pl in s.project.plans) and not r.changed


# ----------------------------------------------------------------- staircase
def test_add_staircase_reports_design_and_is_idempotent():
    s = Session(build_template("bungalow"))
    act = {"action": "add_staircase", "support_beams": "B3 B4", "width": 1.2, "riser": 150, "live": 3}
    r = _run(s, act)
    assert not r.errors and r.changed, r.errors
    msg = r.messages[0]
    for word in ("Staircase ST1", "B3, B4", "dead", "kN/m²", "effective span", "Waist 200 mm", "main T", "deflection"):
        assert word in msg, msg
    typ = s.project.plan("Typical")

    def stair_loads():
        return [(b.mark, pl.w1) for b in typ.beams for pl in b.part_loads if pl.desc == "Stair ST1"]

    first = stair_loads()
    assert len(first) == 4 and {m for m, _ in first} == {"B3", "B4"}
    _run(s, act)
    assert stair_loads() == first and len(s.project.stairs) == 1
    assert s.project.stairs[0]["riser"] == pytest.approx(0.15)
    # MKS display
    _run(s, {"action": "set_units", "system": "MKS"})
    r = _run(s, act)
    assert "t/m²" in r.messages[0] and stair_loads() == first


@pytest.mark.parametrize(
    "params,msg",
    [
        ({"support_beams": ["B3", "B99"]}, "B99"),
        ({}, "support beams"),
        ({"support_beams": ["B3", "B4"], "width": -1}, "width must be positive"),
        ({"support_beams": ["B3", "B4"], "plan": "Attic"}, "plan 'Attic' not found"),
    ],
)
def test_add_staircase_rejects_bad_input(params, msg):
    s = Session(build_template("bungalow"))
    r = _run(s, {"action": "add_staircase", **params})
    assert r.errors and msg in r.errors[0], r.errors
    assert not s.project.stairs


# ----------------------------------------------------------------- water tank
def test_add_water_tank_by_level_name_and_idempotent():
    s = Session(build_template("residential_g4"))
    act = {"action": "add_water_tank", "capacity_l": 10000, "level": "roof", "columns": "C5 C6 C9 C10"}
    r = _run(s, act)
    assert not r.errors, r.errors
    roof = next(i for i, lv in enumerate(s.project.levels, 1) if lv.name == "Roof")
    mine = [j for j in s.project.joint_loads if j.get("source") == "Tank T1"]
    assert len(mine) == 4 and {j["level"] for j in mine} == {roof}
    msg = r.messages[0]
    assert "water 98.1 kN" in msg and "tank" in msg and "on each of C5, C6, C9, C10" in msg
    per = mine[0]["fz"]
    assert f"{per:.1f} kN" in msg
    _run(s, act)
    assert [j for j in s.project.joint_loads if j.get("source") == "Tank T1"] == mine
    assert len(s.project.water_tanks) == 1
    # level by index, top by default
    r = _run(s, {"action": "add_water_tank", "name": "T2", "capacity_l": 5000, "columns": ["C5", "C6"]})
    assert not r.errors and s.project.water_tanks[-1]["level"] == len(s.project.levels)
    r = _run(s, {"action": "add_water_tank", "name": "T2", "capacity_l": 5000, "level": 2, "columns": ["C1"]})
    assert not r.errors and s.project.water_tanks[-1]["level"] == 2 and len(s.project.water_tanks) == 2


@pytest.mark.parametrize(
    "params,msg",
    [
        ({"capacity_l": 10000, "columns": ["C5", "C77"], "level": "Roof"}, "C77"),
        ({"capacity_l": 10000, "columns": ["C5"], "level": "Penthouse"}, "level 'Penthouse' not found"),
        ({"capacity_l": 10000, "columns": ["C5"], "level": 42}, "level 42 does not exist"),
        ({"capacity_l": 0, "columns": ["C5"]}, "positive"),
        ({"columns": ["C5"]}, "capacity_l"),
        ({"capacity_l": 1000}, "supporting columns"),
    ],
)
def test_add_water_tank_rejects_bad_input(params, msg):
    s = Session(build_template("residential_g4"))
    r = _run(s, {"action": "add_water_tank", **params})
    assert r.errors and msg in r.errors[0], r.errors
    assert not s.project.water_tanks


# ----------------------------------------------------------------- grids
def test_add_grids_auto_and_explicit():
    s = Session(build_template("bungalow"))
    s.last["rep"] = "kept"
    r = _run(s, {"action": "add_grids", "grids": "auto"})
    assert not r.errors and r.changed and s.last.get("rep") == "kept"  # drawings only
    g = s.project.grids
    assert [(x["name"], x["pos"]) for x in g if x["axis"] == "x"] == [("A", 0.0), ("B", 4.0), ("C", 8.0)]
    assert [(x["name"], x["pos"]) for x in g if x["axis"] == "y"] == [("1", 0.0), ("2", 4.0), ("3", 7.5)]
    r = _run(s, {"action": "add_grids", "grids": [{"name": "P", "axis": "X", "pos": "1.5"}]})
    assert not r.errors and s.project.grids == [{"name": "P", "axis": "x", "pos": 1.5}]


@pytest.mark.parametrize(
    "grids,msg",
    [
        ([{"name": "A", "axis": "z", "pos": 0}], "axis"),
        ([{"name": "A", "axis": "x", "pos": 0}, {"name": "A", "axis": "x", "pos": 4}], "duplicate"),
        ([{"axis": "x", "pos": 0}], "name"),
        ([], "no grids"),
        ("sideways", "'auto' or a list"),
    ],
)
def test_add_grids_rejects_bad_input(grids, msg):
    s = Session(build_template("bungalow"))
    r = _run(s, {"action": "add_grids", "grids": grids})
    assert r.errors and msg in r.errors[0], r.errors
    assert s.project.grids == []


# ----------------------------------------------------------------- revisions / BOQ / units
def test_save_and_compare_revisions():
    s = Session(build_template("bungalow"))
    r = _run(s, {"action": "compare_revisions"})
    assert r.errors and "two saved revisions" in r.errors[0]
    r = _run(s, {"action": "save_revision", "label": "R1"})
    assert not r.errors and "rep" in s.last and "Revision R1 saved" in r.messages[-1]
    _run(s, {"action": "set_loads", "live": 4.0})
    r = _run(s, {"action": "save_revision", "label": "R2"})
    assert not r.errors
    revs = s.project.meta["revisions"]
    assert [x["label"] for x in revs] == ["R1", "R2"] and revs[1]["total"]["steel"] > revs[0]["total"]["steel"]
    assert set(revs[0]["by_level"]) == {"Foundation", "Plinth", "Floor 1", "Roof"}
    r = _run(s, {"action": "compare_revisions"})
    assert not r.errors
    table = r.messages[0]
    assert table.startswith("Revision R1 → R2")
    for row in ("Total concrete m³", "Total steel t", "Total cost ₹ lakh", "Floor 1 steel t", "Foundation cost"):
        assert row in table, table
    assert "%" in table
    # explicit labels (case-insensitive), unknown label, same label twice
    assert not _run(s, {"action": "compare_revisions", "a": "r2", "b": "R1"}).errors
    assert "not found" in _run(s, {"action": "compare_revisions", "a": "R9"}).errors[0]
    assert "different" in _run(s, {"action": "compare_revisions", "a": "R1", "b": "R1"}).errors[0]
    # saving an existing label replaces it; no label numbers the next one
    r = _run(s, {"action": "save_revision", "label": "R2"})
    assert "updated" in r.messages[-1] and len(s.project.meta["revisions"]) == 2
    _run(s, {"action": "save_revision"})
    assert [x["label"] for x in s.project.meta["revisions"]] == ["R1", "R2", "R3"]


def test_boq_by_floor_and_type(designed_bungalow):
    s = designed_bungalow
    r = _run(s, {"action": "boq"})
    assert not r.errors and not r.changed
    text = r.messages[0]
    lines = text.splitlines()
    assert lines[0].startswith("Bill of quantities by floor") and "Concrete m³" in lines[1]
    assert [ln.split()[0] for ln in lines[2:]] == ["Foundation", "Plinth", "Floor", "Roof", "Total"]
    total = float(lines[-1].split()[1])
    assert total == pytest.approx(s.last["rep"].boq["total_concrete"], abs=0.01)
    r = _run(s, {"action": "boq", "by": "type"})
    rows = [ln.split()[0] for ln in r.messages[0].splitlines()[2:]]
    assert rows == ["Columns", "Beams", "Slabs", "Footings", "Total"]  # no walls in the bungalow
    assert "floor' or 'type" in _run(s, {"action": "boq", "by": "colour"}).errors[0]


def test_set_units_is_display_only():
    s = Session(build_template("bungalow"))
    r = _run(s, {"action": "set_units", "system": "tonnes"})
    assert not r.errors and not r.changed and units.current.mks and "forces in t" in r.messages[0]
    r = _run(s, {"action": "set_units", "system": "kN"})
    assert not r.errors and not units.current.mks
    r = _run(s, {"action": "set_units", "system": "imperial"})
    assert r.errors and "SI (kN) or MKS" in r.errors[0] and not units.current.mks


# ----------------------------------------------------------------- offline parser
@pytest.mark.parametrize(
    "text,expected",
    [
        ("use response spectrum", [{"action": "set_seismic", "method": "response_spectrum"}]),
        ("dynamic analysis", [{"action": "set_seismic", "method": "response_spectrum"}]),
        ("static method", [{"action": "set_seismic", "method": "static"}]),
        ("rigid diaphragm on", [{"action": "set_seismic", "rigid_diaphragm": True}]),
        ("rigid diaphragm off", [{"action": "set_seismic", "rigid_diaphragm": False}]),
        (
            "add shear wall W1 from 0,0 to 3,0 thickness 230",
            [{"action": "add_wall", "mark": "W1", "x1": 0.0, "y1": 0.0, "x2": 3.0, "y2": 0.0, "thickness": 230.0}],
        ),
        (
            "staircase on beams B3 B4 width 1.2",
            [{"action": "add_staircase", "support_beams": ["B3", "B4"], "width": 1.2}],
        ),
        (
            "water tank 10000 litres on C5 C6 C9 C10 at roof",
            [
                {
                    "action": "add_water_tank",
                    "capacity_l": 10000.0,
                    "columns": ["C5", "C6", "C9", "C10"],
                    "level": "Roof",
                }
            ],
        ),
        ("add grids", [{"action": "add_grids", "grids": "auto"}]),
        ("auto grid", [{"action": "add_grids", "grids": "auto"}]),
        ("save revision R1", [{"action": "save_revision", "label": "R1"}]),
        ("compare revisions", [{"action": "compare_revisions"}]),
        ("compare R1 and R2", [{"action": "compare_revisions", "a": "R1", "b": "R2"}]),
        ("show BOQ by floor", [{"action": "boq", "by": "floor"}]),
        ("boq by member type", [{"action": "boq", "by": "type"}]),
        ("units tonnes", [{"action": "set_units", "system": "MKS"}]),
        ("use kN", [{"action": "set_units", "system": "SI"}]),
    ],
)
def test_offline_phrases(text, expected):
    _, acts = parse(text)
    assert acts == expected


def test_offline_new_patterns_do_not_steal_existing_commands():
    assert parse("design")[1] == [{"action": "design"}]
    assert parse("analyze")[1] == [{"action": "analyze"}]
    assert parse("run dynamic analysis")[1] == [
        {"action": "set_seismic", "method": "response_spectrum"},
        {"action": "analyze"},
    ]
    assert parse("set live load 3 kN/m2 on typical")[1] == [{"action": "set_loads", "live": 3.0, "plan": "Typical"}]
    _, acts = parse("zone IV soft soil, M30 Fe500, SBC 250, analyze and design then export staad")
    assert [a["action"] for a in acts] == [
        "set_seismic",
        "set_materials",
        "set_sbc",
        "analyze",
        "design",
        "export",
    ]
    _, acts = parse("G+4 residential in Pune with mumty and a stair cabin", has_model=False)
    assert [a["action"] for a in acts] == ["new_building"] and acts[0]["mumty"]
    # the stair's live load is not a slab load; a wall's grade is not a project-wide material change
    _, acts = parse("staircase on beams B3 B4 live load 5")
    assert acts == [{"action": "add_staircase", "support_beams": ["B3", "B4"], "live": 5.0}]
    _, acts = parse("add wall W2 from 0,0 to 0,4 thick 200 M30")
    assert [a["action"] for a in acts] == ["add_wall"] and acts[0]["grade"] == "M30"
    _, acts = parse("design and save revision R2 then compare revisions")
    assert [a["action"] for a in acts] == ["design", "save_revision", "compare_revisions"]
    for fmt in ("calc", "bbs", "details"):
        assert parse(f"export {fmt}")[1] == [{"action": "export", "format": fmt}]


def test_offline_assistant_applies_tank_and_stair_idempotently():
    a = Assistant(Session(build_template("residential_g4")))
    for _ in range(2):
        reply, res = a.ask("water tank 10000 litres on C5 C6 C9 C10 at roof")
        assert not res.errors and res.changed, reply
        reply, res = a.ask("staircase on beams B15 B16 width 1.2")
        assert not res.errors, reply
    p = a.session.project
    assert len(p.water_tanks) == 1 and len([j for j in p.joint_loads if j.get("source") == "Tank T1"]) == 4
    assert len(p.stairs) == 1
    stair = [pl for b in p.plan("Typical").beams for pl in b.part_loads if pl.desc == "Stair ST1"]
    assert len(stair) == 4


# ----------------------------------------------------------------- CLI
def test_cli_run_method_and_exports(tmp_path, capsys):
    prj = str(tmp_path / "b.pwai")
    project_io.save_project(build_template("bungalow"), prj)
    out = tmp_path / "out"
    rc = cli(
        ["run", prj, "--method", "rsa", "--no-diaphragm", "--units", "mks"]
        + ["--export", "bbs", "calc", "details", "--out-dir", str(out)]
    )
    text = capsys.readouterr().out
    assert rc == 0, text
    assert "method response spectrum" in text and "no rigid diaphragm" in text and "Display units: MKS" in text
    names = sorted(os.listdir(out))
    assert any(n.endswith("_BBS.xlsx") for n in names), names
    assert any(n.endswith("_calc.pdf") for n in names), names
    assert any(n.endswith("_details.dxf") for n in names), names
    assert all(os.path.getsize(out / n) > 0 for n in names)
    assert not units.current.mks  # --units is restored after the run
    assert cli(["run", prj, "--export", "gif", "--out-dir", str(out)]) == 2
    assert cli(["run", str(tmp_path / "missing.pwai")]) == 2
    with pytest.raises(SystemExit) as exc:
        cli(["run", prj, "--method", "pushover"])
    assert exc.value.code == 2


def test_cli_boq(tmp_path, capsys):
    prj = str(tmp_path / "b.pwai")
    project_io.save_project(build_template("bungalow"), prj)
    assert cli(["boq", prj]) == 0
    text = capsys.readouterr().out
    rows = [ln.split()[0] for ln in text.strip().splitlines()[2:]]
    assert rows == ["Foundation", "Plinth", "Floor", "Roof", "Total"], text
    assert "Design done" not in text
    assert cli(["boq", prj, "--by", "type"]) == 0
    assert "Columns" in capsys.readouterr().out
    assert cli(["boq", str(tmp_path / "missing.pwai")]) == 2
