"""Step-by-step design calculation sheets (PDF) for beams, columns, footings and slabs.

Each sheet is rebuilt from the frame analysis with the same IS 456 routines and the same
inputs as :func:`planwin_ai.design.runner.design_all`, then laid out as numbered steps
(description, formula, substituted values, result, IS clause).  Every recomputed value
that is also held in the design report is compared with it; a difference above 0.5 % is
printed on the sheet as a warning instead of being silently shown.

The step data is available without rendering through :func:`beam_steps`,
:func:`column_steps`, :func:`footing_steps`, :func:`slab_steps` (``list[Step]``) and the
``*_sheet`` builders (:class:`Sheet` with header, steps, checks and warnings).
"""

from __future__ import annotations

import datetime as _dt
import math
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape as _esc

import numpy as np

from .. import APP_NAME, COMPANY, __version__
from ..core import geometry as G
from ..core.model import grade_fck
from ..design import is456, is13920
from ..design.is456 import slab as _slab
from ..design.runner import _deflection_span, _end_support, _side_face
from .report_common import DISCLAIMER

if TYPE_CHECKING:
    from ..core.frame import FrameAnalysis, FrameModel
    from ..core.model import Project
    from ..design.report import BeamDesign, ColumnDesign, DesignReport, FootingDesign

__all__ = [
    "Check",
    "Sheet",
    "Step",
    "TOLERANCE",
    "beam_sheet",
    "beam_steps",
    "build_sheets",
    "column_sheet",
    "column_steps",
    "footing_sheet",
    "footing_steps",
    "slab_sheet",
    "slab_steps",
    "write_calc_sheets",
]

TOLERANCE = 0.005  # recomputed vs design report (0.5 %)
_HDR = "#1F3A5F"


# ============================================================================= data
@dataclass
class Step:
    """One numbered calculation step."""

    title: str
    formula: str = ""
    subst: str = ""  # formula with the values substituted
    result: str = ""  # result with units
    clause: str = ""  # IS clause reference
    key: str = ""  # stable identifier for programmatic access (tests, exports)
    value: float | None = None  # numeric result behind ``result``
    group: str = ""  # section heading the step belongs to
    warning: str = ""  # set when the recomputed value differs from the design report


@dataclass
class Check:
    label: str
    ok: bool
    detail: str = ""


@dataclass
class Sheet:
    kind: str  # Beam | Column | Footing | Slab
    ref: str  # e.g. "B11 (member 1)"
    level: str
    header: list[tuple[str, str]]
    steps: list[Step] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # design notes from the report

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)

    def step(self, key: str) -> Step | None:
        return next((s for s in self.steps if s.key == key), None)

    def value(self, key: str) -> float | None:
        s = self.step(key)
        return None if s is None else s.value


def _close(a: float, b: float, tol: float = TOLERANCE) -> bool:
    a, b = float(a), float(b)
    if not (math.isfinite(a) and math.isfinite(b)):
        return a == b
    return abs(a - b) <= tol * max(abs(a), abs(b)) + 1e-6


class _Builder:
    def __init__(self, kind: str, ref: str, level: str, header: list[tuple[str, str]]):
        self.sheet = Sheet(kind, ref, level, header)
        self._group = ""

    def group(self, name: str) -> None:
        self._group = name

    def step(self, title, formula="", subst="", result="", clause="", key="", value=None) -> Step:
        s = Step(title, formula, subst, result, clause, key, None if value is None else float(value), self._group)
        self.sheet.steps.append(s)
        return s

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        self.sheet.checks.append(Check(label, bool(ok), detail))

    def _warn(self, msg: str, step: Step | None) -> None:
        target = step or (self.sheet.steps[-1] if self.sheet.steps else None)
        if target is not None:
            target.warning = f"{target.warning}; {msg}" if target.warning else msg
        self.sheet.warnings.append(msg)

    def compare(self, what: str, recomputed: float, reported: float, unit: str = "", nd: int = 2, step=None) -> bool:
        if _close(recomputed, reported):
            return True
        self._warn(
            f"Recomputed {what} = {recomputed:.{nd}f}{unit} differs from the design report value "
            f"{reported:.{nd}f}{unit} by more than {TOLERANCE * 100:.1f} %",
            step,
        )
        return False

    def compare_text(self, what: str, recomputed: str, reported: str, step=None) -> bool:
        if str(recomputed) == str(reported):
            return True
        self._warn(f"Recomputed {what} '{recomputed}' differs from the design report '{reported}'", step)
        return False


def _today() -> str:
    return _dt.date.today().isoformat()


def _mm2(v: float) -> str:
    return f"{v:.0f} mm²" if math.isfinite(v) else "inadequate (∞)"


def _missing(kind: str, ref: str, level: str, msg: str) -> Sheet:
    s = Sheet(kind, ref, level, [("Member", ref), ("Level", level)])
    s.warnings.append(msg)
    s.checks.append(Check("Recomputation", False, msg))
    return s


