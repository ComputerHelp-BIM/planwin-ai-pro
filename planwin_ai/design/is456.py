"""IS 456:2000 limit-state design routines (SI: N, mm, MPa unless stated).

Each function is self-contained and unit tested against textbook values.
All results are *preliminary* design aids; final designs must be checked
and signed by a qualified structural engineer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

BAR_DIAS = [8, 10, 12, 16, 20, 25, 32]

# ---------------------------------------------------------------- Table 19
_PT = [0.15, 0.25, 0.50, 0.75, 1.00, 1.25, 1.50, 1.75, 2.00, 2.25, 2.50, 2.75, 3.00]
_TC = {
    15: [0.28, 0.35, 0.46, 0.54, 0.60, 0.64, 0.68, 0.71, 0.71, 0.71, 0.71, 0.71, 0.71],
    20: [0.28, 0.36, 0.48, 0.56, 0.62, 0.67, 0.72, 0.75, 0.79, 0.81, 0.82, 0.82, 0.82],
    25: [0.29, 0.36, 0.49, 0.57, 0.64, 0.70, 0.74, 0.78, 0.82, 0.85, 0.88, 0.90, 0.92],
    30: [0.29, 0.37, 0.50, 0.59, 0.66, 0.71, 0.76, 0.80, 0.84, 0.88, 0.91, 0.94, 0.96],
    35: [0.29, 0.37, 0.50, 0.59, 0.67, 0.73, 0.78, 0.82, 0.86, 0.90, 0.93, 0.96, 0.99],
    40: [0.30, 0.38, 0.51, 0.60, 0.68, 0.74, 0.79, 0.84, 0.88, 0.92, 0.95, 0.98, 1.01],
}
_TCMAX = {15: 2.5, 20: 2.8, 25: 3.1, 30: 3.5, 35: 3.7, 40: 4.0}


def _grade_key(fck: float) -> int:
    keys = sorted(_TC)
    return max(k for k in keys if k <= max(fck, 15)) if fck >= 15 else 15


def tau_c(pt: float, fck: float) -> float:
    """Design shear strength of concrete (Table 19), pt in %."""
    row = _TC[_grade_key(fck)]
    return float(np.interp(min(max(pt, 0.15), 3.0), _PT, row))


def tau_c_max(fck: float) -> float:
    return _TCMAX[_grade_key(fck)]


def xu_max_ratio(fy: float) -> float:
    return {250: 0.53, 415: 0.48, 500: 0.46, 550: 0.44}.get(int(fy), 700 / (1100 + 0.87 * fy))


def mu_lim(fck: float, fy: float, b: float, d: float) -> float:
    """Limiting moment of resistance (N·mm), cl G-1.1."""
    k = xu_max_ratio(fy)
    return 0.36 * fck * b * d * d * k * (1 - 0.42 * k)


def ast_singly(Mu: float, fck: float, fy: float, b: float, d: float) -> float:
    """Tension steel for a singly reinforced section (mm^2), Mu in N·mm."""
    if Mu <= 0:
        return 0.0
    disc = 1 - 4.6 * Mu / (fck * b * d * d)
    if disc < 0:
        return float("inf")
    return 0.5 * fck / fy * (1 - math.sqrt(disc)) * b * d


def fsc_doubly(fy: float, dc_over_d: float) -> float:
    """Stress in compression steel at limiting state (SP-16 Table F)."""
    xs = [0.05, 0.10, 0.15, 0.20]
    tab = {415: [355, 353, 342, 329], 500: [424, 412, 395, 370], 250: [217, 217, 217, 217]}
    row = tab.get(int(fy), tab[500])
    return float(np.interp(min(max(dc_over_d, 0.05), 0.20), xs, row))


@dataclass
class FlexureResult:
    Mu: float  # kN·m
    ast: float  # mm^2 tension
    asc: float  # mm^2 compression
    doubly: bool
    ok: bool
    note: str = ""


def flexure(Mu_kNm: float, fck: float, fy: float, b_mm: float, D_mm: float, cover_mm: float) -> FlexureResult:
    Mu = abs(Mu_kNm) * 1e6
    d = D_mm - cover_mm - 8 - 10  # stirrup 8 + half of a 20 bar
    dc = cover_mm + 8 + 10
    ast_min = 0.85 * b_mm * d / fy
    ast_max = 0.04 * b_mm * D_mm
    ml = mu_lim(fck, fy, b_mm, d)
    if Mu <= ml:
        ast = max(ast_singly(Mu, fck, fy, b_mm, d), ast_min if Mu > 0 else 0.0)
        return FlexureResult(Mu_kNm, ast, 0.0, False, ast <= ast_max, "" if ast <= ast_max else "exceeds 4% – increase section")
    fsc = fsc_doubly(fy, dc / d)
    asc = (Mu - ml) / ((fsc - 0.446 * fck) * (d - dc))
    ast = ast_singly(ml, fck, fy, b_mm, d) + asc * fsc / (0.87 * fy)
    ok = ast <= ast_max and asc <= ast_max
    return FlexureResult(Mu_kNm, ast, asc, True, ok, "doubly reinforced" + ("" if ok else " – exceeds 4%, increase section"))


@dataclass
class ShearResult:
    Vu: float
    tau_v: float
    tau_c: float
    legs: int
    dia: int
    spacing: float  # mm
    ok: bool
    note: str = ""


def shear(Vu_kN: float, fck: float, fy: float, b_mm: float, d_mm: float, pt: float, dia: int = 8, legs: int = 2) -> ShearResult:
    """Vertical stirrups per cl 40.4 / 26.5.1.5-6. Stirrup fy is capped at 415 MPa (cl 26.5.1.6)."""
    fyv = min(fy, 415.0)
    Vu = abs(Vu_kN) * 1e3
    tv = Vu / (b_mm * d_mm)
    tc = tau_c(pt, fck)
    tcm = tau_c_max(fck)
    if tv > tcm:
        return ShearResult(Vu_kN, tv, tc, legs, dia, 0.0, False, "τv > τc,max – increase section")
    s_max = min(0.75 * d_mm, 300.0)
    for dia_, legs_ in ((dia, legs), (10, 2), (10, 4), (12, 4)):
        asv = legs_ * math.pi * dia_ * dia_ / 4
        s_min_reinf = 0.87 * fyv * asv / (0.4 * b_mm)
        if tv <= tc:
            s = min(s_max, s_min_reinf)
        else:
            Vus = Vu - tc * b_mm * d_mm
            s = min(0.87 * fyv * asv * d_mm / Vus, s_max, s_min_reinf)
        if s >= 75:
            return ShearResult(Vu_kN, tv, tc, legs_, dia_, math.floor(s / 25) * 25, True)
    return ShearResult(Vu_kN, tv, tc, 4, 12, 0.0, False, "stirrup spacing < 75 mm – increase section")


def select_bars(area: float, width_mm: float, cover_mm: float, min_bars: int = 2, max_layers: int = 1,
                dias=(12, 16, 20, 25, 32)) -> tuple[int, int, float]:
    """Choose (count, dia, provided area) with minimum excess that fits in one layer."""
    best = None
    for dia in dias:
        a1 = math.pi * dia * dia / 4
        n = max(min_bars, math.ceil(area / a1 - 1e-9))
        clear = max(dia, 25)
        fit = (width_mm - 2 * cover_mm - 16 + clear) // (dia + clear)
        if n > fit * max_layers:
            continue
        prov = n * a1
        key = (prov - area, -dia)
        if best is None or key < best[0]:
            best = (key, n, dia, prov)
    if best is None:
        dia = dias[-1]
        n = max(min_bars, math.ceil(area / (math.pi * dia * dia / 4)))
        return n, dia, n * math.pi * dia * dia / 4
    return best[1], best[2], best[3]


def deflection_mf(pt: float, fs: float) -> float:
    """Modification factor for tension steel (IS 456 Fig 4 fit)."""
    pt = max(pt, 0.1)
    mf = 1.0 / (0.225 + 0.003225 * fs - 0.625 * math.log10(1.0 / pt))
    return max(min(mf, 2.0), 0.4)


# ================================================================ columns
def _concrete_stress(eps: np.ndarray, fck: float) -> np.ndarray:
    e = np.clip(eps, 0.0, None)
    r = np.minimum(e / 0.002, 1.0)
    return np.where(e >= 0.002, 0.446 * fck, 0.446 * fck * (2 * r - r * r))


# SP-16 Table A: design stress-strain points for cold-worked bars (IS 456 Fig 23A), as fractions of fyd
_SS_FRAC = [0.80, 0.85, 0.90, 0.95, 0.975, 1.0]
_SS_INEL = [0.0, 0.0001, 0.0003, 0.0007, 0.0010, 0.0020]  # inelastic strain added to fd/Es


def _steel_stress(eps: np.ndarray, fy: float) -> np.ndarray:
    """Design steel stress: bilinear for mild steel, IS 456 Fig 23A curve for HYSD bars."""
    Es = 2e5
    fyd = 0.87 * fy
    e = np.abs(eps)
    if fy <= 250:
        f = np.minimum(Es * e, fyd)
    else:
        strains = [fr * fyd / Es + ie for fr, ie in zip(_SS_FRAC, _SS_INEL)]
        stresses = [fr * fyd for fr in _SS_FRAC]
        f = np.where(e <= strains[0], Es * e, np.interp(e, strains, stresses, right=fyd))
    return np.sign(eps) * f


def interaction_curve(b: float, D: float, As: float, fck: float, fy: float, cover: float,
                      n_points: int = 90, n_strips: int = 40) -> tuple[np.ndarray, np.ndarray]:
    """P–M interaction curve (N, N·mm) for bending about the axis parallel to ``b``.

    Steel ``As`` is equally distributed on four faces (SP-16 assumption).
    Strain compatibility per IS 456 cl 38.1 / 39.1 (pivot at 3D/7 when xu > D).
    Returned arrays are sorted by increasing P and end at (Puz, 0).
    """
    dc = cover + 8 + 10  # to bar centre
    layers = [(dc, As / 4), (D - dc, As / 4)]
    nside = 3
    for k in range(1, nside + 1):
        layers.append((dc + (D - 2 * dc) * k / (nside + 1), As / 2 / nside))
    ys = np.array([y for y, _ in layers])
    as_ = np.array([a for _, a in layers])
    strips = (np.arange(n_strips) + 0.5) * D / n_strips
    dA = b * D / n_strips
    xus = D * np.concatenate([np.geomspace(0.02, 1.0, n_points // 2, endpoint=False),
                              np.geomspace(1.0, 40.0, n_points - n_points // 2)])
    top = np.where(xus <= D, 0.0035, 0.002 * xus / np.maximum(xus - 3 * D / 7, 1e-9))[:, None]
    eps = top * (xus[:, None] - strips[None, :]) / xus[:, None]
    eps_s = top * (xus[:, None] - ys[None, :]) / xus[:, None]
    fc = _concrete_stress(eps, fck)
    fs = _steel_stress(eps_s, fy) - np.where(eps_s > 0, _concrete_stress(eps_s, fck), 0.0)
    Ps = list(fc.sum(axis=1) * dA + fs @ as_)
    Ms = list((fc * (D / 2 - strips)[None, :]).sum(axis=1) * dA + (fs * as_[None, :]) @ (D / 2 - ys))
    Puz = puz(b, D, As, fck, fy)  # cl 39.6 – caps the curve
    P = np.array(Ps)
    M = np.maximum(np.array(Ms), 0.0)
    keep = P < Puz
    # pure tension point closes the curve below (conservative linear branch)
    P = np.concatenate([[-0.87 * fy * As], P[keep], [Puz]])
    M = np.concatenate([[0.0], M[keep], [0.0]])
    order = np.argsort(P)
    return P[order], M[order]


def puz(b: float, D: float, As: float, fck: float, fy: float) -> float:
    """cl 39.6: Puz = 0.45 fck Ac + 0.75 fy Asc with Ac = net concrete area."""
    return 0.45 * fck * (b * D - As) + 0.75 * fy * As


def section_capacity(P_N: float, b: float, D: float, As: float, fck: float, fy: float, cover: float) -> float:
    """Uniaxial moment capacity (N·mm) for axial load ``P_N`` (compression +)."""
    P, M = interaction_curve(b, D, As, fck, fy, cover)
    if P_N >= P[-1]:
        return 0.0
    return float(np.interp(P_N, P, M))


@dataclass
class ColumnCheck:
    ok: bool
    steel_pct: float
    As_req: float
    ratio: float
    governing: str
    bars: str = ""
    ties: str = ""
    notes: list[str] = field(default_factory=list)


def biaxial_ratio(Pu: float, Mux: float, Muy: float, b: float, D: float, As: float, fck: float, fy: float,
                  cover: float, curves=None) -> float:
    """Interaction value (Mux/Mux1)^an + (Muy/Muy1)^an (cl 39.6). Units N, N·mm, mm."""
    Puz = puz(b, D, As, fck, fy)
    if Pu >= Puz:
        return 9.99
    (px, mx), (py, my) = curves or (interaction_curve(b, D, As, fck, fy, cover), interaction_curve(D, b, As, fck, fy, cover))
    mux1 = float(np.interp(Pu, px, mx))
    muy1 = float(np.interp(Pu, py, my))
    r = Pu / Puz
    an = 1.0 if r <= 0.2 else (2.0 if r >= 0.8 else 1.0 + (r - 0.2) / 0.6)
    if mux1 <= 0 or muy1 <= 0:
        return 9.99
    return (abs(Mux) / mux1) ** an + (abs(Muy) / muy1) ** an


def design_column(demands: list[tuple[str, float, float, float]], b_m: float, D_m: float, L_m: float,
                  fck: float, fy: float, cover_m: float = 0.04, pmin: float = 0.8, pmax: float = 4.0,
                  k_eff: float = 1.0) -> ColumnCheck:
    """Find the minimum steel % satisfying all demands.

    ``demands`` – list of (combo, Pu kN (compression +), Mux kN·m about the
    axis parallel to b (bending in D direction), Muy kN·m).
    """
    b, D, cover = b_m * 1000, D_m * 1000, cover_m * 1000
    notes = []
    lex = k_eff * L_m * 1000
    ex_min_x = max(lex / 500 + D / 30, 20)
    ex_min_y = max(lex / 500 + b / 30, 20)
    slender_x, slender_y = lex / D > 12, lex / b > 12
    if slender_x or slender_y:
        notes.append(f"slender (lex/D={lex / D:.1f}, lex/b={lex / b:.1f}) – additional moments added (cl 39.7)")
    # Each demand keeps its own moments: one combination appears twice (top and bottom
    # of the column), so the moments must not be looked up by combination name.
    prepared = []
    for name, Pu_kN, Mx_kNm, My_kNm in demands:
        Pu = max(Pu_kN, 0.0) * 1e3
        prepared.append((name, Pu_kN, Pu, abs(Mx_kNm) * 1e6, abs(My_kNm) * 1e6))
    # additional moments for slender columns (cl 39.7.1), reduced by k (cl 39.7.1.1)
    Max0 = (D / 2000 * (lex / D) ** 2) if slender_x else 0.0  # multiply by Pu
    May0 = (b / 2000 * (lex / b) ** 2) if slender_y else 0.0

    def check(p):
        As = p / 100 * b * D
        curves = (interaction_curve(b, D, As, fck, fy, cover), interaction_curve(D, b, As, fck, fy, cover))
        Puz = puz(b, D, As, fck, fy)
        Pbx = float(curves[0][0][int(np.argmax(curves[0][1]))])  # balanced load ~ P at peak moment
        Pby = float(curves[1][0][int(np.argmax(curves[1][1]))])
        maxr, gov = 0.0, ""
        for name, Pu_kN, Pu, Mx, My in prepared:
            if Pu_kN < 0:  # net tension: P-M interaction on the tension branch (alpha_n = 1)
                Pt = Pu_kN * 1e3
                mx1 = float(np.interp(Pt, *curves[0]))
                my1 = float(np.interp(Pt, *curves[1]))
                r = 9.99 if (mx1 <= 0 or my1 <= 0) else Mx / mx1 + My / my1
                r = max(r, abs(Pt) / (0.87 * fy * As))
            else:
                kx = min(max((Puz - Pu) / max(Puz - Pbx, 1e-9), 0.0), 1.0)
                ky = min(max((Puz - Pu) / max(Puz - Pby, 1e-9), 0.0), 1.0)
                ax, ay = kx * Max0 * Pu, ky * May0 * Pu
                # cl 25.4: for biaxial bending the minimum eccentricity need only be
                # satisfied about one axis at a time – check both and keep the worse
                r = max(biaxial_ratio(Pu, max(Mx, Pu * ex_min_x) + ax, My + ay, b, D, As, fck, fy, cover, curves),
                        biaxial_ratio(Pu, Mx + ax, max(My, Pu * ex_min_y) + ay, b, D, As, fck, fy, cover, curves))
            if r > maxr:
                maxr, gov = r, name
        return As, maxr, gov

    # bisection on 0.1 % steps (capacity is monotonic in steel ratio)
    steps = [round(pmin + 0.1 * k, 2) for k in range(int(round((pmax - pmin) / 0.1)) + 1)]
    lo, hi = 0, len(steps) - 1
    As, r_hi, g_hi = check(steps[hi])
    if r_hi > 1.0:
        p, ratio, gov = steps[hi], r_hi, g_hi
        notes.append(f"fails at {pmax}% steel – increase column size")
    else:
        best = (steps[hi], As, r_hi, g_hi)
        As0, r0, g0 = check(steps[lo])
        if r0 <= 1.0:
            best = (steps[lo], As0, r0, g0)
        else:
            while hi - lo > 1:
                mid = (lo + hi) // 2
                Am, rm, gm = check(steps[mid])
                if rm <= 1.0:
                    hi, best = mid, (steps[mid], Am, rm, gm)
                else:
                    lo = mid
        p, As, ratio, gov = best
    ok = ratio <= 1.0
    n, dia, prov = column_bars(As, b, D)
    tie_dia = max(8, math.ceil(dia / 4 / 2) * 2)
    tie_sp = min(b, D, 16 * dia, 300)
    return ColumnCheck(ok, p, As, ratio, gov, f"{n}-T{dia} ({prov:.0f} mm²)", f"T{tie_dia} @ {int(tie_sp // 25 * 25)} c/c", notes)


def column_bars(As: float, b: float, D: float) -> tuple[int, int, float]:
    best = None
    for dia in (12, 16, 20, 25, 32):
        a1 = math.pi * dia * dia / 4
        perim = 2 * (b + D - 4 * 50)
        n = max(4, math.ceil(As / a1), math.ceil(perim / 300))  # cl 26.5.3.1: <= 300 mm apart
        n += n % 2
        if perim / n < max(dia, 40) + dia:  # minimum clear spacing
            continue
        prov = n * a1
        key = (round((prov - As) / max(As, 1) * 4), n)  # 25 % excess bands, then fewest bars
        if best is None or key < best[0]:
            best = (key, n, dia, prov)
    if best is None:
        n = max(4, math.ceil(As / (math.pi * 32 * 32 / 4)))
        return n, 32, n * math.pi * 32 * 32 / 4
    return best[1], best[2], best[3]


def autosize_depth(Pu_kN: float, b_m: float, fck: float, fy: float, pct: float = 0.8, moment_factor: float = 1.25,
                   step: float = 0.05, min_d: float | None = None) -> float:
    """Column depth for factored axial load (FrameWin AUTOSIZE): Pu·mf <= 0.4fck Ac + 0.67 fy Asc."""
    p = pct / 100
    stress = 0.4 * fck * (1 - p) + 0.67 * fy * p  # N/mm^2
    A = Pu_kN * 1e3 * moment_factor / stress / 1e6  # m^2
    d = max(A / b_m, min_d or b_m)
    return round(math.ceil(d / step - 1e-9) * step, 3)


# ================================================================ footing
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


def design_footing(P_service: float, cb: float, cd: float, sbc: float, fck: float, fy: float, cover: float = 0.05,
                   self_wt_pct: float = 10.0, lateral: list[tuple[float, float, float]] | None = None,
                   equal_overhang: bool = True,
                   ultimate: list[tuple[float, float, float]] | None = None) -> FootingResult:
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

    def bar_str(ast):
        for dia in (10, 12, 16, 20, 25):
            s_ = 1000 * math.pi * dia * dia / 4 / ast
            if s_ >= 100:
                return f"T{dia} @ {int(min(s_, 300) // 10 * 10)} c/c"
        return "T25 @ 100 c/c (check)"

    return FootingResult(round(L, 3), round(B, 3), round(D, 3), astL, astB, bar_str(astL), bar_str(astB),
                         round(q_service, 1), ok and not any("could not" in n for n in notes), notes)


# ================================================================ slabs
# Table 27 (simply supported, corners not held down) – alpha_x, alpha_y
_T27_R = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.75, 2.0]
_T27_AX = [0.062, 0.074, 0.084, 0.093, 0.099, 0.104, 0.113, 0.118]
_T27_AY = [0.062, 0.061, 0.059, 0.055, 0.051, 0.046, 0.037, 0.029]
# Table 26 case 6 (three edges discontinuous, one long edge continuous) – used
# as an envelope for negative moments at continuous edges (conservative).
_T26_NEG = [0.057, 0.064, 0.071, 0.076, 0.080, 0.084, 0.091, 0.097]


@dataclass
class SlabResult:
    mark: str
    lx: float
    ly: float
    kind: str
    D_mm: float
    Mx_pos: float
    My_pos: float
    M_neg: float
    ast_x: str
    ast_y: str
    ast_neg: str
    deflection_ok: bool
    ok: bool
    notes: list[str] = field(default_factory=list)


def design_slab(mark: str, lx: float, ly: float, w_dead: float, w_live: float, D_m: float, fck: float, fy: float,
                kind: str, continuous_edges: int, cover: float = 0.02) -> SlabResult:
    """Slab design by IS 456 coefficient methods.

    two-way: Table 27 for positive moments (conservative) and a Table-26
    envelope for negative moments; one-way: Table 12 coefficients;
    cantilever: wl²/2.
    """
    wu = 1.5 * (w_dead + w_live)
    D = D_m * 1000
    d = D - cover * 1000 - 5
    notes = []
    lx, ly = min(lx, ly), max(lx, ly)
    r = ly / lx if lx else 1.0
    if kind == "cantilever":
        Mx = wu * lx ** 2 / 2
        My = 0.0
        Mneg = Mx
        basic = 7
    elif kind == "two_way" and r <= 2.0:
        Mx = float(np.interp(r, _T27_R, _T27_AX)) * wu * lx ** 2
        My = float(np.interp(r, _T27_R, _T27_AY)) * wu * lx ** 2
        Mneg = float(np.interp(r, _T27_R, _T26_NEG)) * wu * lx ** 2 if continuous_edges else 0.0
        basic = 20 if continuous_edges == 0 else 26
    else:  # one-way
        if continuous_edges:
            Mx = (1.5 * w_dead * lx ** 2 / 12) + (1.5 * w_live * lx ** 2 / 10)
            Mneg = (1.5 * w_dead * lx ** 2 / 10) + (1.5 * w_live * lx ** 2 / 9)
            basic = 26
        else:
            Mx = wu * lx ** 2 / 8
            Mneg = 0.0
            basic = 20
        My = 0.0
        kind = "one_way" if kind != "cantilever" else kind
    ast_min = 0.0012 * 1000 * D

    def steel(M, dd):
        a = ast_singly(M * 1e6, fck, fy, 1000, dd)
        return max(a, ast_min)

    ax = steel(Mx, d)
    ay = steel(My, d - 10) if My else ast_min
    an = steel(Mneg, d) if Mneg else 0.0
    ok = all(math.isfinite(v) for v in (ax, ay, an))
    if not ok:
        notes.append("section inadequate – increase thickness")

    def spacing(ast):
        if not math.isfinite(ast) or ast <= 0:
            return "-"
        for dia in (8, 10, 12):
            s = 1000 * math.pi * dia * dia / 4 / ast
            if s >= 100:
                return f"T{dia} @ {int(min(s, 3 * d, 300) // 10 * 10)} c/c"
        return "T12 @ 100 c/c (check)"

    fs = 0.58 * fy
    pt = 100 * ax / (1000 * d) if math.isfinite(ax) else 1.0
    allowed = basic * deflection_mf(pt, fs)
    actual = lx * 1000 / d
    dok = actual <= allowed
    if kind == "two_way" and lx <= 3.5 and w_live <= 3.0:
        # IS 456 cl 24.1 note 2: span/overall depth 35 (SS) / 40 (continuous) x 0.8 for HYSD
        lim = (40 if continuous_edges else 35) * (0.8 if fy > 250 else 1.0)
        allowed, actual = lim, lx * 1000 / D
        dok = actual <= allowed
    if not dok:
        notes.append(f"deflection: L/d = {actual:.1f} > {allowed:.1f}")
    return SlabResult(mark, lx, ly, kind, D, Mx, My, Mneg, spacing(ax), spacing(ay) if My else spacing(ast_min),
                      spacing(an) if Mneg else "-", dok, ok and dok, notes)
