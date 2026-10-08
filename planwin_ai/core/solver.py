"""Linear-elastic 3-D frame solver (6 DOF per node, Euler-Bernoulli members).

Conventions
-----------
* Global axes: X, Y in plan, Z vertical (up).  Gravity acts in -Z.
* Member local axes: x along the member (n1 -> n2); for horizontal members
  local z is global +Z; for vertical members local y is the column's plan
  "b" direction.  Section width ``b`` lies along local y and depth ``d``
  along local z, so Iy = b d^3/12 is the major axis for beams.
* Member loads are consistent (Hermite) nodal loads, which reproduce exact
  fixed-end actions for prismatic members.

The solver factorises the stiffness matrix once and solves all primary
load cases together; load combinations are linear superpositions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla


@dataclass
class MLoad:
    """Linear member load in *global* direction components (kN/m)."""

    a: float
    b: float
    w1: tuple[float, float, float]
    w2: tuple[float, float, float]

    def scaled(self, k: float) -> MLoad:
        return MLoad(self.a, self.b, tuple(k * v for v in self.w1), tuple(k * v for v in self.w2))


@dataclass
class MPoint:
    x: float
    P: tuple[float, float, float]  # global components (kN)

    def scaled(self, k: float) -> MPoint:
        return MPoint(self.x, tuple(k * v for v in self.P))


@dataclass
class MTorque:
    """Distributed torque about the member axis (kN·m/m, right-hand about local x), linear from a to b."""

    a: float
    b: float
    t1: float
    t2: float

    def scaled(self, k: float) -> MTorque:
        return MTorque(self.a, self.b, k * self.t1, k * self.t2)


@dataclass
class FNode:
    id: int
    x: float
    y: float
    z: float
    level: int = 0
    support: str | None = None  # "fixed" | "pinned"
    tag: str = ""


@dataclass
class FMember:
    id: int
    n1: int
    n2: int
    kind: str  # "beam" | "column"
    b: float
    d: float
    E: float  # kN/m^2
    angle: float = 0.0  # column rotation (deg) of local y from global X
    level: int = 0
    mark: str = ""
    group: str = ""  # original beam id / column mark
    grade: str = "M25"
    torsion_factor: float = 0.1
    i_factor: float = 1.0  # cracked-section modifier on Iy and Iz
    loads: dict[str, list] = field(default_factory=dict)  # case -> [MLoad|MPoint]
    release_mz: tuple[bool, bool] = (False, False)  # not used in v1 solver (reserved)

    @property
    def A(self):
        return self.b * self.d

    @property
    def Iy(self):
        return self.i_factor * self.b * self.d**3 / 12.0

    @property
    def Iz(self):
        return self.i_factor * self.d * self.b**3 / 12.0

    @property
    def J(self):
        a, c = max(self.b, self.d), min(self.b, self.d)
        beta = 1 / 3 - 0.21 * (c / a) * (1 - (c / a) ** 4 / 12)
        return beta * a * c**3 * self.torsion_factor


class FrameSolveError(RuntimeError):
    pass


def rotation(m: FMember, nodes: dict[int, FNode]) -> tuple[np.ndarray, float]:
    """3x3 matrix whose rows are the local x, y, z unit vectors; and length."""
    a, b = nodes[m.n1], nodes[m.n2]
    v = np.array([b.x - a.x, b.y - a.y, b.z - a.z])
    L = float(np.linalg.norm(v))
    if L < 1e-9:
        raise FrameSolveError(f"Member {m.mark or m.id} has zero length")
    ex = v / L
    if abs(ex[2]) > 0.999:  # vertical
        ang = math.radians(m.angle)
        ey = np.array([math.cos(ang), math.sin(ang), 0.0])
        ey = ey - ex * ey.dot(ex)
        ey /= np.linalg.norm(ey)
        ez = np.cross(ex, ey)
    else:
        ez = np.array([0.0, 0.0, 1.0])
        ez = ez - ex * ez.dot(ex)
        ez /= np.linalg.norm(ez)
        ey = np.cross(ez, ex)
    return np.vstack([ex, ey, ez]), L


def local_k(m: FMember, L: float) -> np.ndarray:
    E = m.E
    G = E / 2.4
    A, Iy, Iz, J = m.A, m.Iy, m.Iz, m.J
    k = np.zeros((12, 12))
    ea = E * A / L
    k[0, 0] = k[6, 6] = ea
    k[0, 6] = k[6, 0] = -ea
    gj = G * J / L
    k[3, 3] = k[9, 9] = gj
    k[3, 9] = k[9, 3] = -gj
    # bending in x-y plane (v, rz) about local z
    a1, a2, a3, a4 = 12 * E * Iz / L**3, 6 * E * Iz / L**2, 4 * E * Iz / L, 2 * E * Iz / L
    for i, j, v in (
        (1, 1, a1),
        (1, 5, a2),
        (1, 7, -a1),
        (1, 11, a2),
        (5, 5, a3),
        (5, 7, -a2),
        (5, 11, a4),
        (7, 7, a1),
        (7, 11, -a2),
        (11, 11, a3),
    ):
        k[i, j] = k[j, i] = v
    # bending in x-z plane (w, ry) about local y  (ry = -dw/dx)
    b1, b2, b3, b4 = 12 * E * Iy / L**3, 6 * E * Iy / L**2, 4 * E * Iy / L, 2 * E * Iy / L
    for i, j, v in (
        (2, 2, b1),
        (2, 4, -b2),
        (2, 8, -b1),
        (2, 10, -b2),
        (4, 4, b3),
        (4, 8, b2),
        (4, 10, b4),
        (8, 8, b1),
        (8, 10, b2),
        (10, 10, b3),
    ):
        k[i, j] = k[j, i] = v
    return k


_GX, _GW = np.polynomial.legendre.leggauss(4)


def _hermite(xi: np.ndarray, L: float):
    return (1 - 3 * xi**2 + 2 * xi**3, L * (xi - 2 * xi**2 + xi**3), 3 * xi**2 - 2 * xi**3, L * (-(xi**2) + xi**3))


def equivalent_loads(loads: list, lam: np.ndarray, L: float) -> np.ndarray:
    """Consistent nodal load vector (local axes) for a member's loads."""
    f = np.zeros(12)
    for ld in loads:
        if isinstance(ld, MTorque):
            a, b = max(ld.a, 0.0), min(ld.b, L)
            if b - a < 1e-12:
                continue
            span = ld.b - ld.a if ld.b > ld.a else 1.0
            xs = 0.5 * (b - a) * _GX + 0.5 * (b + a)
            ws = 0.5 * (b - a) * _GW
            t = ld.t1 + (ld.t2 - ld.t1) * (xs - ld.a) / span
            f[3] += np.sum(ws * t * (1 - xs / L))
            f[9] += np.sum(ws * t * xs / L)
            continue
        if isinstance(ld, MPoint):
            q = lam @ np.array(ld.P)
            xi = np.array([min(max(ld.x / L, 0.0), 1.0)])
            N1, N2, N3, N4 = _hermite(xi, L)
            f[0] += q[0] * (1 - xi[0])
            f[6] += q[0] * xi[0]
            f[1] += q[1] * N1[0]
            f[5] += q[1] * N2[0]
            f[7] += q[1] * N3[0]
            f[11] += q[1] * N4[0]
            f[2] += q[2] * N1[0]
            f[4] -= q[2] * N2[0]
            f[8] += q[2] * N3[0]
            f[10] -= q[2] * N4[0]
            continue
        a, b = max(ld.a, 0.0), min(ld.b, L)
        if b - a < 1e-12:
            continue
        q1 = lam @ np.array(ld.w1)
        q2 = lam @ np.array(ld.w2)
        span = ld.b - ld.a if ld.b > ld.a else 1.0
        xs = 0.5 * (b - a) * _GX + 0.5 * (b + a)
        ws = 0.5 * (b - a) * _GW
        t = (xs - ld.a) / span
        qx = q1[:, None] * (1 - t) + q2[:, None] * t  # 3 x ng
        xi = xs / L
        N1, N2, N3, N4 = _hermite(xi, L)
        f[0] += np.sum(ws * qx[0] * (1 - xi))
        f[6] += np.sum(ws * qx[0] * xi)
        f[1] += np.sum(ws * qx[1] * N1)
        f[5] += np.sum(ws * qx[1] * N2)
        f[7] += np.sum(ws * qx[1] * N3)
        f[11] += np.sum(ws * qx[1] * N4)
        f[2] += np.sum(ws * qx[2] * N1)
        f[4] -= np.sum(ws * qx[2] * N2)
        f[8] += np.sum(ws * qx[2] * N3)
        f[10] -= np.sum(ws * qx[2] * N4)
    return f