# ============================================================================= beams
def beam_sheet(project: Project, fa: FrameAnalysis, bd: BeamDesign) -> Sheet:
    """Calculation sheet for one beam, recomputed exactly as ``runner._design_beam`` does (IS 456
    flexure / shear / cl 41 torsion / deflection and, where it applies, IS 13920 ductile detailing)."""
    m = fa.model
    mid = bd.member_id
    ref = f"{bd.mark} (member {mid})"
    mem = m.members.get(mid)
    if mem is None or mem.kind != "beam":
        return _missing("Beam", ref, bd.level, f"member {mid} is not a beam of the analysed model")
    ds = project.design
    fy = ds.fy_main
    fck = grade_fck(mem.grade)
    ductile = is13920.required(project) and project.seismic.enabled
    # ------------------------------------------------ forces (same stations and combinations as the runner)
    n = 9
    sag, hog = np.zeros(n), np.zeros(n)
    sag_c, hog_c = [""] * n, [""] * n
    vmax, v_c, v_x = 0.0, "", 0.0
    tmax, t_c, t_x = 0.0, "", 0.0
    f = None
    for c in fa.ultimate:
        f = fa.forces(mid, c.factors, n)
        M = -f.My  # sagging +
        for i in range(n):
            if M[i] > sag[i]:
                sag[i], sag_c[i] = M[i], c.name
            if M[i] < hog[i]:
                hog[i], hog_c[i] = M[i], c.name
        av = np.abs(f.Vz)
        k = int(np.argmax(av))
        if float(av[k]) > vmax:
            vmax, v_c, v_x = float(av[k]), c.name, float(f.x[k])
        at = np.abs(f.T)
        k = int(np.argmax(at))
        if float(at[k]) > tmax:
            tmax, t_c, t_x = float(at[k]), c.name, float(f.x[k])
    if f is None:
        return _missing("Beam", ref, bd.level, "no ultimate load combinations in the analysis")
    xs = f.x
    L = float(xs[-1])
    b, D = mem.b * 1000, mem.d * 1000
    cover = ds.beam_cover * 1000
    d = D - cover - 18
    i_pos = int(np.argmax(sag))
    M_pos, M_l, M_r = float(sag.max()), float(-hog[0]), float(-hog[-1])

    B = _Builder(
        "Beam",
        ref,
        bd.level,
        [
            ("Project", project.name),
            ("Member", f"Beam {ref}"),
            ("Level", bd.level),
            ("Section b × D", f"{b:.0f} × {D:.0f} mm"),
            ("Segment length", f"{L:.2f} m"),
            ("Concrete", f"{mem.grade} (fck = {fck:g} MPa)"),
            ("Steel", f"fy = {fy:g} MPa (main), {ds.fy_shear:g} MPa (links)"),
            ("Clear cover", f"{cover:.0f} mm"),
            ("Detailing", "IS 456 + IS 13920 (ductile)" if ductile else "IS 456"),
            ("Date", _today()),
        ],
    )
    B.sheet.notes = list(dict.fromkeys(bd.notes))
    comb_clause = "Table 18; IS 1893-1 cl 6.3.4"
    # ------------------------------------------------ 1 design forces
    B.group("Design forces (envelope of all ultimate combinations, 9 stations)")
    B.step(
        "Design sagging moment Mu+",
        "Mu+ = max over combinations and stations of M(x)",
        f"at x = {float(xs[i_pos]):.2f} m, combination {sag_c[i_pos] or '-'}",
        f"{M_pos:.2f} kN·m",
        comb_clause,
        "Mu_pos",
        M_pos,
    )
    B.compare("Mu+", M_pos, bd.M_sag, " kN·m")
    B.step(
        "Design hogging moment Mu− (left support)",
        "Mu−,L = max over combinations of −M(0)",
        f"at x = 0.00 m, combination {hog_c[0] or '-'}",
        f"{M_l:.2f} kN·m",
        comb_clause,
        "Mu_neg_l",
        M_l,
    )
    B.compare("Mu− (left)", M_l, -bd.M_hog_l, " kN·m")
    B.step(
        "Design hogging moment Mu− (right support)",
        "Mu−,R = max over combinations of −M(L)",
        f"at x = {L:.2f} m, combination {hog_c[-1] or '-'}",
        f"{M_r:.2f} kN·m",
        comb_clause,
        "Mu_neg_r",
        M_r,
    )
    B.compare("Mu− (right)", M_r, -bd.M_hog_r, " kN·m")
    B.step(
        "Analysis shear force Vu",
        "Vu = max over combinations and stations of |V(x)|",
        f"at x = {v_x:.2f} m, combination {v_c or '-'}",
        f"{vmax:.2f} kN",
        comb_clause,
        "Vu",
        vmax,
    )
    B.step(
        "Design torsion Tu",
        "Tu = max over combinations and stations of |T(x)|",
        f"at x = {t_x:.2f} m, combination {t_c or '-'}"
        + ("" if tmax > 1.0 else "; Tu ≤ 1 kN·m – torsion design not required"),
        f"{tmax:.2f} kN·m",
        "cl 41.1; " + comb_clause,
        "Tu",
        tmax,
    )
    B.compare("Tu", tmax, bd.T_max, " kN·m")
    # ------------------------------------------------ 2 section
    B.group("Section properties and limits")
    B.step(
        "Effective depth",
        "d = D − c − φlink − φbar/2  (φlink = 8 mm, φbar = 20 mm assumed)",
        f"d = {D:.0f} − {cover:.0f} − 8 − 10",
        f"{d:.0f} mm",
        "cl 26.4",
        "d",
        d,
    )
    k = is456.xu_max_ratio(fy)
    B.step(
        "Limiting neutral axis depth",
        "xu,max/d = 700/(1100 + 0.87 fy)  (tabulated for fy = 250/415/500)",
        f"fy = {fy:g} MPa",
        f"{k:.3f}",
        "cl 38.1 (f) note",
        "xu_max_d",
        k,
    )
    ml = is456.mu_lim(fck, fy, b, d)
    B.step(
        "Limiting moment of resistance",
        "Mu,lim = 0.36 fck b d² (xu,max/d)(1 − 0.42 xu,max/d)",
        f"0.36 × {fck:g} × {b:.0f} × {d:.0f}² × {k:.3f} × (1 − 0.42 × {k:.3f}) / 10⁶",
        f"{ml / 1e6:.2f} kN·m",
        "cl 38.1; Annex G-1.1(c)",
        "Mu_lim",
        ml / 1e6,
    )
    util = max(float(sag.max()), float(-hog.min())) / (ml / 1e6) if ml else 0.0
    B.step(
        "Flexural utilisation (analysis moments)",
        "U = max(Mu+, Mu−) / Mu,lim",
        f"{max(float(sag.max()), float(-hog.min())):.2f} / {ml / 1e6:.2f}",
        f"{util:.3f}",
        "Annex G-1.1",
        "utilisation",
        util,
    )
    B.compare("utilisation", util, bd.utilisation, "", 3)
    ast_min = 0.85 * b * d / fy
    B.step(
        "Minimum tension steel",
        "Ast,min = 0.85 b d / fy",
        f"0.85 × {b:.0f} × {d:.0f} / {fy:g}",
        _mm2(ast_min),
        "cl 26.5.1.1(a)",
        "ast_min_is456",
        ast_min,
    )
    if ductile:
        rho = is13920.rho_min(fck, fy)
        a_d = rho * b * d
        B.step(
            "Minimum steel – ductile (both faces)",
            "ρmin = 0.24 √fck / fy;  As,min = ρmin b d",
            f"0.24 × √{fck:g} / {fy:g} = {rho:.5f};  × {b:.0f} × {d:.0f}",
            _mm2(a_d),
            "IS 13920 cl 6.2.1",
            "ast_min_13920",
            a_d,
        )
        ast_min = max(ast_min, a_d)
    B.step(
        "Minimum steel adopted",
        "Ast,min = max(0.85 b d/fy, ρmin b d)" if ductile else "Ast,min = 0.85 b d / fy",
        f"max({0.85 * b * d / fy:.0f}, {rho * b * d:.0f})" if ductile else "",
        _mm2(ast_min),
        "cl 26.5.1.1(a)" + ("; IS 13920 cl 6.2.1" if ductile else ""),
        "ast_min",
        ast_min,
    )
    ast_max = 0.04 * b * D
    B.step(
        "Maximum tension / compression steel",
        "Ast,max = 0.04 b D",
        f"0.04 × {b:.0f} × {D:.0f}",
        _mm2(ast_max),
        "cl 26.5.1.1(b); 26.5.1.2",
        "ast_max",
        ast_max,
    )
    # ------------------------------------------------ torsion: equivalent moments (cl 41.4.2)
    m_sag, m_hl, m_hr = M_pos, M_l, M_r
    torsion = tmax > 1.0
    if torsion:
        B.group("Torsion – equivalent moments (IS 456 cl 41.4.2)")
        pt0 = 100 * 0.85 * b * d / fy / (b * d)
        t0 = is456.torsion_design(tmax, vmax, m_sag, max(m_hl, m_hr), b, D, cover, fck, fy, ds.fy_shear, pt0)
        Mt = tmax * (1 + D / b) / 1.7
        st = B.step(
            "Equivalent moment from torsion",
            "Mt = Tu (1 + D/b) / 1.7",
            f"{tmax:.2f} × (1 + {D:.0f}/{b:.0f}) / 1.7",
            f"{Mt:.2f} kN·m",
            "cl 41.4.2",
            "Mt",
            Mt,
        )
        B.compare("Mt (design routine)", Mt, t0.Mt, " kN·m", 2, st)
        mh = max(m_hl, m_hr)
        new_sag = max(t0.Me1_sag, t0.Me2_hog)
        B.step(
            "Design moment – bottom steel",
            "Me1 = Mu+ + Mt;  Me2 = Mt − Mu− (reversed, if Mt > Mu−) – larger governs",
            f"max({m_sag:.2f} + {Mt:.2f}, max({Mt:.2f} − {mh:.2f}, 0))",
            f"{new_sag:.2f} kN·m",
            "cl 41.4.2; 41.4.2.1",
            "Me_pos",
            new_sag,
        )
        new_l = max(m_hl + t0.Mt, t0.Me2_sag)
        new_r = max(m_hr + t0.Mt, t0.Me2_sag)
        B.step(
            "Design moments – top steel at supports",
            "Me1 = Mu− + Mt;  Me2 = Mt − Mu+ (if Mt > Mu+) – larger governs",
            f"L: max({m_hl:.2f} + {Mt:.2f}, {t0.Me2_sag:.2f});  R: max({m_hr:.2f} + {Mt:.2f}, {t0.Me2_sag:.2f})",
            f"{new_l:.2f}; {new_r:.2f} kN·m",
            "cl 41.4.2; 41.4.2.1",
            "Me_neg",
            max(new_l, new_r),
        )
        m_sag, m_hl, m_hr = new_sag, new_l, new_r
    # ------------------------------------------------ 3 flexure
    res = {}
    for label, key, Mu in (
        ("Mid-span (sagging)", "pos", m_sag),
        ("Left support (hogging)", "neg_l", m_hl),
        ("Right support (hogging)", "neg_r", m_hr),
    ):
        B.group(f"Flexure – {label}" + (" – moment incl. torsion" if torsion else ""))
        fr = is456.flexure(Mu, fck, fy, b, D, cover)
        res[key] = fr
        _beam_flexure_steps(B, key, Mu, fr, fck, fy, b, d, cover, ml, 0.85 * b * d / fy)  # is456.flexure minimum
    fs, fl, fr_ = res["pos"], res["neg_l"], res["neg_r"]
    # ------------------------------------------------ 4 bars provided (top first: bottom ≥ ½ top when ductile)
    B.group("Reinforcement provided")
    bars = {}
    for label, key, req_terms, rep_area, rep_str in (
        ("Top bars at left support", "top_l", (fl.ast, fs.asc), bd.ast_top_l, bd.top_l),
        ("Top bars at right support", "top_r", (fr_.ast, fs.asc), bd.ast_top_r, bd.top_r),
    ):
        req = max(*req_terms, ast_min)
        nb, dia_, prov = is456.select_bars(req, b, cover)
        bars[key] = (nb, dia_, prov)
        st = B.step(
            label,
            "As,req = max(Ast−, Asc+, Ast,min); bars with the least excess in one layer, clear spacing ≥ max(φ, 25 mm)",
            "max(" + ", ".join(f"{v:.0f}" for v in (*req_terms, ast_min)) + f") = {req:.0f} mm²  →  "
            f"{nb} × π × {dia_}² / 4",
            f"{nb}-T{dia_} = {prov:.0f} mm²",
            "cl 26.5.1.1; 26.3.2",
            f"ast_{key}",
            prov,
        )
        B.compare(f"{label.lower()} area", prov, rep_area, " mm²", 0, st)
        B.compare_text(label.lower(), f"{nb}-T{dia_}", rep_str, st)
    nl, dl, prov_l = bars["top_l"]
    nr, dr, prov_r = bars["top_r"]
    terms = (fs.ast, fl.asc, fr_.asc, ast_min)
    bot_req = max(terms)
    subst = "max(" + ", ".join(f"{v:.0f}" for v in terms) + ")"
    if ductile:
        bot_req = max(bot_req, 0.5 * max(prov_l, prov_r))
        subst = f"max({subst}, ½ × {max(prov_l, prov_r):.0f})"
    nbot, dbot, prov_b = is456.select_bars(bot_req, b, cover)
    bars["bot"] = (nbot, dbot, prov_b)
    st = B.step(
        "Bottom bars (span)",
        "As,req = max(Ast+, Asc,L−, Asc,R−, Ast,min)"
        + ("; ≥ ½ the top steel at the joint faces" if ductile else "")
        + "; least excess in one layer",
        f"{subst} = {bot_req:.0f} mm²  →  {nbot} × π × {dbot}² / 4",
        f"{nbot}-T{dbot} = {prov_b:.0f} mm²",
        "cl 26.5.1.1; 26.3.2" + ("; IS 13920 cl 6.2.3" if ductile else ""),
        "ast_bot",
        prov_b,
    )
    B.compare("bottom bars area", prov_b, bd.ast_bot, " mm²", 0, st)
    B.compare_text("bottom bars", f"{nbot}-T{dbot}", bd.bottom, st)
    flex_ok = fs.ok and fl.ok and fr_.ok
    B.step(
        "Maximum steel check",
        "Ast, Asc ≤ 0.04 b D",
        f"max Ast = {max(fs.ast, fl.ast, fr_.ast):.0f}, max Asc = {max(fs.asc, fl.asc, fr_.asc):.0f} "
        f"{'≤' if flex_ok else '>'} {ast_max:.0f} mm²",
        "OK" if flex_ok else "NOT OK – increase section",
        "cl 26.5.1.1(b); 26.5.1.2",
    )
    if ductile:
        pmax_ = 100 * max(prov_b, prov_l, prov_r) / (b * d)
        B.step(
            "Maximum steel ratio – ductile",
            "p = 100 As / (b d) ≤ 2.5 % on either face",
            f"100 × {max(prov_b, prov_l, prov_r):.0f} / ({b:.0f} × {d:.0f}) = {pmax_:.2f} %",
            "OK" if pmax_ <= 2.5 else "NOT OK – increase section",
            "IS 13920 cl 6.2.2",
            "p_max_ductile",
            pmax_,
        )
    # ------------------------------------------------ 5 design shear
    prov_b, prov_l, prov_r = bars["bot"][2], bars["top_l"][2], bars["top_r"][2]
    pt = 100 * max(prov_l, prov_r) / (b * d)
    v_design = vmax
    col_below = {x.n2: x for x in m.members.values() if x.kind in ("column", "wall")}
    col_above = {x.n1: x for x in m.members.values() if x.kind in ("column", "wall")}
    framed = all(nd in col_below or nd in col_above for nd in (mem.n1, mem.n2))
    if ductile and framed:
        B.group("Capacity design shear (IS 13920 cl 6.3.3)")
        a_nd, b_nd = m.nodes[mem.n1], m.nodes[mem.n2]
        ha = _end_support(mem, a_nd, b_nd, col_below, col_above, mem.n1)
        hb = _end_support(mem, a_nd, b_nd, col_below, col_above, mem.n2)
        lc = max(L - ha - hb, 0.3 * L)
        B.step(
            "Clear span between column faces",
            "lc = L − ½ column width at A − ½ column width at B  (≥ 0.3 L)",
            f"{L:.3f} − {ha:.3f} − {hb:.3f}",
            f"{lc:.3f} m",
            "IS 13920 cl 6.3.3",
            "lc",
            lc,
        )
        g = fa.forces(mid, dict(is13920.GRAVITY), 3)
        vga, vgb = abs(float(g.Vz[0])), abs(float(g.Vz[-1]))
        B.step(
            "Gravity shear",
            "Vg = support shears for 1.2 (DL + LL)",
            "1.2 DL + 1.2 LL",
            f"Vg,A = {vga:.2f}, Vg,B = {vgb:.2f} kN",
            "IS 13920 cl 6.3.3",
            "Vg",
            max(vga, vgb),
        )
        ms = is13920.beam_moment_capacity(prov_b, b, d, fck, fy) / 1e6
        mha = is13920.beam_moment_capacity(prov_l, b, d, fck, fy) / 1e6
        mhb = is13920.beam_moment_capacity(prov_r, b, d, fck, fy) / 1e6
        B.step(
            "Moment capacities of the bars provided",
            "Mu = 0.87 fy Ast d (1 − Ast fy / (b d fck)) ≤ Mu,lim",
            f"Ms: Ast = {prov_b:.0f};  Mh,A: Ast = {prov_l:.0f};  Mh,B: Ast = {prov_r:.0f} mm²",
            f"Ms = {ms:.2f}, Mh,A = {mha:.2f}, Mh,B = {mhb:.2f} kN·m",
            "Annex G-1.1(b); IS 13920 cl 6.3.3",
            "Ms",
            ms,
        )
        sr = is13920.OVERSTRENGTH * (ms + mhb) / lc
        sl = is13920.OVERSTRENGTH * (mha + ms) / lc
        v_cap = is13920.beam_capacity_shear(vga, vgb, ms, mha, ms, mhb, lc)
        B.step(
            "Capacity (sway) shear",
            "sway right: Vu = Vg ± 1.4 (Ms,A + Mh,B)/lc;  sway left: Vu = Vg ± 1.4 (Mh,A + Ms,B)/lc",
            f"1.4 × ({ms:.2f} + {mhb:.2f}) / {lc:.3f} = {sr:.2f};  1.4 × ({mha:.2f} + {ms:.2f}) / {lc:.3f} = "
            f"{sl:.2f};  A: |{vga:.2f} − {sr:.2f}| = {abs(vga - sr):.2f}, {vga:.2f} + {sl:.2f} = {vga + sl:.2f};  "
            f"B: {vgb:.2f} + {sr:.2f} = {vgb + sr:.2f}, |{vgb:.2f} − {sl:.2f}| = {abs(vgb - sl):.2f}",
            f"{v_cap:.2f} kN",
            "IS 13920 cl 6.3.3",
            "V_cap",
            v_cap,
        )
        v_design = max(vmax, v_cap)
        st = B.step(
            "Design shear",
            "Vu = max(analysis shear, capacity shear)",
            f"max({vmax:.2f}, {v_cap:.2f})",
            f"{v_design:.2f} kN",
            "IS 13920 cl 6.3.3",
            "V_design",
            v_design,
        )
    else:
        B.group("Design shear")
        st = B.step(
            "Design shear",
            "Vu = analysis shear"
            + (" (beam not framed into columns at both ends – no capacity shear)" if ductile else ""),
            f"{vmax:.2f} kN",
            f"{v_design:.2f} kN",
            "cl 40.1" + ("; IS 13920 cl 6.3.3" if ductile else ""),
            "V_design",
            v_design,
        )
    B.compare("design shear", v_design, bd.V_max, " kN", 2, st)
    # ------------------------------------------------ 6 shear links (cl 40)
    B.group("Shear (vertical links)")
    B.step(
        "Tension steel at support",
        "pt = 100 As / (b d)",
        f"100 × {max(prov_l, prov_r):.0f} / ({b:.0f} × {d:.0f})",
        f"{pt:.3f} %",
        "Table 19",
        "pt_support",
        pt,
    )
    sh = is456.shear(v_design, fck, ds.fy_shear, b, d, pt)
    tv = v_design * 1e3 / (b * d)
    tc = is456.tau_c(pt, fck)
    tcm = is456.tau_c_max(fck)
    B.step("Nominal shear stress", "τv = Vu / (b d)", f"{v_design:.2f} × 10³ / ({b:.0f} × {d:.0f})", f"{tv:.3f} MPa",
           "cl 40.1", "tau_v", tv)  # fmt: skip
    B.step(
        "Design shear strength of concrete",
        "τc from Table 19 (pt, fck)",
        f"pt = {min(max(pt, 0.15), 3.0):.3f} %, M{fck:g}",
        f"{tc:.3f} MPa",
        "cl 40.2.1; Table 19",
        "tau_c",
        tc,
    )
    B.step(
        "Maximum shear stress",
        "τv ≤ τc,max (Table 20)",
        f"{tv:.3f} {'≤' if tv <= tcm else '>'} {tcm:.2f}",
        "OK" if tv <= tcm else "NOT OK – increase section",
        "cl 40.2.3; Table 20",
        "tau_c_max",
        tcm,
    )
    fyv = min(ds.fy_shear, 415.0)
    B.step(
        "Characteristic strength of links",
        "fyv = min(fy,links, 415)",
        f"min({ds.fy_shear:g}, 415)",
        f"{fyv:g} MPa",
        "cl 40.4(a); 26.5.1.6",
        "fyv",
        fyv,
    )
    vus = v_design * 1e3 - tc * b * d
    if tv > tc:
        B.step(
            "Shear to be carried by links",
            "Vus = Vu − τc b d",
            f"{v_design:.2f} − {tc:.3f} × {b:.0f} × {d:.0f} / 10³",
            f"{vus / 1e3:.2f} kN",
            "cl 40.4",
            "Vus",
            vus / 1e3,
        )
    else:
        B.step(
            "Shear to be carried by links",
            "τv ≤ τc → minimum shear reinforcement only",
            f"{tv:.3f} ≤ {tc:.3f}",
            "Vus = 0",
            "cl 40.3; 26.5.1.6",
            "Vus",
            0.0,
        )
    s_max = min(0.75 * d, 300.0)
    chosen = None
    tried = []
    if tv <= tcm:
        for dia_, legs_ in ((8, 2), (10, 2), (10, 4), (12, 4)):  # same order as is456.shear
            asv = legs_ * math.pi * dia_ * dia_ / 4
            s_mr = 0.87 * fyv * asv / (0.4 * b)
            s_st = 0.87 * fyv * asv * d / vus if tv > tc else math.inf
            s = min(s_max, s_mr, s_st)
            if s >= 75:
                chosen = (dia_, legs_, asv, s_st, s_mr, s)
                break
            tried.append(f"{legs_}L-T{dia_} gives {s:.0f} mm < 75 mm")
    if chosen:
        dia_, legs_, asv, s_st, s_mr, s = chosen
        B.step(
            "Link size",
            "Asv = legs × π φ² / 4",
            (("; ".join(tried) + "; ") if tried else "") + f"{legs_} × π × {dia_}² / 4",
            f"{legs_}L-T{dia_}, Asv = {asv:.0f} mm²",
            "cl 40.4(a)",
            "Asv",
            asv,
        )
        B.step(
            "Spacing for strength",
            "sv = 0.87 fyv Asv d / Vus",
            f"0.87 × {fyv:g} × {asv:.0f} × {d:.0f} / {vus:.0f}" if tv > tc else "not required (Vus = 0)",
            f"{s_st:.0f} mm" if math.isfinite(s_st) else "–",
            "cl 40.4(a)",
            "sv_strength",
            s_st if math.isfinite(s_st) else None,
        )
        B.step(
            "Spacing for minimum shear reinforcement",
            "Asv/(b sv) ≥ 0.4/(0.87 fyv) → sv ≤ 0.87 fyv Asv / (0.4 b)",
            f"0.87 × {fyv:g} × {asv:.0f} / (0.4 × {b:.0f})",
            f"{s_mr:.0f} mm",
            "cl 26.5.1.6",
            "sv_min_reinf",
            s_mr,
        )
        B.step(
            "Maximum spacing",
            "sv ≤ min(0.75 d, 300 mm)",
            f"min(0.75 × {d:.0f}, 300)",
            f"{s_max:.0f} mm",
            "cl 26.5.1.5",
            "sv_max",
            s_max,
        )
        s_prov = math.floor(s / 25) * 25
        st = B.step(
            "Shear links",
            "sv = least of the above, rounded down to 25 mm (≥ 75 mm)",
            f"min({s_st:.0f}, {s_mr:.0f}, {s_max:.0f}) = {s:.0f} mm" if math.isfinite(s_st)
            else f"min({s_mr:.0f}, {s_max:.0f}) = {s:.0f} mm",
            f"{legs_}L-T{dia_} @ {s_prov} c/c",
            "cl 26.5.1.5; 40.4",
            "sv_shear",
            s_prov,
        )  # fmt: skip
        B.compare("shear link spacing (design routine)", s_prov, sh.spacing, " mm", 0, st)
    else:
        why = "τv > τc,max" if tv > tcm else "spacing < 75 mm with 4L-T12 (" + "; ".join(tried) + ")"
        B.step("Shear links", "–", why, "FAIL – increase section", "cl 40.2.3; 40.4", "sv_shear", None)
    # ------------------------------------------------ 7 torsion links (cl 41.3 / 41.4.3)
    tors = None
    if torsion:
        B.group("Torsion – closed stirrups (IS 456 cl 41)")
        ld = max(dl, dr)
        tors = is456.torsion_design(tmax, v_design, m_sag, max(m_hl, m_hr), b, D, cover, fck, fy, ds.fy_shear, pt, ld)
        dt = D - cover - 8 - ld / 2
        Ve = v_design + 1.6 * tmax * 1e3 / b
        st = B.step(
            "Equivalent shear",
            "Ve = Vu + 1.6 Tu / b",
            f"{v_design:.2f} + 1.6 × {tmax:.2f}×10³ / {b:.0f}",
            f"{Ve:.2f} kN",
            "cl 41.3.1",
            "Ve",
            Ve,
        )
        B.compare("Ve (design routine)", Ve, tors.Ve, " kN", 2, st)
        tve = Ve * 1e3 / (b * dt)
        B.step(
            "Equivalent shear stress",
            "τve = Ve / (b d) ≤ τc,max  (d = D − c − 8 − φ/2)",
            f"{Ve:.2f}×10³ / ({b:.0f} × {dt:.0f})",
            f"{tve:.3f} {'≤' if tve <= tcm else '>'} {tcm:.2f} MPa",
            "cl 41.3.1; Table 20",
            "tau_ve",
            tve,
        )
        if tve <= tcm:
            x1, y1 = b - 2 * cover, D - 2 * cover
            b1, d1 = x1 - 8 - ld, y1 - 8 - ld
            B.step(
                "Closed stirrup dimensions",
                "x1 = b − 2c, y1 = D − 2c;  b1 = x1 − 8 − φ, d1 = y1 − 8 − φ (corner bar centres)",
                f"x1 = {x1:.0f}, y1 = {y1:.0f}; φ = {ld} mm",
                f"b1 = {b1:.0f}, d1 = {d1:.0f} mm",
                "cl 41.4.3",
            )
            tc_t = is456.tau_c(pt, fck)
            t1 = tmax * 1e6 / (b1 * d1 * 0.87 * fyv)
            t2 = v_design * 1e3 / (2.5 * d1 * 0.87 * fyv)
            t3 = (tve - tc_t) * b / (0.87 * fyv)
            t4 = 0.4 * b / (0.87 * fyv)
            per_mm = max(t1 + t2, t3, t4)
            B.step(
                "Stirrup area per unit length",
                "Asv/sv = Tu/(b1 d1 0.87 fyv) + Vu/(2.5 d1 0.87 fyv) ≥ (τve − τc) b/(0.87 fyv), ≥ 0.4 b/(0.87 fyv)",
                f"{t1:.4f} + {t2:.4f} = {t1 + t2:.4f};  ({tve:.3f} − {tc_t:.3f}) × {b:.0f}/(0.87 × {fyv:g}) = "
                f"{t3:.4f};  {t4:.4f}",
                f"{per_mm:.4f} mm²/mm",
                "cl 41.4.3; 26.5.1.6",
                "asv_per_mm",
                per_mm,
            )
            s_max_t = min(x1, (x1 + y1) / 4, 300.0)
            B.step(
                "Maximum spacing of closed stirrups",
                "sv ≤ min(x1, (x1 + y1)/4, 300 mm)",
                f"min({x1:.0f}, ({x1:.0f} + {y1:.0f})/4, 300)",
                f"{s_max_t:.0f} mm",
                "cl 26.5.1.7",
                "sv_max_torsion",
                s_max_t,
            )
            t_tried, t_pick = [], None
            for tdia in (8, 10, 12):
                asv_t = 2 * math.pi * tdia * tdia / 4
                s_t = min(asv_t / per_mm, s_max_t)
                if s_t >= 75:
                    t_pick = (tdia, asv_t, s_t)
                    break
                t_tried.append(f"T{tdia} gives {s_t:.0f} mm < 75 mm")
            if t_pick:
                tdia, asv_t, s_t = t_pick
                sp = math.floor(s_t / 25) * 25
                st = B.step(
                    "Closed stirrups for torsion",
                    "sv = 2 π φ²/4 ÷ (Asv/sv) ≤ s,max; rounded down to 25 mm (≥ 75 mm)",
                    (("; ".join(t_tried) + "; ") if t_tried else "")
                    + f"min({asv_t:.0f} / {per_mm:.4f}, {s_max_t:.0f}) = {s_t:.0f} mm",
                    f"2L-T{tdia} @ {sp} c/c (closed)",
                    "cl 41.4.3; 26.5.1.7",
                    "sv_torsion",
                    sp,
                )
                B.compare("torsion stirrup spacing (design routine)", sp, tors.spacing, " mm", 0, st)
            else:
                B.step("Closed stirrups for torsion", "–", "; ".join(t_tried), "FAIL – increase section",
                       "cl 41.4.3", "sv_torsion", None)  # fmt: skip
        sh0 = sh
        if tors.ok and (not sh.ok or tors.spacing * sh.dia**2 <= sh.spacing * tors.dia**2):
            sh = is456.ShearResult(v_design, tors.tau_ve, sh.tau_c, 2, tors.dia, tors.spacing, True)
            gov = "torsion stirrups govern"
        elif not tors.ok:
            sh = is456.ShearResult(v_design, tors.tau_ve, sh.tau_c, 2, 12, 0.0, False, tors.note)
            gov = "FAIL – " + (tors.note or "torsion")
        else:
            gov = "shear links govern"
        B.step(
            "Governing links",
            "torsion stirrups replace the shear links when Asv/sv (torsion) ≥ Asv/sv (shear)",
            f"torsion T{tors.dia} @ {tors.spacing:.0f}"
            + (f" vs shear {sh0.legs}L-T{sh0.dia} @ {sh0.spacing:.0f}" if sh0.ok else " (shear links fail)"),
            gov,
            "cl 41.4.3",
        )
    # ------------------------------------------------ 8 side-face steel
    B.group("Side-face reinforcement")
    side = tors.side_face if tors else _side_face(b, D, cover)
    if tors is not None and D > 450:
        a_side = 0.001 * b * (D - 2 * cover) / 2
        nside = max(2, math.ceil((D - 2 * cover - 100) / 300))
        st = B.step(
            "Side-face bars (torsion, D > 450 mm)",
            "As = 0.1 % of web area per face; bars ≤ 300 mm apart",
            f"0.001 × {b:.0f} × ({D:.0f} − 2 × {cover:.0f}) / 2 = {a_side:.0f} mm²;  "
            f"n = max(2, ⌈({D:.0f} − {2 * cover:.0f} − 100)/300⌉) = {nside}",
            side,
            "cl 26.5.1.3; 26.5.1.7(b)",
        )
    elif tors is None and D > 750:
        a_face = 0.001 * b * D / 2
        nside = max(2, math.ceil((D - 2 * cover - 100) / 300))
        st = B.step(
            "Side-face bars (D > 750 mm)",
            "As = 0.1 % of web area, half on each face; bars ≤ 300 mm apart",
            f"0.001 × {b:.0f} × {D:.0f} / 2 = {a_face:.0f} mm² per face; n = {nside}",
            side,
            "cl 26.5.1.3",
        )
    else:
        st = B.step(
            "Side-face bars",
            "required when D > 750 mm (D > 450 mm with torsion)",
            f"D = {D:.0f} mm",
            "not required",
            "cl 26.5.1.3; 26.5.1.7(b)",
        )
    B.compare_text("side-face steel", side, bd.side_face, st)
    # ------------------------------------------------ 9 links adopted
    B.group("Links adopted" + (" (IS 13920 cl 6.3.5)" if ductile else ""))
    links = links_end = None
    if sh.ok:
        if ductile:
            s_half = math.floor(d / 2 / 25) * 25
            s_mid = min(sh.spacing, s_half)
            B.step(
                "Spacing away from the joints",
                "sv ≤ d/2 (rounded down to 25 mm)",
                f"min({sh.spacing:.0f}, {s_half:.0f})",
                f"{s_mid:.0f} mm",
                "IS 13920 cl 6.3.5",
            )
        else:
            s_mid = sh.spacing
        links = is456.Links(sh.legs, sh.dia, float(s_mid))
        st = B.step(
            "Links in the span",
            "governing links of the steps above",
            f"{sh.legs} legs of T{sh.dia}",
            str(links),
            "cl 26.5.1.5; 40.4" + ("; IS 13920 cl 6.3.5" if ductile else ""),
            "sv",
            float(s_mid),
        )
        if bd.links is not None:
            B.compare("link spacing", float(s_mid), bd.links.spacing, " mm", 0, st)
        if ductile:
            dbmin = min(dbot, dl, dr)
            s_end = min(sh.spacing, d / 4, 8 * dbmin, 100.0)
            s_end_p = float(max(math.floor(s_end / 5) * 5, 50))
            links_end = is456.Links(sh.legs, max(sh.dia, 8), s_end_p)
            st = B.step(
                "Hoops within 2d of the column faces",
                "sv ≤ min(d/4, 8 φmin, 100 mm), rounded down to 5 mm, ≥ 50 mm",
                f"min({sh.spacing:.0f}, {d:.0f}/4, 8 × {dbmin}, 100) = {s_end:.1f} mm; zone 2d = {2 * d:.0f} mm",
                str(links_end),
                "IS 13920 cl 6.3.5",
                "sv_end",
                s_end_p,
            )
            if bd.links_end is not None:
                B.compare("hoop spacing at the joints", s_end_p, bd.links_end.spacing, " mm", 0, st)
    else:
        st = B.step("Links", "–", sh.note or "no valid link arrangement", "FAIL – increase section", "cl 40.4", "sv")
    if links is None:
        stirrups = "FAIL"
    elif links_end is not None:
        stirrups = f"{links_end} (2d from faces) / {int(links.spacing)} c/c"
    else:
        stirrups = str(links)
    B.compare_text("links", stirrups, bd.stirrups, st)
    # ------------------------------------------------ 10 deflection
    B.group("Deflection (span / effective depth)")
    span, basic = _deflection_span(m, mem)
    cond = {7: "cantilever", 20: "simply supported", 26: "continuous"}.get(basic, "")
    B.step(
        "Span and basic L/d ratio",
        "basic L/d = 7 cantilever, 20 simply supported, 26 continuous",
        f"span between supports L = {span:.2f} m, {cond}",
        f"{basic}",
        "cl 23.2.1(a)",
        "basic",
        basic,
    )
    pt_b = 100 * prov_b / (b * d)
    fs_srv = 0.58 * fy * fs.ast / max(prov_b, 1)
    B.step(
        "Steel stress at service",
        "fs = 0.58 fy Ast,req / Ast,prov",
        f"0.58 × {fy:g} × {fs.ast:.0f} / {prov_b:.0f}",
        f"{fs_srv:.1f} MPa",
        "cl 23.2.1(c); Fig 4",
        "fs",
        fs_srv,
    )
    mf = is456.deflection_mf(pt_b, fs_srv)
    B.step(
        "Modification factor (tension steel)",
        "MF = 1 / (0.225 + 0.00322 fs − 0.625 log10(1/pt)), 0.4 ≤ MF ≤ 2.0",
        f"pt = 100 × {prov_b:.0f} / ({b:.0f} × {d:.0f}) = {pt_b:.3f} %, fs = {fs_srv:.1f} MPa",
        f"{mf:.3f}",
        "cl 23.2.1(c); Fig 4",
        "mf",
        mf,
    )
    red = 10.0 / span if span > 10.0 and basic != 7 else 1.0
    B.step(
        "Long-span factor",
        "× 10 / L for spans over 10 m (not cantilevers)",
        f"L = {span:.2f} m",
        f"{red:.3f}",
        "cl 23.2.1(b)",
        "span_factor",
        red,
    )
    allowed = basic * mf * red
    actual = span * 1000 / d
    dok = actual <= allowed if span > 0 else True
    st = B.step(
        "Deflection check",
        "L/d ≤ basic × MF × factor",
        f"{span * 1000:.0f} / {d:.0f} = {actual:.2f} {'≤' if dok else '>'} {basic} × {mf:.3f} × {red:.3f} "
        f"= {allowed:.2f}",
        "OK" if dok else "NOT OK",
        "cl 23.2.1",
        "l_over_d",
        actual,
    )
    if dok != bd.deflection_ok:
        B._warn("Recomputed deflection result differs from the design report", st)
    # ------------------------------------------------ checks
    lim_txt = "within" if flex_ok else "exceeds"
    B.check("Flexure", flex_ok, f"Mu,max = {max(m_sag, m_hl, m_hr):.1f} kN·m; steel {lim_txt} 4 %")
    B.check(
        "Shear",
        sh.ok,
        f"Vu = {v_design:.1f} kN, τv = {tv:.2f} MPa (τc,max = {tcm:.2f}); links {stirrups}"
        + (f" – {sh.note}" if sh.note else ""),
    )
    if tors is not None:
        B.check(
            "Torsion",
            tors.ok,
            f"Tu = {tmax:.2f} kN·m, τve = {tors.tau_ve:.2f} MPa" + (f" – {tors.note}" if tors.note else ""),
        )
    B.check("Deflection", dok, f"L/d = {actual:.1f} (allowed {allowed:.1f})")
    if B.sheet.ok != bd.ok:
        B._warn(f"Overall result {'PASS' if B.sheet.ok else 'FAIL'} differs from the design report", None)
    return B.sheet


