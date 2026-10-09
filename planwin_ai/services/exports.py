"""Export registry – one table of every output format, used by the AI actions, the CLI and the GUI.

Adding an export means adding one :class:`ExportFormat` here: the AI schema, the
``cli run --export`` choices and the GUI menus are all generated from this registry.
Writers receive an :class:`ExportContext`, which runs analysis or design on demand
(through callbacks supplied by the caller) so a writer never has to know how results
are produced or cached.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core.frame import FrameAnalysis, FrameModel
    from ..core.model import Plan, Project
    from ..core.plan_engine import PlanResult
    from ..design.report import DesignReport


def safe_filename(name: str, default: str = "project") -> str:
    """File-system-safe base name (Windows forbids <>:"/\\|?*, trailing dots/spaces and device names)."""
    out = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(name or "")).replace(" ", "_").strip("._ ")[:120]
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com\d|lpt\d)(\..*)?", out):
        out = "_" + out
    return out or default


@dataclass
class ExportContext:
    """What a writer may use.  ``frame()``/``design()`` trigger analysis/design when needed."""

    project: Project
    out_dir: str
    watermark: str
    params: dict[str, Any]
    ensure_frame: Callable[[], tuple[FrameModel, FrameAnalysis]]
    ensure_design: Callable[[], DesignReport]
    plan_results: Callable[[], dict[str, PlanResult]]
    extra: dict[str, Any] = field(default_factory=dict)

    def frame(self) -> tuple[FrameModel, FrameAnalysis]:
        return self.ensure_frame()

    def design(self) -> DesignReport:
        return self.ensure_design()

    def plan(self) -> Plan:
        """The plan named in ``params['plan']`` (default: the first plan)."""
        pr = self.project
        tgt = self.params.get("plan")
        plan = (pr.plan(tgt) if tgt else None) or (pr.plans[0] if pr.plans else None)
        if plan is None:
            raise ValueError("no plan to export")
        return plan

    def default_path(self, suffix: str) -> str:
        return os.path.join(self.out_dir, safe_filename(self.project.name) + suffix)


@dataclass(frozen=True)
class ExportFormat:
    key: str
    label: str  # menu text
    suffix: str  # appended to the project name for the default file name
    file_filter: str  # Qt file-dialog filter
    writer: Callable[[ExportContext, str], str]
    aliases: tuple[str, ...] = ()
    group: str = "Analysis models"  # menu / ribbon grouping
    per_plan: bool = False  # the default file name includes the plan name

    def default_path(self, ctx: ExportContext) -> str:
        if self.per_plan:
            return ctx.default_path(f"_{safe_filename(ctx.plan().name, 'plan')}{self.suffix}")
        return ctx.default_path(self.suffix)


_REGISTRY: dict[str, ExportFormat] = {}


def register(fmt: ExportFormat) -> ExportFormat:
    for k in (fmt.key, *fmt.aliases):
        if k in _REGISTRY and _REGISTRY[k] is not fmt:
            raise ValueError(f"export key '{k}' registered twice")
        _REGISTRY[k] = fmt
    return fmt


def get(key: str) -> ExportFormat:
    fmt = _REGISTRY.get(str(key).strip().lower())
    if fmt is None:
        raise ValueError(f"unknown export format '{key}' – choose from {', '.join(keys())}")
    return fmt


def formats() -> list[ExportFormat]:
    """Distinct formats in registration order."""
    return list({id(f): f for f in _REGISTRY.values()}.values())


def keys() -> list[str]:
    return [f.key for f in formats()]


def run(fmt: ExportFormat, ctx: ExportContext, path: str | None = None) -> str:
    out = path or fmt.default_path(ctx)
    folder = os.path.dirname(os.path.abspath(out))
    os.makedirs(folder, exist_ok=True)
    return fmt.writer(ctx, out)


# ----------------------------------------------------------------------------- writers
def _staad(ctx: ExportContext, path: str) -> str:
    from ..io.staad import write_staad

    return write_staad(ctx.frame()[0], path, watermark=ctx.watermark)


def _etabs(ctx: ExportContext, path: str) -> str:
    from ..io.etabs import write_etabs

    return write_etabs(ctx.frame()[0], path, watermark=ctx.watermark)


def _dxf_plan(ctx: ExportContext, path: str) -> str:
    from ..core.plan_engine import PlanEngine
    from ..io.dxf_io import export_plan_dxf

    plan = ctx.plan()
    d = ctx.project.design
    res = PlanEngine(plan, None, d.two_way_ratio_limit, d.continuity_in_load_transfer).run()
    return export_plan_dxf(plan, path, res, watermark=ctx.watermark)


