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