def _beam_flexure_steps(B: _Builder, key, Mu_kNm, fr, fck, fy, b, d, cover, ml, ast_min) -> None:
    Mu = abs(Mu_kNm) * 1e6
    if Mu <= 0:
        st = B.step("Tension steel", "Mu = 0", "no moment of this sign", "0 mm²", "Annex G-1.1", f"ast_calc_{key}", 0.0)
        B.compare("Ast", 0.0, fr.ast, " mm²", 0, st)
        return
    if Mu <= ml:
        B.step(
            "Type of section",
            "Mu ≤ Mu,lim → singly reinforced",
            f"{Mu_kNm:.2f} ≤ {ml / 1e6:.2f} kN·m",
            "singly reinforced",
            "Annex G-1.1",
        )
        a = is456.ast_singly(Mu, fck, fy, b, d)
        B.step(
            "Tension steel for Mu",
            "Ast = 0.5 fck/fy [1 − √(1 − 4.6 Mu/(fck b d²))] b d",
            f"0.5 × {fck:g}/{fy:g} × [1 − √(1 − 4.6 × {Mu_kNm:.2f}×10⁶ / ({fck:g} × {b:.0f} × {d:.0f}²))] "
            f"× {b:.0f} × {d:.0f}",
            _mm2(a),
            "Annex G-1.1(b)",
            f"ast_calc_{key}",
            a,
        )
        ast = max(a, ast_min)
        st = B.step(
            "Tension steel required",
            "Ast,req = max(Ast, Ast,min)",
            f"max({a:.0f}, {ast_min:.0f})",
            _mm2(ast),
            "cl 26.5.1.1(a)",
            f"ast_flex_{key}",
            ast,
        )
        B.compare("Ast", ast, fr.ast, " mm²", 0, st)
        return
    B.step(
        "Type of section",
        "Mu > Mu,lim → doubly reinforced",
        f"{Mu_kNm:.2f} > {ml / 1e6:.2f} kN·m",
        "doubly reinforced",
        "Annex G-1.2",
    )
    dc = cover + 18
    fsc = is456.fsc_doubly(fy, dc / d)
    B.step(
        "Stress in compression steel",
        "fsc from SP-16 Table F at d'/d",
        f"d' = {dc:.0f} mm, d'/d = {dc / d:.3f}",
        f"{fsc:.0f} MPa",
        "Annex G-1.2; SP-16 Table F",
        f"fsc_{key}",
        fsc,
    )
    asc = (Mu - ml) / ((fsc - 0.446 * fck) * (d - dc))
    st = B.step(
        "Compression steel",
        "Asc = (Mu − Mu,lim) / ((fsc − 0.446 fck)(d − d'))",
        f"({Mu_kNm:.2f} − {ml / 1e6:.2f}) × 10⁶ / (({fsc:.0f} − 0.446 × {fck:g}) × ({d:.0f} − {dc:.0f}))",
        _mm2(asc),
        "Annex G-1.2",
        f"asc_{key}",
        asc,
    )
    B.compare("Asc", asc, fr.asc, " mm²", 0, st)
    a1 = is456.ast_singly(ml, fck, fy, b, d)
    ast = a1 + asc * fsc / (0.87 * fy)
    st = B.step(
        "Tension steel required",
        "Ast = Ast,lim + Asc fsc / (0.87 fy),  Ast,lim from G-1.1(b) with Mu,lim",
        f"{a1:.0f} + {asc:.0f} × {fsc:.0f} / (0.87 × {fy:g})",
        _mm2(ast),
        "Annex G-1.2",
        f"ast_flex_{key}",
        ast,
    )
    B.compare("Ast", ast, fr.ast, " mm²", 0, st)


