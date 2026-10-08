"""IS 456:2000 beam design: flexure (cl 38 / Annex G), shear (cl 40) and deflection (cl 23.2)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .common import ast_singly, fsc_doubly, mu_lim, tau_c, tau_c_max


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
        return FlexureResult(
            Mu_kNm, ast, 0.0, False, ast <= ast_max, "" if ast <= ast_max else "exceeds 4% – increase section"
        )
    fsc = fsc_doubly(fy, dc / d)
    asc = (Mu - ml) / ((fsc - 0.446 * fck) * (d - dc))
    ast = ast_singly(ml, fck, fy, b_mm, d) + asc * fsc / (0.87 * fy)
    ok = ast <= ast_max and asc <= ast_max
    return FlexureResult(
        Mu_kNm, ast, asc, True, ok, "doubly reinforced" + ("" if ok else " – exceeds 4%, increase section")
    )


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


def shear(
    Vu_kN: float, fck: float, fy: float, b_mm: float, d_mm: float, pt: float, dia: int = 8, legs: int = 2
) -> ShearResult:
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


def select_bars(
    area: float, width_mm: float, cover_mm: float, min_bars: int = 2, max_layers: int = 1, dias=(12, 16, 20, 25, 32)
) -> tuple[int, int, float]:
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


# ================================================================ torsion (cl 41)
@dataclass
class TorsionResult:
    Tu: float  # kN·m
    Ve: float  # kN, equivalent shear (cl 41.3.1)
    tau_ve: float  # N/mm²
    Mt: float  # kN·m, equivalent moment from torsion (cl 41.4.2)
    Me1_sag: float  # kN·m, tension steel at the bottom
    Me1_hog: float  # kN·m, tension steel at the top
    Me2_sag: float  # kN·m, compression-face (top) steel needed when Mt > Mu,sag
    Me2_hog: float
    legs: int
    dia: int
    spacing: float  # mm
    ok: bool
    side_face: str
    note: str = ""


def torsion_design(
    Tu_kNm: float,
    Vu_kN: float,
    Mu_sag: float,
    Mu_hog: float,
    b_mm: float,
    D_mm: float,
    cover_mm: float,
    fck: float,
    fy: float,
    fyv: float,
    pt: float,
    long_dia: int = 16,
) -> TorsionResult:
    """Beams under torsion + shear + bending, IS 456 cl 41 (limit state, skew-bending theory).

    * cl 41.3.1  Ve = Vu + 1.6 Tu/b;  τve = Ve/(b d) ≤ τc,max (Table 20)
    * cl 41.4.2  Mt = Tu (1 + D/b)/1.7;  Me1 = Mu + Mt (tension face);  Me2 = Mt − Mu (if Mt > Mu)
    * cl 41.4.3  Asv = Tu sv/(b1 d1 0.87fy) + Vu sv/(2.5 d1 0.87fy) ≥ (τve − τc) b sv/(0.87fy)
    * cl 26.5.1.7 closed stirrups, sv ≤ min(x1, (x1 + y1)/4, 300)
    * cl 26.5.1.3 / 26.5.1.7 (b) side-face bars when D > 450 mm (0.1 % of web area per face)
    Units: kN, kN·m, mm, MPa.  ``Mu_hog`` as a positive magnitude.
    """
    fyv = min(fyv, 415.0)  # cl 26.5.1.6 (stirrup fy capped as for shear)
    Tu, Vu = abs(Tu_kNm), abs(Vu_kN)
    d = D_mm - cover_mm - 8 - long_dia / 2
    Ve = Vu + 1.6 * Tu * 1e3 / b_mm  # Tu (kN·m → kN·mm) / b (mm) = kN
    tve = Ve * 1e3 / (b_mm * d)
    Mt = Tu * (1 + D_mm / b_mm) / 1.7
    me1s, me1h = abs(Mu_sag) + Mt, abs(Mu_hog) + Mt
    me2s = max(Mt - abs(Mu_sag), 0.0)
    me2h = max(Mt - abs(Mu_hog), 0.0)
    tc = tau_c(pt, fck)
    side = ""
    if D_mm > 450:
        a_side = 0.001 * b_mm * (D_mm - 2 * cover_mm) / 2  # each face (cl 26.5.1.3: 0.1 % of web area)
        n = max(2, math.ceil((D_mm - 2 * cover_mm - 100) / 300))  # ≤ 300 mm apart
        dia_s = 10 if a_side / n <= 78.5 else 12
        side = f"{n}-T{dia_s} each face (side-face, cl 26.5.1.3)"
    if tve > tau_c_max(fck):
        return TorsionResult(
            Tu,
            Ve,
            tve,
            Mt,
            me1s,
            me1h,
            me2s,
            me2h,
            2,
            8,
            0.0,
            False,
            side,
            "τve > τc,max under torsion – increase section (cl 41.3.1)",
        )
    x1 = b_mm - 2 * cover_mm  # stirrup outer dimensions
    y1 = D_mm - 2 * cover_mm
    b1 = x1 - 8 - long_dia  # centre-to-centre of corner bars
    d1 = y1 - 8 - long_dia
    s_max = min(x1, (x1 + y1) / 4, 300.0)
    for dia in (8, 10, 12):
        asv = 2 * math.pi * dia * dia / 4  # two legs of a closed stirrup
        per_mm = Tu * 1e6 / (b1 * d1 * 0.87 * fyv) + Vu * 1e3 / (2.5 * d1 * 0.87 * fyv)  # mm²/mm
        per_mm = max(per_mm, (tve - tc) * b_mm / (0.87 * fyv), 0.4 * b_mm / (0.87 * fyv))
        s = min(asv / per_mm, s_max)
        if s >= 75:
            return TorsionResult(
                Tu, Ve, tve, Mt, me1s, me1h, me2s, me2h, 2, dia, float(math.floor(s / 25) * 25), True, side
            )
    return TorsionResult(
        Tu,
        Ve,
        tve,
        Mt,
        me1s,
        me1h,
        me2s,
        me2h,
        2,
        12,
        0.0,
        False,
        side,
        "torsion stirrup spacing < 75 mm – increase section",
    )
