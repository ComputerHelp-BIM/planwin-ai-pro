"""IS 456:2000 isolated pad footing design (cl 34) with SBC, full-contact, punching and one-way shear."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .common import BarMesh, ast_singly, mesh_for, tau_c


@dataclass
class FootingResult:
    L: float  # m (along column D)
    B: float
    D: float  # thickness m
    ast_L: float  # mm^2/m (bars running along L)
    ast_B: float
    bars_L: str
    bars_B: str
    q_max: float  # kN/m^2 service
    ok: bool
    notes: list[str] = field(default_factory=list)
    mesh_L: BarMesh | None = None  # bars running along L
    mesh_B: BarMesh | None = None


def design_footing(
    P_service: float,
    cb: float,
    cd: float,
    sbc: float,
    fck: float,
    fy: float,
    cover: float = 0.05,
    self_wt_pct: float = 10.0,
    lateral: list[tuple[float, float, float]] | None = None,
    equal_overhang: bool = True,
    ultimate: list[tuple[float, float, float]] | None = None,
) -> FootingResult:
    """Isolated pad footing (L along column depth ``cd``).

    * Plan size: gross pressure <= SBC for DL+LL, and <= 1.25 SBC for service
      cases with lateral loads (P, Mx, My); Mx varies pressure along L.
      Full contact is required (q_min >= 0, i.e. e <= L/6), otherwise the
      footing is enlarged.
    * Structural design uses the governing *factored net* pressure including
      moments.  ``ultimate`` – factored reactions (Pu, Mux, Muy) of every
      ultimate combination (IS 875-5: 1.5(DL+LL), 1.2(DL+LL±EL), 1.5(DL±EL),
      0.9DL±1.5EL).  Without it the service cases are scaled (1.5 gravity,
      1.2 lateral) as a fallback.
    * Depth from punching (cl 31.6.3) and one-way shear (cl 31.6.2) with
      tau_c taken at the steel actually provided; flexure at the column face.
    """
    notes = []
    sw = 1 + self_wt_pct / 100
    cases = [(P_service, 0.0, 0.0, 1.0, 1.5)] + [(P, Mx, My, 1.25, 1.2) for P, Mx, My in (lateral or [])]
    A = P_service * sw / sbc
    if equal_overhang:
        bq = cb + cd
        a = (-bq + math.sqrt(bq * bq - 4 * (cb * cd - A))) / 4
        L, B = cd + 2 * a, cb + 2 * a
    else:
        r = cd / cb if cb else 1
        B = math.sqrt(A / r)
        L = r * B
    L = math.ceil(max(L, cd + 0.3) / 0.05) * 0.05
    B = math.ceil(max(B, cb + 0.3) / 0.05) * 0.05

    def pressures(P, Mx, My, L_, B_):
        q = P * sw / (L_ * B_)
        dq = 6 * abs(Mx) / (B_ * L_ * L_) + 6 * abs(My) / (L_ * B_ * B_)
        return q + dq, q - dq

    for _ in range(80):
        ok_size = True
        for P, Mx, My, allow, _f in cases:
            qmax, qmin = pressures(max(P, 0.0), Mx, My, L, B)
            if qmax > allow * sbc * 1.0001 or qmin < -1e-6:
                ok_size = False
                break
        if ok_size:
            break
        L = round(L + 0.1, 3)
        B = math.ceil(round(B + 0.1 * (cb / cd if cd else 1), 3) / 0.05) * 0.05
    else:
        notes.append("could not satisfy SBC / full contact – consider raft or combined footing")
    if any(P < 0 for P, *_ in cases):
        notes.append("net uplift in a lateral case – provide anchorage / check tension in column")
    q_service = P_service * sw / (L * B)
    # governing factored net upward pressure (self weight of footing excluded)
    fact = ultimate if ultimate else [(f * P, f * Mx, f * My) for P, Mx, My, _a, f in cases]
    qu = max(max(P, 0.0) / (L * B) + 6 * abs(Mx) / (B * L * L) + 6 * abs(My) / (L * B * B) for P, Mx, My in fact)
    ast_min_per_m = lambda D_: 0.0012 * 1000 * D_ * 1000  # noqa: E731
    D = 0.3
    ok = True
    while True:
        d = D - cover - 0.012
        Mu_L = qu * B * ((L - cd) / 2) ** 2 / 2  # kN·m, bars along L
        Mu_B = qu * L * ((B - cb) / 2) ** 2 / 2
        astL = max(ast_singly(Mu_L * 1e6 / B, fck, fy, 1000, d * 1000), ast_min_per_m(D))
        astB = max(ast_singly(Mu_B * 1e6 / L, fck, fy, 1000, d * 1000), ast_min_per_m(D))
        flex_ok = math.isfinite(astL) and math.isfinite(astB)
        bo = 2 * ((cb + d) + (cd + d))
        Vp = qu * (L * B - (cb + d) * (cd + d))
        beta = min(cb, cd) / max(cb, cd)
        tp = min(0.5 + beta, 1.0) * 0.25 * math.sqrt(fck)
        punch_ok = Vp * 1e3 / (bo * 1e3 * d * 1e3) <= tp
        V1 = qu * B * max((L - cd) / 2 - d, 0)
        V2 = qu * L * max((B - cb) / 2 - d, 0)
        ptL = 100 * astL / (1000 * d * 1000) if flex_ok else 0.15
        ptB = 100 * astB / (1000 * d * 1000) if flex_ok else 0.15
        one_ok = (V1 / (B * d) / 1e3 <= tau_c(ptL, fck)) and (V2 / (L * d) / 1e3 <= tau_c(ptB, fck))
        if flex_ok and punch_ok and one_ok:
            break
        D = round(D + 0.05, 3)
        if D > 2.5:
            ok = False
            notes.append("depth > 2.5 m – consider raft/pile")
            break

    mesh_L = mesh_for(astL, (10, 12, 16, 20, 25), 300.0)
    mesh_B = mesh_for(astB, (10, 12, 16, 20, 25), 300.0)

    def bar_str(mesh):
        return str(mesh) if mesh else "-"

    return FootingResult(
        round(L, 3),
        round(B, 3),
        round(D, 3),
        astL,
        astB,
        bar_str(mesh_L),
        bar_str(mesh_B),
        round(q_service, 1),
        ok and not any("could not" in n for n in notes),
        notes,
        mesh_L=mesh_L,
        mesh_B=mesh_B,
    )


# ================================================================ combined footing (two columns)
@dataclass
class CombinedFootingResult:
    L: float  # m, along the line through the two columns
    B: float  # m
    D: float  # m
    x_start: float  # m, footing starts this far before column 1 (along the line)
    q_service: float  # kN/m², uniform (resultant at the centre)
    M_hog: float  # kN·m, max hogging (top tension, between the columns)
    M_sag: float  # kN·m, max sagging (bottom tension, under the columns / cantilevers)
    top: BarMesh | None
    bottom: BarMesh | None
    transverse: list[str]  # bottom bars in the bands under each column
    ok: bool
    notes: list[str] = field(default_factory=list)


def design_combined_footing(
    P1: float,
    P2: float,
    Pu1: float,
    Pu2: float,
    s: float,
    c1: tuple[float, float],
    c2: tuple[float, float],
    sbc: float,
    fck: float,
    fy: float,
    cover: float = 0.05,
    self_wt_pct: float = 10.0,
) -> CombinedFootingResult:
    """Rectangular combined footing for two columns ``s`` m apart (IS 456 cl 34, SP 16 / SP 34 practice).

    ``c1``/``c2`` – (dimension along the line, dimension across) of the columns (m).  The
    footing is proportioned so that the resultant of the service loads coincides with its
    centroid (uniform pressure ≤ SBC).  Longitudinally it is a beam loaded by the factored
    net upward pressure and supported by the columns; transversely each column is spread by a
    band of width c + 2d·… (taken as c + d each side, SP 34).  Depth from punching shear at
    d/2 around each column (cl 31.6.3) and one-way shear at d from the column faces
    (cl 34.2.4.1 a); flexure with 0.12 % minimum steel.  Moments from lateral load cases are
    not included – check them for footings resisting significant moments.
    """
    from ...core.beamcalc import LinLoad, PtLoad, diagrams

    notes: list[str] = []
    sw = 1 + self_wt_pct / 100
    xbar = P2 * s / (P1 + P2) if P1 + P2 > 0 else s / 2  # resultant from column 1
    half = max(xbar + c1[0] / 2 + 0.3, s - xbar + c2[0] / 2 + 0.3)
    L = math.ceil(2 * half / 0.05) * 0.05
    x_start = L / 2 - xbar  # footing edge before column 1
    B = max((P1 + P2) * sw / (sbc * L), max(c1[1], c2[1]) + 0.3)
    B = math.ceil(B / 0.05) * 0.05
    q_service = (P1 + P2) * sw / (L * B)
    qu = (Pu1 + Pu2) / (L * B)  # net factored upward pressure
    w = qu * B  # kN/m along the footing
    x1, x2 = x_start, x_start + s
    # beam statics: soil pressure upwards, columns downwards (sign convention of beamcalc: loads down +)
    loads = [LinLoad(0.0, L, -w, -w, "D"), PtLoad(x1, Pu1, "D"), PtLoad(x2, Pu2, "D")]
    dia = diagrams(L, loads, [], None, 241)
    # beamcalc: moment + = tension at the bottom.  Between the columns the net soil pressure
    # bends the footing with tension at the TOP (M < 0); under the columns / in the cantilevers
    # the tension is at the BOTTOM (M > 0).
    M_top = max(float(-dia["M"].min()), 0.0)
    M_bot = max(float(dia["M"].max()), 0.0)
    V = dia["V"]
    xs = dia["x"]
    D = 0.4
    ok = True
    ast_min = 0.0012 * 1000 * D * 1000
    while True:
        d = D - cover - 0.016
        ok_shear = True
        for xc, Pu, c in ((x1, Pu1, c1), (x2, Pu2, c2)):
            bo = 2 * ((c[0] + d) + (c[1] + d))
            Vp = Pu - qu * (c[0] + d) * (c[1] + d)
            beta = min(c) / max(c)
            tp = min(0.5 + beta, 1.0) * 0.25 * math.sqrt(fck)
            if Vp * 1e3 / (bo * 1e3 * d * 1e3) > tp:
                ok_shear = False
            for xf in (xc - c[0] / 2 - d, xc + c[0] / 2 + d):
                if 0 < xf < L:
                    v1 = abs(float(np.interp(xf, xs, V)))
                    if v1 * 1e3 / (B * 1e3 * d * 1e3) > tau_c(0.25, fck):
                        ok_shear = False
        a_top = ast_singly(M_top * 1e6 / B, fck, fy, 1000, d * 1000)
        a_bot = ast_singly(M_bot * 1e6 / B, fck, fy, 1000, d * 1000)
        if ok_shear and math.isfinite(a_top) and math.isfinite(a_bot):
            break
        D = round(D + 0.05, 3)
        if D > 2.5:
            ok = False
            notes.append("depth > 2.5 m – use a raft or piles")
            break
    ast_min = 0.0012 * 1000 * D * 1000
    top = mesh_for(max(a_top, ast_min) if math.isfinite(a_top) else ast_min, (12, 16, 20, 25), 300.0)
    bottom = mesh_for(max(a_bot, ast_min) if math.isfinite(a_bot) else ast_min, (12, 16, 20, 25), 300.0)
    transverse = []
    d = D - cover - 0.016
    for name, Pu, c in (("column 1", Pu1, c1), ("column 2", Pu2, c2)):
        band = c[0] + 2 * d  # SP 34: column width + d on each side
        mt = Pu / B * ((B - c[1]) / 2) ** 2 / 2  # kN·m per metre of band… per unit length of the band
        a_t = max(ast_singly(mt * 1e6, fck, fy, 1000, d * 1000), 0.0012 * 1000 * D * 1000)
        mesh = mesh_for(a_t, (12, 16, 20), 300.0)
        transverse.append(f"{name}: {mesh} over a {band:.2f} m band")
    return CombinedFootingResult(
        round(L, 3),
        round(B, 3),
        round(D, 3),
        round(x_start, 3),
        round(q_service, 1),
        M_top,
        M_bot,
        top,
        bottom,
        transverse,
        ok,
        notes,
    )
