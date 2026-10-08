"""IS 456:2000 column design: strain-compatibility P-M curves, biaxial bending (cl 39.6),
minimum eccentricity (cl 25.4), slenderness (cl 39.7) and FrameWin auto-sizing."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .common import BarSet, Links


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


def interaction_curve(
    b: float, D: float, As: float, fck: float, fy: float, cover: float, n_points: int = 90, n_strips: int = 40
) -> tuple[np.ndarray, np.ndarray]:
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
    xus = D * np.concatenate(
        [np.geomspace(0.02, 1.0, n_points // 2, endpoint=False), np.geomspace(1.0, 40.0, n_points - n_points // 2)]
    )
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
    main_bars: BarSet | None = None
    tie: Links | None = None


def biaxial_ratio(
    Pu: float, Mux: float, Muy: float, b: float, D: float, As: float, fck: float, fy: float, cover: float, curves=None
) -> float:
    """Interaction value (Mux/Mux1)^an + (Muy/Muy1)^an (cl 39.6). Units N, N·mm, mm."""
    Puz = puz(b, D, As, fck, fy)
    if Pu >= Puz:
        return 9.99
    (px, mx), (py, my) = curves or (
        interaction_curve(b, D, As, fck, fy, cover),
        interaction_curve(D, b, As, fck, fy, cover),
    )
    mux1 = float(np.interp(Pu, px, mx))
    muy1 = float(np.interp(Pu, py, my))
    r = Pu / Puz
    an = 1.0 if r <= 0.2 else (2.0 if r >= 0.8 else 1.0 + (r - 0.2) / 0.6)
    if mux1 <= 0 or muy1 <= 0:
        return 9.99
    return (abs(Mux) / mux1) ** an + (abs(Muy) / muy1) ** an


def design_column(
    demands: list[tuple[str, float, float, float]],
    b_m: float,
    D_m: float,
    L_m: float,
    fck: float,
    fy: float,
    cover_m: float = 0.04,
    pmin: float = 0.8,
    pmax: float = 4.0,
    k_eff: float = 1.0,
    min_bar: int = 12,
) -> ColumnCheck:
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
                r = max(
                    biaxial_ratio(Pu, max(Mx, Pu * ex_min_x) + ax, My + ay, b, D, As, fck, fy, cover, curves),
                    biaxial_ratio(Pu, Mx + ax, max(My, Pu * ex_min_y) + ay, b, D, As, fck, fy, cover, curves),
                )
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
    n, dia, prov = column_bars(As, b, D, min_bar)
    tie_dia = max(8, math.ceil(dia / 4 / 2) * 2)
    tie_sp = min(b, D, 16 * dia, 300)
    main = BarSet(n, dia)
    tie = Links(2, tie_dia, float(int(tie_sp // 25 * 25)))
    return ColumnCheck(
        ok, p, As, ratio, gov, f"{main} ({prov:.0f} mm²)", f"T{tie_dia} @ {int(tie.spacing)} c/c", notes, main, tie
    )


def column_bars(As: float, b: float, D: float, min_dia: int = 12) -> tuple[int, int, float]:
    """Even number of bars (≥ 4, ≤ 300 mm apart, cl 26.5.3.1) of one diameter ≥ ``min_dia``.

    Ductile columns use ``min_dia`` = 16 so that the IS 13920 cl 8.2 hoop spacing limit
    6 db (96 mm) stays above the practical 75 mm minimum."""
    best = None
    for dia in (d for d in (12, 16, 20, 25, 32) if d >= min_dia):
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


def autosize_depth(
    Pu_kN: float,
    b_m: float,
    fck: float,
    fy: float,
    pct: float = 0.8,
    moment_factor: float = 1.25,
    step: float = 0.05,
    min_d: float | None = None,
) -> float:
    """Column depth for factored axial load (FrameWin AUTOSIZE): Pu·mf <= 0.4fck Ac + 0.67 fy Asc."""
    p = pct / 100
    stress = 0.4 * fck * (1 - p) + 0.67 * fy * p  # N/mm^2
    A = Pu_kN * 1e3 * moment_factor / stress / 1e6  # m^2
    d = max(A / b_m, min_d or b_m)
    return round(math.ceil(d / step - 1e-9) * step, 3)
