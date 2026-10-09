"""Lateral loads: IS 1893 (Part 1):2016 equivalent static and IS 875 (Part 3):2015 wind.

Functions return per-level storey forces (kN) that the frame builder
distributes to the nodes of each level.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

ZONE_FACTOR = {"II": 0.10, "III": 0.16, "IV": 0.24, "V": 0.36}
MIN_AH = {"II": 0.007, "III": 0.011, "IV": 0.016, "V": 0.024}  # IS 1893:2016 Table 7 note
DAMPING_FACTOR = {0.0: 3.2, 0.02: 1.4, 0.05: 1.0, 0.07: 0.9, 0.10: 0.8, 0.15: 0.7, 0.20: 0.6, 0.25: 0.55, 0.30: 0.5}


def sa_by_g(T: float, soil: str) -> float:
    """Design acceleration coefficient for equivalent static method (5 % damping)."""
    soil = soil.lower()
    if soil in ("hard", "rock", "i", "1"):
        return 2.5 if T <= 0.40 else (1.0 / T if T <= 4.0 else 0.25)
    if soil in ("soft", "iii", "3"):
        return 2.5 if T <= 0.67 else (1.67 / T if T <= 4.0 else 0.42)
    return 2.5 if T <= 0.55 else (1.36 / T if T <= 4.0 else 0.34)


def damping_factor(zeta: float) -> float:
    keys = sorted(DAMPING_FACTOR)
    if zeta <= keys[0]:
        return DAMPING_FACTOR[keys[0]]
    for a, b in zip(keys, keys[1:]):
        if a <= zeta <= b:
            return DAMPING_FACTOR[a] + (DAMPING_FACTOR[b] - DAMPING_FACTOR[a]) * (zeta - a) / (b - a)
    return DAMPING_FACTOR[keys[-1]]


@dataclass
class SeismicResult:
    direction: str
    T: float
    sa_g: float
    Ah: float
    W: float
    Vb: float
    forces: list[float]  # per level index (0..n); 0 for base and levels below seismic base
    heights: list[float]


def seismic_static(
    weights: list[float],
    elevations: list[float],
    base_level: int,
    zone: str,
    importance: float,
    R: float,
    soil: str,
    damping: float,
    infill: bool,
    base_dim: float,
    direction: str,
) -> SeismicResult:
    """Equivalent static base shear and vertical distribution (cl 7.6 / 7.7).

    ``weights[i]`` – seismic weight lumped at level i (index 0 = footing).
    ``base_dim`` – plan dimension (m) along the direction of shaking.
    """
    z0 = elevations[base_level] if 0 <= base_level < len(elevations) else 0.0
    h = max(elevations[-1] - z0, 0.1)
    if infill:
        T = 0.09 * h / math.sqrt(max(base_dim, 0.5))
    else:
        T = 0.075 * h**0.75
    sa = sa_by_g(T, soil) * damping_factor(damping)
    Z = ZONE_FACTOR.get(zone.upper(), 0.16)
    Ah = max((Z / 2) * (importance / R) * sa, MIN_AH.get(zone.upper(), 0.0))
    if T <= 0.1:  # cl 6.4.2: very stiff structures, Ah not less than Z/2 whatever I/R
        Ah = max(Ah, Z / 2)
    idx = [i for i in range(len(weights)) if i > base_level]
    W = sum(weights[i] for i in idx)
    Vb = Ah * W
    hs = [max(elevations[i] - z0, 0.0) for i in range(len(elevations))]
    denom = sum(weights[i] * hs[i] ** 2 for i in idx) or 1.0
    forces = [0.0] * len(weights)
    for i in idx:
        forces[i] = Vb * weights[i] * hs[i] ** 2 / denom
    return SeismicResult(direction, T, sa, Ah, W, Vb, forces, hs)


# ------------------------------------------------------------------ wind
K2_HEIGHTS = [10, 15, 20, 30, 50, 100, 150, 200, 250, 300, 350, 400, 450, 500]
K2_TABLE = {  # IS 875 (Part 3):2015 Table 2, terrain categories 1..4
    1: [1.05, 1.09, 1.12, 1.15, 1.20, 1.26, 1.30, 1.32, 1.34, 1.35, 1.37, 1.38, 1.39, 1.40],
    2: [1.00, 1.05, 1.07, 1.12, 1.17, 1.24, 1.28, 1.30, 1.32, 1.34, 1.36, 1.37, 1.38, 1.39],
    3: [0.91, 0.97, 1.01, 1.06, 1.12, 1.20, 1.24, 1.27, 1.29, 1.31, 1.32, 1.34, 1.35, 1.36],
    4: [0.80, 0.80, 0.80, 0.97, 1.10, 1.20, 1.24, 1.27, 1.28, 1.30, 1.31, 1.32, 1.33, 1.34],
}


def k2(z: float, terrain: int) -> float:
    row = K2_TABLE.get(int(terrain), K2_TABLE[3])
    if z <= K2_HEIGHTS[0]:
        return row[0]
    for (h1, h2), (v1, v2) in zip(zip(K2_HEIGHTS, K2_HEIGHTS[1:]), zip(row, row[1:])):
        if h1 <= z <= h2:
            return v1 + (v2 - v1) * (z - h1) / (h2 - h1)
    return row[-1]


def design_pressure(z: float, vb: float, terrain: int, k1=1.0, k3=1.0, k4=1.0, kd=0.9, ka=1.0, kc=0.9) -> float:
    """Design wind pressure pd (kN/m^2) at height z (cl 6.3, 7.2)."""
    vz = vb * k1 * k2(z, terrain) * k3 * k4
    pz = 0.6 * vz**2 / 1000.0
    pd = max(kd * ka * kc * pz, 0.7 * pz)
    return pd


@dataclass
class WindResult:
    direction: str
    forces: list[float]
    pressures: list[float]


def wind_storey_forces(
    elevations: list[float],
    width_perp: float,
    vb: float,
    terrain: int,
    cf: float,
    parapet: float,
    below_ground: float,
    direction: str,
    **k,
) -> WindResult:
    """Storey forces from tributary heights (half storey below + half above)."""
    n = len(elevations)
    forces = [0.0] * n
    pres = [0.0] * n
    top = elevations[-1]
    for i in range(1, n):
        z = elevations[i]
        lo = max((elevations[i - 1] + z) / 2, below_ground)
        hi = (z + elevations[i + 1]) / 2 if i < n - 1 else z + parapet
        if hi <= lo:
            continue
        z_eff = (z if i < n - 1 else top) - below_ground  # height above ground
        pd = design_pressure(max(z_eff, 1.0), vb, terrain, **k)
        pres[i] = pd
        forces[i] = cf * pd * width_perp * (hi - lo)
    return WindResult(direction, forces, pres)