@dataclass
class FrameResults:
    cases: list[str]
    disp: dict[str, np.ndarray]  # case -> (nnode, 6)
    end_forces: dict[str, dict[int, np.ndarray]]  # case -> member -> 12 local
    reactions: dict[str, dict[int, np.ndarray]]  # case -> node -> 6 global
    node_index: dict[int, int]


@dataclass
class Diaphragm:
    """Rigid floor: ``slaves`` follow the in-plane motion (ux, uy, rz) of ``master``."""

    master: int
    slaves: list[int]


class FrameSolver:
    """Assemble, constrain and solve the frame.

    Rigid diaphragms are enforced exactly with a master–slave transformation
    ``u_full = T u_red`` (``K_red = Tᵀ K T``): a slave's ux, uy and rz are
    ``Ux − (y − ym) Rz``, ``Uy + (x − xm) Rz`` and ``Rz`` of its master; the
    master's uz, rx, ry are not degrees of freedom.  Without diaphragms ``T``
    simply drops the support degrees of freedom.  The factorised ``K_red`` is
    kept so the dynamic analysis can reuse it.
    """

    def __init__(
        self,
        nodes: dict[int, FNode],
        members: dict[int, FMember],
        nodal_loads: dict[str, dict[int, np.ndarray]] | None = None,
        diaphragms: list[Diaphragm] | None = None,
    ):
        self.nodes = nodes
        self.members = members
        self.nodal = nodal_loads or {}
        self.diaphragms = diaphragms or []
        self.idx: dict[int, int] = {}
        self.K: sp.csr_matrix | None = None
        self.T: sp.csr_matrix | None = None
        self.lu = None
        self.red: dict[int, int] = {}  # full dof -> reduced dof (independent dofs only)
        self.cache: dict[int, tuple] = {}

    # ------------------------------------------------------------------ assembly
    def _assemble(self, cases: list[str]) -> np.ndarray:
        nodes, members = self.nodes, self.members
        idx = self.idx = {nid: i for i, nid in enumerate(sorted(nodes))}
        n = len(idx) * 6
        rows, cols, vals = [], [], []
        F = np.zeros((n, len(cases)))
        cache = self.cache = {}
        for m in members.values():
            lam, L = rotation(m, nodes)
            T = np.zeros((12, 12))
            for k in range(4):
                T[3 * k : 3 * k + 3, 3 * k : 3 * k + 3] = lam
            kl = local_k(m, L)
            kg = T.T @ kl @ T
            dofs = np.r_[6 * idx[m.n1] : 6 * idx[m.n1] + 6, 6 * idx[m.n2] : 6 * idx[m.n2] + 6]
            r, c = np.meshgrid(dofs, dofs, indexing="ij")
            rows.append(r.ravel())
            cols.append(c.ravel())
            vals.append(kg.ravel())
            feq = {}
            for ci, case in enumerate(cases):
                f = equivalent_loads(m.loads.get(case, []), lam, L)
                feq[case] = f
                F[dofs, ci] += T.T @ f
            cache[m.id] = (T, kl, dofs, feq)
        if vals:
            self.K = sp.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n))
        else:
            self.K = sp.csr_matrix((n, n))
        for ci, case in enumerate(cases):
            for nid, vec in self.nodal.get(case, {}).items():
                F[6 * idx[nid] : 6 * idx[nid] + 6, ci] += vec
        return F

    def _transformation(self) -> sp.csr_matrix:
        nodes, idx = self.nodes, self.idx
        n = len(idx) * 6
        if not any(nd.support for nd in nodes.values()):
            raise FrameSolveError("No supports defined – the structure is unstable")
        slave_of: dict[int, int] = {}
        masters = set()
        for dg in self.diaphragms:
            masters.add(dg.master)
            for s in dg.slaves:
                if s != dg.master and not nodes[s].support:
                    slave_of[s] = dg.master
        red: dict[int, int] = {}
        for nid in sorted(nodes):
            nd = nodes[nid]
            base = 6 * idx[nid]
            for k in range(6):
                if nd.support == "fixed" or (nd.support == "pinned" and k < 3):
                    continue
                if nid in masters and k in (2, 3, 4):
                    continue  # a diaphragm master only carries the in-plane motion
                if nid in slave_of and k in (0, 1, 5):
                    continue  # expressed through the master below
                red[base + k] = len(red)
        rows, cols, vals = [], [], []
        for full, r in red.items():
            rows.append(full)
            cols.append(r)
            vals.append(1.0)
        for s, mnode in slave_of.items():
            sn, mn = nodes[s], nodes[mnode]
            bs, bm = 6 * idx[s], 6 * idx[mnode]
            ux, uy, rz = red[bm], red[bm + 1], red[bm + 5]
            dx, dy = sn.x - mn.x, sn.y - mn.y
            for full, col, v in ((bs, ux, 1.0), (bs, rz, -dy), (bs + 1, uy, 1.0), (bs + 1, rz, dx), (bs + 5, rz, 1.0)):
                rows.append(full)
                cols.append(col)
                vals.append(v)
        self.red = red
        return sp.csr_matrix((vals, (rows, cols)), shape=(n, len(red)))

    def factorise(self) -> None:
        T = self.T
        Kr = (T.T @ self.K @ T).tocsc()
        # tiny diagonal springs stabilise local mechanisms (e.g. torsion of a member
        # pinned at both ends); genuine mechanisms are detected after the solve
        diag = Kr.diagonal()
        eps = 1e-10 * float(diag.max()) if diag.size and diag.max() > 0 else 1e-12
        Kr = (Kr + sp.identity(Kr.shape[0], format="csc") * eps).tocsc()
        try:
            self.lu = spla.splu(Kr)
        except RuntimeError as exc:  # exactly singular
            raise FrameSolveError(
                "Stiffness matrix is singular – check for unsupported or disconnected members"
            ) from exc

    def displacements(self, F: np.ndarray) -> np.ndarray:
        """Full-length displacement vectors for full-length load vectors (columns)."""
        if self.T.shape[1] == 0:
            return np.zeros_like(F)
        return self.T @ self.lu.solve(np.asarray(self.T.T @ F))

    # ------------------------------------------------------------------ solve
    def solve(self, cases: list[str]) -> FrameResults:
        nodes = self.nodes
        F = self._assemble(cases)
        self.T = self._transformation()
        self.factorise()
        U = self.displacements(F)
        if not np.all(np.isfinite(U)):
            raise FrameSolveError("Solution is not finite – the structure may be a mechanism")
        trans = np.abs(U.reshape(-1, 6, len(cases))[:, :3, :])
        if trans.size and float(trans.max()) > 5.0:
            raise FrameSolveError(
                f"Displacement of {float(trans.max()):.1f} m – the structure is unstable "
                "(check supports, floating columns and missing beams)"
            )
        idx = self.idx
        res = FrameResults(cases, {}, {}, {}, idx)
        R = self.K @ U - F
        for ci, case in enumerate(cases):
            res.disp[case] = U[:, ci].reshape(-1, 6)
            res.end_forces[case] = self.end_forces(U[:, ci], case)
            res.reactions[case] = {
                nid: R[6 * idx[nid] : 6 * idx[nid] + 6, ci] for nid, nd in nodes.items() if nd.support
            }
        return res

    def end_forces(self, u: np.ndarray, case: str | None = None) -> dict[int, np.ndarray]:
        """Local member end forces for a full displacement vector (member loads of ``case`` included)."""
        out = {}
        for mid, (T, kl, dofs, feq) in self.cache.items():
            f = kl @ (T @ u[dofs])
            if case is not None:
                f = f - feq[case]
            out[mid] = f
        return out


