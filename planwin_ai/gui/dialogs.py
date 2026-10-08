"""Dialogs: project settings, AI settings, templates, autosize, column sizes, move/mirror,
joint loads, licence, about and beam diagrams."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, COMPANY, __version__
from ..ai.providers import DEFAULT_MODELS, ProviderConfig, get_key, set_key
from ..ai.templates import TEMPLATES, legacy_samples
from ..core.model import Project
from ..io.cities import city_names, lookup_city
from .theme import PALETTES

if TYPE_CHECKING:
    from .main_window import MainWindow


def _dspin(v, lo=0.0, hi=1e6, dec=3, step=0.1):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(dec)
    s.setSingleStep(step)
    s.setValue(float(v))
    return s


def _buttons(dlg: QDialog) -> QDialogButtonBox:
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    return bb


# =========================================================================== project settings
class SettingsDialog(QDialog):
    """Project info, seismic (IS 1893), wind (IS 875-3), design (IS 456), rates."""

    def __init__(self, parent, project: Project):
        super().__init__(parent)
        self.setWindowTitle("Project settings")
        self.p = project
        self.resize(560, 520)
        tabs = QTabWidget()
        # info
        w = QWidget()
        f = QFormLayout(w)
        self.name = QLineEdit(project.name)
        self.client = QLineEdit(project.client)
        self.engineer = QLineEdit(project.engineer)
        self.location = QLineEdit(project.location)
        for lab, wd in (
            ("Project name", self.name),
            ("Client", self.client),
            ("Engineer", self.engineer),
            ("Location", self.location),
        ):
            f.addRow(lab, wd)
        tabs.addTab(w, "Project")
        # seismic
        s = project.seismic
        w = QWidget()
        f = QFormLayout(w)
        self.s_en = QCheckBox("Include seismic loads (equivalent static, IS 1893-1:2016)")
        self.s_en.setChecked(s.enabled)
        self.zone = QComboBox()
        self.zone.addItems(["II", "III", "IV", "V"])
        self.zone.setCurrentText(s.zone)
        self.imp = QComboBox()
        self.imp.addItems(["1.0", "1.2", "1.5"])
        self.imp.setEditable(True)
        self.imp.setCurrentText(f"{s.importance:g}")
        self.R = QComboBox()
        self.R.addItems(["3.0 (OMRF)", "5.0 (SMRF)"])
        self.R.setCurrentIndex(1 if s.response_reduction >= 5 else 0)
        self.soil = QComboBox()
        self.soil.addItems(["hard", "medium", "soft"])
        self.soil.setCurrentText(s.soil)
        self.damp = _dspin(s.damping, 0, 0.3, 3, 0.01)
        self.infill = QCheckBox("Masonry infill (Ta = 0.09 h/√d)")
        self.infill.setChecked(s.infill)
        self.base = QSpinBox()
        self.base.setRange(0, 50)
        self.base.setValue(s.base_level)
        self.torsion = QCheckBox("Accidental torsion ±0.05 b (cl 7.8.2)")
        self.torsion.setChecked(s.accidental_torsion)
        f.addRow(self.s_en)
        for lab, wd in (
            ("Zone", self.zone),
            ("Importance factor I", self.imp),
            ("Response reduction R", self.R),
            ("Soil type", self.soil),
            ("Damping ratio", self.damp),
            ("", self.infill),
            ("", self.torsion),
            ("Seismic base level index", self.base),
        ):
            f.addRow(lab, wd)
        f.addRow(QLabel("<i>Base level 1 = plinth: weights at and below the plinth are excluded from base shear.</i>"))
        tabs.addTab(w, "Seismic")
        # wind
        wp = project.wind
        w = QWidget()
        f = QFormLayout(w)
        self.w_en = QCheckBox("Include wind loads (IS 875-3:2015)")
        self.w_en.setChecked(wp.enabled)
        self.city = QComboBox()
        self.city.addItems(city_names())
        self.city.setEditable(True)
        self.city.setCurrentText(wp.city)
        self.city.activated.connect(self._city)
        self.vb = _dspin(wp.basic_speed, 0, 80, 1, 1)
        self.terrain = QSpinBox()
        self.terrain.setRange(1, 4)
        self.terrain.setValue(wp.terrain)
        self.k1 = _dspin(wp.k1, 0.5, 1.5, 3, 0.01)
        self.k3 = _dspin(wp.k3, 0.5, 1.5, 3, 0.01)
        self.k4 = _dspin(wp.k4, 1, 1.5, 3, 0.05)
        self.cf = _dspin(wp.force_coeff, 0.5, 2.5, 2, 0.05)
        self.parapet = _dspin(wp.parapet, 0, 5, 2, 0.1)
        self.below = _dspin(wp.below_ground, 0, 20, 2, 0.1)
        f.addRow(self.w_en)
        for lab, wd in (
            ("City (sets Vb & zone)", self.city),
            ("Basic wind speed Vb (m/s)", self.vb),
            ("Terrain category", self.terrain),
            ("k1 risk coefficient", self.k1),
            ("k3 topography", self.k3),
            ("k4 importance (cyclonic)", self.k4),
            ("Force coefficient Cf", self.cf),
            ("Parapet height (m)", self.parapet),
            ("Height below ground (m)", self.below),
        ):
            f.addRow(lab, wd)
        tabs.addTab(w, "Wind")
        # design
        d = project.design
        w = QWidget()
        f = QFormLayout(w)
        self.fy = QComboBox()
        self.fy.addItems(["415", "500", "550"])
        self.fy.setCurrentText(f"{int(d.fy_main)}")
        self.sbc = _dspin(d.sbc, 10, 2000, 0, 10)
        self.cov_b = _dspin(d.beam_cover * 1000, 15, 75, 0, 5)
        self.cov_c = _dspin(d.column_cover * 1000, 20, 75, 0, 5)
        self.cov_s = _dspin(d.slab_cover * 1000, 15, 50, 0, 5)
        self.cov_f = _dspin(d.footing_cover * 1000, 40, 100, 0, 5)
        self.pmin = _dspin(d.min_column_steel_pct, 0.8, 2, 2, 0.1)
        self.pmax = _dspin(d.max_column_steel_pct, 2, 6, 2, 0.5)
        self.ratio = _dspin(d.two_way_ratio_limit, 1, 3, 2, 0.1)
        self.cont = QCheckBox("Continuous beams in PlanWin load take-down")
        self.cont.setChecked(d.continuity_in_load_transfer)
        self.tors = QCheckBox("Torsion release (J = 10 %)")
        self.tors.setChecked(d.torsion_release)
        self.keff = _dspin(d.effective_length_factor, 0.5, 2.5, 2, 0.05)
        self.crb = _dspin(d.crack_beam, 0.1, 1.0, 2, 0.05)
        self.crc = _dspin(d.crack_column, 0.1, 1.0, 2, 0.05)
        for lab, wd in (
            ("Steel grade fy (MPa)", self.fy),
            ("Safe bearing capacity (kN/m²)", self.sbc),
            ("Beam cover (mm)", self.cov_b),
            ("Column cover (mm)", self.cov_c),
            ("Slab cover (mm)", self.cov_s),
            ("Footing cover (mm)", self.cov_f),
            ("Min column steel %", self.pmin),
            ("Max column steel %", self.pmax),
            ("Two-way slab if ly/lx ≤", self.ratio),
            ("Column effective length factor", self.keff),
            ("Cracked I factor – beams", self.crb),
            ("Cracked I factor – columns", self.crc),
        ):
            f.addRow(lab, wd)
        f.addRow(self.cont)
        f.addRow(self.tors)
        tabs.addTab(w, "Design")
        # rates
        w = QWidget()
        f = QFormLayout(w)
        self.rates = {}
        for k, v in d.rates.items():
            sp = _dspin(v, 0, 1e7, 0, 50)
            self.rates[k] = sp
            f.addRow(k.replace("_", " ").replace("m2", "m²").replace("kg", "per kg").title() + " (₹)", sp)
        tabs.addTab(w, "Rates")
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(_buttons(self))

    def _city(self):
        info = lookup_city(self.city.currentText())
        if info:
            if info["vb"]:
                self.vb.setValue(info["vb"])
            self.zone.setCurrentText(info["zone"])

    def apply(self):
        p = self.p
        p.name, p.client, p.engineer, p.location = (
            self.name.text(),
            self.client.text(),
            self.engineer.text(),
            self.location.text(),
        )
        s = p.seismic
        s.enabled, s.zone, s.soil = self.s_en.isChecked(), self.zone.currentText(), self.soil.currentText()
        s.importance = float(self.imp.currentText().split()[0])
        s.response_reduction = 5.0 if self.R.currentIndex() == 1 else 3.0
        s.damping, s.infill, s.base_level = self.damp.value(), self.infill.isChecked(), self.base.value()
        s.accidental_torsion = self.torsion.isChecked()
        w = p.wind
        w.enabled, w.city, w.basic_speed, w.terrain = (
            self.w_en.isChecked(),
            self.city.currentText(),
            self.vb.value(),
            self.terrain.value(),
        )
        w.k1, w.k3, w.k4, w.force_coeff = self.k1.value(), self.k3.value(), self.k4.value(), self.cf.value()
        w.parapet, w.below_ground = self.parapet.value(), self.below.value()
        d = p.design
        d.fy_main = d.fy_shear = float(self.fy.currentText())
        d.sbc = self.sbc.value()
        d.beam_cover, d.column_cover = self.cov_b.value() / 1000, self.cov_c.value() / 1000
        d.slab_cover, d.footing_cover = self.cov_s.value() / 1000, self.cov_f.value() / 1000
        d.min_column_steel_pct, d.max_column_steel_pct = self.pmin.value(), self.pmax.value()
        d.two_way_ratio_limit, d.continuity_in_load_transfer = self.ratio.value(), self.cont.isChecked()
        d.torsion_release, d.effective_length_factor = self.tors.isChecked(), self.keff.value()
        d.crack_beam, d.crack_column = self.crb.value(), self.crc.value()
        for k, sp in self.rates.items():
            d.rates[k] = sp.value()


# =========================================================================== AI settings
class AISettingsDialog(QDialog):
    def __init__(self, parent, cfg: ProviderConfig):
        super().__init__(parent)
        self.setWindowTitle("AI assistant settings")
        self.cfg = cfg
        f = QFormLayout(self)
        self.provider = QComboBox()
        self.provider.addItems(["offline", "claude", "openai", "ollama"])
        self.provider.setCurrentText(cfg.provider)
        self.model = QLineEdit(cfg.model)
        self.base = QLineEdit(cfg.base_url)
        self.base.setPlaceholderText("default endpoint")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.provider.currentTextChanged.connect(self._prov)
        f.addRow("Engine", self.provider)
        f.addRow("Model", self.model)
        f.addRow("API key", self.key)
        f.addRow("Endpoint (optional)", self.base)
        note = QLabel(
            "Keys are stored in Windows Credential Manager, never in project files. Only a compact model summary "
            "(no drawings) is sent to the AI provider. 'offline' works without internet."
        )
        note.setWordWrap(True)
        f.addRow(note)
        f.addRow(_buttons(self))
        self._prov(cfg.provider)

    def _prov(self, p):
        self.model.setPlaceholderText(DEFAULT_MODELS.get(p, ""))
        has = bool(get_key(p)) if p in ("claude", "openai") else False
        self.key.setEnabled(p in ("claude", "openai"))
        self.key.setPlaceholderText("•••••• saved (leave blank to keep)" if has else "paste API key")

    def apply(self) -> ProviderConfig:
        p = self.provider.currentText()
        if self.key.text().strip() and p in ("claude", "openai"):
            if not set_key(p, self.key.text().strip()):
                QMessageBox.warning(
                    self,
                    "AI",
                    "Could not store the key in the credential manager; set the "
                    "ANTHROPIC_API_KEY / OPENAI_API_KEY environment variable instead.",
                )
        return ProviderConfig(p, self.model.text().strip(), self.base.text().strip())


# =========================================================================== templates
class TemplateDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("New from template")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<b>Parametric templates</b> – fully editable, analysis-ready models"))
        self.list = QListWidget()
        for t in TEMPLATES:
            it = QListWidgetItem(f"{t.title}\n    {t.description}")
            it.setData(Qt.UserRole, ("template", t.key))
            self.list.addItem(it)
        for s in legacy_samples():
            it = QListWidgetItem(f"Legacy PlanWin sample: {s}\n    Imported from the original PlanWin sample set")
            it.setData(Qt.UserRole, ("legacy", s))
            self.list.addItem(it)
        self.list.setCurrentRow(1)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        lay.addWidget(self.list)
        lay.addWidget(_buttons(self))

    def choice(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None


# =========================================================================== autosize
class AutoSizeDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Auto-size columns (FrameWin AUTOSIZE)")
        f = QFormLayout(self)
        self.breadth = _dspin(0.0, 0, 2, 3, 0.05)
        self.breadth.setSpecialValueText("keep plan breadth")
        self.pct = _dspin(1.0, 0.8, 4, 2, 0.1)
        self.same = QCheckBox("Same size on all floors")
        self.same.setChecked(True)
        self.step = _dspin(0.05, 0, 0.5, 3, 0.05)
        self.mf = _dspin(1.25, 1.0, 2.0, 2, 0.05)
        self.inc = _dspin(0.0, 0, 0.3, 3, 0.025)
        for lab, wd in (
            ("Breadth b (m)", self.breadth),
            ("Assumed steel %", self.pct),
            ("", self.same),
            ("Max reduction per floor (m)", self.step),
            ("Moment allowance factor", self.mf),
            ("Increase below ground, each side (m)", self.inc),
        ):
            f.addRow(lab, wd)
        info = QLabel("Depth from Pu·factor ≤ 0.4 fck Ac + 0.67 fy Asc using the cumulative PlanWin column loads.")
        info.setWordWrap(True)
        f.addRow(info)
        f.addRow(_buttons(self))


# =========================================================================== column sizes
class ColumnSizesDialog(QDialog):
    """Per-level column sizes (FrameWin 'Size' grid) with copy up/down."""

    def __init__(self, parent, project: Project):
        super().__init__(parent)
        self.p = project
        self.setWindowTitle("Column sizes by level (b × d in m)")
        self.resize(720, 480)
        marks = []
        self.default = {}
        for i, lv in enumerate(project.levels, start=1):
            plan = project.plan(lv.plan)
            for c in plan.columns if plan else []:
                if c.mark not in marks:
                    marks.append(c.mark)
                self.default[(c.mark, i)] = c
        self.marks = sorted(marks, key=lambda m: (len(m), m))
        n = len(project.levels)
        self.t = QTableWidget(len(self.marks), n)
        self.t.setHorizontalHeaderLabels([lv.name for lv in project.levels])
        self.t.setVerticalHeaderLabels(self.marks)
        for r, mk in enumerate(self.marks):
            for c in range(n):
                col = self.default.get((mk, c + 1))
                if col is None:
                    it = QTableWidgetItem("–")
                    it.setFlags(Qt.ItemIsEnabled)
                else:
                    b, d, _ = project.column_size(mk, c + 1, col)
                    it = QTableWidgetItem(f"{b:g} x {d:g}")
                self.t.setItem(r, c, it)
        lay = QVBoxLayout(self)
        lay.addWidget(
            QLabel(
                "Edit as 'b x d'. Select a cell and use the buttons to copy to all levels above/below or the whole row."
            )
        )
        lay.addWidget(self.t)
        row = QHBoxLayout()
        for txt, fn in (
            ("Copy ⇈ above", lambda: self._copy(1)),
            ("Copy ⇊ below", lambda: self._copy(-1)),
            ("Copy to all columns", self._copy_all),
        ):
            b = QPushButton(txt)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(_buttons(self))

    def _copy(self, d):
        r, c = self.t.currentRow(), self.t.currentColumn()
        if r < 0 or c < 0:
            return
        v = self.t.item(r, c).text()
        rng = range(c + 1, self.t.columnCount()) if d > 0 else range(0, c)
        for k in rng:
            if self.t.item(r, k).text() != "–":
                self.t.item(r, k).setText(v)

    def _copy_all(self):
        r, c = self.t.currentRow(), self.t.currentColumn()
        if r < 0 or c < 0:
            return
        v = self.t.item(r, c).text()
        for k in range(self.t.rowCount()):
            if self.t.item(k, c).text() != "–":
                self.t.item(k, c).setText(v)

    def apply(self):
        for r, mk in enumerate(self.marks):
            for c in range(self.t.columnCount()):
                txt = self.t.item(r, c).text().lower().replace("×", "x")
                col = self.default.get((mk, c + 1))
                if col is None or "x" not in txt:
                    continue
                b, d = (float(v) for v in txt.split("x")[:2])
                if b > 0 and d > 0:
                    _, _, ang = self.p.column_size(mk, c + 1, col)
                    self.p.set_column_size(mk, c + 1, b, d, ang)


# =========================================================================== move / mirror / joint load
class MoveCopyDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Move / copy selection")
        f = QFormLayout(self)
        self.dx, self.dy = _dspin(0, -1e4, 1e4, 3, 0.5), _dspin(0, -1e4, 1e4, 3, 0.5)
        self.copy = QCheckBox("Copy (keep originals)")
        self.copy.setChecked(True)
        self.n = QSpinBox()
        self.n.setRange(1, 50)
        f.addRow("ΔX (m)", self.dx)
        f.addRow("ΔY (m)", self.dy)
        f.addRow(self.copy)
        f.addRow("Number of copies", self.n)
        f.addRow(_buttons(self))


class MirrorDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Mirror selection")
        f = QFormLayout(self)
        self.vertical = QRadioButton("About vertical line X = …")
        self.vertical.setChecked(True)
        self.horizontal = QRadioButton("About horizontal line Y = …")
        self.at = _dspin(0, -1e4, 1e4, 3, 0.5)
        self.copy = QCheckBox("Keep originals (mirror copy)")
        self.copy.setChecked(True)
        f.addRow(self.vertical)
        f.addRow(self.horizontal)
        f.addRow("Line position (m)", self.at)
        f.addRow(self.copy)
        f.addRow(_buttons(self))


class JointLoadDialog(QDialog):
    """Water-tank and other concentrated loads on column tops (FrameWin)."""

    def __init__(self, parent, project: Project):
        super().__init__(parent)
        self.p = project
        self.setWindowTitle("Joint loads (e.g. water tank)")
        self.resize(520, 360)
        self.t = QTableWidget(len(project.joint_loads), 4)
        self.t.setHorizontalHeaderLabels(["Level #", "Column mark", "Fz down (kN)", "Case D/L"])
        self.t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for r, jl in enumerate(project.joint_loads):
            for c, k in enumerate(("level", "mark", "fz", "case")):
                self.t.setItem(r, c, QTableWidgetItem(str(jl.get(k, ""))))
        lay = QVBoxLayout(self)
        lay.addWidget(
            QLabel(f"Level # counts from 1 (= {project.levels[0].name if project.levels else 'first level'}).")
        )
        lay.addWidget(self.t)
        row = QHBoxLayout()
        a = QPushButton("+ Row")
        d = QPushButton("− Row")
        a.clicked.connect(lambda: self._add())
        d.clicked.connect(lambda: self.t.removeRow(self.t.currentRow()))
        row.addWidget(a)
        row.addWidget(d)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(_buttons(self))

    def _add(self):
        r = self.t.rowCount()
        self.t.insertRow(r)
        for c, v in enumerate((len(self.p.levels), "C1", "150", "D")):
            self.t.setItem(r, c, QTableWidgetItem(v))

    def apply(self):
        out = []
        for r in range(self.t.rowCount()):
            try:
                out.append(
                    {
                        "level": int(self.t.item(r, 0).text()),
                        "mark": self.t.item(r, 1).text().strip(),
                        "fz": float(self.t.item(r, 2).text()),
                        "case": self.t.item(r, 3).text().strip() or "D",
                    }
                )
            except (ValueError, AttributeError):
                continue
        self.p.joint_loads = out


# =========================================================================== licence & about
class LicenseDialog(QDialog):
    def __init__(self, parent, state_label: str):
        super().__init__(parent)
        self.setWindowTitle("Licence")
        self.resize(520, 340)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>Status:</b> {state_label}"))
        lay.addWidget(QLabel("Paste the licence text you received from Computer Help, or load the .lic file:"))
        self.text = QPlainTextEdit()
        lay.addWidget(self.text)
        b = QPushButton("Load licence file…")
        b.clicked.connect(self._load)
        lay.addWidget(b)
        lay.addWidget(QLabel("Sales & support: support@buildingsoftware.in · www.buildingsoftware.in"))
        lay.addWidget(_buttons(self))

    def _load(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Licence file", "", "Licence (*.lic *.json);;All files (*)")
        if fn:
            with open(fn, encoding="utf-8") as f:
                self.text.setPlainText(f.read())


def about_text(license_label: str) -> str:
    return (
        f"<h2>{APP_NAME} {__version__}</h2>"
        f"<p>AI-assisted structural pre-processor, analysis and IS-code design for RCC framed buildings.<br>"
        f"Successor to PlanWin / FrameWin by {COMPANY}.</p>"
        "<p>Codes: IS 456:2000 · IS 875 (Parts 1–3) · IS 1893 (Part 1):2016<br>"
        "Exports: STAAD.Pro (.std) · ETABS (.e2k) · DXF 2D/3D · Excel · PDF</p>"
        f"<p><b>{license_label}</b></p>"
        "<p style='color:gray'>Results are design aids and must be verified by a qualified structural engineer.</p>"
        f"<p>© {COMPANY} / Building Software · www.buildingsoftware.in</p>"
    )


# =========================================================================== beam diagram
class _Plot(QWidget):
    def __init__(self, x, y, title, unit, theme):
        super().__init__()
        self.x, self.y, self.title, self.unit, self.theme = np.asarray(x), np.asarray(y), title, unit, theme
        self.setMinimumHeight(170)

    def paintEvent(self, _):
        pal = PALETTES[self.theme]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(pal["panel"]))
        m = 40
        W, H = self.width() - 2 * m, self.height() - 2 * m
        if len(self.x) < 2 or W <= 0:
            return
        L = float(self.x[-1]) or 1.0
        ymax = float(np.max(np.abs(self.y))) or 1.0

        def pt(xv, yv):
            # sagging plotted below the axis (engineering convention)
            return QPointF(m + xv / L * W, m + H / 2 + yv / ymax * H / 2)

        p.setPen(QPen(QColor(pal["muted"]), 1))
        p.drawLine(pt(0, 0), pt(L, 0))
        p.setPen(QPen(QColor("#2F7DE1"), 2))
        for i in range(len(self.x) - 1):
            p.drawLine(pt(self.x[i], self.y[i]), pt(self.x[i + 1], self.y[i + 1]))
        p.setPen(QColor(pal["text"]))
        p.drawText(8, 16, f"{self.title}   max {np.max(self.y):.1f} / min {np.min(self.y):.1f} {self.unit}")
        for k in range(9):
            xv = L * k / 8
            yv = float(np.interp(xv, self.x, self.y))
            q = pt(xv, yv)
            p.drawText(QPointF(q.x() - 14, q.y() + (14 if yv >= 0 else -4)), f"{yv:.1f}")


class BeamDiagramDialog(QDialog):
    def __init__(self, parent: MainWindow, beam, br):
        super().__init__(parent)
        self.setWindowTitle(f"Beam {beam.mark} – factored 1.5(D+L) diagrams (PlanWin)")
        self.resize(760, 520)
        dia = br.diagram(True)
        lay = QVBoxLayout(self)
        sup = ", ".join(f"{s.kind} @ {s.x:.2f} m" for s in br.supports)
        lay.addWidget(QLabel(f"Span {br.length:.3f} m · {beam.b * 1000:.0f}×{beam.d * 1000:.0f} mm · supports: {sup}"))
        lay.addWidget(
            _Plot(dia["x"], dia["M"], "Bending moment (sagging +, drawn below axis)", "kN·m", parent.theme_name)
        )
        lay.addWidget(_Plot(dia["x"], dia["V"], "Shear force", "kN", parent.theme_name))
        from ..core.model import grade_fck
        from ..design import is456

        fck = grade_fck(beam.grade)
        fy = parent.project.design.fy_main
        cov = parent.project.design.beam_cover * 1000
        rows = []
        for k in range(9):
            xv = br.length * k / 8
            M = float(np.interp(xv, dia["x"], dia["M"]))
            V = float(np.interp(xv, dia["x"], dia["V"]))
            fl = is456.flexure(abs(M), fck, fy, beam.b * 1000, beam.d * 1000, cov)
            rows.append((f"{k}/8", M, V, fl.ast if M > 0 else 0.0, fl.ast if M < 0 else 0.0))
        t = QTableWidget(len(rows), 5)
        t.setHorizontalHeaderLabels(["Section", "Mu kN·m", "Vu kN", "Bottom Ast mm²", "Top Ast mm²"])
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                t.setItem(r, c, QTableWidgetItem(f"{v:.1f}" if isinstance(v, float) else v))
        lay.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
