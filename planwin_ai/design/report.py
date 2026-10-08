"""Design result containers shared by the runner, reports, exporters and the GUI."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import is456


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


@dataclass
class DesignReport:
    beams: list[BeamDesign] = field(default_factory=list)
    columns: list[ColumnDesign] = field(default_factory=list)
    footings: list[FootingDesign] = field(default_factory=list)
    slabs: list[tuple[str, is456.SlabResult]] = field(default_factory=list)  # (plan, result)
    boq: dict = field(default_factory=dict)
    drifts: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def failures(self) -> int:
        return (
            sum(not b.ok for b in self.beams)
            + sum(not c.ok for c in self.columns)
            + sum(not f.ok for f in self.footings)
            + sum(not s.ok for _, s in self.slabs)
        )