def section_forces(
    m: FMember, nodes: dict[int, FNode], f_end: np.ndarray, loads: list, xs: np.ndarray
) -> dict[str, np.ndarray]:
    """Internal forces at stations ``xs`` (local axes).

    Returns N (tension +), Vy, Vz, T, My, Mz with the left-segment
    convention; for beams ``M_major = -My`` is sagging-positive.
    """
    lam, L = rotation(m, nodes)
    f = f_end
    N = np.full_like(xs, -f[0], dtype=float)
    Vy = np.full_like(xs, f[1], dtype=float)
    Vz = np.full_like(xs, f[2], dtype=float)
    T = np.full_like(xs, -f[3], dtype=float)
    My = -(f[4] + xs * f[2])
    Mz = -f[5] + xs * f[1]
    for ld in loads:
        if isinstance(ld, MTorque):
            a = max(ld.a, 0.0)
            span = ld.b - ld.a if ld.b > ld.a else 1.0
            for i, x in enumerate(xs):
                bb = min(ld.b, x)
                if bb - a <= 1e-12:
                    continue
                ta = ld.t1 + (ld.t2 - ld.t1) * (a - ld.a) / span
                tb = ld.t1 + (ld.t2 - ld.t1) * (bb - ld.a) / span
                T[i] -= 0.5 * (ta + tb) * (bb - a)
            continue
        if isinstance(ld, MPoint):
            q = lam @ np.array(ld.P)
            m_ = xs > ld.x
            N[m_] -= q[0]
            Vy[m_] += q[1]
            Vz[m_] += q[2]
            My[m_] -= (xs[m_] - ld.x) * q[2]
            Mz[m_] += (xs[m_] - ld.x) * q[1]
            continue
        q1 = lam @ np.array(ld.w1)
        q2 = lam @ np.array(ld.w2)
        span = ld.b - ld.a if ld.b > ld.a else 1.0
        a = np.full_like(xs, max(ld.a, 0.0), dtype=float)
        b = np.minimum(ld.b, xs)
        mask = b - a > 1e-12
        if not mask.any():
            continue
        a, b, x = a[mask], b[mask], xs[mask]
        s = 0.5 * (b - a)[:, None] * _GX[None, :] + 0.5 * (b + a)[:, None]  # stations x gauss
        w = 0.5 * (b - a)[:, None] * _GW[None, :]
        t = (s - ld.a) / span
        qx = q1[0] * (1 - t) + q2[0] * t
        qy = q1[1] * (1 - t) + q2[1] * t
        qz = q1[2] * (1 - t) + q2[2] * t
        lever = x[:, None] - s
        N[mask] -= np.sum(w * qx, axis=1)
        Vy[mask] += np.sum(w * qy, axis=1)
        Vz[mask] += np.sum(w * qz, axis=1)
        My[mask] -= np.sum(w * lever * qz, axis=1)
        Mz[mask] += np.sum(w * lever * qy, axis=1)
    return {"x": xs, "N": N, "Vy": Vy, "Vz": Vz, "T": T, "My": My, "Mz": Mz}
