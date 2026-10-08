"""Reinforcement detail drawings (DXF): column, footing, beam and slab schedules.

One DXF holds four sheets side by side in model space (drawing unit: metres,
R2010 like :mod:`planwin_ai.io.dxf_io`).  Every sheet sits inside an
A1-proportion frame with a title block; the sheet scale is the smallest
standard scale at which the sheet content fits, and text heights are
specified in paper millimetres and multiplied by that scale.  The footing and
beam sheets are limited to 1:50 and 1:75; when their details do not fit on one
A1 at that scale they continue on further A1 frames ("BEAM DETAILS (2/3)")
placed next to the first.  Column and slab tables that do not fit at 1:250 get
an enlarged A1-proportion frame ("plot to fit").

Members are drawn at true size in metres; beam cross-sections are enlarged to
an effective 1:20 and labelled accordingly.

Grouping rules
--------------
* Columns: a column mark's *signature* is its (size, bars, ties) at every
  level.  Marks with the same signature share a schedule row.  The table
  columns are the coarsest level ranges over which no row changes.
* Footings: identical L, B, D and bar meshes form one type F1, F2, ...
  (ascending plan area); the table lists the columns each type serves.
* Beams: frame segments with the same plan beam (``group``) and level make
  one physical beam.  Beams with the same b x D, number of spans, spans within
  50 mm and identical bars and links (in either direction) share one detail.
* Slabs: panels with the same kind, thickness and meshes share a row.
* Walls (frame members of kind "wall"): one row per mark and run of levels
  with the same thickness x length; rigid "link" members are never drawn.

Ties, stirrups and side-face bars are drawn exactly as designed: column
confining hoops (``tie_confined`` over ``l0``) / ``tie`` elsewhere, beam
``links_end`` within 2d of column / wall faces / ``links`` elsewhere.
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from dataclasses import dataclass, field

import ezdxf
from ezdxf.enums import TextEntityAlignment

from ..design.is456 import BarMesh, BarSet, Links

# ---------------------------------------------------------------- constants
LAYERS = {
    # name: (colour, lineweight in 1/100 mm)
    "DET_OUTLINE": (7, 35),
    "DET_BAR": (1, 50),
    "DET_LINK": (3, 25),
    "DET_TEXT": (2, 18),
    "DET_DIM": (4, 13),
    "DET_FRAME": (5, 70),
    "DET_TABLE": (8, 18),
}

A1_W, A1_H = 841.0, 594.0  # paper mm
MARGIN_L, MARGIN = 20.0, 10.0
STRIP_H = 70.0  # bottom strip: notes + title block
TB_W = 260.0  # title block width
CONTENT_X0, CONTENT_X1 = MARGIN_L + 10.0, A1_W - MARGIN - 10.0
CONTENT_Y0, CONTENT_Y1 = MARGIN + STRIP_H + 10.0, A1_H - MARGIN - 10.0
CONTENT_W, CONTENT_H = CONTENT_X1 - CONTENT_X0, CONTENT_Y1 - CONTENT_Y0
SCALES = [10, 15, 20, 25, 30, 40, 50, 75, 100, 125, 150, 200, 250]
SECTION_SCALE = 20  # beam sections are drawn at an effective 1:20

NOTE = "Read with structural general notes. Verify before construction."
APP = "PlanWin AI Pro"
CHAR_W = 0.95  # text width estimate as a fraction of the text height per character

_ALIGN = {
    "L": TextEntityAlignment.MIDDLE_LEFT,
    "C": TextEntityAlignment.MIDDLE_CENTER,
    "R": TextEntityAlignment.MIDDLE_RIGHT,
    "BL": TextEntityAlignment.BOTTOM_LEFT,
    "BC": TextEntityAlignment.BOTTOM_CENTER,
    "TL": TextEntityAlignment.TOP_LEFT,
    "TC": TextEntityAlignment.TOP_CENTER,
}


# ---------------------------------------------------------------- small helpers
def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def text_width(s: str, h: float) -> float:
    return CHAR_W * h * len(s)


def _parse_barset(text: str) -> BarSet | None:
    m = re.match(r"\s*(\d+)\s*-\s*T(\d+)", text or "")
    return BarSet(int(m.group(1)), int(m.group(2))) if m else None


def _parse_links(text: str) -> Links | None:
    m = re.match(r"\s*(?:(\d+)L-)?T(\d+)\s*@\s*(\d+)", text or "")
    return Links(int(m.group(1) or 2), int(m.group(2)), float(m.group(3))) if m else None


def _parse_mesh(text: str) -> BarMesh | None:
    m = re.match(r"\s*T(\d+)\s*@\s*(\d+)", text or "")
    return BarMesh(int(m.group(1)), float(m.group(2))) if m else None


def _mesh_key(m: BarMesh | None):
    return (m.dia, round(m.spacing)) if m else None


def _mesh_str(m: BarMesh | None) -> str:
    return f"T{m.dia} @ {int(m.spacing)} c/c" if m else "-"


def wrap_list(items: list[str], max_chars: int) -> list[str]:
    """Join ``items`` with ", " into lines of at most ``max_chars`` (items are never split)."""
    lines: list[str] = []
    cur = ""
    for i, it in enumerate(items):
        piece = it + ("," if i < len(items) - 1 else "")
        cand = f"{cur} {piece}" if cur else piece
        if cur and len(cand) > max_chars:
            lines.append(cur)
            cur = piece
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines or [""]


def level_name(project, i: int) -> str:
    if i <= 0:
        return "Foundation"
    if i - 1 < len(project.levels):
        return project.levels[i - 1].name
    return f"Level {i}"


def _range_label(a: str, b: str) -> str:
    if a == b:
        return a
    ma, mb = re.match(r"^(.*?)(\d+)$", a), re.match(r"^(.*?)(\d+)$", b)
    if ma and mb and ma.group(1) == mb.group(1) and ma.group(1).strip():
        return f"{a}-{mb.group(2)}"
    return f"{a} to {b}"


def levels_label(project, indices) -> str:
    """Compact description of a set of level indices, e.g. "Floor 1-4, Roof"."""
    idx = sorted(set(indices))
    runs: list[list[int]] = []
    for i in idx:
        if runs and i == runs[-1][-1] + 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    return ", ".join(_range_label(level_name(project, r[0]), level_name(project, r[-1])) for r in runs)


def distribute_bars(n: int, half_x: float, half_y: float) -> list[tuple[float, float]]:
    """Centres of ``n`` longitudinal bars on a rectangle centred at the origin.

    ``half_x``/``half_y`` are the half distances between corner-bar centres.
    The four corners are always filled; the remaining bars are shared between
    the two x-faces (y = ±half_y) and the two y-faces (x = ±half_x) in pairs,
    in proportion to the face lengths, and spaced evenly, so the arrangement
    is symmetric about both axes.  An odd extra bar goes to the middle of the
    bottom face (the only case that is not symmetric).
    """
    if n <= 0:
        return []
    corners = [(-half_x, -half_y), (half_x, -half_y), (half_x, half_y), (-half_x, half_y)]
    if n <= 4:
        order = [corners[0], corners[2], corners[1], corners[3]]  # diagonal pairs first
        return order[:n]
    rest = n - 4
    pairs = rest // 2
    lx, ly = 2 * half_x, 2 * half_y
    ky = round(pairs * ly / (lx + ly)) if lx + ly > 0 else pairs // 2  # per y-face (x = ±half_x)
    kx = pairs - ky  # per x-face (y = ±half_y)
    pts = list(corners)
    for j in range(1, kx + 1):
        x = -half_x + lx * j / (kx + 1)
        pts += [(x, -half_y), (x, half_y)]
    for j in range(1, ky + 1):
        y = -half_y + ly * j / (ky + 1)
        pts += [(-half_x, y), (half_x, y)]
    if rest % 2:
        if kx == 0:
            pts.append((0.0, -half_y))
        else:  # place it midway between the two bars nearest the centre of the bottom face
            xs = sorted(p[0] for p in pts if abs(p[1] + half_y) < 1e-12)
            gaps = [((xs[i] + xs[i + 1]) / 2, xs[i + 1] - xs[i]) for i in range(len(xs) - 1)]
            x = min(gaps, key=lambda g: (abs(g[0]), -g[1]))[0]
            pts.append((x, -half_y))
    return pts


# ---------------------------------------------------------------- recorder canvas
class Canvas:
    """Records drawing operations in local sheet coordinates (metres).

    Text sizes are given in paper millimetres and converted with the sheet
    scale; the operations are replayed into model space at an offset.
    """

    def __init__(self, scale: float):
        self.s = scale
        self.ops: list[tuple] = []
        self.ext = [math.inf, math.inf, -math.inf, -math.inf]

    def mm(self, v: float) -> float:
        return v * self.s / 1000.0

    def _grow(self, *pts):
        for x, y in pts:
            e = self.ext
            e[0], e[1], e[2], e[3] = min(e[0], x), min(e[1], y), max(e[2], x), max(e[3], y)

    @property
    def width(self) -> float:
        return max(self.ext[2] - self.ext[0], 0.0)

    @property
    def height(self) -> float:
        return max(self.ext[3] - self.ext[1], 0.0)

    def line(self, p, q, layer="DET_OUTLINE", **attr):
        if math.dist(p, q) < 1e-9:
            return
        self.ops.append(("line", tuple(p), tuple(q), layer, attr))
        self._grow(p, q)

    def poly(self, pts, layer="DET_OUTLINE", close=False, **attr):
        pts = [tuple(p) for p in pts]
        self.ops.append(("poly", pts, close, layer, attr))
        self._grow(*pts)

    def rect(self, x0, y0, x1, y1, layer="DET_OUTLINE", **attr):
        self.poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], layer, close=True, **attr)

    def circle(self, x, y, r, layer="DET_BAR", filled=True):
        self.ops.append(("circle", (x, y), r, layer, filled))
        self._grow((x - r, y - r), (x + r, y + r))

    def text(self, s, x, y, size_mm=2.5, align="L", layer="DET_TEXT", rot=0.0, **attr):
        if s is None or s == "":
            return 0.0
        h = self.mm(size_mm)
        w = text_width(s, h)
        self.ops.append(("text", str(s), (x, y), h, align, layer, rot, attr))
        if rot:
            self._grow((x - h, y - w), (x + h, y + w))
        else:
            x0 = x if align in ("L", "BL", "TL") else (x - w if align == "R" else x - w / 2)
            y0 = y - h / 2 if align in ("L", "C", "R") else (y if align.startswith("B") else y - h)
            self._grow((x0, y0), (x0 + w, y0 + h))
        return w

    def dim(self, p1, p2, offset, vertical=False):
        """Linear dimension between p1 and p2; ``offset`` is the signed distance of the dimension line."""
        if vertical:
            base = (p1[0] + offset, (p1[1] + p2[1]) / 2)
            angle = 90
        else:
            base = ((p1[0] + p2[0]) / 2, p1[1] + offset)
            angle = 0
        self.ops.append(("dim", tuple(base), tuple(p1), tuple(p2), angle))
        h = self.mm(2.5)
        if vertical:
            self._grow((base[0] - 1.5 * h, p1[1]), (base[0] + 1.5 * h, p2[1]))
        else:
            self._grow((p1[0], base[1] - 1.5 * h), (p2[0], base[1] + 1.5 * h))

    def emit(self, msp, dx: float, dy: float, counts: dict | None = None):
        def T(p):
            return (p[0] + dx, p[1] + dy)

        dimscale = self.s / 1000.0
        for op in self.ops:
            kind = op[0]
            if kind == "line":
                _, p, q, layer, attr = op
                msp.add_line(T(p), T(q), dxfattribs={"layer": layer, **attr})
            elif kind == "poly":
                _, pts, close, layer, attr = op
                msp.add_lwpolyline([T(p) for p in pts], close=close, dxfattribs={"layer": layer, **attr})
            elif kind == "circle":
                _, c, r, layer, filled = op
                msp.add_circle(T(c), r, dxfattribs={"layer": layer})
                if filled:
                    hatch = msp.add_hatch(color=256, dxfattribs={"layer": layer})
                    hatch.paths.add_edge_path().add_arc(T(c), r, 0, 360)
                if counts is not None:
                    counts["circles"] = counts.get("circles", 0) + 1
            elif kind == "text":
                _, s, p, h, align, layer, rot, attr = op
                t = msp.add_text(s, height=h, rotation=rot, dxfattribs={"layer": layer, "style": "Standard", **attr})
                t.set_placement(T(p), align=_ALIGN[align])
            elif kind == "dim":
                _, base, p1, p2, angle = op
                d = msp.add_linear_dim(
                    base=T(base),
                    p1=T(p1),
                    p2=T(p2),
                    angle=angle,
                    dimstyle="PW_DETAIL",
                    override={"dimscale": dimscale},
                    dxfattribs={"layer": "DET_DIM"},
                )
                d.render()


def table(cv: Canvas, x: float, y: float, headers: list[str], rows: list[list], size_mm=2.5, min_w=None):
    """Draw a table with its top-left corner at (x, y); cells may hold a string or a list of lines.

    Returns (width, height)."""
    h = cv.mm(size_mm)
    pad = cv.mm(2.0)
    lh = h * 1.6

    def lines(c):
        return [str(v) for v in c] if isinstance(c, list | tuple) else [str(c)]

    ncol = len(headers)
    widths = []
    for j in range(ncol):
        cells = [headers[j]] + [r[j] for r in rows]
        w = max(text_width(s, h) for c in cells for s in lines(c)) + 2 * pad
        if min_w:
            w = max(w, min_w[j] if isinstance(min_w, list) else min_w)
        widths.append(w)
    heights = [max(len(lines(c)) for c in r) * lh + 2 * pad - (lh - h) for r in [headers] + rows]
    W, H = sum(widths), sum(heights)
    cv.rect(x, y - H, x + W, y, "DET_TABLE")
    cv.rect(x, y - heights[0], x + W, y, "DET_TABLE")  # header box
    yy = y
    for i, r in enumerate([headers] + rows):
        if i:
            cv.line((x, yy), (x + W, yy), "DET_TABLE")
        xx = x
        for j, c in enumerate(r):
            for k, s in enumerate(lines(c)):
                cv.text(s, xx + pad, yy - pad - k * lh - h / 2, size_mm, "L")
            xx += widths[j]
        yy -= heights[i]
    xx = x
    for w in widths[:-1]:
        xx += w
        cv.line((xx, y), (xx, y - H), "DET_TABLE")
    return W, H


# ================================================================= COLUMNS
@dataclass
class ColumnCell:
    b: float
    d: float
    bars: BarSet
    tie: Links | None  # ties outside the confining zones (or throughout)
    tie_confined: Links | None = None  # IS 13920 special confining hoops within l0
    l0: float = 0.0  # m
    legs_b: int = 2  # confining hoop legs parallel to D, spaced across b
    legs_d: int = 2  # confining hoop legs parallel to b, spaced across D

    @property
    def size_text(self) -> str:
        return f"{self.b * 1000:.0f}x{self.d * 1000:.0f}, {self.bars}"

    @property
    def tie_text(self) -> str:
        t, c = self.tie, self.tie_confined
        if t is None:
            return "ties: revise"
        out = f"T{t.dia} @ {int(t.spacing)}" if t.legs <= 2 else f"T{t.dia} ({t.legs} legs) @ {int(t.spacing)}"
        if c is not None and self.l0 > 0:
            out = f"T{c.dia} ({self.legs_b}x{self.legs_d} legs) @ {int(c.spacing)} over l0={self.l0 * 1000:.0f} / {out}"
        return out

    @property
    def label(self) -> str:
        return f"{self.size_text}, {self.tie_text}"


@dataclass
class ColumnRow:
    marks: list[str]
    cells: list[ColumnCell | None]  # one per level range
    levels: list[int] = field(default_factory=list)  # level indices where the column exists


def _column_cell(c) -> ColumnCell | None:
    bars = c.main_bars or _parse_barset(c.bars)
    if bars is None:
        return None
    conf = getattr(c, "tie_confined", None)
    tie = c.tie or _parse_links((c.ties or "").split("/")[-1] if conf else c.ties)
    l0 = float(getattr(c, "l0", 0.0) or 0.0)
    lb = ld = conf.legs if conf else 2
    m = re.search(r"\((\d+)\s*[x×]\s*(\d+) legs\)", c.ties or "")
    if m:
        lb, ld = int(m.group(1)), int(m.group(2))
    return ColumnCell(round(c.b, 3), round(c.d, 3), bars, tie, conf, round(l0, 3), lb, ld)


def column_schedule(project, rep) -> tuple[list[tuple[int, int]], list[ColumnRow]]:
    """Group the column designs into schedule rows and level ranges.

    Returns (ranges, rows); ``ranges`` are (first, last) level indices and each
    row has one cell per range (None where the column does not exist)."""
    by_mark: dict[str, dict[int, ColumnCell]] = {}
    for c in rep.columns:
        cell = _column_cell(c)
        if cell is None:
            continue
        li = c.level_index or next((i for i, lv in enumerate(project.levels, 1) if lv.name == c.level), 0)
        by_mark.setdefault(c.mark, {})[li] = cell
    if not by_mark:
        return [], []
    all_lv = sorted({i for v in by_mark.values() for i in v})

    def sig(cell: ColumnCell | None):
        return None if cell is None else (cell.b, cell.d, cell.bars, cell.tie_text)

    groups: dict[tuple, list[str]] = {}
    for mark, per in by_mark.items():
        key = tuple(sig(per.get(i)) for i in all_lv)
        groups.setdefault(key, []).append(mark)
    sigs = list(groups)
    # level ranges: split wherever any row changes (or the levels are not contiguous)
    ranges: list[list[int]] = []
    for k, i in enumerate(all_lv):
        if ranges and i == ranges[-1][-1] + 1 and all(s[k] == s[k - 1] for s in sigs):
            ranges[-1].append(i)
        else:
            ranges.append([i])
    rows = []
    for marks in groups.values():
        rep_mark = sorted(marks, key=natural_key)[0]
        per = by_mark[rep_mark]
        cells = [per.get(r[0]) for r in ranges]
        rows.append(ColumnRow(sorted(marks, key=natural_key), cells, sorted(per)))
    rows.sort(key=lambda r: natural_key(r.marks[0]))
    return [(r[0], r[-1]) for r in ranges], rows


@dataclass
class WallRow:
    mark: str
    thickness: float
    length: float
    levels: list[int]


def wall_schedule(fm) -> list[WallRow]:
    """Shear walls (frame members of kind "wall"): one row per mark and run of identical size."""
    per: dict[str, dict[int, tuple[float, float]]] = {}
    for m in fm.members.values():
        if m.kind == "wall":
            per.setdefault(m.mark, {})[m.level] = (round(m.b, 3), round(m.d, 3))
    rows: list[WallRow] = []
    for mark in sorted(per, key=natural_key):
        for li, size in sorted(per[mark].items()):
            last = rows[-1] if rows else None
            if last and last.mark == mark and (last.thickness, last.length) == size and li == last.levels[-1] + 1:
                last.levels.append(li)
            else:
                rows.append(WallRow(mark, size[0], size[1], [li]))
    return rows


def _pick(values: list[float], n: int, lo: float, hi: float) -> list[float]:
    """``n`` positions for cross-tie legs: evenly chosen from ``values`` (interior bar positions)
    or, when there are too few bars, evenly spaced between ``lo`` and ``hi``."""
    if n <= 0:
        return []
    if len(values) >= n:
        return [values[round((k + 1) * (len(values) + 1) / (n + 1)) - 1] for k in range(n)]
    return [lo + (hi - lo) * (k + 1) / (n + 1) for k in range(n)]


def _draw_column_section(cv: Canvas, cx: float, cy: float, cell: ColumnCell, cover: float) -> int:
    b, d = cell.b, cell.d
    cv.rect(cx - b / 2, cy - d / 2, cx + b / 2, cy + d / 2, "DET_OUTLINE")
    cv.rect(cx - b / 2 + cover, cy - d / 2 + cover, cx + b / 2 - cover, cy + d / 2 - cover, "DET_OUTLINE", color=8)
    hoop = cell.tie_confined or cell.tie
    tdia = (hoop.dia if hoop else 8) / 1000
    off = cover + tdia / 2
    cv.rect(cx - b / 2 + off, cy - d / 2 + off, cx + b / 2 - off, cy + d / 2 - off, "DET_LINK")
    r = cell.bars.dia / 2000
    hx, hy = max(b / 2 - cover - tdia - r, 0.0), max(d / 2 - cover - tdia - r, 0.0)
    pts = distribute_bars(cell.bars.count, hx, hy)
    for x, y in pts:
        cv.circle(cx + x, cy + y, r)
    # cross ties of the confining hoops (legs beyond the two of the perimeter hoop)
    legs_b, legs_d = (cell.legs_b, cell.legs_d) if cell.tie_confined else (2, 2)
    xs = sorted({round(x, 9) for x, y in pts if abs(abs(y) - hy) < 1e-9 and abs(abs(x) - hx) > 1e-9})
    ys = sorted({round(y, 9) for x, y in pts if abs(abs(x) - hx) < 1e-9 and abs(abs(y) - hy) > 1e-9})
    for x in _pick(xs, legs_b - 2, -hx, hx):
        cv.line((cx + x, cy - d / 2 + off), (cx + x, cy + d / 2 - off), "DET_LINK")
    for y in _pick(ys, legs_d - 2, -hy, hy):
        cv.line((cx - b / 2 + off, cy + y), (cx + b / 2 - off, cy + y), "DET_LINK")
    return len(pts)


def _column_sheet(cv: Canvas, project, ranges, rows, walls: list[WallRow]) -> None:
    th = cv.mm(2.5)
    pad = cv.mm(4.0)
    cover = project.design.column_cover
    cv.text("COLUMN SCHEDULE", 0, 0, 7.0, "BL")
    top = -cv.mm(4.0)
    if not rows:
        cv.text("No column designs available.", 0, top - th, 2.5, "TL")
        y_end = top - th * 2
    else:
        y_end = _column_table(cv, project, ranges, rows, top, th, pad, cover)
    if walls:
        y = y_end - cv.mm(12)
        cv.text("WALL SCHEDULE", 0, y, 5.0, "BL")
        body = []
        for w in walls:
            lv = f"{level_name(project, w.levels[0] - 1)} to {level_name(project, w.levels[-1])}"
            body.append([w.mark, f"{w.thickness * 1000:.0f} x {w.length * 1000:.0f}", lv])
        _, H = table(cv, 0, y - cv.mm(3), ["MARK", "THICKNESS x LENGTH (mm)", "LEVELS"], body)
        note = "Shear walls: geometry only - reinforcement as per the wall design."
        cv.text(note, 0, y - cv.mm(3) - H - pad, 2.5, "TL")


def _column_table(cv: Canvas, project, ranges, rows, top, th, pad, cover) -> float:
    """Draw the column schedule table with its notes; returns the lowest y used."""
    cells = [c for r in rows for c in r.cells if c]
    sk_w = max(c.b for c in cells)
    sk_h = max(c.d for c in cells)
    headers = []
    for a, b in ranges:
        lab = f"{level_name(project, a - 1)} to {level_name(project, b)}"
        headers.append([lab] if len(lab) <= 22 else [f"{level_name(project, a - 1)} to", level_name(project, b)])
    txt_w = max(text_width(s, th) for c in cells for s in (c.size_text, c.tie_text))
    hdr_w = max(text_width(s, th) for h in headers for s in h)
    cw = max(sk_w + 2 * pad, txt_w + 2 * pad, hdr_w + 2 * pad)
    mark_lines = [wrap_list(r.marks, 18) for r in rows]
    mw = max(max(text_width(s, th) for s in ml) for ml in mark_lines + [["COLUMN MARKS"]]) + 2 * pad
    hh = pad + max(len(h) for h in headers) * th * 1.6
    row_h = [max(pad + sk_h + pad + th * 3.6 + pad, len(ml) * th * 1.6 + 2 * pad) for ml in mark_lines]
    W = mw + cw * len(ranges)
    H = hh + sum(row_h)
    y0 = top
    cv.rect(0, y0 - H, W, y0, "DET_TABLE")
    cv.line((0, y0 - hh), (W, y0 - hh), "DET_TABLE")
    cv.text("COLUMN MARKS", pad, y0 - hh / 2, 2.5, "L")
    for j, h in enumerate(headers):
        x = mw + cw * j
        cv.line((x, y0), (x, y0 - H), "DET_TABLE")
        for k, s in enumerate(h):
            cv.text(s, x + cw / 2, y0 - hh / 2 + (len(h) - 1) * th * 0.8 - k * th * 1.6, 2.5, "C")
    y = y0 - hh
    for r, ml, rh in zip(rows, mark_lines, row_h):
        for k, s in enumerate(ml):
            cv.text(s, pad, y - pad - th / 2 - k * th * 1.6, 2.5, "L")
        for j, cell in enumerate(r.cells):
            x = mw + cw * j
            if cell is None:
                cv.text("-", x + cw / 2, y - rh / 2, 2.5, "C")
                continue
            _draw_column_section(cv, x + cw / 2, y - pad - sk_h / 2, cell, cover)
            cv.text(cell.size_text, x + cw / 2, y - pad - sk_h - pad - th, 2.5, "C")
            cv.text(cell.tie_text, x + cw / 2, y - pad - sk_h - pad - th * 2.6, 2.5, "C")
        y -= rh
        cv.line((0, y), (W, y), "DET_TABLE")
    notes = [
        f"Clear cover to ties {cover * 1000:.0f} mm. Bars are shown at true size and distributed evenly on the faces.",
        "Ties: confining hoops (legs across b x across D) @ spacing over l0 at both ends of the clear height and"
        " through the joint (IS 13920) / ties elsewhere. Cross ties are drawn at intermediate bars.",
        "Lap longitudinal bars in the middle half of the storey height; not more than 50 % bars at one section.",
    ]
    for k, s in enumerate(notes):
        cv.text(s, 0, y0 - H - pad - k * th * 1.8, 2.5, "TL")
    return y0 - H - pad - len(notes) * th * 1.8


# ================================================================= FOOTINGS
@dataclass
class FootingType:
    mark: str
    L: float
    B: float
    D: float
    mesh_L: BarMesh | None
    mesh_B: BarMesh | None
    columns: list[str]
    col_b: float
    col_d: float


def footing_types(rep) -> list[FootingType]:
    groups: dict[tuple, list] = {}
    for f in rep.footings:
        mL = f.mesh_L or _parse_mesh(f.bars_L)
        mB = f.mesh_B or _parse_mesh(f.bars_B)
        key = (round(f.L, 3), round(f.B, 3), round(f.D, 3), _mesh_key(mL), _mesh_key(mB))
        groups.setdefault(key, []).append((f, mL, mB))
    types = []
    for key, items in sorted(groups.items(), key=lambda kv: (kv[0][0] * kv[0][1], kv[0][2], kv[0][0])):
        f0, mL, mB = items[0]
        types.append(
            FootingType(
                "",
                key[0],
                key[1],
                key[2],
                mL,
                mB,
                sorted((f.mark for f, _, _ in items), key=natural_key),
                max((f.col_b for f, _, _ in items), default=0.0) or 0.3,
                max((f.col_d for f, _, _ in items), default=0.0) or 0.3,
            )
        )
    for i, t in enumerate(types, 1):
        t.mark = f"F{i}"
    return types


def _bar_positions(length: float, cover: float, spacing: float) -> list[float]:
    avail = length - 2 * cover
    if avail <= 0 or spacing <= 0:
        return []
    n = int(avail / spacing + 1e-9) + 1
    start = cover + (avail - (n - 1) * spacing) / 2
    return [start + i * spacing for i in range(n)]


def _footing_block(cv: Canvas, x0: float, y0: float, t: FootingType, cover: float) -> tuple[float, float]:
    """Plan + section of one footing type with the top-left corner at (x0, y0); returns (w, h)."""
    th = cv.mm(2.5)
    gap = cv.mm(14.0)
    L, B, D = t.L, t.B, t.D
    title_lines = [f"{t.mark}  {L * 1000:.0f} x {B * 1000:.0f} x {D * 1000:.0f}"] + [
        "Columns: " + s for s in wrap_list(t.columns, 40)
    ]
    for k, s in enumerate(title_lines):
        cv.text(s, x0, y0 - (k + 0.6 * (k > 0)) * th * 1.7, 3.5 if k == 0 else 2.5, "TL")
    ytop = y0 - (len(title_lines) + 0.6) * th * 1.7 - cv.mm(6)
    # -------------------------------------------------- plan
    px, py = x0 + gap, ytop - B  # bottom-left of plan
    cv.rect(px, py, px + L, py + B, "DET_OUTLINE")
    cv.rect(px + L / 2 - t.col_d / 2, py + B / 2 - t.col_b / 2, px + L / 2 + t.col_d / 2, py + B / 2 + t.col_b / 2)
    cv.line((px + L / 2 - t.col_d / 2, py + B / 2 - t.col_b / 2), (px + L / 2 + t.col_d / 2, py + B / 2 + t.col_b / 2))
    cv.line((px + L / 2 - t.col_d / 2, py + B / 2 + t.col_b / 2), (px + L / 2 + t.col_d / 2, py + B / 2 - t.col_b / 2))
    ysL = _bar_positions(B, cover, t.mesh_L.spacing / 1000) if t.mesh_L else []
    xsB = _bar_positions(L, cover, t.mesh_B.spacing / 1000) if t.mesh_B else []
    for y in ysL:  # bars along L
        cv.line((px + cover, py + y), (px + L - cover, py + y), "DET_BAR")
    for x in xsB:  # bars along B
        cv.line((px + x, py + cover), (px + x, py + B - cover), "DET_BAR")
    cv.dim((px, py), (px + L, py), -cv.mm(7))
    cv.dim((px, py), (px, py + B), -cv.mm(7), vertical=True)
    ly = py - cv.mm(12)
    lab_L = f"{_mesh_str(t.mesh_L)} along L ({len(ysL)} nos)"
    lab_B = f"{_mesh_str(t.mesh_B)} along B ({len(xsB)} nos)"
    cv.text(lab_L, px, ly, 2.5, "TL")
    cv.text(lab_B, px, ly - th * 1.7, 2.5, "TL")
    cv.text("PLAN", px + L / 2, ly - th * 4.0, 3.5, "TC")
    plan_bottom = ly - th * 5.5
    # -------------------------------------------------- section (cut along L)
    pcc = 0.1
    sx = px + L + gap + pcc + max(text_width(lab_B, th) - L - gap, 0.0)
    stub = min(max(0.6, D), 1.0)
    sy = ytop - stub - D  # top of PCC / underside of footing
    cv.rect(sx, sy, sx + L, sy + D, "DET_OUTLINE")
    cv.rect(sx - pcc, sy - pcc, sx + L + pcc, sy, "DET_OUTLINE", color=8)
    cxl, cxr = sx + L / 2 - t.col_d / 2, sx + L / 2 + t.col_d / 2
    cv.line((cxl, sy + D), (cxl, sy + D + stub))
    cv.line((cxr, sy + D), (cxr, sy + D + stub))
    zz = [
        (cxl - 0.03, sy + D + stub),
        (cxl + t.col_d * 0.4, sy + D + stub),
        (cxl + t.col_d * 0.5, sy + D + stub + 0.04),
    ]
    zz += [
        (cxl + t.col_d * 0.5, sy + D + stub - 0.04),
        (cxl + t.col_d * 0.6, sy + D + stub),
        (cxr + 0.03, sy + D + stub),
    ]
    cv.poly(zz, "DET_OUTLINE")  # break line
    dL = (t.mesh_L.dia if t.mesh_L else 12) / 1000
    dB = (t.mesh_B.dia if t.mesh_B else 12) / 1000
    up = max(min(D - 2 * cover, L / 4), 0.05)
    yb = sy + cover + dL / 2
    cv.poly([(sx + cover, yb + up), (sx + cover, yb), (sx + L - cover, yb), (sx + L - cover, yb + up)], "DET_BAR")
    for x in xsB:
        cv.circle(sx + x, yb + dL / 2 + dB / 2, dB / 2)
    # column starter bars with 90 degree bends on the mesh
    for xs_, sgn in ((cxl + 0.05, -1), (cxr - 0.05, 1)):
        ybend = yb + dL / 2 + dB + 0.02
        cv.poly([(xs_, sy + D + stub - 0.05), (xs_, ybend), (xs_ + sgn * min(0.3, L / 2 - 0.1), ybend)], "DET_BAR")
    cv.dim((sx + L, sy), (sx + L, sy + D), cv.mm(8), vertical=True)
    cv.dim((sx - pcc, sy - pcc), (sx + L + pcc, sy - pcc), -cv.mm(7))
    cv.text("PCC M10, 100 thk", sx + L / 2, sy - pcc - cv.mm(14), 2.5, "TC")
    cv.text("Column bars as per schedule", sx + L / 2 + t.col_d / 2 + cv.mm(2), sy + D + stub * 0.6, 2.5, "L")
    cv.text("SECTION", sx + L / 2, sy - pcc - cv.mm(14) - th * 2.5, 3.5, "TC")
    sec_bottom = sy - pcc - cv.mm(14) - th * 4.5
    right = sx + L + pcc + cv.mm(18) + text_width("Column bars as per schedule", th) * 0.5
    return right - x0, y0 - min(plan_bottom, sec_bottom)


def _footing_pages(s: float, project, types: list[FootingType]) -> list[Canvas]:
    cover = project.design.footing_cover
    notes = [
        f"Clear cover {cover * 1000:.0f} mm. Bottom bars bent up at the ends; L is along the column depth.",
        "Footings are founded on PCC M10 100 thick projecting 100 mm. Dimensions in mm.",
    ]

    def head(cv: Canvas, top: float) -> float:
        rows = [
            [
                t.mark,
                wrap_list(t.columns, 30),
                f"{t.L * 1000:.0f}",
                f"{t.B * 1000:.0f}",
                f"{t.D * 1000:.0f}",
                _mesh_str(t.mesh_L),
                _mesh_str(t.mesh_B),
                str(len(t.columns)),
            ]
            for t in types
        ]
        hdr = ["TYPE", "COLUMNS", "L (mm)", "B (mm)", "D (mm)", "BOTTOM BARS ALONG L", "BOTTOM BARS ALONG B", "NOS"]
        return table(cv, 0, top, hdr, rows)[1]

    blocks = [lambda cv, x, y, t=t: _footing_block(cv, x, y, t, cover) for t in types]
    return _flow_pages(s, "FOOTING SCHEDULE", blocks, notes, head if types else None, "No footing designs available.")


def _flow_pages(s, title, blocks, notes, head=None, empty="") -> list[Canvas]:
    """Lay out detail ``blocks`` (callables ``(cv, x, y) -> (w, h)`` drawing with the top-left at x, y)
    in rows across A1 pages at scale ``s``; returns one canvas per page."""
    probe = Canvas(s)
    th, gap_x, gap_y = probe.mm(2.5), probe.mm(18), probe.mm(12)
    W = CONTENT_W * s / 1000
    notes_h = len(notes) * th * 1.8 + gap_y
    limit = -(CONTENT_H * s / 1000 - probe.mm(9)) + notes_h  # lowest y a block may reach
    sizes = [b(Canvas(s), 0.0, 0.0) for b in blocks]
    pages: list[Canvas] = []

    def new_page():
        cv = Canvas(s)
        cv.text(title if not pages else f"{title} (continued)", 0, 0, 7.0, "BL")
        top = -cv.mm(6.0)
        if head is not None and not pages:
            top -= head(cv, top) + cv.mm(10)
        return cv, top

    cv, y = new_page()
    if not blocks and empty:
        cv.text(empty, 0, y - th, 2.5, "TL")
    x, row_h, on_page = 0.0, 0.0, 0
    for b, (w, h) in zip(blocks, sizes):
        if x > 0 and x + w > W:
            x, y, row_h = 0.0, y - row_h - gap_y, 0.0
        if on_page and y - h < limit:
            pages.append(cv)
            cv, y = new_page()
            x, row_h, on_page = 0.0, 0.0, 0
        b(cv, x, y)
        x += w + gap_x
        row_h = max(row_h, h)
        on_page += 1
    yn = y - row_h - gap_y / 2
    for k, n in enumerate(notes):
        cv.text(n, 0, yn - k * th * 1.8, 2.5, "TL")
    pages.append(cv)
    return pages


# ================================================================= BEAMS
@dataclass
class Support:
    kind: str  # "column" | "wall" | "beam" | "free"
    width: float  # along the beam (m)
    above: bool = False  # column / wall continues above
    mark: str = ""

    @property
    def held(self) -> bool:
        """Framed into a column or wall (vertical member) rather than another beam or free."""
        return self.kind in ("column", "wall")


@dataclass
class PhysBeam:
    mark: str
    level_index: int
    b: float
    d: float
    spans: list[float]
    supports: list[Support]
    bottom: list[BarSet]
    tops: list[BarSet | None]  # per support node
    links: list[Links | None]  # per span, away from column faces
    links_end: list[Links | None] = field(default_factory=list)  # per span, within 2d of column faces
    side_face: list[str] = field(default_factory=list)  # per span, side-face bars ("" if none)


@dataclass
class BeamGroup:
    beams: list[PhysBeam]

    @property
    def rep(self) -> PhysBeam:
        return self.beams[0]

    def label(self, project) -> str:
        """e.g. "B3, B7, B12 (Floor 1-4)"; levels with a different set of marks are listed separately."""
        per_level: dict[int, set[str]] = {}
        for b in self.beams:
            per_level.setdefault(b.level_index, set()).add(b.mark)
        by_marks: dict[tuple[str, ...], list[int]] = {}
        for li, marks in sorted(per_level.items()):
            by_marks.setdefault(tuple(sorted(marks, key=natural_key)), []).append(li)
        return "; ".join(f"{', '.join(m)} ({levels_label(project, lv)})" for m, lv in by_marks.items())


def _bigger(a: BarSet | None, b: BarSet | None) -> BarSet | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if (a.area, a.count) >= (b.area, b.count) else b


def physical_beams(project, fm, rep) -> list[PhysBeam]:
    """Aggregate frame segments into one record per plan beam and level."""
    vert = ("column", "wall")  # rigid "link" members of the wall model are not drawn
    col_below = {m.n2: m for m in fm.members.values() if m.kind in vert}
    col_above = {m.n1: m for m in fm.members.values() if m.kind in vert}
    wall_link = {m.n2: m for m in fm.members.values() if m.kind == "link"}  # joint in a wall -> link
    beam_nodes: dict[int, set[str]] = {}
    for m in fm.members.values():
        if m.kind == "beam":
            for n in (m.n1, m.n2):
                beam_nodes.setdefault(n, set()).add(f"{m.group}@{m.level}")
    segs: dict[tuple[str, int], list] = {}
    for bd in rep.beams:
        mem = fm.members.get(bd.member_id)
        if mem is None:
            continue
        segs.setdefault((bd.group or mem.group, bd.level_index or mem.level), []).append((bd, mem))
    out = []
    for (_grp, li), items in segs.items():
        nodes = {n for _, m in items for n in (m.n1, m.n2)}
        pts = {n: (fm.nodes[n].x, fm.nodes[n].y) for n in nodes}
        a, b = max(((p, q) for p in nodes for q in nodes), key=lambda pq: math.dist(pts[pq[0]], pts[pq[1]]))
        if pts[a] > pts[b]:
            a, b = b, a
        L = math.dist(pts[a], pts[b]) or 1.0
        ux, uy = (pts[b][0] - pts[a][0]) / L, (pts[b][1] - pts[a][1]) / L

        def t_of(n, a=a, ux=ux, uy=uy, pts=pts):
            return (pts[n][0] - pts[a][0]) * ux + (pts[n][1] - pts[a][1]) * uy

        ordered = []
        for bd, m in items:
            bot = bd.bottom_bars or _parse_barset(bd.bottom) or BarSet(2, 12)
            tl = bd.top_l_bars or _parse_barset(bd.top_l)
            tr = bd.top_r_bars or _parse_barset(bd.top_r)
            lk = bd.links or _parse_links(bd.stirrups)
            le = getattr(bd, "links_end", None)
            sf = getattr(bd, "side_face", "") or ""
            n1, n2 = m.n1, m.n2
            if t_of(n1) > t_of(n2):
                n1, n2, tl, tr = n2, n1, tr, tl
            ordered.append((t_of(n1), t_of(n2), n1, n2, bot, tl, tr, lk, bd, m, le, sf))
        ordered.sort(key=lambda r: r[0])
        node_seq = [ordered[0][2]] + [r[3] for r in ordered]
        spans = [max(r[1] - r[0], 0.0) for r in ordered]
        bd0, m0 = ordered[0][8], ordered[0][9]
        sups = []
        for n in node_seq:
            if n in col_below or n in col_above:
                c = col_below.get(n) or col_above.get(n)
                ang = math.radians(c.angle)
                half = abs(ux * math.cos(ang) + uy * math.sin(ang)) * c.b / 2
                half += abs(-ux * math.sin(ang) + uy * math.cos(ang)) * c.d / 2
                sups.append(Support(c.kind, 2 * half, n in col_above, c.mark))
            elif n in wall_link and wall_link[n].n1 in col_below:
                w = col_below[wall_link[n].n1]  # beam framing into a wall joint: shown as the wall thickness
                sups.append(Support("wall", w.b, wall_link[n].n1 in col_above, w.mark))
            elif len(beam_nodes.get(n, ())) > 1:
                sups.append(Support("beam", 0.0))
            else:
                sups.append(Support("free", 0.0))
        tops: list[BarSet | None] = []
        for j in range(len(node_seq)):
            left = ordered[j - 1][6] if j > 0 else None
            right = ordered[j][5] if j < len(ordered) else None
            tops.append(_bigger(left, right))
        out.append(
            PhysBeam(
                bd0.mark,
                li,
                round(m0.b, 3),
                round(m0.d, 3),
                spans,
                sups,
                [r[4] for r in ordered],
                tops,
                [r[7] for r in ordered],
                [r[10] for r in ordered],
                [r[11] for r in ordered],
            )
        )
    out.sort(key=lambda p: (p.level_index, natural_key(p.mark)))
    return out


def _same_design(a: PhysBeam, b: PhysBeam, tol: float = 0.05) -> bool:
    if (a.b, a.d, len(a.spans)) != (b.b, b.d, len(b.spans)):
        return False
    for rev in (False, True):
        sp = b.spans[::-1] if rev else b.spans
        bo = b.bottom[::-1] if rev else b.bottom
        tp = b.tops[::-1] if rev else b.tops
        lk = b.links[::-1] if rev else b.links
        le = b.links_end[::-1] if rev else b.links_end
        sf = b.side_face[::-1] if rev else b.side_face
        if (
            all(abs(x - y) <= tol + 1e-9 for x, y in zip(a.spans, sp))
            and a.bottom == bo
            and a.tops == tp
            and a.links == lk
            and a.links_end == le
            and a.side_face == sf
        ):
            return True
    return False


def beam_groups(project, fm, rep) -> list[BeamGroup]:
    """Group physical beams with the same size, spans (within 50 mm) and reinforcement."""
    groups: list[BeamGroup] = []
    for pb in physical_beams(project, fm, rep):
        for g in groups:
            if _same_design(g.rep, pb):
                g.beams.append(pb)
                break
        else:
            groups.append(BeamGroup([pb]))
    return groups


def _side_bars(text: str) -> BarSet | None:
    """Side-face bars per face from the design text, e.g. "2-T12 each face (...)"."""
    return _parse_barset(text) if text else None


def _beam_section(
    cv: Canvas, cx: float, ybot: float, pb: PhysBeam, top: BarSet | None, bot: BarSet, link, k, cover, side=None
):
    """Cross-section magnified by ``k`` with its soffit centre at (cx, ybot)."""
    b, d = pb.b * k, pb.d * k
    c = cover * k
    cv.rect(cx - b / 2, ybot, cx + b / 2, ybot + d)
    ld = (link.dia if link else 8) / 1000 * k
    cv.rect(cx - b / 2 + c + ld / 2, ybot + c + ld / 2, cx + b / 2 - c - ld / 2, ybot + d - c - ld / 2, "DET_LINK")
    for bars, yc in ((bot, ybot + c + ld), (top, ybot + d - c - ld)):
        if not bars:
            continue
        r = bars.dia / 2000 * k
        y = yc + r if yc < ybot + d / 2 else yc - r
        n = bars.count
        x0, x1 = cx - b / 2 + c + ld + r, cx + b / 2 - c - ld - r
        for i in range(n):
            x = (x0 + x1) / 2 if n == 1 else x0 + (x1 - x0) * i / (n - 1)
            cv.circle(x, y, r)
    if side:  # side-face bars, evenly between the top and bottom layers on both faces
        r = side.dia / 2000 * k
        ya, yb = ybot + c + ld + 3 * r, ybot + d - c - ld - 3 * r
        for i in range(side.count):
            y = ya + (yb - ya) * (i + 1) / (side.count + 1)
            for x in (cx - b / 2 + c + ld + r, cx + b / 2 - c - ld - r):
                cv.circle(x, y, r)


def _beam_detail(cv: Canvas, x0: float, y0: float, n: int, g: BeamGroup, project) -> tuple[float, float]:
    """Elevation + two sections of one beam group with the top-left corner at (x0, y0); returns (w, h)."""
    pb = g.rep
    th = cv.mm(2.5)
    cover = project.design.beam_cover
    D = pb.d
    title = f"BM{n}: {g.label(project)}"
    sub = f"{pb.b * 1000:.0f} x {D * 1000:.0f}, span(s) " + " + ".join(f"{s * 1000:.0f}" for s in pb.spans)
    side_txt = sorted({t for t in pb.side_face if t})
    if side_txt:
        sub += "; side face: " + " / ".join(t.split(" (")[0] for t in side_txt)
    cv.text(title, x0, y0, 3.5, "TL")
    cv.text(sub, x0, y0 - th * 1.9, 2.5, "TL")
    stub = max(0.4, D * 0.6)
    xs = [0.0]
    for s in pb.spans:
        xs.append(xs[-1] + s)
    w0 = pb.supports[0].width / 2
    ex0 = x0 + w0 + cv.mm(4)  # elevation origin (centre of first support)
    ys = y0 - th * 1.9 - th * 2.5 - th * 3.5 - stub - D  # soffit
    Ltot = xs[-1]
    wn = pb.supports[-1].width / 2
    # outline + supports
    cv.line((ex0 - w0, ys), (ex0 + Ltot + wn, ys))
    cv.line((ex0 - w0, ys + D), (ex0 + Ltot + wn, ys + D))
    cv.line((ex0 - w0, ys), (ex0 - w0, ys + D))
    cv.line((ex0 + Ltot + wn, ys), (ex0 + Ltot + wn, ys + D))
    for x, sp in zip(xs, pb.supports):
        X = ex0 + x
        if sp.held:
            hw = sp.width / 2
            for xx in (X - hw, X + hw):
                cv.line((xx, ys), (xx, ys - stub))
                if sp.above:
                    cv.line((xx, ys + D), (xx, ys + D + stub))
            cv.line((X - hw - 0.03, ys - stub), (X + hw + 0.03, ys - stub), color=8)
            if sp.above:
                cv.line((X - hw - 0.03, ys + D + stub), (X + hw + 0.03, ys + D + stub), color=8)
            cv.text(sp.mark if sp.kind == "column" else f"{sp.mark} (wall)", X, ys - stub - th, 2.5, "TC")
        elif sp.kind == "beam":
            cv.line((X, ys - 0.1), (X, ys + D + 0.1), "DET_OUTLINE", color=8)
            cv.text("SB", X, ys - 0.1 - th, 2.0, "TC")
    # bars
    dl = (next((lk.dia for lk in pb.links if lk), 8)) / 1000
    yb = ys + cover + dl + pb.bottom[0].dia / 2000
    ytop = ys + D - cover - dl
    hook = 0.15
    hw = [sp.width / 2 for sp in pb.supports]
    for i, bars in enumerate(pb.bottom):
        xa = ex0 + xs[i] - (hw[i] - cover if i == 0 else hw[i])
        xb = ex0 + xs[i + 1] + (hw[i + 1] - cover if i == len(pb.spans) - 1 else hw[i + 1])
        pts = [(xa, yb), (xb, yb)]
        if i == 0 and pb.supports[0].held:
            pts.insert(0, (xa, yb + hook))
        if i == len(pb.spans) - 1 and pb.supports[-1].held:
            pts.append((xb, yb + hook))
        cv.poly(pts, "DET_BAR")
        cv.text(f"{bars} (bottom)", ex0 + xs[i] + 0.35 * (xs[i + 1] - xs[i]), ys - th * 1.4, 2.5, "TC")
    free = [sp.kind == "free" for sp in pb.supports]
    for j, top in enumerate(pb.tops):
        if top is None or free[j]:
            continue  # a free (cantilever) end is covered by the bars of the support behind it
        yt = ytop - top.dia / 2000
        left = pb.spans[j - 1] if j > 0 else 0.0
        right = pb.spans[j] if j < len(pb.spans) else 0.0
        # over a cantilever the support bars run to the tip; otherwise 0.3 x span beyond the face
        if left and free[j - 1]:
            xa = ex0 + xs[j - 1] + cover
        else:
            xa = ex0 + xs[j] - hw[j] - 0.3 * left if left else ex0 + xs[j] - hw[j] + cover
        if right and free[j + 1]:
            xb = ex0 + xs[j + 1] - cover
        else:
            xb = ex0 + xs[j] + hw[j] + 0.3 * right if right else ex0 + xs[j] + hw[j] - cover
        pts = [(xa, yt), (xb, yt)]
        if (not left and pb.supports[j].held) or (left and free[j - 1]):
            pts.insert(0, (xa, yt - hook))
        if (not right and pb.supports[j].held) or (right and free[j + 1]):
            pts.append((xb, yt - hook))
        cv.poly(pts, "DET_BAR")
        tx = ex0 + xs[j] + (hw[j] + 0.2 * right if right else -hw[j] - 0.2 * left)
        cv.text(f"{top} (top)", tx, ys + D + th * 1.2, 2.5, "BC")
    hanger = BarSet(2, 12)
    for i, s in enumerate(pb.spans):  # hangers lap 0.05 x span with the support bars
        if free[i] or free[i + 1]:
            continue
        xa = ex0 + xs[i] + hw[i] + 0.25 * s
        xb = ex0 + xs[i + 1] - hw[i + 1] - 0.25 * s
        if xb - xa > 0.05:
            cv.line((xa, ytop - 0.006 - 0.012), (xb, ytop - 0.006 - 0.012), "DET_BAR")
            cv.text(f"{hanger} hanger", ex0 + (xs[i] + xs[i + 1]) / 2, ys + D + th * 2.9, 2.5, "BC")
    # side-face bars (one face visible in elevation)
    for i, txt in enumerate(pb.side_face):
        sb = _side_bars(txt)
        if not sb:
            continue
        ya, yb2 = yb + 0.03, ytop - 0.03
        for q in range(sb.count):
            yy = ya + (yb2 - ya) * (q + 1) / (sb.count + 1)
            cv.line((ex0 + xs[i] + hw[i], yy), (ex0 + xs[i + 1] - hw[i + 1], yy), "DET_BAR", color=6)
    # stirrups: links_end within 2d of column / wall faces (IS 13920 cl 6.3.5), links elsewhere
    d_eff = D - cover - dl - max(bs.dia for bs in pb.bottom) / 2000
    yz = ys - stub - th * 3.2  # spacing annotation strip
    for i, lk in enumerate(pb.links):
        fa = ex0 + xs[i] + hw[i]
        fb = ex0 + xs[i + 1] - hw[i + 1]
        if fb - fa <= 0.1:
            continue
        if lk is None:
            cv.text("LINKS: REVISE SECTION", (fa + fb) / 2, yz, 2.5, "TC")
            continue
        le = pb.links_end[i] if i < len(pb.links_end) else None
        za_on = le is not None and pb.supports[i].held
        zb_on = le is not None and pb.supports[i + 1].held
        zl = min(2 * d_eff, (fb - fa) / (za_on + zb_on)) if za_on or zb_on else 0.0
        z0 = fa + (zl if za_on else 0.0)
        z1 = fb - (zl if zb_on else 0.0)
        for za, zb, zl_ in ((fa, z0, le), (z0, z1, lk), (z1, fb, le)):
            if zb - za <= 1e-6 or zl_ is None:
                continue
            legs = "" if zl_.legs == 2 else f"{zl_.legs}L-"
            sp = zl_.spacing
            sp_m = sp / 1000
            k = 0
            x = za + 0.05
            while x <= zb - 1e-6 and k < 2000:
                cv.line((x, ys + cover), (x, ys + D - cover), "DET_LINK")
                x += sp_m
                k += 1
            cv.line((za, yz + th * 0.6), (zb, yz + th * 0.6), "DET_DIM")
            for xx in (za, zb):
                cv.line((xx, yz + th * 0.2), (xx, yz + th * 1.0), "DET_DIM")
            cv.text(f"{legs}T{zl_.dia}@{int(sp)}", (za + zb) / 2, yz, 2.0, "TC")
    # section marks
    jsup = max(range(len(pb.tops)), key=lambda j: pb.tops[j].area if pb.tops[j] else 0.0)
    imid = max(range(len(pb.spans)), key=lambda i: (pb.bottom[i].area, pb.spans[i]))
    x1 = ex0 + xs[jsup] + (hw[jsup] + 0.1 if jsup < len(pb.spans) else -hw[jsup] - 0.1)
    x2 = ex0 + xs[imid] + 0.75 * pb.spans[imid]
    for xx, lab in ((x1, "1"), (x2, "2")):
        cv.line((xx, ys - 0.08), (xx, ys - 0.02), "DET_TEXT")
        cv.line((xx, ys + D + 0.02), (xx, ys + D + 0.08), "DET_TEXT")
        cv.text(lab, xx, ys - 0.08 - cv.mm(0.5), 2.0, "TC")
    elev_right = ex0 + Ltot + wn + cv.mm(4)
    elev_right = max(elev_right, x0 + text_width(title, cv.mm(3.5)), x0 + text_width(sub, th))
    # sections
    k = max(1.0, cv.s / SECTION_SCALE)
    sec_gap = cv.mm(18)
    sx = elev_right + sec_gap
    bw = pb.b * k
    isup = min(jsup, len(pb.links) - 1)
    lk_sup = pb.links[isup]
    le_sup = pb.links_end[isup] if isup < len(pb.links_end) else None
    if le_sup is not None and pb.supports[jsup].held:  # the support section lies within 2d of the face
        lk_sup = le_sup
    lk_mid = pb.links[imid]
    side_sup = _side_bars(pb.side_face[isup]) if isup < len(pb.side_face) else None
    side_mid = _side_bars(pb.side_face[imid]) if imid < len(pb.side_face) else None
    bot_sup = pb.bottom[min(jsup, len(pb.bottom) - 1)]
    sec_scale = f"1:{int(round(cv.s / k))}"
    labels = []
    for idx, (top, bot, lk, side, name) in enumerate(
        (
            (pb.tops[jsup], bot_sup, lk_sup, side_sup, "SECTION 1-1 (support)"),
            (hanger, pb.bottom[imid], lk_mid, side_mid, "SECTION 2-2 (midspan)"),
        )
    ):
        cx = sx + bw / 2 + idx * (bw + cv.mm(45))
        _beam_section(cv, cx, ys, pb, top, bot, lk, k, cover, side)
        if side:
            cv.text(f"{side} EF", cx + bw / 2 + cv.mm(2), ys + pb.d * k / 2 - cv.mm(4), 2.0, "L")
        cv.text(f"{top}", cx + bw / 2 + cv.mm(2), ys + pb.d * k - cv.mm(2), 2.0, "L")
        cv.text(f"{bot}", cx + bw / 2 + cv.mm(2), ys + cv.mm(2), 2.0, "L")
        cv.text(str(lk) if lk else "links: revise", cx + bw / 2 + cv.mm(2), ys + pb.d * k / 2, 2.0, "L")
        cv.text(name, cx, ys - th * 1.2, 2.5, "TC")
        cv.text(f"({sec_scale})", cx, ys - th * 2.7, 2.0, "TC")
        labels.append(cx + bw / 2 + cv.mm(2) + text_width(str(lk) if lk else "links: revise", cv.mm(2.0)))
    right = max(labels + [sx + bw + cv.mm(45) + bw])
    bottom = min(yz - th * 1.5, ys - th * 4.0)
    return right - x0, y0 - bottom


def _beam_pages(s: float, project, groups: list[BeamGroup]) -> list[Canvas]:
    notes = [
        f"Clear cover {project.design.beam_cover * 1000:.0f} mm. Top bars extend 0.3 x span beyond the support face;"
        " bottom bars run full span into the supports.",
        "Hanger bars 2-T12 lap with the support bars. Stirrups with 135 degree hooks;"
        " first stirrup 50 mm from the face.",
        "SB = secondary beam / beam junction. Identical beams (size, spans within 50 mm, reinforcement)"
        " are detailed once.",
    ]
    if any(le for g in groups for le in g.rep.links_end):
        notes.append("Closer stirrup spacing applies within 2d of column / wall faces (IS 13920 cl 6.3.5).")
    if any(t for g in groups for t in g.rep.side_face):
        notes.append("Side-face bars (EF = each face) shown in magenta, lapped at supports.")
    blocks = [lambda cv, x, y, n=n, g=g: _beam_detail(cv, x, y, n, g, project) for n, g in enumerate(groups, 1)]
    return _flow_pages(s, "BEAM DETAILS", blocks, notes, None, "No beam designs available.")


# ================================================================= SLABS
@dataclass
class SlabGroup:
    mark: str
    kind: str
    D_mm: float
    mesh_x: BarMesh | None
    mesh_y: BarMesh | None
    mesh_neg: BarMesh | None
    panels: list[tuple[str, str, float, float]]  # (plan, mark, lx, ly)


def slab_groups(rep) -> list[SlabGroup]:
    groups: dict[tuple, SlabGroup] = {}
    for plan, s in rep.slabs:
        mx = getattr(s, "mesh_x", None) or _parse_mesh(getattr(s, "ast_x", ""))
        my = getattr(s, "mesh_y", None) or _parse_mesh(getattr(s, "ast_y", ""))
        mn = getattr(s, "mesh_neg", None) or _parse_mesh(getattr(s, "ast_neg", ""))
        key = (s.kind, round(s.D_mm), _mesh_key(mx), _mesh_key(my), _mesh_key(mn))
        g = groups.get(key)
        if g is None:
            g = groups[key] = SlabGroup("", s.kind, s.D_mm, mx, my, mn, [])
        g.panels.append((plan, s.mark, s.lx, s.ly))
    out = sorted(groups.values(), key=lambda g: (-g.D_mm, g.kind, -max(p[2] for p in g.panels)))
    for i, g in enumerate(out, 1):
        g.mark = f"ST{i}"
    return out


def _slab_sheet(cv: Canvas, project, groups: list[SlabGroup]) -> None:
    th = cv.mm(2.5)
    cv.text("SLAB SCHEDULE", 0, 0, 7.0, "BL")
    top = -cv.mm(4.0)
    if not groups:
        cv.text("No slab designs available.", 0, top - th, 2.5, "TL")
        return
    rows = []
    for g in groups:
        plans: dict[str, list[str]] = {}
        for plan, mark, _, _ in g.panels:
            plans.setdefault(plan, []).append(mark)
        lx = sorted({round(p[2], 2) for p in g.panels})
        ly = sorted({round(p[3], 2) for p in g.panels})
        span = (f"{lx[0]:.2f}" if len(lx) == 1 else f"{lx[0]:.2f}-{lx[-1]:.2f}") + " x "
        span += f"{ly[0]:.2f}" if len(ly) == 1 else f"{ly[0]:.2f}-{ly[-1]:.2f}"
        rows.append(
            [
                g.mark,
                [p for p in plans],
                [", ".join(sorted(m, key=natural_key)) for m in plans.values()],
                span,
                f"{g.D_mm:.0f}",
                g.kind.replace("_", "-"),
                _mesh_str(g.mesh_x),
                _mesh_str(g.mesh_y),
                _mesh_str(g.mesh_neg),
            ]
        )
    for r in rows:  # wrap long panel lists, keeping the plan column aligned
        wrapped_p, wrapped_m = [], []
        for p, ms in zip(r[1], r[2]):
            parts = wrap_list(ms.split(", "), 36)
            wrapped_p += [p] + [""] * (len(parts) - 1)
            wrapped_m += parts
        r[1], r[2] = wrapped_p, wrapped_m
    hdr = ["TYPE", "PLAN", "PANELS", "lx x ly (m)", "D (mm)", "KIND", "BOTTOM SHORT", "BOTTOM LONG", "TOP (SUPPORTS)"]
    _, H = table(cv, 0, top, hdr, rows)
    notes = [
        f"Clear cover {project.design.slab_cover * 1000:.0f} mm. Short-span bottom bars below long-span bars.",
        "Top bars over continuous supports extend 0.3 x short span from the support face; curtail per IS 456 / SP 34.",
        "Cantilever slabs: main bars at top, anchored into the back span.",
    ]
    for k, s in enumerate(notes):
        cv.text(s, 0, top - H - cv.mm(6) - k * th * 1.8, 2.5, "TL")


# ================================================================= sheets / frame
def _sheet_notes(project) -> list[str]:
    ds = project.design
    grades = sorted({lv.grade for lv in project.levels}) or ["M25"]
    return [
        "GENERAL NOTES",
        "1. All dimensions in mm, levels in m, unless noted otherwise.",
        f"2. Concrete {', '.join(grades)}; reinforcement Fe{ds.fy_main:.0f} (T = HYSD bar), ties Fe{ds.fy_shear:.0f}.",
        f"3. Clear cover: beams {ds.beam_cover * 1000:.0f}, columns {ds.column_cover * 1000:.0f}, "
        f"slabs {ds.slab_cover * 1000:.0f}, footings {ds.footing_cover * 1000:.0f} mm.",
        "4. Lap and anchorage lengths as per IS 456 cl 26.2; do not scale from the drawing.",
    ]


def _frame(msp, fx, fy, s, title, project, watermark, sheet_no, n_sheets, scale_label, date):
    def P(x, y):
        return (fx + x * s / 1000, fy + y * s / 1000)

    def rect(x0, y0, x1, y1, layer="DET_FRAME"):
        msp.add_lwpolyline([P(x0, y0), P(x1, y0), P(x1, y1), P(x0, y1)], close=True, dxfattribs={"layer": layer})

    def line(x0, y0, x1, y1):
        msp.add_line(P(x0, y0), P(x1, y1), dxfattribs={"layer": "DET_FRAME"})

    def txt(t, x, y, h, align="L", **attr):
        e = msp.add_text(t, height=h * s / 1000, dxfattribs={"layer": "DET_TEXT", "style": "Standard", **attr})
        e.set_placement(P(x, y), align=_ALIGN[align])

    rect(0, 0, A1_W, A1_H)
    rect(MARGIN_L, MARGIN, A1_W - MARGIN, A1_H - MARGIN)
    ys = MARGIN + STRIP_H
    line(MARGIN_L, ys, A1_W - MARGIN, ys)
    x_tb = A1_W - MARGIN - TB_W
    line(x_tb, MARGIN, x_tb, ys)
    for k, s_ in enumerate(_sheet_notes(project)):
        txt(s_, MARGIN_L + 4, ys - 6 - k * 6.5, 3.0 if k == 0 else 2.5)
    # title block rows (top to bottom)
    rows = [12.0, 10.0, 10.0, 10.0, 10.0, 18.0]
    y = ys
    cuts = []
    for h in rows[:-1]:
        y -= h
        cuts.append(y)
        line(x_tb, y, A1_W - MARGIN, y)
    xl = x_tb + 4
    txt(APP, xl, ys - 6, 5.0, "L")
    if watermark:
        txt(watermark, A1_W - MARGIN - 4, ys - 6, 4.0, "R", color=1)
    txt("PROJECT:", xl, cuts[0] - 5, 2.5)
    txt(project.name or "-", xl + 26, cuts[0] - 5, 3.5)
    txt("CLIENT:", xl, cuts[1] - 5, 2.5)
    txt(project.client or "-", xl + 26, cuts[1] - 5, 3.5)
    txt("SHEET:", xl, cuts[2] - 5, 2.5)
    txt(title, xl + 26, cuts[2] - 5, 3.5)
    xm = x_tb + TB_W / 3
    xm2 = x_tb + 2 * TB_W / 3
    line(xm, cuts[3], xm, cuts[4] + 0)
    line(xm2, cuts[3], xm2, cuts[4] + 0)
    txt(f"DATE: {date}", xl, cuts[3] - 5, 2.5)
    txt(f"SCALE: {scale_label}", xm + 4, cuts[3] - 5, 2.5)
    txt(f"SHEET {sheet_no} OF {n_sheets}", xm2 + 4, cuts[3] - 5, 2.5)
    txt(NOTE, xl, cuts[4] - 6, 2.5)
    if project.engineer:
        txt(f"Engineer: {project.engineer}", xl, cuts[4] - 12, 2.5)
    if watermark:  # repeated large in the notes strip, clear of the drawing content
        txt(watermark, (MARGIN_L + x_tb) / 2 + 60, MARGIN + STRIP_H / 2, 12.0, "C", color=9)


def _doc():
    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.M
    doc.header["$INSUNITS"] = 6
    doc.header["$MEASUREMENT"] = 1
    for name, (color, lw) in LAYERS.items():
        if name not in doc.layers:
            doc.layers.add(name, color=color, lineweight=lw)
    if "PW_DETAIL" not in doc.dimstyles:
        ds = doc.dimstyles.new("PW_DETAIL")
        ds.dxf.dimtxt = 2.5
        ds.dxf.dimasz = 2.0
        ds.dxf.dimtsz = 1.2  # oblique ticks instead of arrows
        ds.dxf.dimexo = 1.0
        ds.dxf.dimexe = 1.5
        ds.dxf.dimgap = 0.8
        ds.dxf.dimdec = 0
        ds.dxf.dimlfac = 1000.0  # model metres -> millimetres
        ds.dxf.dimtad = 1
        ds.dxf.dimtih = 0
        ds.dxf.dimtoh = 0
        ds.dxf.dimclrt = 4
        ds.dxf.dimzin = 8
    return doc


def _fits(cv: Canvas, s: float) -> float:
    """Enlargement factor the A1 frame needs at scale ``s`` (1.0 when the content fits)."""
    return max(1.0, cv.width / (CONTENT_W * s / 1000), cv.height / (CONTENT_H * s / 1000))


def _fit(build, scales) -> list[tuple[Canvas, int, float]]:
    """Pages of a sheet at the smallest scale in ``scales`` at which everything fits on one A1.

    Otherwise the largest scale is used with continuation pages; a page whose content
    still does not fit gets an enlarged (A1-proportion) frame.  Returns [(canvas, scale, k)]."""
    for s in scales:
        pages = build(s)
        if len(pages) == 1 and _fits(pages[0], s) == 1.0:
            return [(pages[0], s, 1.0)]
    s = scales[-1]
    return [(cv, s, _fits(cv, s)) for cv in pages]


def build_detail_doc(project, fm, rep, watermark: str = ""):
    """Build the detail drawing document.

    Returns (doc, info) where ``info`` holds the sheet extents in model space
    (``info["sheets"][key]`` spans all pages of that sheet), the scales used, the
    number of bar circles drawn per sheet and the grouped schedules that were drawn."""
    doc = _doc()
    msp = doc.modelspace()
    ranges, col_rows = column_schedule(project, rep)
    walls = wall_schedule(fm)
    ftypes = footing_types(rep)
    bgroups = beam_groups(project, fm, rep)
    sgroups = slab_groups(rep)

    def single(fn, *args):
        def build(s):
            cv = Canvas(s)
            fn(cv, project, *args)
            return [cv]

        return build

    col_title = "COLUMN AND WALL SCHEDULE" if walls else "COLUMN SCHEDULE"
    sheets = [
        ("columns", col_title, single(_column_sheet, ranges, col_rows, walls), SCALES),
        ("footings", "FOOTING SCHEDULE", lambda s: _footing_pages(s, project, ftypes), SCALES[: SCALES.index(50) + 1]),
        ("beams", "BEAM DETAILS", lambda s: _beam_pages(s, project, bgroups), SCALES[2 : SCALES.index(75) + 1]),
        ("slabs", "SLAB SCHEDULE", single(_slab_sheet, sgroups), SCALES),
    ]
    laid = [(key, title, _fit(build, scales)) for key, title, build, scales in sheets]
    n_frames = sum(len(pages) for _, _, pages in laid)
    date = _dt.date.today().isoformat()
    info: dict = {"sheets": {}, "scales": {}, "circles": {}, "pages": {}}
    fx = 0.0
    no = 0
    for key, title, pages in laid:
        x_start = fx
        top = 0.0
        circles = 0
        for i, (cv, s, k) in enumerate(pages, 1):
            no += 1
            fs = s * k  # frame scale (paper mm -> model metres x 1000)
            scale_label = f"1:{s}" if k == 1.0 else f"1:{s} (plot to fit)"
            page_title = title if len(pages) == 1 else f"{title} ({i}/{len(pages)})"
            _frame(msp, fx, 0.0, fs, page_title, project, watermark, no, n_frames, scale_label, date)
            # content: its top-left corner at the top-left of the drawing area
            dx = fx + CONTENT_X0 * fs / 1000 - cv.ext[0]
            dy = CONTENT_Y1 * fs / 1000 - cv.ext[3]
            counts: dict = {}
            cv.emit(msp, dx, dy, counts)
            circles += counts.get("circles", 0)
            w = A1_W * fs / 1000
            top = max(top, A1_H * fs / 1000)
            fx += w + 0.05 * w
            info["scales"].setdefault(key, []).append((s, k))
        info["sheets"][key] = (x_start, 0.0, fx - 0.05 * w, top)
        info["pages"][key] = len(pages)
        info["circles"][key] = circles
    info["column_ranges"] = ranges
    info["column_rows"] = col_rows
    info["wall_rows"] = walls
    info["footing_types"] = ftypes
    info["beam_groups"] = bgroups
    info["slab_groups"] = sgroups
    return doc, info


def write_detail_drawings(path: str, project, fm, rep, watermark: str = "") -> str:
    """Write column, footing, beam and slab reinforcement details to one DXF (model space, metres)."""
    doc, _ = build_detail_doc(project, fm, rep, watermark)
    doc.saveas(path)
    return path
