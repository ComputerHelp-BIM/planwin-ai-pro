"""Application commands: views, theme, units, AI assistant, licence and help."""

from __future__ import annotations

import json

from PySide6.QtWidgets import QApplication, QMessageBox

from ... import APP_NAME, units
from ...licensing.license import install_license
from .. import dialogs
from ..theme import qpalette, qss

QUICK_START = (
    "1. File ▸ New from template, or press Ctrl+K and describe your building to the AI assistant.\n"
    "2. Plan tab: draw slabs (R / P), place columns (C or Auto columns), walls (W), then Auto beams.\n"
    "3. Analyse plan (F5) – check the Issues tab until the load difference is ~0 %.\n"
    "4. Frame tab ▸ Levels / Copy floor: assign a plan to each level with storey heights.\n"
    "5. Loads tab: staircase and water-tank wizards, Project settings (zone, wind, SBC).\n"
    "6. Frame tab: Auto-size columns, seismic method and diaphragm, then Analyse frame (F6).\n"
    "7. Design tab: Design all (F7), IS 13920 and irregularity checks, Optimise sizes, revisions.\n"
    "8. Output tab: STAAD / ETABS / DXF / Excel / PDF / calculation sheets / BBS / detail drawings.\n\n"
    "Shortcuts: S select · H pan · R rectangle slab · P polygon slab · C column · B beam · W wall · "
    "D measure · A area · Shift+D dimension · F8 ortho · F zoom extents · Del delete · Ctrl+Z/Y undo/redo · "
    "double-click a beam for BM/SF · double-click a ribbon tab to collapse the ribbon."
)


class AppCommands:
    # ------------------------------------------------------------------ views
    def show_result_tab(self, name: str) -> bool:
        if name not in self.results.tables:
            return False
        self.results.show_tab(name)
        return True

    def show_plan_view(self):
        self.show_workspace()
        self.tabs.setCurrentIndex(0)

    def show_3d_view(self):
        self.show_workspace()
        self.tabs.setCurrentIndex(1)

    # ------------------------------------------------------------------ start page / workspace
    def on_start_page(self) -> bool:
        return self.central.currentWidget() is self.start_page

    def show_start(self):
        """Start page in place of the plan/3-D views; the panels are hidden until work starts."""
        if not self.on_start_page():
            # isHidden, not isVisible: at start-up the window itself is not shown yet
            self._dock_visible = [not d.isHidden() for d in self.panel_docks()]
            for d in self.panel_docks():
                d.hide()
        self.start_page.refresh()
        self.central.setCurrentWidget(self.start_page)

    def show_workspace(self):
        if not self.on_start_page():
            return
        self.central.setCurrentWidget(self.tabs)
        for d, vis in zip(self.panel_docks(), self._dock_visible or [True] * 4, strict=False):
            d.setVisible(vis)
        self._dock_visible = None
        self.canvas.zoom_extents()

    def open_results(self, name: str):
        self.show_workspace()
        self.results_dock.show()
        self.results_dock.raise_()
        self.show_result_tab(name)

    def start_with_ai(self, text: str):
        """Start-page prompt: open the workspace and send the description to the assistant."""
        self.show_workspace()
        self.chat.show()
        self.chat.raise_()
        self.chat.send(text)

    def search_commands(self) -> list:
        """Everything the command search (Ctrl+Q) can find: ribbon commands, results tables, plans."""
        from ..command_search import Command, from_action

        where: dict[int, str] = {}  # action → ribbon tab, shown as the result's category
        for name, pg in self.ribbon.pages.items():
            for g in pg.groups.values():
                for b in g.buttons:
                    where.setdefault(id(b.defaultAction()), name)
        out = [
            from_action(a, where.get(id(a), ""), "export download file" if k.startswith("export_") else "")
            for k, a in self.cmd.items()
            if a.isEnabled() and k not in ("search", "method", "renumber")
        ]
        out += [
            Command(f"Show results: {name}", lambda n=name: self.open_results(n), "Results", "table results tab")
            for name in self.results.tables
        ]
        out += [
            Command(f"Go to plan: {p.name}", lambda n=p.name: (self.show_workspace(), self.set_current_plan(n)), "Plan")
            for p in self.project.plans
        ]
        return out

    def focus_properties(self):
        self.props_dock.show()
        self.props_dock.raise_()

    def toggle_chat(self):
        if self.chat.isVisible() and not self.chat.visibleRegion().isEmpty():
            self.chat.hide()
        else:
            self.chat.show()
            self.chat.raise_()
            self.chat.input.setFocus()

    def _goto_issue(self, issue):
        if issue.at:
            self.tabs.setCurrentIndex(0)
            self.canvas.focus_point(*issue.at)
        if issue.obj_id and self.current_plan() and self.current_plan().find(issue.obj_id):
            self.canvas.select_ids([issue.obj_id])

    # ------------------------------------------------------------------ theme and units
    def apply_theme(self, name: str):
        self.theme_name = name
        self.settings.setValue("theme", name)
        app = QApplication.instance()
        app.setPalette(qpalette(name))  # widgets the stylesheet does not reach never pick up the OS palette
        app.setStyleSheet(qss(name))
        if hasattr(self, "chat"):
            self.chat.rerender()
        if "dark" in getattr(self, "cmd", {}):
            self.cmd["dark"].setChecked(name == "dark")
        self.canvas.update()
        self.view3d.canvas.update()

    def toggle_theme(self):
        self.apply_theme("dark" if self.theme_name == "light" else "light")

    def set_units(self, system: str):
        """Display units only – the model and every file stay in kN."""
        units.set_system(system)
        self.settings.setValue("units", units.current.system)
        if self.units_combo.currentData() != units.current.system:
            self.units_combo.setCurrentIndex(self.units_combo.findData(units.current.system))
        self.refresh_all()
        self.statusBar().showMessage(f"Units: {'tonnes (t, t·m)' if units.current.mks else 'kN, kN·m'}", 4000)

    # ------------------------------------------------------------------ AI, licence, help
    def ai_settings(self):
        dlg = dialogs.AISettingsDialog(self, self.assistant.config)
        if dlg.exec():
            cfg = dlg.apply()
            self.assistant.config = cfg
            self.settings.setValue("ai/provider", cfg.provider)
            self.settings.setValue("ai/model", cfg.model)
            self.settings.setValue("ai/base_url", cfg.base_url)
            self.chat.refresh_provider()

    def license_dialog(self):
        dlg = dialogs.LicenseDialog(self, self.license.label())
        if dlg.exec() and dlg.text.toPlainText().strip():
            try:
                self.license = install_license(dlg.text.toPlainText())
            except (ValueError, json.JSONDecodeError) as exc:
                QMessageBox.critical(self, "Licence", f"Licence not accepted: {exc}")
                return
            self.session.watermark = self.license.watermark
            self.session.exports_allowed = self.license.exports_allowed
            self.lic_lbl.setText(self.license.label())
            QMessageBox.information(self, "Licence", self.license.label())

    def about(self):
        QMessageBox.about(self, f"About {APP_NAME}", dialogs.about_text(self.license.label()))

    def quick_start(self):
        QMessageBox.information(self, "Quick start", QUICK_START)
