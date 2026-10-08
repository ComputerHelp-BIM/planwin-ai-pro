"""Headless command line for automation and CI.

Examples::

    planwin-ai cli templates
    planwin-ai cli new --template office_g5 --out office.pwai
    planwin-ai cli ask "G+4 residential in Pune, 3x2 bays of 4.5 m" --out model.pwai
    planwin-ai cli run model.pwai --design --export staad etabs excel pdf --out-dir results/
    planwin-ai cli import-plw old.plw --out converted.pwai
"""

from __future__ import annotations

import argparse
import os
import sys

from . import APP_NAME, __version__


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="planwin-ai cli", description=f"{APP_NAME} {__version__} command line")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("templates", help="list templates")
    n = sub.add_parser("new", help="create a project from a template")
    n.add_argument("--template", required=True)
    n.add_argument("--out", required=True)
    a = sub.add_parser("ask", help="build/modify a model with a natural-language prompt (offline engine)")
    a.add_argument("prompt")
    a.add_argument("--project")
    a.add_argument("--out", required=True)
    r = sub.add_parser("run", help="analyse / design / export a project")
    r.add_argument("project")
    r.add_argument("--design", action="store_true")
    r.add_argument("--autosize", action="store_true")
    r.add_argument("--export", nargs="*", default=[])
    r.add_argument("--out-dir", default=".")
    i = sub.add_parser("import-plw", help="convert a legacy PlanWin .plw plan")
    i.add_argument("plw")
    i.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    from .ai.actions import Session, execute
    from .ai.assistant import Assistant
    from .ai.templates import TEMPLATES, build_template
    from .core.model import Level, Project
    from .io import project_io
    from .licensing.license import current_state

    lic = current_state()
    if args.cmd == "templates":
        for t in TEMPLATES:
            print(f"{t.key:16} {t.title} – {t.description}")
        return 0
    if args.cmd == "new":
        print(project_io.save_project(build_template(args.template), args.out))
        return 0
    if args.cmd == "ask":
        prj = project_io.load_project(args.project) if args.project else Project()
        asst = Assistant(Session(prj, out_dir=os.path.dirname(os.path.abspath(args.out)), watermark=lic.watermark,
                                 exports_allowed=lic.exports_allowed))
        reply, res = asst.ask(args.prompt)
        print(reply)
        project_io.save_project(asst.session.project, args.out)
        return 1 if res.errors else 0
    if args.cmd == "import-plw":
        from .io.legacy_plw import read_plw

        plan, rep = read_plw(args.plw)
        prj = Project(name=plan.name)
        prj.plans.append(plan)
        prj.levels = [Level("Plinth", plan.name, 1.5, "M20"), Level("Floor 1", plan.name, 3.0, "M20")]
        print(f"PlanWin {rep.version}: {rep.slabs} slabs, {rep.columns} columns, {rep.beams} beams")
        print(project_io.save_project(prj, args.out))
        return 0
    if args.cmd == "run":
        os.makedirs(args.out_dir, exist_ok=True)
        s = Session(project_io.load_project(args.project), out_dir=args.out_dir, watermark=lic.watermark,
                    exports_allowed=lic.exports_allowed)
        acts = []
        if args.autosize:
            acts.append({"action": "autosize_columns"})
        acts.append({"action": "design" if args.design else "analyze"})
        acts += [{"action": "export", "format": f} for f in args.export]
        res = execute(s, acts)
        print("\n".join(res.messages))
        for e in res.errors:
            print("ERROR:", e, file=sys.stderr)
        return 1 if res.errors else 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
