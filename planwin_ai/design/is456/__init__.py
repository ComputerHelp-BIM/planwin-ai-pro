"""IS 456:2000 limit-state design routines (SI: N, mm, MPa unless stated).

Each module is self-contained and unit tested against textbook values.  All results are
*preliminary* design aids; final designs must be checked and signed by a qualified
structural engineer.  ``from planwin_ai.design import is456`` keeps working: every public
name is re-exported here.
"""

from .beam import FlexureResult, ShearResult, deflection_mf, flexure, select_bars, shear
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
from .common import BAR_DIAS, ast_singly, fsc_doubly, mu_lim, tau_c, tau_c_max, xu_max_ratio
from .footing import FootingResult, design_footing
from .slab import SlabResult, design_slab

__all__ = [
    "BAR_DIAS",
    "ColumnCheck",
    "FlexureResult",
    "FootingResult",
    "ShearResult",
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
]
