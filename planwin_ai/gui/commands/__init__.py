"""Main-window commands, grouped by ribbon area.

Each module is a mixin for :class:`~planwin_ai.gui.main_window.MainWindow`: it holds the
command handlers only and relies on the window for state (``project``, ``session``,
``canvas``, ``mutate`` …).  The ribbon layout itself lives in :mod:`..ribbon_layout`.
"""

from .app import AppCommands
from .file import FileCommands
from .frame import FrameCommands
from .plan import PlanCommands

__all__ = ["AppCommands", "FileCommands", "FrameCommands", "PlanCommands"]
