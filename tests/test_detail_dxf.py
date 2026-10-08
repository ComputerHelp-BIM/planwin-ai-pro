"""Reinforcement detail drawings (planwin_ai.io.detail_dxf)."""

import re

import ezdxf
import pytest

from planwin_ai.ai.templates import build_template
from planwin_ai.core.model import Beam, Column, Level, Plan, Project, Slab, Wall
from planwin_ai.design.runner import run_full
from planwin_ai.io.detail_dxf import (
    LAYERS,
    build_detail_doc,
    distribute_bars,
    physical_beams,
    wall_schedule,
    write_detail_drawings,
)


@pytest.fixture(scope="module")
def bungalow(tmp_path_factory):
    prj = build_template("bungalow")
    fm, _, rep = run_full(prj)
    path = str(tmp_path_factory.mktemp("detail") / "details.dxf")
    assert write_detail_drawings(path, prj, fm, rep, watermark="TRIAL") == path
    _, info = build_detail_doc(prj, fm, rep, "TRIAL")
    return prj, fm, rep, ezdxf.readfile(path), info


def _texts(doc, box=None):
    out = []
    for e in doc.modelspace().query("TEXT MTEXT"):
        p = e.dxf.insert
        if box and not (box[0] <= p.x <= box[2] and box[1] <= p.y <= box[3]):
            continue
        out.append(e.dxf.text if e.dxftype() == "TEXT" else e.plain_text())
    return out


def _has_token(texts, token):
    pat = re.compile(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])")
    return any(pat.search(t) for t in texts)


def test_file_is_valid_and_has_all_layers(bungalow):
    _, _, _, doc, _ = bungalow
    auditor = doc.audit()
    assert not auditor.has_errors, [str(e) for e in auditor.errors]
    for name in LAYERS:
        assert name in doc.layers
    assert doc.units == ezdxf.units.M
    texts = _texts(doc)
    for s in ("COLUMN SCHEDULE", "FOOTING SCHEDULE", "BEAM DETAILS", "SLAB SCHEDULE", "PlanWin AI Pro", "TRIAL"):
        assert any(s in t for t in texts), s
    assert any("Verify before construction" in t for t in texts)


def test_sheets_are_side_by_side(bungalow):
    *_, info = bungalow
    boxes = [info["sheets"][k] for k in ("columns", "footings", "beams", "slabs")]
    for a, b in zip(boxes, boxes[1:]):
        assert a[2] < b[0]
    for x0, y0, x1, y1 in boxes:  # A1 proportion (one page each for the bungalow)
        assert (x1 - x0) / (y1 - y0) == pytest.approx(841 / 594, rel=1e-6)


def test_column_schedule(bungalow):
    prj, _, rep, doc, info = bungalow
    box = info["sheets"]["columns"]
    texts = _texts(doc, box)
    for mark in {c.mark for c in rep.columns}:
        assert _has_token(texts, mark), mark
    rows = info["column_rows"]
    assert sorted(m for r in rows for m in r.marks) == sorted({c.mark for c in rep.columns})
    expected = sum(c.bars.count for r in rows for c in r.cells if c is not None)
    circles = [
        e
        for e in doc.modelspace().query("CIRCLE[layer=='DET_BAR']")
        if box[0] <= e.dxf.center.x <= box[2] and box[1] <= e.dxf.center.y <= box[3]
    ]
    assert expected > 0
    assert len(circles) == expected
    for r in rows:
        for c in r.cells:
            if c is not None:
                assert re.fullmatch(r"\d+x\d+, \d+-T\d+", c.size_text)
                assert any(c.size_text in t for t in texts)
                assert any(c.tie_text in t for t in texts)


def _norm_ties(text):
    return re.sub(r"\s+", " ", text.replace("×", "x").replace("l0 = ", "l0=").replace(" c/c", "")).strip()