def _dxf_frame(ctx: ExportContext, path: str) -> str:
    from ..io.dxf_io import export_frame_dxf

    return export_frame_dxf(ctx.frame()[0], path)


def _excel(ctx: ExportContext, path: str) -> str:
    from ..io.excel_report import write_excel

    rep = ctx.design()
    fm, fa = ctx.frame()
    return write_excel(path, ctx.project, ctx.plan_results(), fm, fa, rep, ctx.watermark)


def _pdf(ctx: ExportContext, path: str) -> str:
    from ..io.pdf_report import write_pdf

    rep = ctx.design()
    fm, fa = ctx.frame()
    return write_pdf(path, ctx.project, ctx.plan_results(), fm, rep, ctx.watermark, fa=fa)


def _bbs(ctx: ExportContext, path: str) -> str:
    from ..io.bbs import build_bbs, write_bbs_excel

    rep = ctx.design()
    fm, _ = ctx.frame()
    return write_bbs_excel(path, build_bbs(ctx.project, fm, rep), ctx.project, ctx.watermark)


def _boq(ctx: ExportContext, path: str) -> str:
    from ..io.boq_excel import write_boq_excel

    return write_boq_excel(path, ctx.project, ctx.design(), ctx.watermark)


def _schedules(ctx: ExportContext, path: str) -> str:
    from ..io.schedules import write_schedules_excel

    return write_schedules_excel(path, ctx.project, ctx.design(), ctx.watermark)


def _details(ctx: ExportContext, path: str) -> str:
    from ..io.detail_dxf import write_detail_drawings

    rep = ctx.design()
    fm, _ = ctx.frame()
    return write_detail_drawings(path, ctx.project, fm, rep, ctx.watermark)


def _calc(ctx: ExportContext, path: str) -> str:
    from ..io.calc_sheets import write_calc_sheets

    rep = ctx.design()
    fm, fa = ctx.frame()
    sel = ctx.params
    return write_calc_sheets(
        path,
        ctx.project,
        fm,
        fa,
        rep,
        member_ids=sel.get("member_ids"),
        footing_marks=sel.get("footing_marks"),
        slabs=sel.get("slabs"),
        watermark=ctx.watermark,
    )


def _project(ctx: ExportContext, path: str) -> str:
    from ..io.project_io import save_project

    return save_project(ctx.project, path)


register(ExportFormat("staad", "STAAD.Pro file (.std)", ".std", "STAAD (*.std)", _staad, ("std",)))
register(ExportFormat("etabs", "ETABS file (.e2k)", ".e2k", "ETABS (*.e2k)", _etabs, ("e2k",)))
register(
    ExportFormat(
        "dxf", "DXF – current plan", "_2DPLAN.dxf", "DXF (*.dxf)", _dxf_plan, ("cad",), group="Drawings", per_plan=True
    )
)
register(ExportFormat("dxf3d", "DXF – 3-D frame", "_3d.dxf", "DXF (*.dxf)", _dxf_frame, group="Drawings"))
register(ExportFormat("excel", "Excel workbook", ".xlsx", "Excel (*.xlsx)", _excel, ("xlsx",), group="Reports"))
register(ExportFormat("pdf", "PDF report", ".pdf", "PDF (*.pdf)", _pdf, ("report",), group="Reports"))
register(
    ExportFormat(
        "calc",
        "Design calculation sheets (PDF)",
        "_calc.pdf",
        "PDF (*.pdf)",
        _calc,
        ("calcs", "calculations"),
        group="Reports",
    )
)
register(
    ExportFormat(
        "bbs",
        "Bar bending schedule (Excel)",
        "_BBS.xlsx",
        "Excel (*.xlsx)",
        _bbs,
        ("bar bending schedule",),
        group="Reports",
    )
)
register(
    ExportFormat(
        "boq",
        "BOQ & cost estimate (Excel)",
        "_BOQ.xlsx",
        "Excel (*.xlsx)",
        _boq,
        ("bill of quantities", "estimate", "cost"),
        group="Reports",
    )
)
register(
    ExportFormat(
        "schedules",
        "Member schedules (Excel)",
        "_schedules.xlsx",
        "Excel (*.xlsx)",
        _schedules,
        ("schedule", "column schedule", "beam schedule"),
        group="Reports",
    )
)
register(
    ExportFormat(
        "details",
        "Reinforcement detail drawings (DXF)",
        "_details.dxf",
        "DXF (*.dxf)",
        _details,
        ("detail", "drawings"),
        group="Drawings",
    )
)
register(
    ExportFormat(
        "project", "PlanWin AI Pro project", ".pwai", "PlanWin AI Pro (*.pwai)", _project, ("pwai",), group="Project"
    )
)
