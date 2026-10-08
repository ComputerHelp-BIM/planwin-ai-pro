"""IS 456:2000 isolated pad footing design (cl 34) with SBC, full-contact, punching and one-way shear."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .common import ast_singly, tau_c


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

    def bar_str(ast):
        for dia in (10, 12, 16, 20, 25):
            s_ = 1000 * math.pi * dia * dia / 4 / ast
            if s_ >= 100:
                return f"T{dia} @ {int(min(s_, 300) // 10 * 10)} c/c"
        return "T25 @ 100 c/c (check)"

    return FootingResult(
        round(L, 3),
        round(B, 3),
        round(D, 3),
        astL,
        astB,
        bar_str(astL),
        bar_str(astB),
        round(q_service, 1),
        ok and not any("could not" in n for n in notes),
        notes,
    )