def test_column_ties_follow_the_design(bungalow):
    """The schedule draws the IS 13920 confining hoops and ties the design produced."""
    prj, _, rep, _, info = bungalow
    rows = info["column_rows"]
    ranges = info["column_ranges"]
    seen = 0
    for c in rep.columns:
        row = next(r for r in rows if c.mark in r.marks)
        j = next(k for k, (a, b) in enumerate(ranges) if a <= c.level_index <= b)
        cell = row.cells[j]
        assert cell.tie_text == _norm_ties(c.ties)
        if c.tie_confined is not None:
            seen += 1
            assert cell.tie_confined == c.tie_confined and cell.l0 == pytest.approx(c.l0, abs=5e-4)
            assert f"over l0={c.l0 * 1000:.0f}" in cell.tie_text
    assert seen == len(rep.columns)  # the bungalow is in a seismic zone: IS 13920 applies


def test_footing_schedule(bungalow):
    _, _, rep, doc, info = bungalow
    types = info["footing_types"]
    assert 1 <= len(types) <= len(rep.footings)
    texts = _texts(doc, info["sheets"]["footings"])
    for f in rep.footings:
        assert _has_token(texts, f.mark), f.mark
    served = sorted(m for t in types for m in t.columns)
    assert served == sorted(f.mark for f in rep.footings)
    for t in types:
        assert _has_token(texts, t.mark)
    # identical footings share a type
    keys = {(f.L, f.B, f.D, str(f.mesh_L), str(f.mesh_B)) for f in rep.footings}
    assert len(types) == len(keys)


def test_beam_links_follow_the_design(bungalow):
    _, _, rep, doc, info = bungalow
    texts = _texts(doc, info["sheets"]["beams"])
    ends = [b.links_end for b in rep.beams if b.links_end is not None]
    assert ends  # IS 13920 hoops near column faces
    for lk in {(b.links_end or b.links) for b in rep.beams if b.links}:
        assert any(f"T{lk.dia}@{int(lk.spacing)}" in t for t in texts)
    for b in rep.beams:
        if b.links:
            assert any(f"T{b.links.dia}@{int(b.links.spacing)}" in t for t in texts)
    assert not any("WALL SCHEDULE" in t for t in _texts(doc))  # no walls in the bungalow


def test_beam_details(bungalow):
    prj, fm, rep, doc, info = bungalow
    groups = info["beam_groups"]
    pbs = physical_beams(prj, fm, rep)
    assert len(pbs) == len({(b.group, b.level_index) for b in rep.beams})
    assert sum(len(g.beams) for g in groups) == len(pbs)
    assert len(groups) < len(pbs)  # identical beams are detailed once
    texts = _texts(doc, info["sheets"]["beams"])
    titles = [t for t in texts if re.match(r"BM\d+: ", t)]
    assert len(titles) == len(groups)
    for b in rep.beams:
        assert any(_has_token([t], b.mark) for t in titles), b.mark
    for g in groups:
        r = g.rep
        for o in g.beams:
            assert (o.b, o.d, len(o.spans)) == (r.b, r.d, len(r.spans))
            assert max(abs(x - y) for x, y in zip(sorted(o.spans), sorted(r.spans))) <= 0.05 + 1e-9


def test_slab_schedule(bungalow):
    _, _, rep, doc, info = bungalow
    groups = info["slab_groups"]
    assert sum(len(g.panels) for g in groups) == len(rep.slabs)
    texts = _texts(doc, info["sheets"]["slabs"])
    for _, s in rep.slabs:
        assert _has_token(texts, s.mark)


@pytest.mark.parametrize("n", [4, 6, 8, 10, 12, 14, 16, 20])
@pytest.mark.parametrize("hx,hy", [(0.1, 0.15), (0.2, 0.2), (0.3, 0.1)])
def test_distribute_bars_symmetric_with_corners(n, hx, hy):
    pts = distribute_bars(n, hx, hy)
    assert len(pts) == n
    assert len({(round(x, 9), round(y, 9)) for x, y in pts}) == n

    def key(ps):
        return sorted((round(x, 9), round(y, 9)) for x, y in ps)

    for c in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)):
        assert any(abs(x - c[0]) < 1e-12 and abs(y - c[1]) < 1e-12 for x, y in pts)
    for x, y in pts:  # every bar on a face
        assert abs(abs(x) - hx) < 1e-12 or abs(abs(y) - hy) < 1e-12
    assert key(pts) == key([(-x, y) for x, y in pts])
    assert key(pts) == key([(x, -y) for x, y in pts])
    # longer faces get at least as many bars
    on_y_faces = sum(abs(abs(x) - hx) < 1e-12 and abs(abs(y) - hy) > 1e-12 for x, y in pts)
    on_x_faces = sum(abs(abs(y) - hy) < 1e-12 and abs(abs(x) - hx) > 1e-12 for x, y in pts)
    if hy > hx:
        assert on_y_faces >= on_x_faces
    elif hx > hy:
        assert on_x_faces >= on_y_faces


