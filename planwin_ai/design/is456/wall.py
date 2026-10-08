"""RC structural (shear) wall design: IS 456:2000 cl 32 and IS 13920:2016 cl 10.

Units: N, mm, MPa unless stated.  In-plane behaviour governs and is designed here:

* proportions (IS 13920 cl 10.1.2 thickness ≥ 150 mm, cl 10.1.3 length ≥ 4 t),
* minimum vertical and horizontal steel (IS 13920 cl 10.1.5: 0.25 % both ways in ductile
  walls; IS 456 cl 32.5: 0.12 % vertical / 0.20 % horizontal for HYSD bars otherwise),
  two curtains when t > 200 mm (cl 10.1.7), bar diameter ≤ t/10 (cl 10.1.8) and spacing
  ≤ min(Lw/5, 3t, 450 mm) (cl 10.1.9),
* in-plane shear (IS 13920 cl 10.2 / IS 456 cl 32.4): τv = Vu/(t dw), dw = 0.8 Lw,
  τv ≤ τc,max, horizontal steel for (τv − τc),
* combined axial load and in-plane bending (cl 10.3) by strain compatibility with the
  vertical steel distributed uniformly along the wall,
* boundary elements (cl 10.4.1) where the extreme-fibre compressive stress under factored
  loads, from a linearly elastic model of the gross section, exceeds 0.2 fck.

Out-of-plane bending of the wall (IS 456 cl 32.2) is reported but not designed – walls
in the wide-column model carry little out-of-plane moment; check slender walls manually.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .column import _concrete_stress, _steel_stress
from .common import tau_c, tau_c_max


@dataclass
class WallCheck:
    ok: bool
    rho_v: float  # vertical steel ratio (both curtains together)
    rho_h: float  # horizontal steel ratio
    curtains: int
    vertical: str
    horizontal: str
    boundary: str
    ratio: float  # in-plane P–M utilisation at the adopted ρv
    tau_v: float
    tau_c: float
    notes: list[str] = field(default_factory=list)


def pm_curve(
    t: float,
    Lw: float,
    rho_v: float,
    fck: float,
    fy: float,
    cover: float = 40.0,
    n_points: int = 80,
    n_strips: int = 60,
) -> tuple[np.ndarray, np.ndarray]:
    """In-plane P–M interaction (N, N·mm) of a wall t × Lw with vertical steel ρv distributed
    uniformly along the length (IS 456 cl 38.1 / 39.1 strain compatibility)."""
    n_layers = max(int(Lw / 150), 4)
    ys = np.linspace(cover + 10, Lw - cover - 10, n_layers)
    As = rho_v * t * Lw
    as_ = np.full(n_layers, As / n_layers)
    strips = (np.arange(n_strips) + 0.5) * Lw / n_strips
    dA = t * Lw / n_strips
    xus = Lw * np.concatenate(
        [np.geomspace(0.02, 1.0, n_points // 2, endpoint=False), np.geomspace(1.0, 40.0, n_points - n_points // 2)]
    )
    top = np.where(xus <= Lw, 0.0035, 0.002 * xus / np.maximum(xus - 3 * Lw / 7, 1e-9))[:, None]
    eps = top * (xus[:, None] - strips[None, :]) / xus[:, None]
    eps_s = top * (xus[:, None] - ys[None, :]) / xus[:, None]
    fc = _concrete_stress(eps, fck)
    fs = _steel_stress(eps_s, fy) - np.where(eps_s > 0, _concrete_stress(eps_s, fck), 0.0)
    P = fc.sum(axis=1) * dA + fs @ as_
    M = np.maximum((fc * (Lw / 2 - strips)[None, :]).sum(axis=1) * dA + (fs * as_[None, :]) @ (Lw / 2 - ys), 0.0)
    puz = 0.45 * fck * (t * Lw - As) + 0.75 * fy * As
    keep = P < puz
    P = np.concatenate([[-0.87 * fy * As], P[keep], [puz]])
    M = np.concatenate([[0.0], M[keep], [0.0]])
    order = np.argsort(P)
    return P[order], M[order]


def _ratio(demands: list[tuple[float, float]], curve) -> float:
    P, M = curve
    worst = 0.0
    for Pu, Mu in demands:
        if Pu >= P[-1]:
            return 9.99
        cap = float(np.interp(Pu, P, M))
        worst = max(worst, 9.99 if cap <= 0 else abs(Mu) / cap)
    return worst


def _mesh(rho: float, t: float, curtains: int, s_max: float, dias=(8, 10, 12, 16, 20)) -> tuple[int, float]:
    """Bar diameter and spacing (mm) per curtain for steel ratio ρ (≤ t/10 diameter, cl 10.1.8)."""
    a_face = rho * t * 1000 / curtains  # mm² per metre per curtain
    for dia in dias:
        if dia > t / 10 + 1e-9:
            break
        s = 1000 * math.pi * dia**2 / 4 / a_face
        if s >= 100:
            return dia, float(int(min(s, s_max) // 10 * 10))
    dia = int(min(max(d for d in dias if d <= t / 10 + 1e-9) if any(d <= t / 10 for d in dias) else 8, 20))
    return dia, 100.0


def design_wall(
    demands: list[tuple[str, float, float, float]],
    t_m: float,
    Lw_m: float,
    fck: float,
    fy: float,
    fyv: float,
    ductile: bool,
    cover_m: float = 0.04,
) -> WallCheck:
    """``demands`` – (combo, Pu kN compression +, in-plane Mu kN·m, in-plane Vu kN) at wall sections."""
    t, Lw, cover = t_m * 1000, Lw_m * 1000, cover_m * 1000
    notes: list[str] = []
    ok = True
    if t < 150:
        notes.append("thickness < 150 mm (IS 13920 cl 10.1.2)")
        ok = False
    if Lw < 4 * t:
        notes.append("length < 4 × thickness – design as a column (IS 13920 cl 10.1.3)")
    rv_min, rh_min = (0.0025, 0.0025) if ductile else ((0.0012, 0.0020) if fy > 415 else (0.0015, 0.0025))
    curtains = 2 if (t > 200 or ductile and t >= 200) else 1
    # ---- shear (cl 10.2)
    dw = 0.8 * Lw
    Vu = max((abs(v) for *_, v in demands), default=0.0)
    tv = Vu * 1e3 / (t * dw)
    if tv > tau_c_max(fck):
        notes.append(f"τv = {tv:.2f} > τc,max = {tau_c_max(fck):.2f} N/mm² – thicken the wall (cl 10.2.2)")
        ok = False
    # ---- in-plane P–M: smallest ρv (0.1 % steps) that satisfies every demand
    pm = [(max(P, -1e9) * 1e3, M * 1e6) for _, P, M, _ in demands]
    rho_v, ratio = rv_min, 9.99
    for k in range(0, 60):
        rho = rv_min + 0.001 * k
        if rho > 0.04 + 1e-9:
            break
        ratio = _ratio(pm, pm_curve(t, Lw, rho, fck, fy, cover))
        rho_v = rho
        if ratio <= 1.0:
            break
    if ratio > 1.0:
        notes.append(f"in-plane P–M not satisfied at 4 % vertical steel (ratio {ratio:.2f}) – lengthen/thicken wall")
        ok = False
    tc = tau_c(100 * rho_v, fck)
    rho_h = max(rh_min, (tv - tc) / (0.87 * min(fyv, 415.0)) if tv > tc else 0.0)
    if ductile and Vu * 1e3 > 0.25 * math.sqrt(fck) * t * dw and curtains == 1:
        curtains = 2  # cl 10.1.7: two curtains when Vu > 0.25 √fck t dw
    s_max = min(Lw / 5, 3 * t, 450.0)  # cl 10.1.9
    dv, sv = _mesh(rho_v, t, curtains, s_max)
    dh, shz = _mesh(rho_h, t, curtains, s_max)
    face = "each face" if curtains == 2 else "single curtain"
    # ---- boundary elements (cl 10.4.1): σ = P/A + M/Z > 0.2 fck under factored loads
    Ag, Z = t * Lw, t * Lw**2 / 6
    sig = max((P * 1e3 / Ag + abs(M) * 1e6 / Z for _, P, M, _ in demands), default=0.0)
    if sig > 0.2 * fck:
        lb = max(300.0, 0.1 * Lw, 2 * t)
        boundary = (
            f"required (σ = {sig:.1f} > 0.2 fck = {0.2 * fck:.1f} N/mm²): confined zones ≥ {lb:.0f} mm "
            "at both ends with hoops as IS 13920 cl 8.1 (cl 10.4)"
        )
        notes.append("boundary elements required (IS 13920 cl 10.4) – verify their length and confinement")
    else:
        boundary = f"not required (σ = {sig:.1f} ≤ 0.2 fck)"
    return WallCheck(
        ok,
        rho_v,
        rho_h,
        curtains,
        f"T{dv} @ {int(sv)} c/c {face}",
        f"T{dh} @ {int(shz)} c/c {face}",
        boundary,
        ratio,
        tv,
        tc,
        notes,
    )
