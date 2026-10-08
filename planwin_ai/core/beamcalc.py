"""Load representation and 1-D beam statics.

Loads are expressed in beam-local coordinate ``x`` (0 = start, L = end),
downward positive.  Two load cases are tracked separately everywhere:
``"D"`` (dead) and ``"L"`` (live), mirroring legacy PlanWin output.

Two analysis modes:

* :func:`simple_span_reactions` – each span between consecutive supports is
  treated as simply supported (overhangs attach to the end spans).  This is
  the classic PlanWin load-takedown assumption.
* :func:`continuous_beam` – stiffness solution with pinned supports
  (optionally fixed ends) used for preliminary bending design.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CASES = ("D", "L")


@dataclass
class LinLoad:
    a: float
    b: float
    w1: float
    w2: float
    case: str = "D"
    src: str = ""

    def resultant(self, lo: float = -1e18, hi: float = 1e18) -> tuple[float, float]:
        """Force and centroid of the part of the load inside [lo, hi]."""
        a, b = max(self.a, lo), min(self.b, hi)
        if b - a <= 1e-12 or self.b - self.a <= 1e-12:
            return 0.0, 0.5 * (a + b)
        wa = self.w_at(a)
        wb = self.w_at(b)
        L = b - a
        F = 0.5 * (wa + wb) * L
        if abs(wa + wb) < 1e-15:
            return 0.0, 0.5 * (a + b)
        xc = a + L * (wa + 2 * wb) / (3 * (wa + wb))
        return F, xc

    def w_at(self, x: float) -> float:
        if self.b - self.a <= 1e-12:
            return self.w1
        t = (x - self.a) / (self.b - self.a)
        return self.w1 + t * (self.w2 - self.w1)


@dataclass
class PtLoad:
    x: float
    P: float
    case: str = "D"
    src: str = ""


def total(loads, case: str | None = None) -> float:
    s = 0.0
    for ld in loads:
        if case and ld.case != case:
            continue
        s += ld.resultant()[0] if isinstance(ld, LinLoad) else ld.P
    return s


def simple_span_reactions(L: float, supports: list[float], loads, case: str) -> list[float]:
    """Reactions (upward +) at each support using simple spans.

    ``supports`` must be sorted.  With a single support the whole beam is a
    cantilever and the support takes the full load.
    """
    n = len(supports)
    R = [0.0] * n
    if n == 0:
        return R
    loads = [ld for ld in loads if ld.case == case]
    if n == 1:
        R[0] = total(loads)
        return R
    for i in range(n - 1):
        xa, xb = supports[i], supports[i + 1]
        lo = 0.0 if i == 0 else xa
        hi = L if i == n - 2 else xb
        Fsum = Msum = 0.0
        for ld in loads:
            if isinstance(ld, LinLoad):
                F, xc = ld.resultant(lo, hi)
            else:
                inside = lo <= ld.x < hi or (i == n - 2 and abs(ld.x - hi) < 1e-9)
                if not inside:
                    continue
                F, xc = ld.P, ld.x
            Fsum += F
            Msum += F * (xc - xa)
        rb = Msum / (xb - xa)
        R[i] += Fsum - rb
        R[i + 1] += rb
    return R


def continuous_beam(L: float, supports: list[float], loads, case: str | None = None,
                    fixed_start: bool = False, fixed_end: bool = False, max_el: float | None = None) -> list[float]:
    """Reactions of a prismatic continuous beam on rigid pinned supports.

    Uses Euler-Bernoulli elements; reactions are independent of EI for a
    prismatic member so EI = 1 is used.  Returns reactions (upward +) in the
    order of ``supports``.  Requires at least two supports (or one fixed).
    """
    sel = [ld for ld in loads if case is None or ld.case == case]
    if not supports:
        return []
    max_el = max_el or max(L / 40.0, 0.05)
    xs = {0.0, L, *supports}
    for ld in sel:
        if isinstance(ld, LinLoad):
            xs.update((min(max(ld.a, 0), L), min(max(ld.b, 0), L)))
        else:
            xs.add(min(max(ld.x, 0), L))
    base = sorted(xs)
    nodes = [base[0]]
    for x in base[1:]:
        gap = x - nodes[-1]
        if gap < 1e-9:
            continue
        k = int(np.ceil(gap / max_el))
        start = nodes[-1]
        nodes.extend([start + gap * (j + 1) / k for j in range(k)])
    nodes = np.array(nodes)
    nn = len(nodes)
    ndof = 2 * nn
    K = np.zeros((ndof, ndof))
    F = np.zeros(ndof)
    for e in range(nn - 1):
        h = nodes[e + 1] - nodes[e]
        k = np.array([[12, 6 * h, -12, 6 * h], [6 * h, 4 * h * h, -6 * h, 2 * h * h],
                      [-12, -6 * h, 12, -6 * h], [6 * h, 2 * h * h, -6 * h, 4 * h * h]]) / h ** 3
        dofs = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
        K[np.ix_(dofs, dofs)] += k
        xa, xb = nodes[e], nodes[e + 1]
        for ld in sel:
            if not isinstance(ld, LinLoad):
                continue
            a, b = max(ld.a, xa), min(ld.b, xb)
            if b - a < 1e-12:
                continue
            if abs(a - xa) < 1e-9 and abs(b - xb) < 1e-9:
                wi, wj = ld.w_at(xa), ld.w_at(xb)
                f = np.array([h * (7 * wi + 3 * wj) / 20, h * h * (3 * wi + 2 * wj) / 60,
                              h * (3 * wi + 7 * wj) / 20, -h * h * (2 * wi + 3 * wj) / 60])
            else:  # partial cover: lump resultant with lever rule
                Fr, xc = ld.resultant(a, b)
                t = (xc - xa) / h
                f = np.array([Fr * (1 - t), 0.0, Fr * t, 0.0])
            F[dofs] -= f  # downward load -> negative (upward +)
    for ld in sel:
        if isinstance(ld, PtLoad):
            i = int(np.argmin(np.abs(nodes - ld.x)))
            F[2 * i] -= ld.P
    fixed = []
    sup_nodes = [int(np.argmin(np.abs(nodes - s))) for s in supports]
    fixed.extend(2 * i for i in sup_nodes)
    if fixed_start:
        fixed.append(2 * sup_nodes[0] + 1)
    if fixed_end:
        fixed.append(2 * sup_nodes[-1] + 1)
    free = [i for i in range(ndof) if i not in set(fixed)]
    u = np.zeros(ndof)
    try:
        u[free] = np.linalg.solve(K[np.ix_(free, free)], F[free])
    except np.linalg.LinAlgError:
        return simple_span_reactions(L, supports, sel, case or "D")
    R = K @ u - F
    return [float(R[2 * i]) for i in sup_nodes]


def diagrams(L: float, loads, reactions: list[tuple[float, float]], case: str | None = None,
             n: int = 81) -> dict[str, np.ndarray]:
    """Shear and moment along the beam by statics.

    ``reactions`` – list of (x, R) upward forces.  Sagging moment positive,
    shear positive when the left part is pushed up.
    Returns dict with x, V, M sampled at ``n`` points plus every support.
    """
    sel = [ld for ld in loads if case is None or ld.case == case]
    xs = set(np.linspace(0.0, L, n).tolist())
    for x, _ in reactions:
        xs.update((max(x - 1e-6, 0.0), min(x + 1e-6, L)))
    x = np.array(sorted(xs))
    V = np.zeros_like(x)
    M = np.zeros_like(x)
    for xr, R in reactions:
        m = x > xr
        V[m] += R
        M[m] += R * (x[m] - xr)
    for ld in sel:
        if isinstance(ld, PtLoad):
            m = x > ld.x
            V[m] -= ld.P
            M[m] -= ld.P * (x[m] - ld.x)
        else:
            for i, xi in enumerate(x):
                if xi <= ld.a:
                    continue
                F, xc = ld.resultant(ld.a, min(ld.b, xi))
                V[i] -= F
                M[i] -= F * (xi - xc)
    return {"x": x, "V": V, "M": M}


def eighth_points(L: float, dia: dict[str, np.ndarray]) -> list[dict[str, float]]:
    """Values at the nine 1/8 sections used by legacy PlanWin reports."""
    out = []
    for k in range(9):
        xi = L * k / 8
        out.append({"x": xi, "M": float(np.interp(xi, dia["x"], dia["M"])),
                    "V": float(np.interp(xi, dia["x"], dia["V"]))})
    return out