def beam_steps(project: Project, fa: FrameAnalysis, bd: BeamDesign) -> list[Step]:
    """The numbered steps of :func:`beam_sheet` (pure data, no PDF)."""
    return beam_sheet(project, fa, bd).steps


# ============================================================================= columns
def _column_demands(fa: FrameAnalysis, mid: int):
    """(combo, end, Pu kN, Mux kN·m, Muy kN·m) for both ends of every ultimate combination."""
    out = []
    for c in fa.ultimate:
        f = fa.forces(mid, c.factors, 3)
        for k, end in ((0, "bottom"), (-1, "top")):
            out.append((c.name, end, float(-f.N[k]), float(f.My[k]), float(f.Mz[k])))
    return out


def _column_eval(prepared, p, b, D, fck, fy, cover, ex, ey, Max0, May0):
    """Mirror of the per-demand check inside ``is456.design_column`` with intermediate values."""
    As = p / 100 * b * D
    curves = (
        is456.interaction_curve(b, D, As, fck, fy, cover),
        is456.interaction_curve(D, b, As, fck, fy, cover),
    )
    Puz = is456.puz(b, D, As, fck, fy)
    Pbx = float(curves[0][0][int(np.argmax(curves[0][1]))])
    Pby = float(curves[1][0][int(np.argmax(curves[1][1]))])
    rows = []
    for name, end, Pu_kN, Pu, Mx, My in prepared:
        row = {"name": name, "end": end, "Pu_kN": Pu_kN, "Pu": Pu, "Mx": Mx, "My": My}
        if Pu_kN < 0:
            Pt = Pu_kN * 1e3
            mx1 = float(np.interp(Pt, *curves[0]))
            my1 = float(np.interp(Pt, *curves[1]))
            r = 9.99 if (mx1 <= 0 or my1 <= 0) else Mx / mx1 + My / my1
            rt = abs(Pt) / (0.87 * fy * As)
            row.update(tension=True, mx1=mx1, my1=my1, rt=rt, r=max(r, rt))
        else:
            kx = min(max((Puz - Pu) / max(Puz - Pbx, 1e-9), 0.0), 1.0)
            ky = min(max((Puz - Pu) / max(Puz - Pby, 1e-9), 0.0), 1.0)
            ax, ay = kx * Max0 * Pu, ky * May0 * Pu
            mxa, mya = max(Mx, Pu * ex) + ax, My + ay
            mxb, myb = Mx + ax, max(My, Pu * ey) + ay
            ra = is456.biaxial_ratio(Pu, mxa, mya, b, D, As, fck, fy, cover, curves)
            rb = is456.biaxial_ratio(Pu, mxb, myb, b, D, As, fck, fy, cover, curves)
            rr = Pu / Puz
            an = 1.0 if rr <= 0.2 else (2.0 if rr >= 0.8 else 1.0 + (rr - 0.2) / 0.6)
            row.update(
                tension=False,
                kx=kx,
                ky=ky,
                ax=ax,
                ay=ay,
                mxa=mxa,
                mya=mya,
                mxb=mxb,
                myb=myb,
                ra=ra,
                rb=rb,
                mx1=float(np.interp(Pu, *curves[0])),
                my1=float(np.interp(Pu, *curves[1])),
                an=an,
                r=max(ra, rb),
            )
        rows.append(row)
    return As, Puz, Pbx, Pby, rows