def test_distribute_bars_small_and_odd_counts():
    assert distribute_bars(0, 0.1, 0.1) == []
    assert len(distribute_bars(2, 0.1, 0.2)) == 2
    pts = distribute_bars(7, 0.1, 0.2)
    assert len(pts) == 7
    assert len({(round(x, 9), round(y, 9)) for x, y in pts}) == 7


def test_continuous_beam_with_junction_cantilever_and_wall(tmp_path):
    plan = Plan(name="P")
    xy = [(0, 0), (4, 0), (8, 0), (0, 5), (4, 5), (8, 5)]
    plan.columns = [Column(mark=f"C{i}", x=x, y=y, b=0.3, d=0.45) for i, (x, y) in enumerate(xy, 1)]
    plan.beams = [
        Beam(mark="B1", x1=0, y1=0, x2=9.5, y2=0, d=0.5),
        Beam(mark="B2", x1=0, y1=5, x2=11, y2=5, d=0.5),  # ends on wall W1
        Beam(mark="B3", x1=0, y1=0, x2=0, y2=5),
        Beam(mark="B4", x1=4, y1=0, x2=4, y2=5),
        Beam(mark="B5", x1=8, y1=0, x2=8, y2=5),
        Beam(mark="B6", x1=2, y1=0, x2=2, y2=5, d=0.4),
    ]
    plan.slabs = [
        Slab(mark="S1", points=[[0, 0], [2, 0], [2, 5], [0, 5]]),
        Slab(mark="S2", points=[[2, 0], [4, 0], [4, 5], [2, 5]]),
        Slab(mark="S3", points=[[4, 0], [8, 0], [8, 5], [4, 5]]),
    ]
    plan.walls = [Wall(mark="W1", x1=11, y1=0, x2=11, y2=5, thickness=0.2)]
    prj = Project(name="Multi", plans=[plan], levels=[Level("L1", "P", 3.0), Level("L2", "P", 3.0)])
    fm, _, rep = run_full(prj)
    pbs = physical_beams(prj, fm, rep)
    b1 = next(p for p in pbs if p.mark == "B1" and p.level_index == 1)
    assert [round(s, 3) for s in b1.spans] == [2.0, 2.0, 4.0, 1.5]
    assert [s.kind for s in b1.supports] == ["column", "beam", "column", "column", "free"]
    assert b1.supports[0].width == pytest.approx(0.3)
    b2 = next(p for p in pbs if p.mark == "B2" and p.level_index == 1)
    assert [s.kind for s in b2.supports][-1] == "wall"
    assert b2.supports[-1].mark == "W1" and b2.supports[-1].width == pytest.approx(0.2)
    assert any(m.kind == "link" for m in fm.members.values())
    assert {p.mark for p in pbs} == {f"B{i}" for i in range(1, 7)}  # rigid links are not beams
    walls = wall_schedule(fm)
    assert [(w.mark, w.thickness, w.length, w.levels) for w in walls] == [("W1", 0.2, 5.0, [1, 2])]
    path = str(tmp_path / "multi.dxf")
    write_detail_drawings(path, prj, fm, rep)
    doc = ezdxf.readfile(path)
    assert not doc.audit().has_errors
    texts = _texts(doc)
    assert any("WALL SCHEDULE" in t for t in texts)
    assert any("200 x 5000" in t for t in texts)
    assert any("W1 (wall)" in t for t in texts)
