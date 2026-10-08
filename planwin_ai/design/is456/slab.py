"""IS 456:2000 slab design by coefficient methods (Annex D Tables 26/27, cl 24)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .beam import deflection_mf
from .common import ast_singly

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


def design_slab(
    mark: str,
    lx: float,
    ly: float,
    w_dead: float,
    w_live: float,
    D_m: float,
    fck: float,
    fy: float,
    kind: str,
    continuous_edges: int,
    cover: float = 0.02,
) -> SlabResult:
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
        Mx = wu * lx**2 / 2
        My = 0.0
        Mneg = Mx
        basic = 7
    elif kind == "two_way" and r <= 2.0:
        Mx = float(np.interp(r, _T27_R, _T27_AX)) * wu * lx**2
        My = float(np.interp(r, _T27_R, _T27_AY)) * wu * lx**2
        Mneg = float(np.interp(r, _T27_R, _T26_NEG)) * wu * lx**2 if continuous_edges else 0.0
        basic = 20 if continuous_edges == 0 else 26
    else:  # one-way
        if continuous_edges:
            Mx = (1.5 * w_dead * lx**2 / 12) + (1.5 * w_live * lx**2 / 10)
            Mneg = (1.5 * w_dead * lx**2 / 10) + (1.5 * w_live * lx**2 / 9)
            basic = 26
        else:
            Mx = wu * lx**2 / 8
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
    return SlabResult(
        mark,
        lx,
        ly,
        kind,
        D,
        Mx,
        My,
        Mneg,
        spacing(ax),
        spacing(ay) if My else spacing(ast_min),
        spacing(an) if Mneg else "-",
        dok,
        ok and dok,
        notes,
    )
