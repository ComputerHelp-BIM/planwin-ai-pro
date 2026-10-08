"""PDF design report (reportlab)."""

from __future__ import annotations

import datetime as _dt
from xml.sax.saxutils import escape as _esc

from .. import APP_NAME, COMPANY, __version__
from ..core.frame import FrameAnalysis, FrameModel
from ..core.model import Project
from ..core.plan_engine import PlanResult
from ..design.report import DesignReport
from ..units import Units
from .excel_report import (
    BOQ_HEADERS,
    boq_rows,
    convert_rows,
    ductile_summary,
    seismic_method_summary,
    storey_shear_rows,
)
from .report_common import DISCLAIMER

#: characters outside the standard PDF fonts (WinAnsi) used in the design notes
_PLAIN = str.maketrans(
    {
        "≥": ">=",
        "≤": "<=",
        "√": "sqrt",
        "→": "->",
        "ρ": "rho",
        "τ": "tau",
        "Σ": "sum ",
        "φ": "phi",
        "α": "alpha",
        "β": "beta",
        "γ": "gamma",
        "δ": "delta",
        "Δ": "delta ",
        "λ": "lambda",
        "₹": "INR",
        "≈": "~",
        "≠": "!=",
        "−": "-",
        "σ": "sigma",
    }
)


def _plain(s: str) -> str:
    """Text that renders with the built-in Helvetica font."""
    return str(s).translate(_PLAIN)


