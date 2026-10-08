"""Project settings (IS 1893 / IS 875-3 / IS 456 / rates) and AI assistant settings."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...ai.providers import DEFAULT_MODELS, ProviderConfig, get_key, set_key
from ...core.model import Project
from ...design import is13920
from ...io.cities import city_names, lookup_city
from .common import _buttons, _dspin

#: (label, SeismicParams.method)
METHODS = (
    ("Auto – IS 1893 cl 7.7.1", "auto"),
    ("Equivalent static", "static"),
    ("Response spectrum", "response_spectrum"),
)


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
        self.s_en = QCheckBox("Include seismic loads (IS 1893-1:2016)")
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
        self.method = QComboBox()
        for txt, key in METHODS:
            self.method.addItem(txt, key)
        self.method.setCurrentIndex(max(self.method.findData(s.method), 0))
        self.rigid = QCheckBox("Rigid floor diaphragm (cl 7.6.4)")
        self.rigid.setChecked(s.rigid_diaphragm)
        self.ductile = QLabel()
        self.ductile.setWordWrap(True)
        self.zone.currentTextChanged.connect(self._ductile)
        self.R.currentIndexChanged.connect(self._ductile)
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
            ("Analysis method", self.method),
            ("", self.rigid),
        ):
            f.addRow(lab, wd)
        f.addRow(QLabel("<i>Base level 1 = plinth: weights at and below the plinth are excluded from base shear.</i>"))
        f.addRow(self.ductile)
        self._ductile()
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

    def _ductile(self, *_):
        sp = SimpleNamespace(
            zone=self.zone.currentText(), response_reduction=5.0 if self.R.currentIndex() == 1 else 3.0
        )
        req = is13920.required(SimpleNamespace(seismic=sp))
        self.ductile.setText(
            "<i>IS 13920:2016 ductile detailing <b>applies</b> (zone III–V or SMRF) and is checked in the design.</i>"
            if req
            else "<i>IS 13920:2016 ductile detailing does not apply (zone II, OMRF).</i>"
        )

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
        s.method, s.rigid_diaphragm = self.method.currentData(), self.rigid.isChecked()
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
