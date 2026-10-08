"""Modal analysis and IS 1893 (Part 1):2016 response spectrum method (cl 7.7).

Method
------
* Masses: the seismic weight of each level above the seismic base (the same weights as
  the equivalent static method, cl 7.4).  On a rigid-diaphragm floor the mass sits at the
  master joint (centre of mass) with translational mass W/g in X and Y and a rotational
  inertia from the floor's gravity-load distribution; otherwise it is lumped at the column
  and wall joints in proportion to their gravity load.
* The stiffness is statically condensed onto the mass degrees of freedom with the already
  factorised frame stiffness (flexibility under unit loads, inverted), and the generalised
  eigenproblem K φ = ω² M φ is solved exactly (dense, small).
* Spectrum: design acceleration coefficient Sa/g for the response spectrum method
  (cl 6.4.2 (a)) times the damping factor (Table 3); Ak = Z/2 · I/R · Sa/g.
* Enough modes are used to reach 90 % modal mass in each direction (cl 7.7.5.2) and
  responses are combined by CQC (cl 7.7.5.4 (a)).
* If the dynamic base shear is below the equivalent static base shear computed with the
  empirical period (cl 7.6.2), every response is scaled up by V̄B / VB (cl 7.7.3).

Combined responses are positive envelopes.  Load combinations use them with the sign of
their factor (±RSX), which is the usual way of combining spectral results with gravity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import scipy.linalg as sla

from .lateral import ZONE_FACTOR, damping_factor
from .solver import section_forces

if TYPE_CHECKING:
    from .frame import FrameModel

G_ACC = 9.80665  # m/s²


def sa_by_g_rsa(T: float, soil: str) -> float:
    """Sa/g for the response spectrum method, 5 % damping (IS 1893-1:2016 cl 6.4.2 (a))."""
    soil = soil.lower()
    if soil in ("hard", "rock", "i", "1"):
        t2, c, tail = 0.40, 1.00, 0.25
    elif soil in ("soft", "iii", "3"):
        t2, c, tail = 0.67, 1.67, 0.42
    else:
        t2, c, tail = 0.55, 1.36, 0.34
    if T < 0.10:
        return 1.0 + 15.0 * T
    if T <= t2:
        return 2.5
    if T <= 4.0:
        return c / T
    return tail


def cqc_matrix(omega: np.ndarray, zeta: float) -> np.ndarray:
    """CQC cross-modal coefficients ρij (cl 7.7.5.4 (a)), equal damping ζ in all modes."""
    beta = omega[None, :] / omega[:, None]
    num = 8 * zeta**2 * (1 + beta) * beta**1.5
    den = (1 - beta**2) ** 2 + 4 * zeta**2 * beta * (1 + beta) ** 2
    return num / den


def cqc(values: np.ndarray, rho: np.ndarray) -> np.ndarray:
    """Combine modal values (…, modes) → positive envelope (…)."""
    v = np.asarray(values, dtype=float)
    return np.sqrt(np.maximum(np.einsum("...i,ij,...j->...", v, rho, v), 0.0))


def dynamic_analysis_required(project, height: float, regular: bool) -> bool:
    """IS 1893-1:2016 cl 7.7.1: linear dynamic analysis for all buildings other than
    regular buildings lower than 15 m in seismic zone II."""
    return not (regular and height < 15.0 and project.seismic.zone.upper() == "II")


@dataclass
class Mode:
    number: int
    period: float
    omega: float
    phi: np.ndarray  # shape over the mass dofs
    mass_x: float  # effective modal mass ratio (0..1)
    mass_y: float
    mass_rz: float


@dataclass
class ModalResult:
    modes: list[Mode]
    total_mass: float  # kN·s²/m (= t)
    mass_dofs: list[tuple[int, int]]  # (node id, dof index 0=ux 1=uy 5=rz)
    masses: np.ndarray
    shapes_full: np.ndarray  # (n_full, n_modes) displacement under the modal inertia pattern (unit Γ·A)
    unit_disp: np.ndarray  # (n_full, n_mass) displacement for unit loads at the mass dofs
    kc: np.ndarray  # condensed stiffness

    def cumulative(self, axis: str) -> list[float]:
        acc, out = 0.0, []
        for m in self.modes:
            acc += getattr(m, f"mass_{axis}")
            out.append(acc)
        return out


@dataclass
class RSResult:
    """Response spectrum results for one direction ("RSX" or "RSY")."""

    case: str
    axis: int  # 0 = X, 1 = Y
    modes_used: int
    scale: float
    vb_dynamic: float  # before scaling
    vb_static: float
    storey_shear: list[float]  # scaled CQC storey shears per level index
    storey_force: list[float]  # equivalent storey forces (differences of shears), for exports
    member_modal: dict[int, np.ndarray] = field(default_factory=dict)  # mid -> (12, modes) local end forces
    reaction_modal: dict[int, np.ndarray] = field(default_factory=dict)  # nid -> (6, modes)
    disp_modal: np.ndarray | None = None  # (n_full, modes)
    rho: np.ndarray | None = None
    _station_cache: dict = field(default_factory=dict, repr=False)

    # ----------------------------------------------------------------- envelopes
    def member_envelope(self, model: FrameModel, mid: int, xs: np.ndarray) -> dict[str, np.ndarray]:
        key = (mid, len(xs), float(xs[-1]) if len(xs) else 0.0)
        if key in self._station_cache:
            return self._station_cache[key]
        m = model.members[mid]
        fk = self.member_modal[mid]
        comps = {k: np.zeros((len(xs), fk.shape[1])) for k in ("N", "Vy", "Vz", "T", "My", "Mz")}
        for j in range(fk.shape[1]):
            sf = section_forces(m, model.nodes, fk[:, j], [], xs)
            for k in comps:
                comps[k][:, j] = sf[k]
        env = {k: self.scale * cqc(v, self.rho) for k, v in comps.items()}
        self._station_cache[key] = env
        return env

    def reaction_envelope(self, nid: int) -> np.ndarray:
        r = self.reaction_modal.get(nid)
        return np.zeros(6) if r is None else self.scale * cqc(r, self.rho)

    def displacement_envelope(self, index: int) -> np.ndarray:
        return self.scale * cqc(self.disp_modal[6 * index : 6 * index + 6, :], self.rho)

    def drift_envelope(self, i_top: int, i_bot: int, axis: int) -> float:
        """CQC of the modal inter-storey drift between two joints (by full node index)."""
        d = self.disp_modal[6 * i_top + axis, :] - self.disp_modal[6 * i_bot + axis, :]
        return float(self.scale * cqc(d, self.rho))


# --------------------------------------------------------------------------- modal
def _mass_model(model: FrameModel) -> tuple[list[tuple[int, int]], np.ndarray]:
    """Mass degrees of freedom and masses (t, t·m²) above the seismic base."""
    weights = [lv.weight for lv in model.levels]
    base = model.p.seismic.base_level
    dofs: list[tuple[int, int]] = []
    masses: list[float] = []
    for i in range(1, len(model.levels)):
        if i <= base or weights[i] <= 0:
            continue
        lv = model.levels[i]
        mass = weights[i] / G_ACC
        pts = model._gravity_points(i)
        W = sum(w for _, _, w in pts)
        if lv.master is not None:
            mn = model.nodes[lv.master]
            if W > 1e-9:
                ip = sum(w * ((x - mn.x) ** 2 + (y - mn.y) ** 2) for x, y, w in pts) / W * mass
            else:  # uniform rectangle fallback
                plan = model.p.plan(lv.plan)
                x0, y0, x1, y1 = plan.extents()
                ip = mass * ((x1 - x0) ** 2 + (y1 - y0) ** 2) / 12.0
            for k, mm in ((0, mass), (1, mass), (5, max(ip, 1e-6 * mass))):
                dofs.append((lv.master, k))
                masses.append(mm)
            continue
        # no diaphragm: lump at column joints by their gravity-load share (matched by column mark)
        cols = list(lv.column_nodes.items())
        if not cols:
            continue
        res = lv.result
        share = {}
        for mark, nid in cols:
            cl = next((v for v in res.columns.values() if v.mark == mark), None) if res else None
            share[nid] = max(cl.dead + 0.25 * cl.live, 0.0) if cl else 0.0
        tot = sum(share.values())
        for _mark, nid in cols:
            frac = share[nid] / tot if tot > 1e-9 else 1.0 / len(cols)
            for k in (0, 1):
                dofs.append((nid, k))
                masses.append(mass * frac)
    return dofs, np.array(masses)


def modal_analysis(model: FrameModel) -> ModalResult:
    solver = model.solver
    if solver is None or solver.lu is None:
        raise RuntimeError("run the static analysis first")
    dofs, masses = _mass_model(model)
    if not dofs:
        raise ValueError("no seismic mass above the seismic base level")
    idx = solver.idx
    n_full = len(idx) * 6
    full = [6 * idx[nid] + k for nid, k in dofs]
    E = np.zeros((n_full, len(full)))
    for j, fd in enumerate(full):
        E[fd, j] = 1.0
    unit = solver.displacements(E)  # (n_full, n_mass)
    flex = unit[full, :]
    flex = 0.5 * (flex + flex.T)
    kc = np.linalg.inv(flex)
    kc = 0.5 * (kc + kc.T)
    M = np.diag(masses)
    w2, phi = sla.eigh(kc, M)
    w2 = np.maximum(w2, 1e-12)
    omega = np.sqrt(w2)
    # participation
    rx = np.array([1.0 if k == 0 else 0.0 for _, k in dofs])
    ry = np.array([1.0 if k == 1 else 0.0 for _, k in dofs])
    rr = np.array([1.0 if k == 5 else 0.0 for _, k in dofs])
    tot_x = float(rx @ M @ rx) or 1.0
    tot_y = float(ry @ M @ ry) or 1.0
    tot_r = float(rr @ M @ rr) or 1.0
    modes = []
    for j in range(len(omega)):
        p = phi[:, j]
        mm = float(p @ M @ p)
        modes.append(
            Mode(
                j + 1,
                2 * math.pi / omega[j],
                float(omega[j]),
                p,
                float((p @ M @ rx) ** 2 / mm / tot_x),
                float((p @ M @ ry) ** 2 / mm / tot_y),
                float((p @ M @ rr) ** 2 / mm / tot_r),
            )
        )
    shapes = unit @ (kc @ phi)  # full displacement of each mode shape (static condensation recovery)
    return ModalResult(modes, float(masses[rx > 0].sum()), dofs, masses, shapes, unit, kc)


def response_spectrum(model: FrameModel, modal: ModalResult, case: str, vb_static: float) -> RSResult:
    """Spectral response in X (``case`` "RSX") or Y ("RSY"), CQC-combined and scaled (cl 7.7.3)."""
    s = model.p.seismic
    axis = 0 if case.endswith("X") else 1
    solver = model.solver
    Z = ZONE_FACTOR.get(s.zone.upper(), 0.16)
    df = damping_factor(s.damping)
    dofs, masses = modal.mass_dofs, modal.masses
    r = np.array([1.0 if k == axis else 0.0 for _, k in dofs])
    # modes to 90 % participation in this direction (cl 7.7.5.2), at least 3 when available
    attr = "x" if axis == 0 else "y"
    cum = modal.cumulative(attr)
    n_use = next((i + 1 for i, c in enumerate(cum) if c >= 0.90), len(cum))
    n_use = min(max(n_use, min(3, len(cum))), len(cum))
    used = modal.modes[:n_use]
    omega = np.array([m.omega for m in used])
    rho = cqc_matrix(omega, s.damping)
    P = np.zeros((len(dofs), n_use))
    for j, m in enumerate(used):
        gamma = float(m.phi @ (masses * r)) / float(m.phi @ (masses * m.phi))
        A = Z / 2 * s.importance / s.response_reduction * sa_by_g_rsa(m.period, s.soil) * df
        P[:, j] = masses * m.phi * gamma * A * G_ACC  # modal inertia forces (kN, kN·m)
    U = modal.unit_disp @ P  # (n_full, modes)
    # storey shears per mode
    nlev = len(model.levels)
    lvl_of = {nid: model.nodes[nid].level for nid, _ in dofs}
    Fl = np.zeros((nlev, n_use))
    for row, (nid, k) in enumerate(dofs):
        if k == axis:
            Fl[lvl_of[nid], :] += P[row, :]
    shears = np.cumsum(Fl[::-1, :], axis=0)[::-1, :]  # V_i = Σ_{j>=i} F_j
    vb_dyn = float(cqc(shears[0], rho)) if nlev else 0.0
    scale = max(1.0, vb_static / vb_dyn) if vb_dyn > 1e-9 else 1.0
    storey_shear = [float(scale * cqc(shears[i], rho)) for i in range(nlev)]
    storey_force = [storey_shear[i] - (storey_shear[i + 1] if i + 1 < nlev else 0.0) for i in range(nlev)]
    res = RSResult(case, axis, n_use, scale, vb_dyn, vb_static, storey_shear, storey_force, rho=rho, disp_modal=U)
    for j in range(n_use):
        ef = solver.end_forces(U[:, j])
        for mid, f in ef.items():
            res.member_modal.setdefault(mid, np.zeros((12, n_use)))[:, j] = f
    KU = solver.K @ U
    for nid, nd in model.nodes.items():
        if nd.support:
            i = solver.idx[nid]
            res.reaction_modal[nid] = KU[6 * i : 6 * i + 6, :]
    return res
