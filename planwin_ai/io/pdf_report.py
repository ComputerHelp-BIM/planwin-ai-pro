"""PDF design report (reportlab)."""

from __future__ import annotations

import datetime as _dt
from xml.sax.saxutils import escape as _esc

from .. import APP_NAME, COMPANY, __version__
from ..core.frame import FrameModel
from ..core.model import Project
from ..core.plan_engine import PlanResult
from ..design.report import DesignReport
from .report_common import DISCLAIMER


def write_pdf(
    path: str,
    project: Project,
    plan_results: dict[str, PlanResult],
    fm: FrameModel | None = None,
    rep: DesignReport | None = None,
    watermark: str = "",
) -> str:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        path,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"{project.name} – {APP_NAME}",
    )
    story = []

    def table(headers, rows, widths=None):
        body = [[(f"{v:.2f}" if isinstance(v, float) else str(v)) for v in r] for r in rows]
        data = ([headers] if headers else []) + body
        t = Table(data, repeatRows=1 if headers else 0, colWidths=widths)
        st = [("FONTSIZE", (0, 0), (-1, -1), 7.5), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey)]
        if headers:
            st += [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3A5F")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F5F9")]),
            ]
        else:
            st += [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E8EDF3")),
                ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#E8EDF3")),
            ]
        for i, r in enumerate(rows, start=1 if headers else 0):
            if any(v in ("NO", "FAIL") for v in r):
                st.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F8D7DA")))
        t.setStyle(TableStyle(st))
        return t

    story.append(Paragraph(f"<b>{_esc(project.name)}</b>", styles["Title"]))
    story.append(
        Paragraph(
            f"Structural pre-processing, analysis &amp; design report – {APP_NAME} {__version__}", styles["Normal"]
        )
    )
    if watermark:
        story.append(Paragraph(f"<font color='red'><b>{_esc(watermark)}</b></font>", styles["Normal"]))
    story.append(Spacer(1, 6 * mm))
    meta = [
        ["Client", project.client or "-", "Engineer", project.engineer or "-"],
        ["Location", project.location or "-", "Date", _dt.date.today().isoformat()],
        [
            "Seismic",
            f"Zone {project.seismic.zone}, I={project.seismic.importance}, "
            f"R={project.seismic.response_reduction}, {project.seismic.soil} soil",
            "Wind",
            f"{project.wind.city}: Vb {project.wind.basic_speed} m/s, terrain {project.wind.terrain}",
        ],
        [
            "Codes",
            "IS 456:2000, IS 875 (Parts 1-3), IS 1893 (Part 1):2016",
            "Height",
            f"{project.elevations()[-1]:.2f} m",
        ],
    ]
    story.append(table(None, meta))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(DISCLAIMER, styles["Italic"]))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("Load take-down check (PlanWin)", styles["Heading2"]))
    story.append(
        table(
            ["Plan", "Applied DL", "Applied LL", "Reaction DL", "Reaction LL", "Imbalance %", "Errors"],
            [
                (n, r.applied["D"], r.applied["L"], r.reacted["D"], r.reacted["L"], r.imbalance_pct, len(r.errors))
                for n, r in plan_results.items()
            ],
        )
    )
    if fm and (fm.seismic or fm.wind):
        story.append(Paragraph("Lateral loads", styles["Heading2"]))
        rows = [(k, r.T, r.sa_g, f"{r.Ah:.4f}", r.W, r.Vb) for k, r in fm.seismic.items()]
        if rows:
            story.append(table(["Dir", "T (s)", "Sa/g", "Ah", "W (kN)", "VB (kN)"], rows))
        rows = [(k, sum(r.forces)) for k, r in fm.wind.items()]
        if rows:
            story.append(Spacer(1, 3 * mm))
            story.append(table(["Wind dir", "Base shear (kN)"], rows))
    if rep:
        story.append(PageBreak())
        story.append(Paragraph("Column design (IS 456 cl 39.6 biaxial)", styles["Heading2"]))
        story.append(
            table(
                ["Level", "Col", "b x D (mm)", "Pu kN", "Mux", "Muy", "p %", "Bars", "Ties", "Ratio", "OK"],
                [
                    (
                        c.level,
                        c.mark,
                        f"{c.b * 1000:.0f}x{c.d * 1000:.0f}",
                        c.Pu,
                        c.Mux,
                        c.Muy,
                        c.steel_pct,
                        c.bars,
                        c.ties,
                        c.utilisation,
                        "YES" if c.ok else "NO",
                    )
                    for c in rep.columns
                ],
            )
        )
        story.append(PageBreak())
        story.append(Paragraph("Beam design", styles["Heading2"]))
        story.append(
            table(
                [
                    "Level",
                    "Beam",
                    "b x D",
                    "Span",
                    "Mu+",
                    "Mu- L",
                    "Mu- R",
                    "Vu",
                    "Bottom",
                    "Top L",
                    "Top R",
                    "Stirrups",
                    "OK",
                ],
                [
                    (
                        b.level,
                        b.mark,
                        f"{b.b * 1000:.0f}x{b.d * 1000:.0f}",
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
                    )
                    for b in rep.beams
                ],
            )
        )
        story.append(PageBreak())
        story.append(Paragraph("Footing design (isolated)", styles["Heading2"]))
        story.append(
            table(
                ["Col", "P (kN)", "L x B x D (m)", "Bars along L", "Bars along B", "q (kN/m²)", "OK"],
                [
                    (
                        f.mark,
                        f.P_service,
                        f"{f.L:.2f} x {f.B:.2f} x {f.D:.2f}",
                        f.bars_L,
                        f.bars_B,
                        f.q,
                        "YES" if f.ok else "NO",
                    )
                    for f in rep.footings
                ],
            )
        )
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph("Slab design", styles["Heading2"]))
        story.append(
            table(
                ["Plan", "Slab", "lx x ly", "Type", "D mm", "Short", "Long", "Top", "OK"],
                [
                    (
                        pn,
                        s.mark,
                        f"{s.lx:.2f} x {s.ly:.2f}",
                        s.kind,
                        s.D_mm,
                        s.ast_x,
                        s.ast_y,
                        s.ast_neg,
                        "YES" if s.ok else "NO",
                    )
                    for pn, s in rep.slabs
                ],
            )
        )
        story.append(PageBreak())
        story.append(Paragraph("Quantities &amp; cost estimate", styles["Heading2"]))
        story.append(
            table(
                ["Item", "Unit", "Qty", "Rate", "Amount"],
                list(rep.boq.get("lines", [])) + [("TOTAL", "", "", "", rep.boq.get("cost", 0.0))],
            )
        )
        if rep.warnings:
            story.append(Spacer(1, 4 * mm))
            story.append(Paragraph("Warnings", styles["Heading2"]))
            for w in rep.warnings:
                story.append(Paragraph(f"• {_esc(w)}", styles["Normal"]))

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawString(12 * mm, 8 * mm, f"{APP_NAME} {__version__} – {COMPANY}   {watermark}")
        canvas.drawRightString(landscape(A4)[0] - 12 * mm, 8 * mm, f"Page {_doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return path
