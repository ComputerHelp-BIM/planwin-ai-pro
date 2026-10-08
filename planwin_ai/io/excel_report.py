"""Excel workbook of plan checks, analysis results, member designs and BOQ."""

from __future__ import annotations

import datetime as _dt

from .. import APP_NAME, COMPANY, __version__
from ..core.beamcalc import eighth_points
from ..core.frame import FrameAnalysis, FrameModel
from ..core.model import Project
from ..core.plan_engine import PlanResult
from ..design.report import DesignReport
from .report_common import DISCLAIMER


def write_excel(
    path: str,
    project: Project,
    plan_results: dict[str, PlanResult],
    fm: FrameModel | None = None,
    fa: FrameAnalysis | None = None,
    rep: DesignReport | None = None,
    watermark: str = "",
) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="1F3A5F")
    head_font = Font(color="FFFFFF", bold=True)
    bad_fill = PatternFill("solid", fgColor="F8D7DA")

    def sheet(title, headers, rows, bad_col: int | None = None):
        ws = wb.create_sheet(title[:31])
        ws.append(headers)
        for c in ws[1]:
            c.fill, c.font = head_fill, head_font
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for r in rows:
            ws.append([round(v, 3) if isinstance(v, float) else v for v in r])
            if bad_col is not None and r[bad_col] in (False, "FAIL", "NO"):
                for c in ws[ws.max_row]:
                    c.fill = bad_fill
        for i, h in enumerate(headers, 1):
            width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows[:200]] + [8])
            ws.column_dimensions[get_column_letter(i)].width = min(width + 2, 45)
        ws.freeze_panes = "A2"
        return ws

    ws = wb.active
    ws.title = "Summary"
    ws.append([f"{APP_NAME} {__version__} – {COMPANY}"])
    ws["A1"].font = Font(size=14, bold=True)
    info = [
        ("Project", project.name),
        ("Client", project.client),
        ("Engineer", project.engineer),
        ("Location", project.location),
        ("Date", _dt.date.today().isoformat()),
        ("Seismic zone", project.seismic.zone),
        ("Wind city / Vb", f"{project.wind.city} / {project.wind.basic_speed} m/s"),
        ("Height (m)", round(project.elevations()[-1], 3)),
        ("Levels", len(project.levels)),
    ]
    if watermark:
        info.append(("Licence", watermark))
    for k, v in info:
        ws.append([k, v])
    ws.append([])
    ws.append([DISCLAIMER])
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 50

    rows = []
    for pname, pr in plan_results.items():
        rows.append(
            (
                pname,
                pr.applied["D"],
                pr.applied["L"],
                pr.reacted["D"],
                pr.reacted["L"],
                pr.imbalance_pct,
                len(pr.errors),
                len(pr.warnings),
            )
        )
    sheet(
        "Plan checks",
        [
            "Plan",
            "Applied DL kN",
            "Applied LL kN",
            "Reaction DL kN",
            "Reaction LL kN",
            "Imbalance %",
            "Errors",
            "Warnings",
        ],
        rows,
    )
    rows = []
    for pname, pr in plan_results.items():
        for cl in pr.columns.values():
            rows.append((pname, cl.mark, cl.dead, cl.live, cl.total))
    sheet("Column loads (plan)", ["Plan", "Column", "Dead kN", "Live kN", "Total kN"], rows)
    rows = []
    for pname, pr in plan_results.items():
        plan = project.plan(pname)
        bmap = {b.id: b for b in plan.beams} if plan else {}
        for bid, br in pr.beams.items():
            b = bmap.get(bid)
            if not b:
                continue
            dia = br.diagram(True)
            pts = eighth_points(br.length, dia)
            rows.append(
                (
                    pname,
                    b.mark,
                    br.length,
                    b.b,
                    b.d,
                    br.total("D"),
                    br.total("L"),
                    br.equivalent_udl(),
                    *[p["M"] for p in pts],
                    max(abs(p["V"]) for p in pts),
                )
            )
    sheet(
        "Beam loads (plan)",
        [
            "Plan",
            "Beam",
            "Span m",
            "b m",
            "D m",
            "Total DL kN",
            "Total LL kN",
            "Eq. UDL kN/m",
            *[f"Mu @{k}/8 kNm" for k in range(9)],
            "Vu max kN",
        ],
        rows,
    )
    issues = []
    for pname, pr in plan_results.items():
        for i in pr.issues:
            issues.append((pname, i.level, i.kind, i.message))
    if fm:
        for i in fm.issues:
            issues.append(("Frame", i.level, i.kind, i.message))
    sheet("Issues", ["Where", "Level", "Type", "Message"], issues)
    if fa and fm:
        rows = []
        for nid, nd in fm.nodes.items():
            if not nd.support:
                continue
            for c in fa.combos:
                r = fa.reaction(nid, c.factors)
                rows.append((nd.tag, c.name, *[float(v) for v in r]))
        sheet(
            "Support reactions",
            ["Column", "Combination", "FX kN", "FY kN", "FZ kN", "MX kNm", "MY kNm", "MZ kNm"],
            rows,
        )
        rows = [(k, r.T, r.sa_g, r.Ah, r.W, r.Vb) for k, r in fm.seismic.items()]
        if rows:
            sheet("Seismic", ["Direction", "T s", "Sa/g", "Ah", "W kN", "VB kN"], rows)
        rows = []
        for k, r in fm.wind.items():
            for i, f in enumerate(r.forces):
                if f:
                    rows.append((k, fm.levels[i].name, r.pressures[i], f))
        if rows:
            sheet("Wind", ["Direction", "Level", "pd kN/m2", "Storey force kN"], rows)
    if rep:
        sheet(
            "Beam design",
            [
                "Level",
                "Beam",
                "b m",
                "D m",
                "Span m",
                "Mu+ kNm",
                "Mu- left",
                "Mu- right",
                "Vu kN",
                "Bottom",
                "Top left",
                "Top right",
                "Stirrups",
                "OK",
                "Notes",
            ],
            [
                (
                    b.level,
                    b.mark,
                    b.b,
                    b.d,
                    b.span,
                    b.M_sag,
                    b.M_hog_l,
                    b.M_hog_r,
                    b.V_max,
                    b.bottom,
                    b.top_l,
                    b.top_r,
                    b.stirrups,
                    "YES" if b.ok else "NO",
                    "; ".join(b.notes),
                )
                for b in rep.beams
            ],
            bad_col=13,
        )
        sheet(
            "Column design",
            [
                "Level",
                "Column",
                "b m",
                "D m",
                "Pu kN",
                "Mux kNm",
                "Muy kNm",
                "Steel %",
                "Bars",
                "Ties",
                "Governing",
                "Interaction",
                "OK",
                "Notes",
            ],
            [
                (
                    c.level,
                    c.mark,
                    c.b,
                    c.d,
                    c.Pu,
                    c.Mux,
                    c.Muy,
                    c.steel_pct,
                    c.bars,
                    c.ties,
                    c.governing,
                    c.utilisation,
                    "YES" if c.ok else "NO",
                    "; ".join(c.notes),
                )
                for c in rep.columns
            ],
            bad_col=12,
        )
        sheet(
            "Footing design",
            ["Column", "P service kN", "L m", "B m", "D m", "Bars along L", "Bars along B", "q kN/m2", "OK", "Notes"],
            [
                (
                    f.mark,
                    f.P_service,
                    f.L,
                    f.B,
                    f.D,
                    f.bars_L,
                    f.bars_B,
                    f.q,
                    "YES" if f.ok else "NO",
                    "; ".join(f.notes),
                )
                for f in rep.footings
            ],
            bad_col=8,
        )
        sheet(
            "Slab design",
            [
                "Plan",
                "Slab",
                "lx m",
                "ly m",
                "Type",
                "D mm",
                "Mx+ kNm/m",
                "My+ kNm/m",
                "M- kNm/m",
                "Short span",
                "Long span",
                "Support (top)",
                "OK",
                "Notes",
            ],
            [
                (
                    pn,
                    s.mark,
                    s.lx,
                    s.ly,
                    s.kind,
                    s.D_mm,
                    s.Mx_pos,
                    s.My_pos,
                    s.M_neg,
                    s.ast_x,
                    s.ast_y,
                    s.ast_neg,
                    "YES" if s.ok else "NO",
                    "; ".join(s.notes),
                )
                for pn, s in rep.slabs
            ],
            bad_col=12,
        )
        sheet(
            "Drift",
            ["Case", "Level", "Drift mm", "Ratio", "OK (<=0.004)"],
            [(d["case"], d["level"], d["drift_mm"], d["ratio"], "YES" if d["ok"] else "NO") for d in rep.drifts],
            bad_col=4,
        )
        boq = rep.boq
        sheet(
            "BOQ & Cost",
            ["Item", "Unit", "Quantity", "Rate (INR)", "Amount (INR)"],
            list(boq.get("lines", []))
            + [
                ("TOTAL", "", "", "", boq.get("cost", 0.0)),
                ("Steel / concrete", "kg/m³", boq.get("steel_per_m3", 0.0), "", ""),
            ],
        )
    wb.save(path)
    return path
