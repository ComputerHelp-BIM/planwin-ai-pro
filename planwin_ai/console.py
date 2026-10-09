"""Console output for the windowed Windows exe.

PlanWinAIPro.exe is built as a GUI program (no console window), so ``PlanWinAIPro.exe cli …``
had nowhere to print: ``sys.stdout`` is ``None``.  :func:`attach_parent_console` attaches the
process to the console of the shell that started it (cmd / PowerShell) and points stdout and
stderr at it.  In cmd the prompt may come back before the output; use
``start /wait PlanWinAIPro.exe cli …`` to wait for the command to finish.
"""

from __future__ import annotations

import sys

ATTACH_PARENT_PROCESS = -1


def attach_parent_console() -> bool:
    """Route stdout/stderr to the parent console on Windows when they are missing.
    Returns True when a console was attached; does nothing elsewhere or when output already works."""
    if sys.platform != "win32" or (sys.stdout is not None and sys.stderr is not None):
        return False
    try:
        import ctypes

        if not ctypes.windll.kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
            return False  # started from Explorer: there is no console to write to
        out = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115 (process-long)
    except (OSError, AttributeError):
        return False
    if sys.stdout is None:
        sys.stdout = out
    if sys.stderr is None:
        sys.stderr = out
    print()  # start below the prompt the shell has already printed
    return True
