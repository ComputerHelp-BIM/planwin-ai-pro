"""IS 456:2000 limit-state design routines (SI: N, mm, MPa unless stated).

Each module is self-contained and unit tested against textbook values.  All results are
*preliminary* design aids; final designs must be checked and signed by a qualified
structural engineer.  ``from planwin_ai.design import is456`` keeps working: every public
name is re-exported here.
"""

from .beam import FlexureResult, ShearResult, TorsionResult, deflection_mf, flexure, select_bars, shear, torsion_design
from .column import (
    ColumnCheck,
    autosize_depth,
    biaxial_ratio,
    column_bars,
    design_column,
    interaction_curve,
    puz,
    section_capacity,
)
from .common import (
    BAR_DIAS,
    BarMesh,
    BarSet,
    Links,
    ast_singly,
    fsc_doubly,
    mesh_for,
    mu_lim,
    tau_c,
    tau_c_max,
    xu_max_ratio,
)
from .footing import CombinedFootingResult, FootingResult, design_combined_footing, design_footing
from .slab import SlabResult, design_slab
from .wall import WallCheck, design_wall

__all__ = [
    "BAR_DIAS",
    "BarMesh",
    "BarSet",
    "Links",
    "mesh_for",
    "ColumnCheck",
    "FlexureResult",
    "FootingResult",
    "CombinedFootingResult",
    "design_combined_footing",
    "ShearResult",
    "TorsionResult",
    "torsion_design",
    "SlabResult",
    "ast_singly",
    "autosize_depth",
    "biaxial_ratio",
    "column_bars",
    "deflection_mf",
    "design_column",
    "design_footing",
    "design_slab",
    "flexure",
    "fsc_doubly",
    "interaction_curve",
    "mu_lim",
    "puz",
    "section_capacity",
    "select_bars",
    "shear",
    "tau_c",
    "tau_c_max",
    "xu_max_ratio",
    "WallCheck",
    "design_wall",
]
