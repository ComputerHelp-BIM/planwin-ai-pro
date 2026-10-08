"""IS 456:2000 tables and section formulae shared by all member types (SI: N, mm, MPa)."""

from __future__ import annotations

import math

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
