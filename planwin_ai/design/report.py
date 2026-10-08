"""Design result containers shared by the runner, reports, exporters and the GUI."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import is456
from .is456 import BarMesh, BarSet, Links


@dataclass
class BeamDesign:
    member_id: int
    mark: str
    level: str
    b: float
    d: float
    span: float
    M_sag: float
    M_hog_l: float
    M_hog_r: float
    V_max: float
    bottom: str
    top_l: str
    top_r: str
    stirrups: str
    ast_bot: float
    ast_top_l: float
    ast_top_r: float
    deflection_ok: bool
    ok: bool
    utilisation: float
    notes: list[str] = field(default_factory=list)
    # structured detailing (for BBS, drawings and calculation sheets)
    bottom_bars: BarSet | None = None
    top_l_bars: BarSet | None = None
    top_r_bars: BarSet | None = None
    links: Links | None = None
    group: str = ""  # id of the plan beam this member segment belongs to
    level_index: int = 0
    links_end: Links | None = None  # IS 13920 cl 6.3.5 hoops within 2d of column faces
    T_max: float = 0.0  # kN·m, factored torsion (IS 456 cl 41)
    side_face: str = ""  # side-face reinforcement (cl 26.5.1.3 / 41.4.1)


@dataclass
class ColumnDesign:
    member_id: int
    mark: str
    level: str
    b: float
    d: float
    Pu: float
    Mux: float
    Muy: float
    steel_pct: float
    bars: str
    ties: str
    governing: str
    ok: bool
    utilisation: float
    As: float
    notes: list[str] = field(default_factory=list)
    main_bars: BarSet | None = None
    tie: Links | None = None
    level_index: int = 0
    height: float = 0.0  # storey height (m), node to node
    clear_height: float = 0.0  # unsupported length used for slenderness (m)
    tie_confined: Links | None = None  # IS 13920 cl 8 special confining hoops within l0
    l0: float = 0.0  # m, confining length at each end (cl 8.1)


@dataclass
class FootingDesign:
    mark: str
    P_service: float
    L: float
    B: float
    D: float
    bars_L: str
    bars_B: str
    q: float
    ok: bool
    ast_L: float
    ast_B: float
    notes: list[str] = field(default_factory=list)
    mesh_L: BarMesh | None = None  # bars running along L
    mesh_B: BarMesh | None = None
    col_b: float = 0.0
    col_d: float = 0.0
    x: float = 0.0
    y: float = 0.0
    angle: float = 0.0  # column angle (deg); L is along the column depth d


@dataclass
class DesignReport:
    beams: list[BeamDesign] = field(default_factory=list)
    columns: list[ColumnDesign] = field(default_factory=list)
    footings: list[FootingDesign] = field(default_factory=list)
    slabs: list[tuple[str, is456.SlabResult]] = field(default_factory=list)  # (plan, result)
    boq: dict = field(default_factory=dict)
    drifts: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ductile: list = field(default_factory=list)  # is13920.DuctileCheck (empty when IS 13920 does not apply)
    walls: list = field(default_factory=list)  # WallDesign
    combined_footings: list = field(default_factory=list)  # CombinedFootingDesign
    irregularities: list = field(default_factory=list)  # irregularity.Irregularity
    modal: object | None = None  # dynamics.ModalResult when response spectrum analysis was used
    seismic_method: str = "static"  # "static" | "response spectrum"

    @property
    def ductile_failures(self) -> int:
        return sum(not d.ok for d in self.ductile)

    @property
    def failures(self) -> int:
        return (
            sum(not b.ok for b in self.beams)
            + sum(not c.ok for c in self.columns)
            + sum(not f.ok for f in self.footings)
            + sum(not s.ok for _, s in self.slabs)
            + sum(not w.ok for w in self.walls)
            + sum(not c.ok for c in self.combined_footings)
            + self.ductile_failures
        )
