"""Dialogs (``from . import dialogs; dialogs.SettingsDialog(...)``).

Dialogs that change the model expose ``apply()``; the main window runs it through
``mutate(desc, dlg.apply)`` after ``exec()`` so the change is one undo step.
"""

from .common import _buttons, _dspin, _Plot
from .frame import AutoSizeDialog, ColumnSizesDialog, CopyFloorsDialog, JointLoadDialog, duplicate_plan
from .misc import (
    GridsDialog,
    LicenseDialog,
    MirrorDialog,
    MoveCopyDialog,
    TemplateDialog,
    about_text,
    grids_from_columns,
)
from .results import BeamDiagramDialog, RevisionsDialog
from .settings import AISettingsDialog, SettingsDialog
from .wizards import StairDialog, TankDialog, remove_staircase, remove_water_tank

__all__ = [
    "AISettingsDialog",
    "AutoSizeDialog",
    "BeamDiagramDialog",
    "ColumnSizesDialog",
    "CopyFloorsDialog",
    "GridsDialog",
    "JointLoadDialog",
    "LicenseDialog",
    "MirrorDialog",
    "MoveCopyDialog",
    "RevisionsDialog",
    "SettingsDialog",
    "StairDialog",
    "TankDialog",
    "TemplateDialog",
    "_Plot",
    "_buttons",
    "_dspin",
    "about_text",
    "duplicate_plan",
    "grids_from_columns",
    "remove_staircase",
    "remove_water_tank",
]