def column_sheet(project: Project, fa: FrameAnalysis, cd: ColumnDesign) -> Sheet:
    """Calculation sheet for one column, recomputed exactly as ``design_all`` does."""
    m = fa.model
    mid = cd.member_id
    ref = f"{cd.mark} (member {mid})"
    mem = m.members.get(mid)
    if mem is None or mem.kind != "column":
        return _missing("Column", ref, cd.level, f"member {mid} is not a column of the analysed model")
    ds = project.design
    fy = ds.fy_main
    fck = grade_fck(mem.grade)
    dem = _column_demands(fa, mid)
    if not dem:
        return _missing("Column", ref, cd.level, "no ultimate load combinations in the analysis")
    Pmax = Mx_ = My_ = 0.0
    pmax_dem = None
    for name, end, Pu, dx, dy in dem:
        if Pu > Pmax:
            Pmax, Mx_, My_, pmax_dem = Pu, abs(dx), abs(dy), (name, end)
    top = mem.n2
    beams_top = [bm for bm in m.members.values() if bm.kind == "beam" and top in (bm.n1, bm.n2)]
    dmax = max((bm.d for bm in beams_top), default=0.0)
    H = abs(m.nodes[mem.n2].z - m.nodes[mem.n1].z)
    Lu = max(H - dmax, 0.5)
    pmin, pmaxs, keff = ds.min_column_steel_pct, ds.max_column_steel_pct, ds.effective_length_factor
    chk = is456.design_column(
        [(n_, P_, x_, y_) for n_, _e, P_, x_, y_ in dem],
        mem.b,
        mem.d,
        Lu,
        fck,
        fy,
        ds.column_cover,
        pmin,
        pmaxs,
        keff,
    )
    b, D, cover = mem.b * 1000, mem.d * 1000, ds.column_cover * 1000
    B = _Builder(
        "Column",
        ref,
        cd.level,
        [
            ("Project", project.name),
            ("Member", f"Column {ref}"),
            ("Level", cd.level),
            ("Section b × D", f"{b:.0f} × {D:.0f} mm"),
            ("Storey height", f"{H:.2f} m"),
            ("Concrete", f"{mem.grade} (fck = {fck:g} MPa)"),
            ("Steel", f"fy = {fy:g} MPa"),
            ("Clear cover", f"{cover:.0f} mm"),
            ("Engineer", project.engineer or "-"),
            ("Date", _today()),
        ],
    )
    B.sheet.notes = list(dict.fromkeys(cd.notes))
    # ------------------------------------------------ forces
    B.group("Design forces (all ultimate combinations, top and bottom of the column)")
    B.step(
        "Demands checked",
        "each ultimate combination at both ends; Mux bends in the D direction, Muy in the b direction",
        f"{len(fa.ultimate)} combinations × 2 ends",
        f"{len(dem)} demands",
        "Table 18; IS 1893-1 cl 6.3.4",
    )
    st = B.step(
        "Maximum axial load",
        "Pu,max with its moments",
        f"{pmax_dem[0]} ({pmax_dem[1]})" if pmax_dem else "no compression",
        f"Pu = {Pmax:.1f} kN, Mux = {Mx_:.1f}, Muy = {My_:.1f} kN·m",
        "Table 18",
        "Pu_max",
        Pmax,
    )
    B.compare("Pu,max", Pmax, cd.Pu, " kN", 1, st)
    B.compare("Mux at Pu,max", Mx_, cd.Mux, " kN·m", 2, st)
    B.compare("Muy at Pu,max", My_, cd.Muy, " kN·m", 2, st)
    # ------------------------------------------------ slenderness
    B.group("Effective length and slenderness")
    st = B.step(
        "Unsupported length",
        "l = H − depth of the deepest beam framing in at the top (≥ 0.5 m)",
        f"{H:.3f} − {dmax:.3f}",
        f"{Lu:.3f} m",
        "cl 25.1.3",
        "l_unsupported",
        Lu,
    )
    if cd.clear_height:
        B.compare("unsupported length", Lu, cd.clear_height, " m", 3, st)
    lex = keff * Lu * 1000
    B.step(
        "Effective length",
        "lex = k l  (k from the project setting)",
        f"{keff:g} × {Lu * 1000:.0f}",
        f"{lex:.0f} mm",
        "cl 25.2; Table 28; Annex E",
        "lex",
        lex,
    )
    sx, sy = lex / D > 12, lex / b > 12
    B.step(
        "Slenderness ratios",
        "lex/D and lex/b; short column if both ≤ 12",
        f"lex/D = {lex:.0f}/{D:.0f} = {lex / D:.2f}; lex/b = {lex:.0f}/{b:.0f} = {lex / b:.2f}",
        ("slender" if (sx or sy) else "short")
        + (f" (about {'x' if sx else ''}{' and ' if sx and sy else ''}{'y' if sy else ''})" if sx or sy else ""),
        "cl 25.1.2",
        "slenderness",
        max(lex / D, lex / b),
    )
    ex = max(lex / 500 + D / 30, 20)
    ey = max(lex / 500 + b / 30, 20)
    B.group("Minimum eccentricity (one axis at a time)")
    B.step(
        "Minimum eccentricity – x (D direction)",
        "ex,min = l/500 + D/30 ≥ 20 mm  (l taken as lex)",
        f"{lex:.0f}/500 + {D:.0f}/30",
        f"{ex:.1f} mm",
        "cl 25.4",
        "ex_min",
        ex,
    )
    B.step(
        "Minimum eccentricity – y (b direction)",
        "ey,min = l/500 + b/30 ≥ 20 mm  (l taken as lex)",
        f"{lex:.0f}/500 + {b:.0f}/30",
        f"{ey:.1f} mm",
        "cl 25.4",
        "ey_min",
        ey,
    )
    Max0 = (D / 2000 * (lex / D) ** 2) if sx else 0.0
    May0 = (b / 2000 * (lex / b) ** 2) if sy else 0.0
    # ------------------------------------------------ evaluation at the adopted steel
    prepared = [(n_, e_, P_, max(P_, 0.0) * 1e3, abs(x_) * 1e6, abs(y_) * 1e6) for n_, e_, P_, x_, y_ in dem]
    p = chk.steel_pct
    As, Puz, Pbx, Pby, rows = _column_eval(prepared, p, b, D, fck, fy, cover, ex, ey, Max0, May0)
    gi, maxr = 0, 0.0
    for i, r in enumerate(rows):
        if r["r"] > maxr:
            gi, maxr = i, r["r"]
    g = rows[gi]
    Pu = g["Pu"]
    B.group("Governing demand (highest interaction ratio at the adopted steel)")
    st = B.step(
        "Governing combination",
        "demand with the largest interaction ratio",
        f"{g['name']} ({g['end']} of column)",
        f"Pu = {g['Pu_kN']:.1f} kN, Mux = {g['Mx'] / 1e6:.2f}, Muy = {g['My'] / 1e6:.2f} kN·m",
        "cl 39.6",
        "Pu_gov",
        g["Pu_kN"],
    )
    B.compare_text("governing combination", g["name"], cd.governing, st)
    B.compare_text("governing combination (design routine)", chk.governing, cd.governing, st)
    if not g["tension"]:
        B.step(
            "Minimum-eccentricity moments",
            "Mx,min = Pu ex,min;  My,min = Pu ey,min",
            f"{g['Pu_kN']:.1f} × {ex:.1f} / 10³;  {g['Pu_kN']:.1f} × {ey:.1f} / 10³",
            f"{Pu * ex / 1e6:.2f}; {Pu * ey / 1e6:.2f} kN·m",
            "cl 25.4",
        )
        B.group("Additional moments for slenderness")
        B.step(
            "Additional moments",
            "Max = Pu D/2000 (lex/D)²;  May = Pu b/2000 (lex/b)²  (only if slender about that axis)",
            f"{g['Pu_kN']:.1f} × {Max0:.1f} / 10³;  {g['Pu_kN']:.1f} × {May0:.1f} / 10³",
            f"{Max0 * Pu / 1e6:.2f}; {May0 * Pu / 1e6:.2f} kN·m",
            "cl 39.7.1",
        )
    B.group(f"Capacity at p = {p:.1f} % (strain compatibility, steel on four faces)")
    st = B.step(
        "Longitudinal steel area",
        "Asc = p b D / 100",
        f"{p:.1f} × {b:.0f} × {D:.0f} / 100",
        _mm2(As),
        "cl 26.5.3.1",
        "As_req",
        As,
    )
    B.compare("Asc", As, cd.As, " mm²", 0, st)
    B.step(
        "Pure axial capacity",
        "Puz = 0.45 fck (Ag − Asc) + 0.75 fy Asc",
        f"(0.45 × {fck:g} × ({b:.0f} × {D:.0f} − {As:.0f}) + 0.75 × {fy:g} × {As:.0f}) / 10³",
        f"{Puz / 1e3:.1f} kN",
        "cl 39.6",
        "Puz",
        Puz / 1e3,
    )
    if g["tension"]:
        B.step(
            "Uniaxial capacities (tension branch)",
            "Mux1, Muy1 at Pu from the P–M interaction curves",
            f"Pu = {g['Pu_kN']:.1f} kN (net tension)",
            f"Mux1 = {g['mx1'] / 1e6:.2f}, Muy1 = {g['my1'] / 1e6:.2f} kN·m",
            "cl 38.1; 39.1",
        )
        st = B.step(
            "Interaction ratio",
            "max(Mux/Mux1 + Muy/Muy1, Pt/(0.87 fy Asc))  (αn = 1)",
            f"{g['Mx'] / 1e6:.2f}/{g['mx1'] / 1e6:.2f} + {g['My'] / 1e6:.2f}/{g['my1'] / 1e6:.2f}; "
            f"{abs(g['Pu_kN']):.1f}×10³/(0.87 × {fy:g} × {As:.0f}) = {g['rt']:.3f}",
            f"{g['r']:.3f}",
            "cl 39.6",
            "ratio",
            g["r"],
        )
    else:
        B.step(
            "Balanced loads",
            "Pb = P at the peak of each interaction curve",
            "from P–M curves",
            f"Pbx = {Pbx / 1e3:.1f}, Pby = {Pby / 1e3:.1f} kN",
            "cl 39.7.1.1",
        )
        B.step(
            "Reduction factors for additional moments",
            "k = (Puz − Pu) / (Puz − Pb) ≤ 1",
            f"kx = ({Puz / 1e3:.1f} − {g['Pu_kN']:.1f}) / ({Puz / 1e3:.1f} − {Pbx / 1e3:.1f}); "
            f"ky = (… − {Pby / 1e3:.1f})",
            f"kx = {g['kx']:.3f}, ky = {g['ky']:.3f}",
            "cl 39.7.1.1",
        )
        B.step(
            "Design moments – case A (emin about x)",
            "Mux,d = max(Mux, Pu ex,min) + kx Max;  Muy,d = Muy + ky May",
            f"max({g['Mx'] / 1e6:.2f}, {Pu * ex / 1e6:.2f}) + {g['ax'] / 1e6:.2f};  "
            f"{g['My'] / 1e6:.2f} + {g['ay'] / 1e6:.2f}",
            f"{g['mxa'] / 1e6:.2f}; {g['mya'] / 1e6:.2f} kN·m",
            "cl 25.4; 39.7.1",
        )
        B.step(
            "Design moments – case B (emin about y)",
            "Mux,d = Mux + kx Max;  Muy,d = max(Muy, Pu ey,min) + ky May",
            f"{g['Mx'] / 1e6:.2f} + {g['ax'] / 1e6:.2f};  "
            f"max({g['My'] / 1e6:.2f}, {Pu * ey / 1e6:.2f}) + {g['ay'] / 1e6:.2f}",
            f"{g['mxb'] / 1e6:.2f}; {g['myb'] / 1e6:.2f} kN·m",
            "cl 25.4; 39.7.1",
        )
        B.step(
            "Uniaxial moment capacities at Pu",
            "Mux1, Muy1 from the P–M interaction curves (SP-16 assumptions)",
            f"Pu = {g['Pu_kN']:.1f} kN",
            f"Mux1 = {g['mx1'] / 1e6:.2f}, Muy1 = {g['my1'] / 1e6:.2f} kN·m",
            "cl 38.1; 39.1; 39.6",
            "Mux1",
            g["mx1"] / 1e6,
        )
        rr = Pu / Puz if Puz else 0.0
        B.step(
            "Exponent αn",
            "αn = 1.0 for Pu/Puz ≤ 0.2, 2.0 for ≥ 0.8, linear between",
            f"Pu/Puz = {g['Pu_kN']:.1f} / {Puz / 1e3:.1f} = {rr:.3f}",
            f"{g['an']:.3f}",
            "cl 39.6",
            "alpha_n",
            g["an"],
        )

        def _terms(mx, my):
            if Pu >= Puz:
                return "Pu ≥ Puz – section inadequate"
            an = f"{g['an']:.2f}"
            return f"({mx / 1e6:.2f}/{g['mx1'] / 1e6:.2f})^{an} + ({my / 1e6:.2f}/{g['my1'] / 1e6:.2f})^{an}"

        B.step("Interaction – case A", "(Mux,d/Mux1)^αn + (Muy,d/Muy1)^αn", _terms(g["mxa"], g["mya"]),
               f"{g['ra']:.3f}", "cl 39.6", "ratio_a", g["ra"])  # fmt: skip
        B.step("Interaction – case B", "(Mux,d/Mux1)^αn + (Muy,d/Muy1)^αn", _terms(g["mxb"], g["myb"]),
               f"{g['rb']:.3f}", "cl 39.6", "ratio_b", g["rb"])  # fmt: skip
        st = B.step(
            "Interaction ratio",
            "max(case A, case B) ≤ 1.0",
            f"max({g['ra']:.3f}, {g['rb']:.3f})",
            f"{g['r']:.3f} {'≤' if g['r'] <= 1 else '>'} 1.0",
            "cl 39.6",
            "ratio",
            g["r"],
        )
    B.compare("interaction ratio", g["r"], cd.utilisation, "", 3, st)
    B.compare("interaction ratio (design routine)", chk.ratio, cd.utilisation, "", 3, st)
    # ------------------------------------------------ steel
    B.group("Longitudinal steel and ties")
    if p > pmin + 1e-9 and chk.ok:
        pl = round(p - 0.1, 2)
        _a, _pz, _bx, _by, rows_l = _column_eval(prepared, pl, b, D, fck, fy, cover, ex, ey, Max0, May0)
        rl = max(r["r"] for r in rows_l)
        B.step(
            "Minimum steel that works",
            "lowest p in 0.1 % steps (from p,min) with all ratios ≤ 1.0",
            f"at p = {pl:.1f} %: max ratio = {rl:.3f} > 1.0",
            f"p = {p:.1f} %",
            "cl 39.6",
        )
    st = B.step(
        "Steel percentage adopted",
        "p,min ≤ p ≤ p,max",
        f"{pmin:g} ≤ {p:.1f} ≤ {pmaxs:g}",
        f"{p:.2f} %" + ("" if chk.ok else " – FAILS at p,max"),
        "cl 26.5.3.1(a), (b)",
        "steel_pct",
        p,
    )
    B.compare("steel %", p, cd.steel_pct, " %", 2, st)
    nb, dia, prov = is456.column_bars(As, b, D)
    perim = 2 * (b + D - 4 * 50)
    st = B.step(
        "Longitudinal bars",
        "least excess; spacing along the perimeter ≤ 300 mm; ≥ 4 bars",
        f"perimeter 2(b + D − 200) = {perim:.0f} mm; spacing = {perim / nb:.0f} mm; "
        f"pprov = {100 * prov / (b * D):.2f} %",
        f"{nb}-T{dia} = {prov:.0f} mm²",
        "cl 26.5.3.1(a), (c), (g)",
        "As_prov",
        prov,
    )
    B.compare_text("bars", f"{nb}-T{dia} ({prov:.0f} mm²)", cd.bars, st)
    tdia = max(8, math.ceil(dia / 4 / 2) * 2)
    tsp = min(b, D, 16 * dia, 300)
    tprov = int(tsp // 25 * 25)
    st = B.step(
        "Lateral ties",
        "φt ≥ max(φ/4, 6 mm) (8 mm used); pitch ≤ min(b, D, 16 φ, 300)",
        f"φ/4 = {dia / 4:.1f} mm; min({b:.0f}, {D:.0f}, {16 * dia}, 300) = {tsp:.0f} mm",
        f"T{tdia} @ {tprov} c/c",
        "cl 26.5.3.2(c)",
        "tie_spacing",
        tprov,
    )
    ties_txt = f"T{tdia} @ {tprov} c/c"
    B.compare_text("ties (design routine)", ties_txt, chk.ties, st)
    tie_out, tie_dia_out = float(tprov), tdia
    ductile = is13920.required(project) and project.seismic.enabled
    if ductile and chk.main_bars is not None:
        B.group("Ductile detailing – special confining hoops (IS 13920)")
        main = chk.main_bars
        fyh = min(ds.fy_shear, 415.0)
        l0 = is13920.confining_length(max(b, D), Lu * 1000)
        st = B.step(
            "Confining length at each end",
            "l0 = max(larger column dimension, clear height / 6, 450 mm)",
            f"max({max(b, D):.0f}, {Lu * 1000:.0f}/6 = {Lu * 1000 / 6:.0f}, 450)",
            f"{l0:.0f} mm",
            "IS 13920 cl 8.1",
            "l0",
            l0,
        )
        B.compare("l0", l0 / 1000, cd.l0, " m", 3, st)
        conf = is13920.column_confinement(b, D, cover, fck, fyh, main.dia, main.count, chk.tie.dia if chk.tie else 8)
        Bk, Dk = b - 2 * cover, D - 2 * cover
        Ag, Ak = b * D, max(Bk * Dk, 1.0)
        B.step(
            "Hoop arrangement",
            "legs ≤ 300 mm apart, every cross-tie engaging a bar; h = larger panel between parallel legs",
            f"core {Bk:.0f} × {Dk:.0f} mm; {conf.legs_b} legs across b, {conf.legs_d} legs across D",
            f"T{conf.dia}, h = {conf.h:.0f} mm",
            "IS 13920 cl 8.1",
            "h",
            conf.h,
        )
        per = conf.h * fck / fyh * max(0.18 * (Ag / Ak - 1.0), 0.05)
        B.step(
            "Spacing from the hoop area",
            "Ash ≥ max(0.18 s h fck/fy (Ag/Ak − 1), 0.05 s h fck/fy)"
            " → s ≤ Ash / [h fck/fy max(0.18 (Ag/Ak − 1), 0.05)]",
            f"Ag/Ak = {Ag:.0f}/{Ak:.0f} = {Ag / Ak:.3f};  Ash = π × {conf.dia}²/4 = {conf.ash:.1f} mm²;  "
            f"{conf.ash:.1f} / {per:.4f}",
            f"{conf.s_ash:.0f} mm",
            "IS 13920 cl 8.2",
            "s_ash",
            conf.s_ash,
        )
        B.step(
            "Spacing limit within l0",
            "s ≤ min(least dimension/4, 6 φmin, 100 mm)",
            f"min({min(b, D):.0f}/4, 6 × {main.dia}, 100)",
            f"{conf.s_limit:.0f} mm",
            "IS 13920 cl 8.2",
            "s_limit",
            conf.s_limit,
        )
        st = B.step(
            "Special confining hoops",
            "s = min(s,Ash, s,limit) rounded down to 5 mm, ≥ 75 mm",
            f"min({conf.s_ash:.0f}, {conf.s_limit:.0f}) → {conf.s:.0f} mm; Ash,req = {conf.ash_req:.1f} mm²",
            f"T{conf.dia} ({conf.legs_b}×{conf.legs_d} legs) @ {int(conf.s)}" + ("" if conf.ok else " – NOT OK"),
            "IS 13920 cl 8.1; 8.2",
            "s_confined",
            conf.s,
        )
        if cd.tie_confined is not None:
            B.compare("confining hoop spacing", conf.s, cd.tie_confined.spacing, " mm", 0, st)
        if chk.tie is not None:
            s_out = min(chk.tie.spacing, min(b, D) / 2, 300.0)
            tie_out = float(math.floor(s_out / 25) * 25)
            tie_dia_out = max(chk.tie.dia, conf.dia)
            st = B.step(
                "Ties outside l0",
                "s ≤ min(IS 456 pitch, b/2, 300 mm), rounded down to 25 mm; φ ≥ hoop φ",
                f"min({chk.tie.spacing:.0f}, {min(b, D):.0f}/2, 300) = {s_out:.0f} mm",
                f"T{tie_dia_out} @ {int(tie_out)} c/c",
                "IS 13920 cl 7.6.1",
                "tie_spacing_out",
                tie_out,
            )
            ties_txt = (
                f"T{conf.dia} ({conf.legs_b}×{conf.legs_d} legs) @ {int(conf.s)} over l0 = {l0:.0f} "
                f"/ T{tie_dia_out} @ {int(tie_out)} c/c"
            )
    B.compare_text("ties", ties_txt, cd.ties, st)
    if cd.tie is not None:
        B.compare("tie spacing", tie_out, cd.tie.spacing, " mm", 0, st)
    # ------------------------------------------------ checks
    B.check("Biaxial interaction", g["r"] <= 1.0, f"ratio {g['r']:.3f} ({g['name']}, {g['end']})")
    B.check(
        "Steel percentage",
        chk.ok and pmin - 1e-9 <= p <= pmaxs + 1e-9,
        f"p = {p:.2f} % ({nb}-T{dia})" + ("" if chk.ok else f" – more than p,max = {pmaxs:g} % needed"),
    )
    if B.sheet.ok != cd.ok:
        B._warn(f"Overall result {'PASS' if B.sheet.ok else 'FAIL'} differs from the design report", None)
    return B.sheet


def column_steps(project: Project, fa: FrameAnalysis, cd: ColumnDesign) -> list[Step]:
    return column_sheet(project, fa, cd).steps


# ============================================================================= footings
def _footing_inputs(fa: FrameAnalysis, fd: FootingDesign):
    """Base column and reactions exactly as ``design_all`` collects them."""
    m = fa.model
    cand = [
        mem
        for mem in m.members.values()
        if mem.kind in ("column", "wall") and m.nodes[mem.n1].support and mem.mark == fd.mark
    ]
    cand.sort(key=lambda mem: math.dist((m.nodes[mem.n1].x, m.nodes[mem.n1].y), (fd.x, fd.y)))
    if not cand:
        return None
    mem = cand[0]
    nid = mem.n1
    serv = next((c for c in fa.combos if c.kind == "service" and set(c.factors) == {"DL", "LL"}), None)
    if serv is None:
        return None
    lat_serv = [c for c in fa.combos if c.kind == "service" and c is not serv]
    swap = abs(math.sin(math.radians(mem.angle))) > 0.7
    P = float(fa.reaction(nid, serv.factors)[2])

    def coll(combos):
        out = []
        for c in combos:
            r = fa.reaction(nid, c.factors)
            mx, my = abs(float(r[3])), abs(float(r[4]))
            out.append((c.name, float(r[2]), my if swap else mx, mx if swap else my))
        return out

    return mem, serv, P, coll(lat_serv), coll(fa.ultimate), swap


def _footing_state(D, L, B, cb, cd, qu, fck, fy, cover):
    """One pass of the depth loop of ``is456.design_footing``."""
    d = D - cover - 0.012
    Mu_L = qu * B * ((L - cd) / 2) ** 2 / 2
    Mu_B = qu * L * ((B - cb) / 2) ** 2 / 2
    amin = 0.0012 * 1000 * D * 1000
    aL = is456.ast_singly(Mu_L * 1e6 / B, fck, fy, 1000, d * 1000)
    aB = is456.ast_singly(Mu_B * 1e6 / L, fck, fy, 1000, d * 1000)
    astL, astB = max(aL, amin), max(aB, amin)
    flex_ok = math.isfinite(astL) and math.isfinite(astB)
    bo = 2 * ((cb + d) + (cd + d))
    Vp = qu * (L * B - (cb + d) * (cd + d))
    beta = min(cb, cd) / max(cb, cd)
    ks = min(0.5 + beta, 1.0)
    tp = ks * 0.25 * math.sqrt(fck)
    tvp = Vp * 1e3 / (bo * 1e3 * d * 1e3)
    V1 = qu * B * max((L - cd) / 2 - d, 0)
    V2 = qu * L * max((B - cb) / 2 - d, 0)
    ptL = 100 * astL / (1000 * d * 1000) if flex_ok else 0.15
    ptB = 100 * astB / (1000 * d * 1000) if flex_ok else 0.15
    tv1, tv2 = V1 / (B * d) / 1e3, V2 / (L * d) / 1e3
    tc1, tc2 = is456.tau_c(ptL, fck), is456.tau_c(ptB, fck)
    return {
        "d": d,
        "Mu_L": Mu_L,
        "Mu_B": Mu_B,
        "amin": amin,
        "aL": aL,
        "aB": aB,
        "astL": astL,
        "astB": astB,
        "flex_ok": flex_ok,
        "bo": bo,
        "Vp": Vp,
        "beta": beta,
        "ks": ks,
        "tp": tp,
        "tvp": tvp,
        "punch_ok": tvp <= tp,
        "V1": V1,
        "V2": V2,
        "ptL": ptL,
        "ptB": ptB,
        "tv1": tv1,
        "tv2": tv2,
        "tc1": tc1,
        "tc2": tc2,
        "one_ok": tv1 <= tc1 and tv2 <= tc2,
    }


def footing_sheet(project: Project, fa: FrameAnalysis, fd: FootingDesign) -> Sheet:
    """Calculation sheet for one isolated footing, recomputed exactly as ``design_all`` does."""
    ref = f"F-{fd.mark}"
    inp = _footing_inputs(fa, fd)
    if inp is None:
        return _missing("Footing", ref, "Foundation", f"no supported base column '{fd.mark}' in the analysed model")
    mem, serv, P, lat, ult, swap = inp
    ds = project.design
    fy = ds.fy_main
    fck = grade_fck(mem.grade)
    sbc, swp, cov = ds.sbc, ds.footing_self_weight_pct, ds.footing_cover
    cb, cdd = mem.b, mem.d
    fr = is456.design_footing(P, cb, cdd, sbc, fck, fy, cov, swp, [x[1:] for x in lat], ultimate=[x[1:] for x in ult])
    B = _Builder(
        "Footing",
        ref,
        "Foundation",
        [
            ("Project", project.name),
            ("Member", f"Isolated footing under column {fd.mark}"),
            ("Column b × D", f"{cb * 1000:.0f} × {cdd * 1000:.0f} mm" + (" (rotated 90°)" if swap else "")),
            ("Footing L × B × D", f"{fr.L:.2f} × {fr.B:.2f} × {fr.D:.2f} m (L along column D)"),
            ("Concrete", f"{mem.grade} (fck = {fck:g} MPa)"),
            ("Steel", f"fy = {fy:g} MPa"),
            ("Safe bearing capacity", f"{sbc:g} kN/m²"),
            ("Clear cover", f"{cov * 1000:.0f} mm"),
            ("Engineer", project.engineer or "-"),
            ("Date", _today()),
        ],
    )
    B.sheet.notes = list(dict.fromkeys(fd.notes))
    for what, a, b_, nd in (("L", fr.L, fd.L, 3), ("B", fr.B, fd.B, 3), ("D", fr.D, fd.D, 3)):
        if not _close(a, b_):
            B.compare(f"footing {what}", a, b_, " m", nd)
    sw = 1 + swp / 100
    # ------------------------------------------------ sizing
    B.group("Service loads and plan size")
    st = B.step(
        "Service axial load",
        "P = vertical reaction for DL + LL (service)",
        serv.name,
        f"{P:.1f} kN",
        "IS 875 (Parts 1, 2)",
        "P_service",
        P,
    )
    B.compare("service load", P, fd.P_service, " kN", 1, st)
    A = P * sw / sbc
    B.step(
        "Area required (with self weight)",
        "A = P (1 + w%) / SBC",
        f"{P:.1f} × (1 + {swp:g}/100) / {sbc:g}",
        f"{A:.3f} m²",
        "cl 34.1; IS 1904",
        "A_req",
        A,
    )
    bq = cb + cdd
    a = (-bq + math.sqrt(bq * bq - 4 * (cb * cdd - A))) / 4
    L0 = math.ceil(max(cdd + 2 * a, cdd + 0.3) / 0.05) * 0.05
    B0 = math.ceil(max(cb + 2 * a, cb + 0.3) / 0.05) * 0.05
    B.step(
        "Equal-overhang plan size",
        "(D + 2a)(b + 2a) = A → a = [−(b + D) + √((b + D)² − 4(bD − A))]/4; round up to 50 mm",
        f"a = {a:.3f} m; L = {cdd:.2f} + 2a = {cdd + 2 * a:.3f}; B = {cb:.2f} + 2a = {cb + 2 * a:.3f}",
        f"{L0:.2f} × {B0:.2f} m",
        "cl 34.1",
    )
    if not (_close(L0, fr.L) and _close(B0, fr.B)):
        B.step(
            "Enlarged for lateral cases / full contact",
            "L, B increased in 100 mm steps until every service case passes",
            f"from {L0:.2f} × {B0:.2f} m",
            f"{fr.L:.2f} × {fr.B:.2f} m",
            "IS 1893-1 cl 6.3.5.2",
        )
    L, Bw = fr.L, fr.B
    q = P * sw / (L * Bw)
    st = B.step(
        "Gross pressure – gravity (DL + LL)",
        "q = P (1 + w%) / (L B) ≤ SBC",
        f"{P:.1f} × {sw:.2f} / ({L:.2f} × {Bw:.2f})",
        f"{q:.1f} {'≤' if q <= sbc * 1.0001 else '>'} {sbc:g} kN/m²",
        "cl 34.1; IS 1904",
        "q_service",
        q,
    )
    B.compare("service pressure", q, fd.q, " kN/m²", 1, st)
    gq_ok = q <= sbc * 1.0001
    lat_ok, contact_ok = True, True
    if lat:

        def press(Pp, Mx, My):
            qq = max(Pp, 0.0) * sw / (L * Bw)
            dq = 6 * Mx / (Bw * L * L) + 6 * My / (L * Bw * Bw)
            return qq + dq, qq - dq

        pr = [(name, Pp, Mx, My, *press(Pp, Mx, My)) for name, Pp, Mx, My in lat]
        gmax = max(pr, key=lambda t: t[4])
        gmin = min(pr, key=lambda t: t[5])
        lat_ok = gmax[4] <= 1.25 * sbc * 1.0001
        contact_ok = gmin[5] >= -1e-6
        B.step(
            "Max pressure – service with lateral load",
            "qmax = P(1 + w%)/(LB) + 6Mx/(BL²) + 6My/(LB²) ≤ 1.25 SBC",
            f"{gmax[0]}: {max(gmax[1], 0):.1f}×{sw:.2f}/({L:.2f}×{Bw:.2f}) + 6×{gmax[2]:.1f}/({Bw:.2f}×{L:.2f}²) "
            f"+ 6×{gmax[3]:.1f}/({L:.2f}×{Bw:.2f}²)",
            f"{gmax[4]:.1f} {'≤' if lat_ok else '>'} {1.25 * sbc:.0f} kN/m²",
            "IS 1893-1 cl 6.3.5.2",
            "q_max_lateral",
            gmax[4],
        )
        B.step(
            "Min pressure – full contact",
            "qmin = P(1 + w%)/(LB) − 6Mx/(BL²) − 6My/(LB²) ≥ 0",
            f"{gmin[0]}",
            f"{gmin[5]:.1f} kN/m² {'≥' if contact_ok else '<'} 0",
            "IS 1904 (no tension)",
            "q_min_lateral",
            gmin[5],
        )
    # ------------------------------------------------ factored pressure
    B.group("Factored net upward pressure (footing self weight excluded)")
    if ult:
        qus = [(name, max(Pp, 0.0) / (L * Bw) + 6 * Mx / (Bw * L * L) + 6 * My / (L * Bw * Bw), Pp, Mx, My)
               for name, Pp, Mx, My in ult]  # fmt: skip
        gq = max(qus, key=lambda t: t[1])
        qu = gq[1]
        B.step(
            "Governing factored pressure",
            "qu = Pu/(LB) + 6Mux/(BL²) + 6Muy/(LB²), max over ultimate combinations",
            f"{gq[0]}: {max(gq[2], 0):.1f}/({L:.2f}×{Bw:.2f}) + 6×{gq[3]:.1f}/({Bw:.2f}×{L:.2f}²) "
            f"+ 6×{gq[4]:.1f}/({L:.2f}×{Bw:.2f}²)",
            f"{qu:.1f} kN/m²",
            "Table 18; cl 34.2.3.1",
            "qu",
            qu,
        )
    else:  # no ultimate combinations: is456 scales the service cases
        cases = [(P, 0.0, 0.0, 1.5)] + [(Pp, Mx, My, 1.2) for _n, Pp, Mx, My in lat]
        qu = max(max(f * Pp, 0.0) / (L * Bw) + 6 * f * Mx / (Bw * L * L) + 6 * f * My / (L * Bw * Bw)
                 for Pp, Mx, My, f in cases)  # fmt: skip
        B.step("Governing factored pressure", "service cases × 1.5 (gravity) / 1.2 (lateral)", "", f"{qu:.1f} kN/m²",
               "Table 18", "qu", qu)  # fmt: skip
    # ------------------------------------------------ depth / flexure / shear
    Dt = fr.D
    s = _footing_state(Dt, L, Bw, cb, cdd, qu, fck, fy, cov)
    d = s["d"]
    B.group(f"Depth D = {Dt * 1000:.0f} mm")
    B.step(
        "Effective depth",
        "d = D − c − φ (≈ 12 mm, mean of both layers)",
        f"{Dt * 1000:.0f} − {cov * 1000:.0f} − 12",
        f"{d * 1000:.0f} mm",
        "cl 26.4.2.2",
        "d",
        d * 1000,
    )
    B.group("Flexure at the column face")
    for dirn, Mu, span_w, other, cdim, a_, ast, mesh, rep_ast, rep_bars, k_ in (
        ("L", s["Mu_L"], Bw, L, cdd, s["aL"], s["astL"], fr.mesh_L, fd.ast_L, fd.bars_L, "L"),
        ("B", s["Mu_B"], L, Bw, cb, s["aB"], s["astB"], fr.mesh_B, fd.ast_B, fd.bars_B, "B"),
    ):
        B.step(
            f"Moment – bars along {dirn}",
            "Mu = qu × width × ((span − c)/2)² / 2",
            f"{qu:.1f} × {span_w:.2f} × (({other:.2f} − {cdim:.2f})/2)² / 2",
            f"{Mu:.2f} kN·m",
            "cl 34.2.3.1; 34.2.3.2",
            f"Mu_{k_}",
            Mu,
        )
        B.step(
            f"Steel per metre – along {dirn}",
            "Ast = 0.5 fck/fy [1 − √(1 − 4.6 Mu/(fck b d²))] b d,  b = 1000 mm",
            f"Mu = {Mu:.2f}/{span_w:.2f} = {Mu / span_w:.2f} kN·m/m; d = {d * 1000:.0f} mm",
            _mm2(a_) + "/m",
            "Annex G-1.1(b)",
            f"ast_calc_{k_}",
            a_,
        )
        st = B.step(
            f"Steel required – along {dirn}",
            "Ast,req = max(Ast, 0.12 % b D)",
            f"max({a_:.0f}, 0.0012 × 1000 × {Dt * 1000:.0f})",
            _mm2(ast) + "/m",
            "cl 26.5.2.1; 34.3",
            f"ast_{k_}",
            ast,
        )
        B.compare(f"Ast along {dirn}", ast, rep_ast, " mm²/m", 0, st)
        st = B.step(
            f"Bars along {dirn}",
            "smallest bar giving spacing ≥ 100 mm; spacing ≤ 300 mm (rounded down to 10 mm)",
            f"{mesh.area_per_m:.0f} mm²/m provided" if mesh else "–",
            str(mesh) if mesh else "–",
            "cl 26.3.3(b)(1)",
            f"spacing_{k_}",
            mesh.spacing if mesh else None,
        )
        B.compare_text(f"bars along {dirn}", str(mesh) if mesh else "-", rep_bars, st)
    B.group("Shear")
    B.step(
        "Punching shear – perimeter at d/2",
        "bo = 2[(b + d) + (D + d)];  Vp = qu [LB − (b + d)(D + d)]",
        f"bo = 2[({cb:.2f} + {d:.3f}) + ({cdd:.2f} + {d:.3f})] = {s['bo']:.3f} m; "
        f"Vp = {qu:.1f} × [{L:.2f}×{Bw:.2f} − {cb + d:.3f}×{cdd + d:.3f}]",
        f"Vp = {s['Vp']:.1f} kN",
        "cl 31.6.1; 34.2.4.1(b)",
        "Vp",
        s["Vp"],
    )
    B.step(
        "Punching shear stress check",
        "τv = Vp/(bo d) ≤ ks 0.25 √fck,  ks = 0.5 + βc ≤ 1",
        f"{s['Vp']:.1f}×10³/({s['bo'] * 1000:.0f} × {d * 1000:.0f}) = {s['tvp']:.3f};  "
        f"βc = {s['beta']:.3f}, ks = {s['ks']:.3f}, ks 0.25√{fck:g} = {s['tp']:.3f}",
        f"{s['tvp']:.3f} {'≤' if s['punch_ok'] else '>'} {s['tp']:.3f} MPa",
        "cl 31.6.3.1",
        "tau_punch",
        s["tvp"],
    )
    for dirn, V, wdt, tv, pt, tc, k_ in (
        ("L", s["V1"], Bw, s["tv1"], s["ptL"], s["tc1"], "L"),
        ("B", s["V2"], L, s["tv2"], s["ptB"], s["tc2"], "B"),
    ):
        other, cdim = (L, cdd) if dirn == "L" else (Bw, cb)
        B.step(
            f"One-way shear at d from face – along {dirn}",
            "V = qu × width × ((span − c)/2 − d);  τv = V/(width d) ≤ τc(pt)",
            f"V = {qu:.1f} × {wdt:.2f} × ({(other - cdim) / 2:.3f} − {d:.3f}) = {V:.1f} kN; pt = {pt:.3f} %",
            f"{tv:.3f} {'≤' if tv <= tc else '>'} {tc:.3f} MPa",
            "cl 34.2.4.1(a); Table 19",
            f"tau_one_way_{k_}",
            tv,
        )
    ok_now = s["flex_ok"] and s["punch_ok"] and s["one_ok"]
    if Dt > 0.3 + 1e-9:
        s0 = _footing_state(round(Dt - 0.05, 3), L, Bw, cb, cdd, qu, fck, fy, cov)
        fails = [n for n, k in (("flexure", "flex_ok"), ("punching", "punch_ok"), ("one-way shear", "one_ok"))
                 if not s0[k]]  # fmt: skip
        B.step(
            "Depth adopted",
            "smallest D in 50 mm steps from 300 mm satisfying flexure, punching and one-way shear",
            f"at D = {(Dt - 0.05) * 1000:.0f} mm: {', '.join(fails) or 'all pass'} fail(s)",
            f"D = {Dt * 1000:.0f} mm",
            "cl 34.1.2; 34.2.4",
            "D",
            Dt * 1000,
        )
    else:
        B.step(
            "Depth adopted",
            "minimum D = 300 mm (edge ≥ 150 mm) satisfies all checks",
            "",
            f"D = {Dt * 1000:.0f} mm",
            "cl 34.1.2",
            "D",
            Dt * 1000,
        )
    B.check("Bearing pressure (gravity)", gq_ok, f"q = {q:.1f} ≤ {sbc:g} kN/m²")
    if lat:
        B.check("Bearing pressure (lateral)", lat_ok, f"qmax ≤ {1.25 * sbc:.0f} kN/m²")
        B.check("Full contact", contact_ok, "qmin ≥ 0")
    B.check(
        "Flexure", s["flex_ok"], f"T{fr.mesh_L.dia if fr.mesh_L else '-'} / T{fr.mesh_B.dia if fr.mesh_B else '-'} mesh"
    )
    B.check("Punching shear", s["punch_ok"], f"τv = {s['tvp']:.3f} ≤ {s['tp']:.3f} MPa")
    B.check("One-way shear", s["one_ok"], f"τv = {max(s['tv1'], s['tv2']):.3f} MPa")
    if not fr.ok:
        B.check("Design routine", False, "; ".join(fr.notes) or "footing could not be designed")
    if not ok_now and fr.ok:
        B._warn("Recomputed checks at the adopted depth do not all pass", None)
    if B.sheet.ok != fd.ok:
        B._warn(f"Overall result {'PASS' if B.sheet.ok else 'FAIL'} differs from the design report", None)
    return B.sheet


def footing_steps(project: Project, fa: FrameAnalysis, fd: FootingDesign) -> list[Step]:
    return footing_sheet(project, fa, fd).steps


# ============================================================================= slabs
def _slab_inputs(project: Project) -> list[dict]:
    """Slab design inputs in the same order (and with the same rules) as ``design_all``."""
    ds = project.design
    out = []
    done = set()
    for lv in project.levels:
        plan = project.plan(lv.plan)
        if plan is None or plan.name in done:
            continue
        done.add(plan.name)
        for s in plan.slabs:
            if s.distribution == "on_grade" or len(s.points) < 3:
                continue
            pts = s.pts
            edges = s.edges()
            lens = [G.dist(a, b) for a, b in edges]
            rect = G.is_rectangle(pts)
            if rect:
                lx, ly = sorted(lens[:2])
            else:
                x0, y0, x1, y1 = G.bbox(pts)
                lx, ly = sorted((x1 - x0, y1 - y0))
            cont = 0
            for a, b in edges:
                for o in plan.slabs:
                    if o is s:
                        continue
                    if any(G.collinear_overlap(a, b, c, d) for c, d in o.edges()):
                        cont += 1
                        break
            kind = (
                "cantilever"
                if s.distribution == "cantilever"
                else (
                    "one_way"
                    if s.distribution in ("one_way", "one_way_long") or ly / max(lx, 1e-6) > ds.two_way_ratio_limit
                    else "two_way"
                )
            )
            out.append(
                {
                    "plan": plan.name,
                    "level": lv.name,
                    "grade": lv.grade,
                    "slab": s,
                    "lx": lx,
                    "ly": ly,
                    "rect": rect,
                    "cont": cont,
                    "kind": kind,
                    "edges": len(edges),
                }
            )
    return out


def slab_sheet(project: Project, inp: dict, res: is456.SlabResult) -> Sheet:
    """Calculation sheet for one slab panel (``inp`` from the runner-equivalent input scan)."""
    ds = project.design
    s = inp["slab"]
    fy = ds.fy_main
    fck = grade_fck(inp["grade"])
    lx, ly, cont, kind_in = inp["lx"], inp["ly"], inp["cont"], inp["kind"]
    w_d, w_l = s.dead, s.live_load
    Dm = s.thickness
    cov = ds.slab_cover
    design = is456.design_slab(s.mark, lx, ly, w_d, w_l, Dm, fck, fy, kind_in, cont, cov)
    ref = f"{s.mark} ({inp['plan']})"
    B = _Builder(
        "Slab",
        ref,
        inp["plan"],
        [
            ("Project", project.name),
            ("Member", f"Slab {s.mark}" + (f" – {s.room}" if s.room else "")),
            ("Plan / level", f"{inp['plan']} (first used at {inp['level']})"),
            ("Thickness D", f"{Dm * 1000:.0f} mm"),
            ("Spans lx × ly", f"{lx:.2f} × {ly:.2f} m"),
            ("Concrete", f"{inp['grade']} (fck = {fck:g} MPa)"),
            ("Steel", f"fy = {fy:g} MPa"),
            ("Clear cover", f"{cov * 1000:.0f} mm"),
            ("Engineer", project.engineer or "-"),
            ("Date", _today()),
        ],
    )
    B.sheet.notes = list(dict.fromkeys(res.notes))
    B.group("Geometry and type")
    st = B.step(
        "Effective spans",
        "lx = shorter, ly = longer side" + ("" if inp["rect"] else " of the bounding box"),
        f"{inp['edges']} edges",
        f"lx = {lx:.3f} m, ly = {ly:.3f} m",
        "cl 22.2; Annex D",
        "lx",
        lx,
    )
    B.compare("lx", lx, res.lx, " m", 3, st)
    B.compare("ly", ly, res.ly, " m", 3, st)
    r = ly / lx if lx else 1.0
    B.step("Aspect ratio", "ly / lx", f"{ly:.3f} / {lx:.3f}", f"{r:.3f}", "cl 24.4; Annex D", "ratio", r)
    B.step(
        "Continuous edges",
        "edges shared with an adjacent slab",
        f"{inp['edges']} edges checked",
        f"{cont}",
        "Annex D-1",
        "cont",
        cont,
    )
    if kind_in == "cantilever":
        kind, why = "cantilever", "slab defined as cantilever"
    elif kind_in == "two_way" and r <= 2.0:
        kind, why = "two_way", f"ly/lx = {r:.2f} ≤ {ds.two_way_ratio_limit:g}"
    else:
        kind = "one_way"
        why = (
            f"distribution set to {s.distribution}"
            if s.distribution in ("one_way", "one_way_long")
            else f"ly/lx = {r:.2f} > {min(ds.two_way_ratio_limit, 2.0):g}"
        )
    st = B.step(
        "Slab type",
        "two-way if ly/lx ≤ limit, otherwise one-way",
        why,
        kind.replace("_", "-"),
        "cl 24.4; Annex D",
    )
    B.compare_text("slab type", kind, res.kind, st)
    B.group("Loads (per m²)")
    B.step(
        "Dead load",
        "wd = D γc + floor finish + other",
        f"{Dm:.3f} × {s.density:g} + {s.floor_finish:g} + {s.other:g}",
        f"{w_d:.2f} kN/m²",
        "IS 875 (Part 1)",
        "w_dead",
        w_d,
    )
    B.step("Live load", "wl (occupancy)", "", f"{w_l:.2f} kN/m²", "IS 875 (Part 2)", "w_live", w_l)
    wu = 1.5 * (w_d + w_l)
    B.step(
        "Factored load",
        "wu = 1.5 (wd + wl)",
        f"1.5 × ({w_d:.2f} + {w_l:.2f})",
        f"{wu:.2f} kN/m²",
        "Table 18",
        "wu",
        wu,
    )
    B.group("Design moments (per metre width)")
    if kind == "cantilever":
        Mx, My, Mn = wu * lx**2 / 2, 0.0, wu * lx**2 / 2
        basic = 7
        B.step("Cantilever moment", "M = wu l² / 2", f"{wu:.2f} × {lx:.3f}² / 2", f"{Mx:.2f} kN·m/m",
               "cl 22.2(c)", "Mx", Mx)  # fmt: skip
    elif kind == "two_way":
        ax = float(np.interp(r, _slab._T27_R, _slab._T27_AX))
        ay = float(np.interp(r, _slab._T27_R, _slab._T27_AY))
        Mx, My = ax * wu * lx**2, ay * wu * lx**2
        B.step(
            "Moment coefficients (positive)",
            "αx, αy from Table 27 (simply supported, corners free), interpolated",
            f"ly/lx = {r:.3f}",
            f"αx = {ax:.4f}, αy = {ay:.4f}",
            "Annex D-2; Table 27",
        )
        B.step("Short-span moment", "Mx = αx wu lx²", f"{ax:.4f} × {wu:.2f} × {lx:.3f}²", f"{Mx:.2f} kN·m/m",
               "Annex D-2", "Mx", Mx)  # fmt: skip
        B.step("Long-span moment", "My = αy wu lx²", f"{ay:.4f} × {wu:.2f} × {lx:.3f}²", f"{My:.2f} kN·m/m",
               "Annex D-2", "My", My)  # fmt: skip
        if cont:
            an = float(np.interp(r, _slab._T27_R, _slab._T26_NEG))
            Mn = an * wu * lx**2
            B.step(
                "Support moment (continuous edges)",
                "M− = αx− wu lx²,  αx− from Table 26 (case 6 envelope)",
                f"{an:.4f} × {wu:.2f} × {lx:.3f}²",
                f"{Mn:.2f} kN·m/m",
                "Annex D-1.1; Table 26",
                "Mneg",
                Mn,
            )
        else:
            Mn = 0.0
        basic = 20 if cont == 0 else 26
    else:
        if cont:
            Mx = 1.5 * w_d * lx**2 / 12 + 1.5 * w_l * lx**2 / 10
            Mn = 1.5 * w_d * lx**2 / 10 + 1.5 * w_l * lx**2 / 9
            B.step(
                "Span moment (continuous)",
                "M+ = 1.5 wd l²/12 + 1.5 wl l²/10",
                f"1.5 × {w_d:.2f} × {lx:.3f}²/12 + 1.5 × {w_l:.2f} × {lx:.3f}²/10",
                f"{Mx:.2f} kN·m/m",
                "cl 22.5.1; Table 12",
                "Mx",
                Mx,
            )
            B.step(
                "Support moment (continuous)",
                "M− = 1.5 wd l²/10 + 1.5 wl l²/9",
                f"1.5 × {w_d:.2f} × {lx:.3f}²/10 + 1.5 × {w_l:.2f} × {lx:.3f}²/9",
                f"{Mn:.2f} kN·m/m",
                "cl 22.5.1; Table 12",
                "Mneg",
                Mn,
            )
            basic = 26
        else:
            Mx, Mn = wu * lx**2 / 8, 0.0
            B.step("Span moment (simply supported)", "M = wu l² / 8", f"{wu:.2f} × {lx:.3f}² / 8",
                   f"{Mx:.2f} kN·m/m", "cl 22.2", "Mx", Mx)  # fmt: skip
            basic = 20
        My = 0.0
    for what, a, b_ in (("Mx", Mx, res.Mx_pos), ("My", My, res.My_pos), ("M−", Mn, res.M_neg)):
        B.compare(what, a, b_, " kN·m/m")
    B.group("Reinforcement")
    D = Dm * 1000
    d = D - cov * 1000 - 5
    B.step("Effective depth", "d = D − c − φ/2 (φ = 10 mm)", f"{D:.0f} − {cov * 1000:.0f} − 5", f"{d:.0f} mm",
           "cl 26.4", "d", d)  # fmt: skip
    amin = 0.0012 * 1000 * D
    B.step("Minimum steel", "Ast,min = 0.12 % b D (HYSD)", f"0.0012 × 1000 × {D:.0f}", _mm2(amin) + "/m",
           "cl 26.5.2.1", "ast_min", amin)  # fmt: skip
    s_max = min(3 * d, 300.0)
    B.step("Maximum bar spacing", "s ≤ min(3d, 300 mm)", f"min(3 × {d:.0f}, 300)", f"{s_max:.0f} mm",
           "cl 26.3.3(b)(1)", "s_max", s_max)  # fmt: skip
    ax_ = None
    asts = []
    for label, M, dd, mesh, rep_str, key in (
        ("Short span, bottom", Mx, d, design.mesh_x, res.ast_x, "x"),
        ("Long span, bottom", My, d - 10, design.mesh_y, res.ast_y, "y"),
        ("Continuous supports, top", Mn, d, design.mesh_neg, res.ast_neg, "neg"),
    ):
        if key == "neg" and not M:
            continue
        if M:
            a = is456.ast_singly(M * 1e6, fck, fy, 1000, dd)
            ast = max(a, amin)
            subst = (
                f"0.5 × {fck:g}/{fy:g} × [1 − √(1 − 4.6 × {M:.2f}×10⁶ / ({fck:g} × 1000 × {dd:.0f}²))] "
                f"× 1000 × {dd:.0f} = {a:.0f};  max({a:.0f}, {amin:.0f})"
            )
            formula = "Ast = 0.5 fck/fy [1 − √(1 − 4.6 Mu/(fck b d²))] b d ≥ Ast,min"
        else:
            ast = amin
            subst = "distribution steel"
            formula = "Ast = Ast,min"
        if key == "x":
            ax_ = ast
        asts.append(ast)
        B.step(f"{label} – steel", formula, subst + (f" (d = {dd:.0f} mm)" if key == "y" else ""),
               _mm2(ast) + "/m", "Annex G-1.1(b); cl 26.5.2.1", f"ast_{key}", ast)  # fmt: skip
        st = B.step(
            f"{label} – bars",
            "smallest of T8/T10/T12 giving spacing ≥ 100 mm; rounded down to 10 mm",
            f"{mesh.area_per_m:.0f} mm²/m provided" if mesh else "–",
            str(mesh) if mesh else "–",
            "cl 26.3.3(b)(1)",
            f"spacing_{key}",
            mesh.spacing if mesh else None,
        )
        B.compare_text(f"{label.lower()} bars", str(mesh) if mesh else "-", rep_str, st)
    B.group("Deflection")
    if kind == "two_way" and lx <= 3.5 and w_l <= 3.0:
        lim = (40 if cont else 35) * (0.8 if fy > 250 else 1.0)
        actual = lx * 1000 / D
        dok = actual <= lim
        st = B.step(
            "Span / overall depth (two-way, lx ≤ 3.5 m, LL ≤ 3 kN/m²)",
            "lx/D ≤ 35 (simply supported) or 40 (continuous), × 0.8 for HYSD steel",
            f"{lx * 1000:.0f} / {D:.0f} = {actual:.2f} {'≤' if dok else '>'} {lim:.1f}",
            "OK" if dok else "NOT OK",
            "cl 24.1 note 2",
            "l_over_D",
            actual,
        )
    else:
        fs = 0.58 * fy
        pt = 100 * ax_ / (1000 * d) if ax_ is not None and math.isfinite(ax_) else 1.0
        mf = is456.deflection_mf(pt, fs)
        allowed = basic * mf
        actual = lx * 1000 / d
        dok = actual <= allowed
        B.step(
            "Modification factor",
            "basic L/d (7 / 20 / 26) × MF(pt, fs = 0.58 fy)",
            f"pt = {pt:.3f} %, fs = {fs:.0f} MPa, basic = {basic}",
            f"MF = {mf:.3f}",
            "cl 23.2.1; 24.1; Fig 4",
            "mf",
            mf,
        )
        st = B.step(
            "Span / effective depth",
            "lx/d ≤ basic × MF",
            f"{lx * 1000:.0f} / {d:.0f} = {actual:.2f} {'≤' if dok else '>'} {basic} × {mf:.3f} = {allowed:.2f}",
            "OK" if dok else "NOT OK",
            "cl 23.2.1; 24.1",
            "l_over_d",
            actual,
        )
    if dok != res.deflection_ok:
        B._warn("Recomputed deflection result differs from the design report", st)
    flex_ok = all(math.isfinite(v) for v in asts)
    B.check("Flexure", flex_ok, f"short span {res.ast_x}; long span {res.ast_y}; top {res.ast_neg}")
    B.check("Deflection", dok, f"span/depth {actual:.1f}")
    if B.sheet.ok != res.ok:
        B._warn(f"Overall result {'PASS' if B.sheet.ok else 'FAIL'} differs from the design report", None)
    return B.sheet


def slab_steps(project: Project, inp: dict, res: is456.SlabResult) -> list[Step]:
    return slab_sheet(project, inp, res).steps


# ============================================================================= selection
def build_sheets(
    project: Project,
    fm: FrameModel | None,
    fa: FrameAnalysis,
    rep: DesignReport,
    member_ids: list[int] | None = None,
    footing_marks: list[str] | None = None,
    slabs: list[tuple[str, str]] | None = None,
) -> list[Sheet]:
    """Sheets for the selection (``None`` = all of that type, ``[]`` = none), in the order
    beams, columns, footings, slabs.  Unknown ids / marks raise ``ValueError``."""
    del fm  # the analysis carries its own model; kept for API symmetry with the other writers
    if member_ids is None:
        beams, cols = list(rep.beams), list(rep.columns)
    else:
        ids = {int(i) for i in member_ids}
        known = {b.member_id for b in rep.beams} | {c.member_id for c in rep.columns}
        unknown = sorted(ids - known)
        if unknown:
            raise ValueError(f"no beam or column design for member id(s) {', '.join(map(str, unknown))}")
        beams = [b for b in rep.beams if b.member_id in ids]
        cols = [c for c in rep.columns if c.member_id in ids]
    if footing_marks is None:
        foots = list(rep.footings)
    else:
        want = {str(x) for x in footing_marks}
        unknown = sorted(want - {f.mark for f in rep.footings})
        if unknown:
            raise ValueError(f"no footing design for column mark(s) {', '.join(unknown)}")
        foots = [f for f in rep.footings if f.mark in want]
    if slabs is None:
        slab_sel = list(rep.slabs)
    else:
        want_s = {(str(p), str(mk)) for p, mk in slabs}
        unknown = sorted(want_s - {(p, s.mark) for p, s in rep.slabs})
        if unknown:
            raise ValueError("no slab design for " + ", ".join(f"{mk} ({p})" for p, mk in unknown))
        slab_sel = [(p, s) for p, s in rep.slabs if (p, s.mark) in want_s]

    sheets = [beam_sheet(project, fa, b) for b in beams]
    sheets += [column_sheet(project, fa, c) for c in cols]
    sheets += [footing_sheet(project, fa, f) for f in foots]
    if slab_sel:
        inputs = _slab_inputs(project)
        order = {id(s): i for i, (_p, s) in enumerate(rep.slabs)}
        used: set[int] = set()
        for pn, res in slab_sel:
            i = order[id(res)]
            inp = inputs[i] if i < len(inputs) else None
            if inp is None or inp["plan"] != pn or inp["slab"].mark != res.mark:  # report out of step: match by name
                inp = next(
                    (
                        x
                        for j, x in enumerate(inputs)
                        if j not in used and x["plan"] == pn and x["slab"].mark == res.mark
                    ),
                    None,
                )
            if inp is None:
                sheets.append(_missing("Slab", f"{res.mark} ({pn})", pn, "slab not found in the project"))
                continue
            used.add(inputs.index(inp))
            sheets.append(slab_sheet(project, inp, res))
    return sheets


# ============================================================================= PDF
_NEEDED = "τα√≤≥−φβπ²×·–→"
_FONT_CANDIDATES = [
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    ("arial.ttf", "arialbd.ttf"),
    ("Arial.ttf", "Arial Bold.ttf"),
    ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"),
    ("FreeSans.ttf", "FreeSansBold.ttf"),
]
_FONT_DIRS = [
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/usr/share/fonts/TTF",
    "/usr/share/fonts/truetype/liberation",
    "/usr/share/fonts/truetype/freefont",
    "/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
]
_FONT_CACHE: tuple[str, str, bool] | None = None
_ASCII = {
    "τ": "tau",
    "α": "alpha",
    "β": "beta",
    "φ": "phi",
    "π": "pi",
    "√": "sqrt",
    "≤": "<=",
    "≥": ">=",
    "−": "-",
    "→": "->",
    "∞": "inf",
    "⁶": "^6",
    "³": "^3",
    "…": "...",
}


def _fonts() -> tuple[str, str, bool]:
    """(regular, bold, unicode) – a TTF with Greek/maths glyphs if one is installed, else Helvetica."""
    global _FONT_CACHE
    if _FONT_CACHE is not None:
        return _FONT_CACHE
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for reg, bold in _FONT_CANDIDATES:
        for folder in _FONT_DIRS:
            p1, p2 = os.path.join(folder, reg), os.path.join(folder, bold)
            if not (os.path.isfile(p1) and os.path.isfile(p2)):
                continue
            try:
                f1, f2 = TTFont("PWCalc", p1), TTFont("PWCalc-Bold", p2)
                if not all(ord(ch) in f1.face.charToGlyph for ch in _NEEDED):
                    continue
                pdfmetrics.registerFont(f1)
                pdfmetrics.registerFont(f2)
                for b_, i_, name in ((0, 0, "PWCalc"), (1, 0, "PWCalc-Bold"), (0, 1, "PWCalc"), (1, 1, "PWCalc-Bold")):
                    addMapping("PWCalc", b_, i_, name)
                _FONT_CACHE = ("PWCalc", "PWCalc-Bold", True)
                return _FONT_CACHE
            except Exception:  # unreadable / unsupported font file – try the next one
                continue
    _FONT_CACHE = ("Helvetica", "Helvetica-Bold", False)
    return _FONT_CACHE


def _plain(s: str, unicode_ok: bool) -> str:
    s = str(s)
    if unicode_ok:
        return s
    for k, v in _ASCII.items():
        s = s.replace(k, v)
    return s.encode("cp1252", errors="replace").decode("cp1252")


def write_calc_sheets(
    path: str,
    project: Project,
    fm: FrameModel | None,
    fa: FrameAnalysis,
    rep: DesignReport,
    member_ids: list[int] | None = None,
    footing_marks: list[str] | None = None,
    slabs: list[tuple[str, str]] | None = None,
    watermark: str = "",
) -> str:
    """Write step-by-step calculation sheets (portrait A4 PDF) and return ``path``.

    ``member_ids`` selects beams and columns by frame member id, ``footing_marks`` footings
    by column mark and ``slabs`` slab panels by (plan name, slab mark).  ``None`` means every
    member of that type, an empty list none.
    """
    sheets = build_sheets(project, fm, fa, rep, member_ids, footing_marks, slabs)
    _render(path, project, sheets, watermark)
    return path


def _render(path: str, project: Project, sheets: list[Sheet], watermark: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate,
        Flowable,
        Frame,
        KeepTogether,
        PageBreak,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    font, bold, uni = _fonts()

    def T(s) -> str:  # escaped paragraph text
        return _esc(_plain(s, uni))

    W, H = A4
    margin = 15 * mm
    hdr = colors.HexColor(_HDR)
    grey = colors.HexColor("#555555")
    st_cell = ParagraphStyle("cell", fontName=font, fontSize=7.6, leading=9.4)
    st_small = ParagraphStyle("small", parent=st_cell, fontSize=7, leading=8.6)
    st_head = ParagraphStyle("head", parent=st_cell, fontName=bold, textColor=colors.white)
    st_body = ParagraphStyle("body", fontName=font, fontSize=9, leading=12)
    st_h1 = ParagraphStyle("h1", fontName=bold, fontSize=15, leading=19, textColor=hdr, spaceAfter=6)
    st_sheet = ParagraphStyle("sheet", fontName=bold, fontSize=12.5, leading=16, textColor=hdr, spaceAfter=4)
    st_title = ParagraphStyle("title", fontName=bold, fontSize=24, leading=30, alignment=TA_CENTER, textColor=hdr)
    st_proj = ParagraphStyle("proj", fontName=bold, fontSize=17, leading=22, alignment=TA_CENTER, spaceBefore=8)
    st_center = ParagraphStyle("center", parent=st_body, alignment=TA_CENTER)

    def P(text: str, style=st_cell) -> Paragraph:
        return Paragraph(text, style)

    toc_w, toc_h, toc_size = 16 * mm, 11.0, 8.5

    class _PageRef(Flowable):
        """TOC page number: a PDF form drawn here and defined when the sheet is laid out, so the
        document is built in a single pass (every sheet starts a new page)."""

        def __init__(self, key: str):
            super().__init__()
            self.key = key

        def wrap(self, aw, ah):
            return toc_w, toc_h

        def draw(self):
            self.canv.doForm(f"pg_{self.key}")

    class _Doc(BaseDocTemplate):
        current = ""

        def beforeDocument(self):
            self.current = ""

        def afterFlowable(self, flowable):
            key = getattr(flowable, "_pw_key", None)
            if key:
                self.current = flowable._pw_label
                canv = self.canv
                canv.bookmarkPage(key)
                canv.addOutlineEntry(flowable._pw_label, key, 0)
                canv.saveState()  # the form must not leak its font into the page's text state
                canv.beginForm(f"pg_{key}", 0, 0, toc_w, toc_h)
                canv.setFont(font, toc_size)
                canv.drawRightString(toc_w - 2, 2.5, str(self.page))
                canv.endForm()
                canv.restoreState()

    def page_end(canv, doc):
        canv.saveState()
        canv.setFont(font, 7)
        canv.setFillColor(grey)
        canv.drawString(margin, H - 10 * mm, _plain(f"{project.name} – design calculation sheets", uni)[:110])
        if doc.current:
            canv.drawRightString(W - margin, H - 10 * mm, _plain(doc.current, uni)[:90])
        canv.setStrokeColor(hdr)
        canv.setLineWidth(0.5)
        canv.line(margin, H - 11.5 * mm, W - margin, H - 11.5 * mm)
        canv.drawString(margin, 8 * mm, _plain(f"{APP_NAME} {__version__} – {COMPANY}   {watermark}", uni))
        canv.drawRightString(W - margin, 8 * mm, f"Page {doc.page}")
        canv.restoreState()

    doc = _Doc(
        path,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=16 * mm,
        bottomMargin=14 * mm,
        title=f"{project.name} – calculation sheets",
        author=project.engineer or APP_NAME,
        creator=f"{APP_NAME} {__version__}",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPageEnd=page_end)])

    def kv_table(rows, widths):
        data = [[P(f"<b>{T(a)}</b>"), P(T(b)), P(f"<b>{T(c)}</b>"), P(T(d))] for a, b, c, d in rows]
        t = Table(data, colWidths=widths)
        t.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E8EDF3")),
                    ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#E8EDF3")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        return t

    def pairs(items):
        items = list(items)
        if len(items) % 2:
            items.append(("", ""))
        return [(*items[i], *items[i + 1]) for i in range(0, len(items), 2)]

    # ------------------------------------------------------------------ cover
    ds = project.design
    story = [
        Spacer(1, 22 * mm),
        P(T("Design calculation sheets"), st_title),
        P(T(project.name), st_proj),
        Spacer(1, 3 * mm),
        P(T(f"IS 456:2000 · IS 1893 (Part 1):2016 · IS 875 – {APP_NAME} {__version__}"), st_center),
    ]
    if watermark:
        story.append(P(f"<font color='red'><b>{T(watermark)}</b></font>", st_center))
    story.append(Spacer(1, 10 * mm))
    meta = [
        ("Client", project.client or "-"),
        ("Engineer", project.engineer or "-"),
        ("Location", project.location or "-"),
        ("Date", _today()),
        ("Main steel", f"fy = {ds.fy_main:g} MPa"),
        ("Link steel", f"fy = {ds.fy_shear:g} MPa (≤ 415 used, cl 40.4)"),
        ("Covers", f"beam {ds.beam_cover * 1000:.0f}, column {ds.column_cover * 1000:.0f}, "
                   f"slab {ds.slab_cover * 1000:.0f}, footing {ds.footing_cover * 1000:.0f} mm"),
        ("SBC", f"{ds.sbc:g} kN/m² (+{ds.footing_self_weight_pct:g} % self weight)"),
        ("Column steel", f"{ds.min_column_steel_pct:g} – {ds.max_column_steel_pct:g} %"),
        ("Effective length factor", f"k = {ds.effective_length_factor:g}"),
    ]  # fmt: skip
    cw = (W - 2 * margin) / 2
    story.append(kv_table(pairs(meta), [cw * 0.32, cw * 0.68, cw * 0.32, cw * 0.68]))
    story.append(Spacer(1, 6 * mm))
    story.append(P(T("Sheets in this set"), st_h1))
    kinds = ["Beam", "Column", "Footing", "Slab"]
    rows = [[P(T(h), st_head) for h in ("Type", "Sheets", "PASS", "FAIL", "Members")]]
    for k in kinds:
        ks = [s for s in sheets if s.kind == k]
        if not ks:
            continue
        marks = ", ".join(s.ref.split(" (member")[0] for s in ks)
        if len(marks) > 260:
            marks = marks[:257] + "…"
        rows.append(
            [P(T(k)), P(str(len(ks))), P(str(sum(s.ok for s in ks))), P(str(sum(not s.ok for s in ks))), P(T(marks))]
        )
    if len(rows) == 1:
        rows.append([P(T("No members selected")), "", "", "", ""])
    t = Table(rows, colWidths=[22 * mm, 16 * mm, 14 * mm, 14 * mm, W - 2 * margin - 66 * mm], repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), hdr),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(t)
    story.append(Spacer(1, 4 * mm))
    n_warn = sum(1 for s in sheets if s.warnings)
    story.append(
        P(
            T(
                "Every value on these sheets is recomputed from the frame analysis with the same IS 456 routines "
                "and inputs as the design run and compared with the design report; differences above "
                f"{TOLERANCE * 100:.1f} % are flagged on the sheet."
            ),
            st_body,
        )
    )
    if n_warn:
        story.append(
            P(f"<font color='red'><b>{T(f'{n_warn} sheet(s) contain recomputation warnings.')}</b></font>", st_body)
        )
    story.append(Spacer(1, 3 * mm))
    story.append(P(f"<i>{T(DISCLAIMER)}</i>", st_body))
    # ------------------------------------------------------------------ contents
    story.append(PageBreak())
    story.append(P(T("Table of contents"), st_h1))
    st_toc = ParagraphStyle("toc", fontName=font, fontSize=toc_size, leading=toc_h)
    if sheets:
        trows = []
        for i, sh in enumerate(sheets, 1):
            txt = _plain(f"{i}. {sh.kind} {sh.ref} – {sh.level}", uni)
            if len(txt) > 95:
                txt = txt[:92] + "..."
            trows.append(
                [
                    P(f"<a href='#sheet{i}'>{_esc(txt)}</a>", st_toc),
                    P(f"<b>{'PASS' if sh.ok else 'FAIL'}</b>", st_toc),
                    _PageRef(f"sheet{i}"),
                ]
            )
        tst_toc = [
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#D0D7E1")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ]
        tst_toc += [("TEXTCOLOR", (1, r), (1, r), colors.HexColor("#B00020")) for r, s in enumerate(sheets) if not s.ok]
        tt = Table(trows, colWidths=[W - 2 * margin - 16 * mm - toc_w, 16 * mm, toc_w])
        tt.setStyle(TableStyle(tst_toc))
        story.append(tt)
    else:
        story.append(P(T("No calculation sheets selected."), st_body))
    # ------------------------------------------------------------------ sheets
    full = W - 2 * margin
    widths = [8 * mm, 38 * mm, full - 8 * mm - 38 * mm - 34 * mm - 24 * mm, 34 * mm, 24 * mm]
    for i, sh in enumerate(sheets, 1):
        story.append(PageBreak())
        verdict = "PASS" if sh.ok else "FAIL"
        title = P(T(f"Sheet {i} – {sh.kind} {sh.ref}"), st_sheet)
        title._pw_key = f"sheet{i}"
        title._pw_label = _plain(f"Sheet {i}: {sh.kind} {sh.ref} – {sh.level}", uni)
        story.append(title)
        story.append(kv_table(pairs(sh.header), [full * 0.16, full * 0.34, full * 0.16, full * 0.34]))
        story.append(Spacer(1, 3 * mm))
        data = [[P(T(h), st_head) for h in ("#", "Description", "Formula / substituted values", "Result", "IS ref")]]
        tst = [
            ("BACKGROUND", (0, 0), (-1, 0), hdr),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
        grp = None
        num = 0
        for stp in sh.steps:
            if stp.group and stp.group != grp:
                grp = stp.group
                data.append([P(f"<b>{T(grp)}</b>"), "", "", "", ""])
                r = len(data) - 1
                tst += [("SPAN", (0, r), (-1, r)), ("BACKGROUND", (0, r), (-1, r), colors.HexColor("#D9E2EC"))]
            num += 1
            ftxt = T(stp.formula)
            if stp.subst:
                ftxt += ("<br/>" if ftxt else "") + f"<font color='#555555'>{T(stp.subst)}</font>"
            data.append(
                [P(str(num)), P(T(stp.title)), P(ftxt), P(f"<b>{T(stp.result)}</b>"), P(T(stp.clause), st_small)]
            )
            if stp.warning:
                data.append([P(f"<font color='#B00020'><b>{T('WARNING: ' + stp.warning)}</b></font>"), "", "", "", ""])
                r = len(data) - 1
                tst += [("SPAN", (0, r), (-1, r)), ("BACKGROUND", (0, r), (-1, r), colors.HexColor("#FFF3CD"))]
        if len(data) == 1:
            data.append([P(""), P(T("No calculation steps")), "", "", ""])
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle(tst))
        story.append(t)
        story.append(Spacer(1, 4 * mm))
        # summary box
        ok_c, bad_c = colors.HexColor("#D4EDDA"), colors.HexColor("#F8D7DA")
        srows = [[P(T(h), st_head) for h in ("Check", "Details", "Status")]]
        sst = [
            ("BACKGROUND", (0, 0), (-1, 0), hdr),
            ("BOX", (0, 0), (-1, -1), 1.2, hdr),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]
        for c in sh.checks:
            srows.append([P(T(c.label)), P(T(c.detail)), P(f"<b>{'PASS' if c.ok else 'FAIL'}</b>")])
            sst.append(("BACKGROUND", (2, len(srows) - 1), (2, len(srows) - 1), ok_c if c.ok else bad_c))
        srows.append([P(f"<b>{T('OVERALL RESULT')}</b>"), "", P(f"<b>{verdict}</b>")])
        r = len(srows) - 1
        sst += [("SPAN", (0, r), (1, r)), ("BACKGROUND", (0, r), (-1, r), ok_c if sh.ok else bad_c)]
        if sh.warnings:
            for w in sh.warnings:
                srows.append([P(f"<font color='#B00020'>{T('Recomputation: ' + w)}</font>"), "", ""])
                sst.append(("SPAN", (0, len(srows) - 1), (-1, len(srows) - 1)))
        else:
            srows.append(
                [P(T(f"All recomputed values agree with the design report (tolerance {TOLERANCE * 100:.1f} %)."),
                   st_small), "", ""]
            )  # fmt: skip
            sst.append(("SPAN", (0, len(srows) - 1), (-1, len(srows) - 1)))
        for note in sh.notes:
            srows.append([P(T("Design note: " + note), st_small), "", ""])
            sst.append(("SPAN", (0, len(srows) - 1), (-1, len(srows) - 1)))
        sb = Table(srows, colWidths=[45 * mm, full - 45 * mm - 25 * mm, 25 * mm])
        sb.setStyle(TableStyle(sst))
        story.append(KeepTogether([P(T("Summary"), st_sheet), sb]))
    doc.build(story)
