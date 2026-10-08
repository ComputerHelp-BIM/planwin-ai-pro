"""``python -m planwin_ai`` launches the GUI; ``python -m planwin_ai cli ...`` the command line."""

import sys

if len(sys.argv) > 1 and sys.argv[1] == "cli":
    from .cli import main as cli_main

    sys.exit(cli_main(sys.argv[2:]))
else:
    from .app import main

    sys.exit(main())
