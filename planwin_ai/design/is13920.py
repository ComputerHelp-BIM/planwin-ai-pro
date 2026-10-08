"""IS 13920:2016 ductile detailing checks for RC moment-resisting frames.

The checks work only on what is already available after :func:`runner.design_all` –
the frame model, the analysis results and the IS 456 design report – no new analysis
is run (the 1.2(DL+LL) gravity case of cl 6.3.3 is a linear combination of the solved
primary load cases).

Conventions
-----------
* Section formulae use N, mm, MPa; the frame model is in m, kN, kN·m.  Results shown
  to the user are in mm, kN and kN·m.
* Beams: member ``n1`` is the *left* end (``top_l_bars``), ``n2`` the *right* end.
  ``Vz`` is the shear in the sagging-positive convention (``dM/dx = Vz`` with
  ``M = -My``).
* Columns: local y (width ``b``) is along plan direction ``(cos a, sin a)`` and the depth
  ``D`` along ``(-sin a, cos a)``.  Bending "in the D direction" (beams framing along the
  depth axis) uses ``is456.interaction_curve(b, D, ...)``; bending in the b direction uses
  ``interaction_curve(D, b, ...)``.

All results are preliminary design aids that must be checked by a qualified engineer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..core.model import grade_fck
from . import is456

__all__ = [
    "Confinement",
    "DuctileCheck",
    "beam_capacity_shear",
    "beam_moment_capacity",
    "check_ductility",
    "column_confinement",
    "confining_length",
    "required",
    "rho_min",
]

OVERSTRENGTH = 1.4  # cl 6.3.3, 7.2.1, 7.5
GRAVITY = {"DL": 1.2, "LL": 1.2}  # cl 6.3.3 gravity shear
RHO_MAX = 0.025  # cl 6.2.2
HOOP_DIAS = (8, 10, 12, 16)
MAX_LEG_SPACING = 300.0  # mm, parallel legs of rectangular hoops (cl 8.1)
MIN_COL_HOOP_SPACING = 75.0  # mm, cl 8.2
MIN_BEAM_HOOP_SPACING = 50.0  # mm, practical limit for placing concrete between hoops
N_STATIONS = 21


# ===================================================================== result type
@dataclass
class DuctileCheck:
    kind: str  # "beam" | "column" | "joint"
    mark: str  # member mark (joint: column mark at the joint)
    level: str
    member_id: int | None
    checks: list[tuple[str, bool, str]] = field(default_factory=list)  # (clause + name, passed, detail)
    detailing: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(passed for _, passed, _ in self.checks)

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append((name, bool(passed), detail))


def required(project) -> bool:
    """IS 13920 applies to special moment-resisting frames (R >= 5) and in zones III, IV and V."""
    s = project.seismic
    try:
        r = float(s.response_reduction)
    except (TypeError, ValueError):
        r = 0.0
    return r >= 5.0 or str(s.zone).strip().upper() in ("III", "IV", "V")


# ===================================================================== section formulae
def rho_min(fck: float, fy: float) -> float:
    """cl 6.2.1: minimum tension steel ratio 0.24 √fck / fy (fraction, not %)."""
    return 0.24 * math.sqrt(fck) / fy


def beam_moment_capacity(ast: float, b: float, d: float, fck: float, fy: float) -> float:
    """Moment of resistance (N·mm) of a rectangular section with tension steel ``ast`` (mm²):
    Mu = 0.87 fy Ast d (1 − Ast fy / (b d fck)) (IS 456 G-1.1 b), capped at Mu,lim."""
    if ast <= 0 or b <= 0 or d <= 0:
        return 0.0
    mu_lim = is456.mu_lim(fck, fy, b, d)
    xu = 0.87 * fy * ast / (0.36 * fck * b)
    if xu >= is456.xu_max_ratio(fy) * d:  # over-reinforced: the formula no longer applies
        return float(mu_lim)
    mu = 0.87 * fy * ast * d * (1 - ast * fy / (b * d * fck))
    return float(min(mu, mu_lim))


def _sway_shear(vz, sway_r: float, sway_l: float):
    """Design shear |V| along the span for gravity shear ``vz`` (sagging-positive convention,
    i.e. +V at the left support and −V at the right one) plus the capacity sway shears:
    sway to the right (sagging at A, hogging at B) gives −sway_r, sway to the left +sway_l."""
    vz = np.asarray(vz, dtype=float)
    return np.maximum(np.abs(vz - sway_r), np.abs(vz + sway_l))


def beam_capacity_shear(
    vg_a: float,
    vg_b: float,
    ms_a: float,
    mh_a: float,
    ms_b: float,
    mh_b: float,
    clear_span: float,
) -> float:
    """cl 6.3.3 capacity design shear (kN) of a beam AB.

    ``vg_a``/``vg_b``: 1.2(DL+LL) support shears (kN, magnitudes); ``ms_*``/``mh_*``: sagging /
    hogging moment capacities at the faces of A and B (kN·m); ``clear_span`` in m.

    Sway right: Vu,a = Vg,a − 1.4 (Ms,A + Mh,B)/L;  Vu,b = Vg,b + 1.4 (Ms,A + Mh,B)/L
    Sway left:  Vu,a = Vg,a + 1.4 (Mh,A + Ms,B)/L;  Vu,b = Vg,b − 1.4 (Mh,A + Ms,B)/L
    Returns the largest magnitude.
    """
    L = max(clear_span, 1e-6)
    sr = OVERSTRENGTH * (ms_a + mh_b) / L
    sl = OVERSTRENGTH * (mh_a + ms_b) / L
    return float(np.max(_sway_shear([abs(vg_a), -abs(vg_b)], sr, sl)))


def confining_length(D_max: float, clear_height: float) -> float:
    """cl 8.1: special confining length l0 (mm) = max(larger dimension, clear height/6, 450)."""
    return max(D_max, clear_height / 6.0, 450.0)


@dataclass
class Confinement:
    """Special confining hoops for a rectangular column (cl 8.1 b, 8.2)."""

    dia: int
    legs_b: int  # legs parallel to D, spaced across the width b
    legs_d: int  # legs parallel to b, spaced across the depth D
    h: float  # mm, longer hoop/cross-tie panel dimension measured to the outer face
    s_ash: float  # mm, largest spacing satisfying the Ash formulae
    s_limit: float  # mm, min(min dim/4, 6 db, 100)
    s: float  # mm, adopted spacing within l0
    ash_req: float  # mm², required area of one leg at spacing s
    ok: bool

    @property
    def ash(self) -> float:
        return math.pi * self.dia**2 / 4


def _round_down(s: float, step: float = 5.0) -> float:
    if not math.isfinite(s):
        return s
    return math.floor(s / step + 1e-9) * step


def _ash_per_mm(h: float, Ag: float, Ak: float, fck: float, fy: float) -> float:
    """Ash per mm of hoop spacing (mm²/mm): max(0.18 h fck/fy (Ag/Ak − 1), 0.05 h fck/fy)."""
    return h * fck / fy * max(0.18 * (Ag / Ak - 1.0), 0.05)


def _bars_per_face(n: int, b: float, D: float) -> tuple[int, int]:
    """Bars on each b-face and each D-face for ``n`` bars spread round the perimeter."""
    half = max(n - 4, 0) // 2
    kd = int(half * D / (b + D) + 0.5)
    return 2 + half - kd, 2 + kd


def column_confinement(
    b: float,
    D: float,
    cover: float,
    fck: float,
    fy_h: float,
    db_min: float,
    n_bars: int = 4,
    dia_min: int = 8,
) -> Confinement:
    """Choose hoop diameter, legs and spacing within l0 (all mm, MPa).

    Ag = b D; Ak = (b − 2c)(D − 2c) measured to the outside of the hoops (``cover`` is the
    clear cover to the hoops).  ``h`` is the larger clear panel between parallel legs measured
    to their outer faces, i.e. ``core/(legs − 1)``; parallel legs are at most 300 mm apart
    and every cross-tie must engage a longitudinal bar, so the number of legs per direction
    is limited by the bars on that face.  The cheapest arrangement (hoop steel volume per
    metre) whose spacing satisfies both Ash formulae, cl 8.2 limits and s >= 75 mm is chosen.
    """
    Bk, Dk = b - 2 * cover, D - 2 * cover
    Ag, Ak = b * D, max(Bk * Dk, 1.0)
    s_limit = min(min(b, D) / 4, 6 * db_min, 100.0)
    on_b_face, on_d_face = _bars_per_face(n_bars, b, D)
    need_b = max(2, math.ceil(Bk / MAX_LEG_SPACING - 1e-9) + 1)
    need_d = max(2, math.ceil(Dk / MAX_LEG_SPACING - 1e-9) + 1)
    best = None
    fallback = None
    for dia in [x for x in HOOP_DIAS if x >= dia_min] or [dia_min]:
        area = math.pi * dia * dia / 4
        for nb in range(need_b, max(need_b, on_b_face) + 1):
            for nd in range(need_d, max(need_d, on_d_face) + 1):
                h = max(Bk / (nb - 1), Dk / (nd - 1))
                s_ash = area / _ash_per_mm(h, Ag, Ak, fck, fy_h)
                s = _round_down(min(s_limit, s_ash))
                cost = area * (nb * Dk + nd * Bk) / max(s, 1.0)
                cand = Confinement(dia, nb, nd, h, s_ash, s_limit, s, _ash_per_mm(h, Ag, Ak, fck, fy_h) * s, True)
                if s >= MIN_COL_HOOP_SPACING:
                    if best is None or cost < best[0] - 1e-9:
                        best = (cost, cand)
                elif fallback is None or s > fallback.s:
                    fallback = cand
    if best is not None:
        return best[1]
    fb = fallback
    return Confinement(fb.dia, fb.legs_b, fb.legs_d, fb.h, fb.s_ash, fb.s_limit, fb.s, fb.ash_req, False)


def _shear_spacing(Vu_kN: float, Vc_N: float, fy_v: float, asv: float, d: float) -> float:
    """Largest stirrup spacing (mm) with Vc + 0.87 fy Asv d / s >= Vu (inf when Vc suffices)."""
    Vus = Vu_kN * 1e3 - Vc_N
    if Vus <= 0:
        return math.inf
    return 0.87 * fy_v * asv * d / Vus


# ===================================================================== frame checks
class _Ctx:
    """Topology and cached results shared by the member and joint checks."""

    def __init__(self, fa, project, rep):
        self.fa = fa
        self.m = fa.model
        self.p = project
        self.ds = project.design
        self.fy = self.ds.fy_main
        self.fyh = self.ds.fy_shear
        self.fyv = min(self.ds.fy_shear, 415.0)  # IS 456 cl 40.4: stirrup fy <= 415 MPa
        self.beams = {b.member_id: b for b in rep.beams}
        self.cols = {c.member_id: c for c in rep.columns}
        self.level_names = {lv.index: lv.name for lv in self.m.levels}
        self.col_below: dict[int, int] = {}
        self.col_above: dict[int, int] = {}
        self.beams_at: dict[int, list[int]] = {}
        self.group_at: dict[tuple[int, str, int], list[int]] = {}
        for mid, mem in self.m.members.items():
            if mem.kind == "column":
                lo, hi = (mem.n1, mem.n2) if self.m.nodes[mem.n1].z <= self.m.nodes[mem.n2].z else (mem.n2, mem.n1)
                self.col_below.setdefault(hi, mid)
                self.col_above.setdefault(lo, mid)
            elif mem.kind == "beam":
                for n in (mem.n1, mem.n2):
                    self.beams_at.setdefault(n, []).append(mid)
                    self.group_at.setdefault((mem.level, mem.group, n), []).append(mid)
        self.col_nodes = set(self.col_below) | set(self.col_above)
        self._curves: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}
        self._col_forces: dict[int, list[tuple[str, float, float]]] = {}
        self.hoops: dict[int, tuple[Confinement, float, float]] = {}  # column id -> (conf, s in l0, s outside)

    # ------------------------------------------------------------ geometry helpers
    def xy(self, n: int) -> np.ndarray:
        nd = self.m.nodes[n]
        return np.array([nd.x, nd.y])

    def col_at(self, n: int) -> int | None:
        return self.col_below.get(n, self.col_above.get(n))

    @staticmethod
    def col_axes(mem) -> tuple[np.ndarray, np.ndarray]:
        a = math.radians(mem.angle)
        return np.array([math.cos(a), math.sin(a)]), np.array([-math.sin(a), math.cos(a)])

    def half_along(self, n: int, u: np.ndarray) -> float:
        """Half extent (m) of the column at node ``n`` along plan direction ``u``."""
        cid = self.col_at(n)
        if cid is None:
            return 0.0
        c = self.m.members[cid]
        ub, ud = self.col_axes(c)
        return abs(float(u @ ub)) * c.b / 2 + abs(float(u @ ud)) * c.d / 2

    def walk(self, mid: int, node: int) -> tuple[int, int]:
        """Follow the plan beam (same group and level) through ``node`` until a column or its end.
        Returns (end node, member touching that end)."""
        mem = self.m.members[mid]
        seen, prev, cur = {mid}, mid, node
        while cur not in self.col_nodes:
            nxt = [k for k in self.group_at.get((mem.level, mem.group, cur), []) if k not in seen]
            if not nxt:
                break
            prev = nxt[0]
            seen.add(prev)
            pm = self.m.members[prev]
            cur = pm.n2 if pm.n1 == cur else pm.n1
        return cur, prev

    # ------------------------------------------------------------ beam capacities
    def beam_d(self, bd, bars) -> float:
        D = bd.d * 1000
        link = bd.links.dia if bd.links else 8
        return D - self.ds.beam_cover * 1000 - link - (bars.dia / 2 if bars else 10)

    def beam_caps(self, mid: int, node: int) -> tuple[float, float] | None:
        """(sagging, hogging) moment capacities (kN·m) of beam ``mid`` at its end ``node``."""
        bd = self.beams.get(mid)
        if bd is None or bd.bottom_bars is None or bd.top_l_bars is None or bd.top_r_bars is None:
            return None
        mem = self.m.members[mid]
        fck = grade_fck(mem.grade)
        top = bd.top_l_bars if node == mem.n1 else bd.top_r_bars
        b = bd.b * 1000
        ms = beam_moment_capacity(bd.bottom_bars.area, b, self.beam_d(bd, bd.bottom_bars), fck, self.fy)
        mh = beam_moment_capacity(top.area, b, self.beam_d(bd, top), fck, self.fy)
        return ms / 1e6, mh / 1e6

    def joint_beam_sum(self, node: int, ub: np.ndarray, ud: np.ndarray) -> tuple[dict[str, float], list[str]]:
        """ΣMb (kN·m) at ``node`` for sway along the column b axis ("B") and depth axis ("D"):
        hogging on one side + sagging on the other, the larger of the two sway senses.  Inclined
        beams are assigned to the nearer axis with their capacity times |cos θ|."""
        acc = {"B": [0.0, 0.0, 0.0, 0.0], "D": [0.0, 0.0, 0.0, 0.0]}  # hog+, sag+, hog-, sag-
        missing = []
        for mid in self.beams_at.get(node, []):
            mem = self.m.members[mid]
            other = mem.n2 if mem.n1 == node else mem.n1
            v = self.xy(other) - self.xy(node)
            L = float(np.hypot(*v))
            if L < 1e-9:
                continue
            v = v / L
            cb, cd = float(v @ ub), float(v @ ud)
            key, c = ("B", cb) if abs(cb) >= abs(cd) else ("D", cd)
            caps = self.beam_caps(mid, node)
            if caps is None:
                missing.append(mem.mark or str(mid))
                continue
            ms, mh = caps[0] * abs(c), caps[1] * abs(c)
            s = acc[key]
            if c > 0:
                s[0] += mh
                s[1] += ms
            else:
                s[2] += mh
                s[3] += ms
        return {k: max(s[0] + s[3], s[1] + s[2]) for k, s in acc.items()}, missing

    # ------------------------------------------------------------ column capacities
    def curve(self, b: float, D: float, As: float, fck: float) -> tuple[np.ndarray, np.ndarray]:
        key = (round(b, 1), round(D, 1), round(As, 1), fck)
        if key not in self._curves:
            self._curves[key] = is456.interaction_curve(b, D, As, fck, self.fy, self.ds.column_cover * 1000)
        return self._curves[key]

    def col_moment(self, cid: int, axis: str, P_kN: float) -> float:
        """Uniaxial design moment capacity (kN·m) of column ``cid`` at axial load ``P_kN``.
        ``axis`` "D": bending in the depth direction (about the axis parallel to b)."""
        cd = self.cols[cid]
        mem = self.m.members[cid]
        b, D = mem.b * 1000, mem.d * 1000
        width, depth = (b, D) if axis == "D" else (D, b)
        P, M = self.curve(width, depth, cd.main_bars.area, grade_fck(mem.grade))
        Pn = P_kN * 1e3
        if Pn >= P[-1] or Pn <= P[0]:
            return 0.0
        return float(np.interp(Pn, P, M)) / 1e6

    def col_forces(self, cid: int) -> list[tuple[str, float, float, float, float]]:
        """Per ultimate combo: (name, Pu bottom kN, Pu top kN, max |Vy| kN, max |Vz| kN)."""
        if cid not in self._col_forces:
            mem = self.m.members[cid]
            up = self.m.nodes[mem.n2].z >= self.m.nodes[mem.n1].z
            out = []
            for c in self.fa.ultimate:
                f = self.fa.forces(cid, c.factors, 2)
                p1, p2 = -float(f.N[0]), -float(f.N[-1])
                bot, top = (p1, p2) if up else (p2, p1)
                out.append((c.name, bot, top, float(np.max(np.abs(f.Vy))), float(np.max(np.abs(f.Vz)))))
            self._col_forces[cid] = out
        return self._col_forces[cid]

    @staticmethod
    def dir_label(u: np.ndarray) -> str:
        return "X" if abs(u[0]) >= abs(u[1]) else "Y"


# ===================================================================== beams
def _check_beam(ctx: _Ctx, mid: int) -> DuctileCheck:
    bd = ctx.beams[mid]
    mem = ctx.m.members[mid]
    out = DuctileCheck("beam", bd.mark, bd.level, mid)
    if bd.links is None or bd.bottom_bars is None or bd.top_l_bars is None or bd.top_r_bars is None:
        out.add("IS 456 design", False, "beam design failed (no stirrups/bars) – resize before ductile detailing")
        return out
    if not bd.ok:
        out.add("IS 456 design", False, "IS 456 design does not pass: " + ("; ".join(bd.notes) or "see design"))
    fck = grade_fck(mem.grade)
    fy = ctx.fy
    b, D = bd.b * 1000, bd.d * 1000
    cover = ctx.ds.beam_cover * 1000
    d = D - cover - 18  # same effective depth as the IS 456 shear design
    bot, tl, tr = bd.bottom_bars, bd.top_l_bars, bd.top_r_bars
    a_bot, a_tl, a_tr = bot.area, tl.area, tr.area
    a_top_max = max(a_tl, a_tr)

    # ---- geometry: span between column faces along this plan beam
    p1, p2 = ctx.xy(mem.n1), ctx.xy(mem.n2)
    Lm = float(np.hypot(*(p2 - p1)))
    u = (p2 - p1) / max(Lm, 1e-9)
    A, mA = ctx.walk(mid, mem.n1)
    B, mB = ctx.walk(mid, mem.n2)
    colA, colB = A in ctx.col_nodes, B in ctx.col_nodes
    hA = ctx.half_along(A, u) if colA else 0.0
    hB = ctx.half_along(B, u) if colB else 0.0
    L0 = float(np.hypot(*(ctx.xy(B) - ctx.xy(A)))) - hA - hB
    L0 = max(L0, 0.1)

    # ---- cl 6.1 proportions
    out.add("6.1.1 Width ≥ 200 mm", b >= 200 - 1e-6, f"b = {b:.0f} mm")
    out.add("6.1.2 Width/depth ≥ 0.3", b / D >= 0.3 - 1e-9, f"b/D = {b:.0f}/{D:.0f} = {b / D:.2f}")
    out.add(
        "6.1.3 Depth ≤ clear span/4",
        D <= L0 * 1000 / 4 + 1e-6,
        f"D = {D:.0f} mm, clear span L0 = {L0 * 1000:.0f} mm, L0/4 = {L0 * 250:.0f} mm",
    )

    # ---- cl 6.2 longitudinal steel
    rmin = rho_min(fck, fy)
    rho = {"bottom": a_bot / (b * d), "top left": a_tl / (b * d), "top right": a_tr / (b * d)}
    low = min(rho.values())
    out.add(
        "6.2.1 Min. steel 0.24√fck/fy (top & bottom)",
        low >= rmin - 1e-9,
        f"ρmin = 0.24√{fck:g}/{fy:g} = {100 * rmin:.3f} %; provided "
        + ", ".join(f"{k} {100 * v:.3f} %" for k, v in rho.items()),
    )
    high = max(rho.values())
    out.add(
        "6.2.2 Max. steel ≤ 2.5 %",
        high <= RHO_MAX + 1e-9,
        f"max ρ = {100 * high:.2f} % ({', '.join(f'{k} {100 * v:.2f} %' for k, v in rho.items())})",
    )
    out.add(
        "6.2.3 Bottom ≥ ½ top steel at joint faces",
        a_bot >= 0.5 * a_top_max - 1e-6,
        f"bottom {bot} = {a_bot:.0f} mm² vs 0.5 × top: left {0.5 * a_tl:.0f}, right {0.5 * a_tr:.0f} mm²",
    )
    cont_req = 0.25 * a_top_max
    out.add(
        "6.2.3 Steel at any section ≥ ¼ max top at faces",
        a_bot >= cont_req - 1e-6,
        f"0.25 × {a_top_max:.0f} = {cont_req:.0f} mm²; bottom {a_bot:.0f} mm² (top: continuous bars, see detailing)",
    )

    # ---- cl 6.3 shear: capacity design
    lk = bd.links
    asv = lk.legs * math.pi * lk.dia**2 / 4

    def face_caps(is_col: bool, m_end: int, node: int, own: int) -> tuple[float, float]:
        if not is_col:
            return 0.0, 0.0  # no plastic hinge where the beam is supported by another beam
        return ctx.beam_caps(m_end, node) or ctx.beam_caps(mid, own) or (0.0, 0.0)

    ms_a, mh_a = face_caps(colA, mA, A, mem.n1)
    ms_b, mh_b = face_caps(colB, mB, B, mem.n2)
    sway_r = OVERSTRENGTH * (ms_a + mh_b) / L0
    sway_l = OVERSTRENGTH * (mh_a + ms_b) / L0

    xs = None
    env = None
    for c in ctx.fa.ultimate:
        f = ctx.fa.forces(mid, c.factors, N_STATIONS)
        xs = f.x
        v = np.abs(f.Vz)
        env = v if env is None else np.maximum(env, v)
    g = ctx.fa.forces(mid, GRAVITY, N_STATIONS)
    xs = g.x if xs is None else xs
    env = np.zeros_like(xs) if env is None else env
    frame = colA or colB
    v_cap = _sway_shear(g.Vz, sway_r, sway_l) if frame else np.zeros_like(xs)
    v_des = np.maximum(env, v_cap)

    z1 = (ctx.half_along(mem.n1, u) + 2 * d / 1000) if mem.n1 in ctx.col_nodes else 0.0
    z2 = (Lm - ctx.half_along(mem.n2, u) - 2 * d / 1000) if mem.n2 in ctx.col_nodes else Lm
    end_mask = (xs <= z1 + 1e-9) | (xs >= z2 - 1e-9)
    has_end = mem.n1 in ctx.col_nodes or mem.n2 in ctx.col_nodes
    mid_mask = ~end_mask
    v_end = float(v_des[end_mask].max()) if end_mask.any() else 0.0
    v_mid = float(v_des[mid_mask].max()) if mid_mask.any() else 0.0
    v_max = max(float(v_des.max()), bd.V_max)
    pt = 100 * min(a_bot, a_tl, a_tr) / (b * d)
    Vc = is456.tau_c(pt, fck) * b * d
    tv = v_max * 1e3 / (b * d)
    tcmax = is456.tau_c_max(fck)
    s_end_req = _shear_spacing(max(v_end, bd.V_max if has_end else 0.0), Vc, ctx.fyv, asv, d)
    s_mid_req = _shear_spacing(v_mid if mid_mask.any() else v_max, Vc, ctx.fyv, asv, d)
    s_gov = min(s_end_req, s_mid_req) if has_end else s_mid_req
    shear_ok = tv <= tcmax + 1e-9 and s_gov >= MIN_BEAM_HOOP_SPACING
    cap_txt = (
        f"1.2(DL+LL) + 1.4(Ms+Mh)/L0: Ms,A {ms_a:.0f}, Mh,A {mh_a:.0f}, Ms,B {ms_b:.0f}, Mh,B {mh_b:.0f} kN·m, "
        f"L0 {L0:.2f} m → sway {max(sway_r, sway_l):.1f} kN; "
        if frame
        else "not framing into a column – analysis shear only; "
    )
    out.add(
        "6.3.3 Capacity-design shear",
        shear_ok,
        cap_txt + f"Vu = max(analysis {bd.V_max:.1f}, capacity {float(v_cap.max()):.1f}) = {v_max:.1f} kN, "
        f"τv = {tv:.2f} ≤ τc,max {tcmax:.1f} MPa; {lk.legs}L-T{lk.dia} needs s ≤ "
        + (f"{s_gov:.0f} mm" if math.isfinite(s_gov) else "— (Vc suffices)")
        + ("" if shear_ok else " – use larger/more hoop legs or a bigger section"),
    )
    out.add("6.3.2 Hoop dia ≥ 8 mm, 135° hooks", lk.dia >= 8, f"T{lk.dia} hoops, 135° hooks + 10 db (≥ 75 mm)")

    # ---- cl 6.3.5 hoop spacing
    db_min = min(bot.dia, tl.dia, tr.dia)
    lim_end = min(d / 4, 8 * db_min, 100.0)
    lim_mid = d / 2
    s_end = _round_down(min(lim_end, s_end_req))
    s_min_reinf = 0.87 * ctx.fyv * asv / (0.4 * b)  # IS 456 cl 26.5.1.6
    s_mid = _round_down(min(lim_mid, lk.spacing, s_mid_req, s_min_reinf), 25.0 if lim_mid >= 150 else 5.0)
    sp_ok = (s_end >= MIN_BEAM_HOOP_SPACING if has_end else True) and s_mid >= MIN_BEAM_HOOP_SPACING
    out.add(
        "6.3.5 Hoop spacing",
        sp_ok,
        (
            f"within 2d = {2 * d:.0f} mm of column faces: ≤ min(d/4 = {d / 4:.0f}, 8db = {8 * db_min:.0f}, 100) "
            f"→ {s_end:.0f} mm; "
            if has_end
            else ""
        )
        + f"elsewhere ≤ d/2 = {lim_mid:.0f} → {s_mid:.0f} mm (IS 456 design: {int(lk.spacing)} mm)",
    )

    # ---- detailing
    hoop = f"{lk.legs}L-T{lk.dia}"
    ends = [nm for nm, n in (("left", mem.n1), ("right", mem.n2)) if n in ctx.col_nodes]
    if ends:
        where = "each column face" if len(ends) == 2 else f"the {ends[0]} column face"
        out.detailing.append(
            f"Hoops {hoop} (135° hooks) @ {s_end:.0f} c/c over 2d = {2 * d:.0f} mm from {where}; "
            "first hoop 50 mm from the face"
        )
        out.detailing.append(f"Hoops {hoop} @ {s_mid:.0f} c/c elsewhere")
    else:
        out.detailing.append(f"Hoops {hoop} (135° hooks) @ {s_mid:.0f} c/c (no column at either end of this segment)")
    top_dia = min(tl.dia, tr.dia)
    n_cont = max(2, math.ceil(cont_req / (math.pi * top_dia**2 / 4) - 1e-9))
    out.detailing.append(
        f"Continuous top bars ≥ {cont_req:.0f} mm² (e.g. {n_cont}-T{top_dia}) and bottom {bot} throughout the "
        f"span; bottom at each face ≥ {0.5 * a_top_max:.0f} mm² (6.2.3)"
    )
    out.detailing.append(
        f"Laps: not within the joint nor within 2d = {2 * d:.0f} mm of a column face; ≤ 50 % of bars lapped at a "
        f"section; hoops @ ≤ {min(150.0, s_mid):.0f} c/c over the lap length (6.2.6)"
    )
    if ends:
        out.detailing.append("Anchor top and bottom bars into the column core: Ld + 10 db with 90° bend (6.2.5)")
    return out


# ===================================================================== columns
def _check_column(ctx: _Ctx, cid: int) -> DuctileCheck:
    cd = ctx.cols[cid]
    mem = ctx.m.members[cid]
    out = DuctileCheck("column", cd.mark, cd.level, cid)
    if cd.main_bars is None or cd.tie is None:
        out.add("IS 456 design", False, "column design failed (no bars/ties) – resize before ductile detailing")
        return out
    if not cd.ok:
        out.add("IS 456 design", False, "IS 456 design does not pass: " + ("; ".join(cd.notes) or cd.governing))
    fck = grade_fck(mem.grade)
    b, D = cd.b * 1000, cd.d * 1000
    bmin, bmax = min(b, D), max(b, D)
    cover = ctx.ds.column_cover * 1000
    out.add("7.1.1 Min. dimension ≥ 300 mm", bmin >= 300 - 1e-6, f"{b:.0f} × {D:.0f} mm")
    out.add("7.1.2 b/D ≥ 0.4", bmin / bmax >= 0.4 - 1e-9, f"{bmin:.0f}/{bmax:.0f} = {bmin / bmax:.2f}")

    # ---- cl 8: special confining reinforcement
    db = cd.main_bars.dia
    clear = (cd.clear_height or cd.height) * 1000
    l0 = confining_length(bmax, clear)
    conf = column_confinement(b, D, cover, fck, ctx.fyh, db, cd.main_bars.count, max(8, cd.tie.dia))
    Ag, Ak = b * D, (b - 2 * cover) * (D - 2 * cover)
    out.add(
        "8.1(b) Confining steel Ash",
        conf.ok,
        f"Ag/Ak = {Ag:.0f}/{Ak:.0f}; h = {conf.h:.0f} mm; T{conf.dia} ({conf.ash:.0f} mm²) allows "
        f"s ≤ {conf.s_ash:.0f} mm; Ash,req at {conf.s:.0f} mm = {conf.ash_req:.0f} mm²"
        + ("" if conf.dia == cd.tie.dia else f" (IS 456 tie T{cd.tie.dia} insufficient – use T{conf.dia})"),
    )
    out.add(
        "8.2 Hoop spacing within l0",
        conf.s >= MIN_COL_HOOP_SPACING and conf.s <= conf.s_limit + 1e-9,
        f"l0 = max({bmax:.0f}, {clear:.0f}/6 = {clear / 6:.0f}, 450) = {l0:.0f} mm; s ≤ min(bmin/4 = {bmin / 4:.0f}, "
        f"6db = {6 * db}, 100) and ≥ 75 → {conf.s:.0f} mm",
    )

    # ---- cl 7.5 capacity shear, per direction
    ub, ud = ctx.col_axes(mem)
    lo, hi = (mem.n1, mem.n2) if ctx.m.nodes[mem.n1].z <= ctx.m.nodes[mem.n2].z else (mem.n2, mem.n1)
    sums_top, _ = ctx.joint_beam_sum(hi, ub, ud)
    sums_bot, _ = ctx.joint_beam_sum(lo, ub, ud) if ctx.m.nodes[lo].support is None else ({"B": 0.0, "D": 0.0}, [])
    forces = ctx.col_forces(cid)
    hst = cd.height or abs(ctx.m.nodes[hi].z - ctx.m.nodes[lo].z)
    asv_leg = conf.ash
    fy_v = ctx.fyv
    s_out_lim = min(bmin / 2, 300.0)
    s_req_all = math.inf
    for axis, width, depth, legs, v_an, u in (
        ("D", b, D, conf.legs_b, max((f[4] for f in forces), default=0.0), ud),
        ("B", D, b, conf.legs_d, max((f[3] for f in forces), default=0.0), ub),
    ):
        dd = depth - cover - conf.dia - db / 2
        sum_mb = max(sums_top[axis], sums_bot[axis])
        v_cap = OVERSTRENGTH * sum_mb / max(hst, 1e-6)
        vu = max(v_an, v_cap)
        pt = 100 * (cd.main_bars.area / 2) / (width * dd)
        Vc = is456.tau_c(pt, fck) * width * dd
        tv = vu * 1e3 / (width * dd)
        s_req = _shear_spacing(vu, Vc, fy_v, legs * asv_leg, dd)
        s_req_all = min(s_req_all, s_req)
        ok = tv <= is456.tau_c_max(fck) + 1e-9 and s_req >= MIN_COL_HOOP_SPACING
        out.add(
            f"7.5 Capacity shear (along {ctx.dir_label(u)})",
            ok,
            f"Vu = max(analysis {v_an:.1f}, 1.4 ΣMb/hst = 1.4 × {sum_mb:.0f}/{hst:.2f} = {v_cap:.1f}) = {vu:.1f} kN; "
            f"τv = {tv:.2f} MPa; {legs} legs T{conf.dia} need s ≤ "
            + (f"{s_req:.0f} mm" if math.isfinite(s_req) else "— (Vc suffices)"),
        )
    s_l0 = _round_down(min(conf.s, s_req_all))
    s_out = max(_round_down(min(s_out_lim, s_req_all), 25.0 if s_out_lim >= 150 else 5.0), s_l0)
    out.add(
        "7.6.1 Hoop spacing outside l0",
        s_out >= MIN_COL_HOOP_SPACING,
        f"≤ min(bmin/2 = {bmin / 2:.0f}, 300) and shear → {s_out:.0f} mm (IS 456 design: {int(cd.tie.spacing)} mm)",
    )
    ctx.hoops[cid] = (conf, s_l0, s_out)
    legs = f"{conf.legs_b}×{conf.legs_d} legs"
    out.detailing.append(
        f"Special confining zone l0 = {l0:.0f} mm at both ends of the clear height and through the "
        "beam–column joint (8.1, 9.1)"
    )
    out.detailing.append(
        f"Confining hoops T{conf.dia} ({legs}, 135° hooks; cross-ties engage the longitudinal bars) "
        f"@ {s_l0:.0f} c/c within l0 – first hoop 50 mm from the beam face"
    )
    out.detailing.append(f"Hoops T{conf.dia} ({legs}) @ {s_out:.0f} c/c over the rest of the height")
    out.detailing.append(
        f"Laps only in the central half of the clear height, ≤ 50 % of bars at a section, "
        f"hoops @ ≤ {min(150.0, s_out):.0f} c/c over the lap length (7.3.3)"
    )
    return out


# ===================================================================== joints
def _check_joint(ctx: _Ctx, node: int) -> DuctileCheck:
    below = ctx.col_below[node]
    above = ctx.col_above.get(node)
    cm = ctx.m.members[below]
    nd = ctx.m.nodes[node]
    out = DuctileCheck("joint", cm.mark, ctx.level_names.get(nd.level, str(nd.level)), below)
    if above is None:
        out.add("7.2.1 Strong column–weak beam", True, "roof joint – exempt (no column above)")
        return out
    for cid in (below, above):
        cd = ctx.cols.get(cid)
        if cd is None or cd.main_bars is None:
            mk = ctx.m.members[cid].mark
            out.add("7.2.1 Strong column–weak beam", False, f"column {mk} has no design – cannot check")
            return out
    ub, ud = ctx.col_axes(cm)
    sums, missing = ctx.joint_beam_sum(node, ub, ud)
    if missing:
        out.add("7.2.1 Strong column–weak beam", False, f"beam(s) {', '.join(missing)} have no design")
    f_below = {f[0]: f[2] for f in ctx.col_forces(below)}  # top of the column below
    f_above = {f[0]: f[1] for f in ctx.col_forces(above)}  # bottom of the column above
    am = ctx.m.members[above]
    aub, aud = ctx.col_axes(am)
    done = False
    for axis, u in (("D", ud), ("B", ub)):
        mb = sums[axis]
        if mb <= 1e-9:
            continue
        done = True
        # the column above may be rotated: use its axis closest to this sway direction
        a_axis = "B" if abs(float(u @ aub)) >= abs(float(u @ aud)) else "D"
        best = None
        for name, p_lo in f_below.items():
            p_up = f_above.get(name, 0.0)
            mc = ctx.col_moment(below, axis, p_lo) + ctx.col_moment(above, a_axis, p_up)
            if best is None or mc < best[0]:
                best = (mc, name, p_lo, p_up)
        mc, name, p_lo, p_up = best
        need = OVERSTRENGTH * mb
        out.add(
            f"7.2.1 Strong column–weak beam (along {ctx.dir_label(u)})",
            mc >= need - 1e-6,
            f"ΣMc = {mc:.0f} kN·m (Pu below {p_lo:.0f}, above {p_up:.0f} kN, {name}) vs 1.4 ΣMb = "
            f"1.4 × {mb:.0f} = {need:.0f} kN·m; ratio {mc / need:.2f}",
        )
    if not done and not missing:
        out.add("7.2.1 Strong column–weak beam", True, "no beams framing into this joint")
    hp = ctx.hoops.get(below)
    if hp:
        conf, s_l0, _ = hp
        out.detailing.append(
            f"Continue column hoops T{conf.dia} ({conf.legs_b}×{conf.legs_d} legs) @ {s_l0:.0f} c/c through the "
            "joint (9.1)"
        )
    return out


# ===================================================================== entry point
def check_ductility(fa, project, rep) -> list[DuctileCheck]:
    """IS 13920:2016 checks for every designed beam, column and beam–column joint."""
    ctx = _Ctx(fa, project, rep)
    out: list[DuctileCheck] = []
    for mid, mem in ctx.m.members.items():
        if mem.kind == "beam" and mid in ctx.beams:
            out.append(_check_beam(ctx, mid))
    for mid, mem in ctx.m.members.items():
        if mem.kind == "column" and mid in ctx.cols:
            out.append(_check_column(ctx, mid))
    joints = sorted(
        (n for n in ctx.col_below if ctx.beams_at.get(n)),
        key=lambda n: (ctx.m.nodes[n].level, n),
    )
    for n in joints:
        out.append(_check_joint(ctx, n))
    return out
