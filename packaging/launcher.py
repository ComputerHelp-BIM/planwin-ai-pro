"""PyInstaller entry script for PlanWin AI Pro (GUI, CLI via `cli` argument)."""

import sys

if len(sys.argv) > 1 and sys.argv[1] == "cli":
    from planwin_ai.cli import main as cli_main

    sys.exit(cli_main(sys.argv[2:]))

from planwin_ai.app import main

sys.exit(main())
