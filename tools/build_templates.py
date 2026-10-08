#!/usr/bin/env python3
"""Pre-build and optimise the template library (run before releasing).

Each parametric template is generated, its members are iteratively resized
until IS 456 design and IS 1893 drift checks pass, and the result is stored
in planwin_ai/data/templates/<key>.pwai so users get passing models instantly.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from planwin_ai.ai.templates import TEMPLATES, generate_template  # noqa: E402
from planwin_ai.design.runner import optimize_sizes  # noqa: E402
from planwin_ai.io.project_io import save_project  # noqa: E402

OUT = ROOT / "planwin_ai" / "data" / "templates"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    bad = 0
    for t in TEMPLATES:
        t0 = time.time()
        prj = generate_template(t.key)
        log, rep = optimize_sizes(prj, max_iter=10)
        prj.meta["template"] = {"key": t.key, "failures": rep.failures, "log": log}
        save_project(prj, str(OUT / f"{t.key}.pwai"))
        drift = sum(not d["ok"] for d in rep.drifts)
        bad += rep.failures + drift
        print(f"{t.key:16} failures={rep.failures} drift={drift} ({time.time() - t0:.1f}s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
