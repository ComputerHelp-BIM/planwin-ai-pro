"""Assistant orchestration: prompt -> (offline parser | LLM) -> actions -> execute."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import offline
from .actions import ActionResult, Session, execute
from .providers import ProviderConfig, ProviderError, chat


@dataclass
class Turn:
    role: str  # user | assistant
    content: str


@dataclass
class Assistant:
    session: Session
    config: ProviderConfig = field(default_factory=ProviderConfig)
    history: list[Turn] = field(default_factory=list)

    def summary(self) -> str:
        p = self.session.project
        s = p.summary()
        last = self.session.last
        if "rep" in last:
            rep = last["rep"]
            s["design"] = {
                "failures": rep.failures,
                "warnings": rep.warnings[:5],
                "concrete_m3": round(rep.boq["total_concrete"], 1),
                "steel_t": round(rep.boq["total_steel"] / 1000, 2),
            }
            worst = sorted(rep.columns, key=lambda c: -c.utilisation)[:3]
            s["critical_columns"] = [(c.mark, c.level, round(c.Pu), c.steel_pct, c.ok) for c in worst]
        if "fm" in last:
            s["lateral"] = {
                k: {"T": round(v.T, 3), "Ah": round(v.Ah, 4), "VB": round(v.Vb, 1)}
                for k, v in last["fm"].seismic.items()
            }
        s["parametric"] = bool(p.meta.get("grid_spec"))
        s["seismic"] = {"method": p.seismic.method, "rigid_diaphragm": p.seismic.rigid_diaphragm}
        s["walls"] = {pl.name: [w.mark for w in pl.walls] for pl in p.plans if pl.walls}
        s["stairs"] = [x.get("name") for x in p.stairs]
        s["water_tanks"] = [x.get("name") for x in p.water_tanks]
        s["grids"] = len(p.grids)
        s["revisions"] = [x.get("label") for x in p.meta.get("revisions") or []]
        return json.dumps(s, default=str)[:6000]

    def context(self) -> tuple[str, bool]:
        """(model summary, has_model) – call on the thread that owns the project."""
        return self.summary(), bool(self.session.project.plans)

    def plan(self, text: str, context: tuple[str, bool] | None = None) -> tuple[str, list[dict]]:
        """Interpret a prompt into (reply, actions) without touching the model.

        The GUI runs this on a worker thread while the user may keep editing, so it
        passes ``context`` captured on the GUI thread; the project is then never read here.
        """
        summary, has_model = context if context is not None else self.context()
        self.history.append(Turn("user", text))
        if self.config.provider == "offline":
            return offline.parse(text, has_model)
        try:
            out = chat(self.config, [t.__dict__ for t in self.history], summary)
            return out["reply"], out["actions"]
        except ProviderError as exc:
            reply, actions = offline.parse(text, has_model)
            return f"({exc} – used offline assistant) " + reply, actions

    def apply(self, reply: str, actions: list[dict]) -> tuple[str, ActionResult]:
        """Execute actions on the session (call from the GUI thread)."""
        res = execute(self.session, actions)
        lines = [reply] if reply else []
        lines += res.messages
        lines += [f"✖ {e}" for e in res.errors]
        final = "\n".join(lines).strip() or "Done."
        self.history.append(Turn("assistant", final))
        return final, res

    def ask(self, text: str) -> tuple[str, ActionResult]:
        """Plan + apply in one call (CLI / tests). Never raises."""
        try:
            reply, actions = self.plan(text)
        except Exception as exc:  # defensive: provider bugs must not crash callers
            reply, actions = f"✖ {exc}", []
        return self.apply(reply, actions)