def write_pdf(
    path: str,
    project: Project,
    plan_results: dict[str, PlanResult],
    fm: FrameModel | None = None,
    rep: DesignReport | None = None,
    watermark: str = "",
    fa: FrameAnalysis | None = None,
    units: Units | None = None,
) -> str:
    """Write the PDF report.  ``fa`` adds the response spectrum results (else taken from ``fm.rs``);
    ``units`` (default: the app-wide :data:`planwin_ai.units.current`) sets force/moment display units."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    if units is None:
        from .. import units as _units

        units = _units.current
    styles = getSampleStyleSheet()
    cell_style = ParagraphStyle("cell", parent=styles["Normal"], fontSize=7, leading=8.5)
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

    def fmt(v, wrap: int):
        if isinstance(v, float):
            return f"{v:.2f}"
        if hasattr(v, "wrapOn"):  # already a flowable (e.g. a Paragraph)
            return v
        v = _plain(v)
        return Paragraph(_esc(v), cell_style) if wrap and len(v) > wrap else v

    def table(headers, rows, widths=None, wrap: int = 0):
        """``wrap``: cells longer than this many characters wrap (0 = never)."""
        if headers:
            headers, rows = convert_rows(headers, rows, units)
        body = [[fmt(v, wrap) for v in r] for r in rows]
        data = ([[_plain(h) for h in headers]] if headers else []) + body
        t = Table(data, repeatRows=1 if headers else 0, colWidths=widths)
        st = [
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]
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

    def sub(title: str, flowable) -> None:
        """Sub-heading kept on the same page as the start of its table."""
        story.append(KeepTogether([Paragraph(title, styles["Heading3"]), flowable]))

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
            "IS 456:2000, IS 875 (Parts 1-3), IS 1893 (Part 1):2016"
            + (", IS 13920:2016" if rep and rep.ductile else "")
            + f"; units {'MKS (t, m)' if units.mks else 'SI (kN, m)'}",
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
            ["Plan", "Applied DL kN", "Applied LL kN", "Reaction DL kN", "Reaction LL kN", "Imbalance %", "Errors"],
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
    sm = seismic_method_summary(project, fm, fa, rep)
    if project.seismic.enabled and (fm or rep):
        story.append(Paragraph("Seismic analysis method (IS 1893-1:2016 cl 7.7.1)", styles["Heading2"]))
        story.append(
            table(
                None,
                [
                    ["Method used", sm["method"], "Dynamic analysis required", "YES" if sm["required"] else "NO"],
                    [
                        "Why",
                        Paragraph(_esc(_plain(sm["why"])), cell_style),
                        "Irregular configuration",
                        "YES" if sm["irregular"] else "NO",
                    ],
                ],
                widths=[38 * mm, 120 * mm, 45 * mm, 20 * mm],
            )
        )
        modal = sm["modal"]
        if modal is not None:
            story.append(Spacer(1, 3 * mm))
            cx, cy = modal.cumulative("x"), modal.cumulative("y")
            sub(
                "Modal periods and effective modal mass (cl 7.7.5.2)",
                table(
                    ["Mode", "T (s)", "Mass X %", "Mass Y %", "Mass RZ %", "Cumulative X %", "Cumulative Y %"],
                    [
                        (m.number, m.period, 100 * m.mass_x, 100 * m.mass_y, 100 * m.mass_rz, 100 * cx[i], 100 * cy[i])
                        for i, m in enumerate(modal.modes)
                    ],
                ),
            )
        if sm["rs"]:
            story.append(Spacer(1, 3 * mm))
            sub(
                "Response spectrum scaled to the static base shear (cl 7.7.3)",
                table(
                    ["Direction", "Modes used", "VB dynamic kN", "VB static kN", "Scale factor"],
                    [(k, r.modes_used, r.vb_dynamic, r.vb_static, r.scale) for k, r in sm["rs"].items()],
                ),
            )
        rows = storey_shear_rows(fm, sm["rs"])
        if rows:
            story.append(Spacer(1, 3 * mm))
            sub("Storey shears", table(["Level", "Static VX kN", "Static VY kN", "RSX kN", "RSY kN"], rows))
    if rep and rep.irregularities:
        story.append(Paragraph("Irregularity (IS 1893-1:2016 Tables 5 and 6)", styles["Heading2"]))
        story.append(
            table(
                ["Table", "Irregularity", "Clause", "Regular", "Status", "Detail"],
                [
                    (
                        i.table,
                        i.name,
                        i.clause,
                        "-" if i.irregular is None else ("NO" if i.irregular else "YES"),
                        i.status,
                        i.detail,
                    )
                    for i in rep.irregularities
                ],
                widths=[28 * mm, 55 * mm, 20 * mm, 15 * mm, 34 * mm, 121 * mm],
                wrap=60,
            )
        )
    if rep:
        story.append(PageBreak())
        story.append(Paragraph("Column design (IS 456 cl 39.6 biaxial)", styles["Heading2"]))
        story.append(
            table(
                ["Level", "Col", "b x D (mm)", "Pu kN", "Mux kNm", "Muy kNm", "p %", "Bars", "Ties", "Ratio", "OK"],
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
        if rep.walls:
            story.append(Spacer(1, 5 * mm))
            story.append(Paragraph("Shear wall design (IS 456 cl 32 / IS 13920 cl 10)", styles["Heading2"]))
            story.append(
                table(
                    [
                        "Level",
                        "Wall",
                        "t x Lw (m)",
                        "Pu kN",
                        "Mu kNm",
                        "Vu kN",
                        "Vert. %",
                        "Horiz. %",
                        "Vertical",
                        "Horizontal",
                        "Boundary",
                        "Ratio",
                        "OK",
                    ],
                    [
                        (
                            w.level,
                            w.mark,
                            f"{w.t:.2f} x {w.Lw:.2f}",
                            w.Pu,
                            w.Mu,
                            w.Vu,
                            100 * w.rho_v,
                            100 * w.rho_h,
                            w.vertical,
                            w.horizontal,
                            w.boundary,
                            w.utilisation,
                            "YES" if w.ok else "NO",
                        )
                        for w in rep.walls
                    ],
                    widths=[16 * mm, 12 * mm, 20 * mm, 15 * mm, 16 * mm, 14 * mm, 14 * mm, 14 * mm]
                    + [36 * mm, 36 * mm, 56 * mm, 12 * mm, 12 * mm],
                    wrap=20,
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
                    "Mu+ kNm",
                    "Mu- L kNm",
                    "Mu- R kNm",
                    "Vu kN",
                    "Tu kNm",
                    "Bottom",
                    "Top L",
                    "Top R",
                    "Stirrups",
                    "End zone (2d)",
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
                        b.T_max,
                        b.bottom,
                        b.top_l,
                        b.top_r,
                        b.stirrups,
                        str(b.links_end) if b.links_end else "-",
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
        if rep.combined_footings:
            story.append(Spacer(1, 5 * mm))
            story.append(Paragraph("Combined footings", styles["Heading2"]))
            story.append(
                table(
                    ["Columns", "P (kN)", "L x B x D (m)", "q (kN/m²)", "Top", "Bottom", "Transverse", "OK"],
                    [
                        (
                            " + ".join(c.marks),
                            c.P_service,
                            f"{c.L:.2f} x {c.B:.2f} x {c.D:.2f}",
                            c.q,
                            c.top,
                            c.bottom,
                            "; ".join(c.transverse),
                            "YES" if c.ok else "NO",
                        )
                        for c in rep.combined_footings
                    ],
                    widths=[30 * mm, 20 * mm, 35 * mm, 20 * mm, 35 * mm, 35 * mm, 85 * mm, 13 * mm],
                    wrap=40,
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
        if rep.ductile:
            ds = ductile_summary(rep)
            story.append(PageBreak())
            story.append(Paragraph("Ductile detailing – IS 13920:2016", styles["Heading2"]))
            by_kind = {}
            for d in rep.ductile:
                k = by_kind.setdefault(d.kind, [0, 0])
                k[0] += 1
                k[1] += not d.ok
            story.append(
                table(
                    ["Member kind", "Members checked", "Members failing"],
                    [(k, v[0], v[1]) for k, v in by_kind.items()] + [("All", ds["members"], ds["members_failing"])],
                )
            )
            story.append(Spacer(1, 2 * mm))
            story.append(
                Paragraph(f"{ds['checks']} checks: {ds['passed']} passed, {ds['failed']} failed.", styles["Normal"])
            )
            if ds["failures"]:
                story.append(Spacer(1, 3 * mm))
                sub(
                    "Failed checks",
                    table(
                        ["Kind", "Member", "Level", "Check", "Result", "Detail"],
                        [(d.kind, d.mark, d.level, name, "FAIL", det) for d, name, _ok, det in ds["failures"]],
                        widths=[16 * mm, 18 * mm, 22 * mm, 60 * mm, 14 * mm, 143 * mm],
                        wrap=40,
                    ),
                )
        story.append(PageBreak())
        story.append(Paragraph("Quantities &amp; cost estimate", styles["Heading2"]))
        story.append(
            table(
                ["Item", "Unit", "Qty", "Rate", "Amount"],
                list(rep.boq.get("lines", [])) + [("TOTAL", "", "", "", rep.boq.get("cost", 0.0))],
            )
        )
        if rep.boq.get("by_level"):
            story.append(Spacer(1, 4 * mm))
            sub("Quantities by floor", table(["Floor", *BOQ_HEADERS], boq_rows(rep.boq["by_level"])))
        if rep.boq.get("by_type"):
            story.append(Spacer(1, 4 * mm))
            sub("Quantities by member type", table(["Member type", *BOQ_HEADERS], boq_rows(rep.boq["by_type"])))
        if rep.warnings:
            story.append(Spacer(1, 4 * mm))
            story.append(Paragraph("Warnings", styles["Heading2"]))
            for w in rep.warnings:
                story.append(Paragraph(f"• {_esc(_plain(w))}", styles["Normal"]))

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawString(12 * mm, 8 * mm, f"{APP_NAME} {__version__} – {COMPANY}   {watermark}")
        canvas.drawRightString(landscape(A4)[0] - 12 * mm, 8 * mm, f"Page {_doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return path
