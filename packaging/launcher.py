"""PyInstaller entry script for PlanWin AI Pro (GUI, CLI via `cli` argument)."""

import sys

from planwin_ai.console import attach_parent_console

if len(sys.argv) > 1 and (sys.argv[1] == "cli" or sys.argv[1] in ("--selftest", "--version")):
    attach_parent_console()  # the exe has no console of its own: print into the shell that started it

if len(sys.argv) > 1 and sys.argv[1] == "cli":
    from planwin_ai.cli import main as cli_main

    sys.exit(cli_main(sys.argv[2:]))

from planwin_ai.app import main

sys.exit(main())
