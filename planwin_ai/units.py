"""Display units: SI (kN) or MKS (tonnes, as in legacy PlanWin).

The engine always works in kN, m and MPa.  Only what the user sees and types is converted,
through one :class:`Units` object, so a value is never converted twice.  1 tonne-force =
9.80665 kN (the legacy PlanWin conversion).

Quantities
----------
``force`` kN ↔ t · ``moment`` kN·m ↔ t·m · ``line`` kN/m ↔ t/m · ``area`` kN/m² ↔ t/m² ·
``unit_weight`` kN/m³ ↔ t/m³ · ``length`` m · ``stress`` N/mm² (MPa) is never converted.
"""

from __future__ import annotations

from dataclasses import dataclass

T_TO_KN = 9.80665

_SI = {
    "force": "kN",
    "moment": "kN·m",
    "line": "kN/m",
    "area": "kN/m²",
    "unit_weight": "kN/m³",
    "length": "m",
    "stress": "N/mm²",
}
_MKS = {
    "force": "t",
    "moment": "t·m",
    "line": "t/m",
    "area": "t/m²",
    "unit_weight": "t/m³",
    "length": "m",
    "stress": "N/mm²",
}
_CONVERTED = {"force", "moment", "line", "area", "unit_weight"}


@dataclass(frozen=True)
class Units:
    system: str = "SI"  # "SI" | "MKS"

    @property
    def mks(self) -> bool:
        return self.system.upper() == "MKS"

    def label(self, quantity: str) -> str:
        return (_MKS if self.mks else _SI)[quantity]

    def show(self, value_kn: float, quantity: str) -> float:
        """Engine value (kN based) → display value."""
        if self.mks and quantity in _CONVERTED:
            return value_kn / T_TO_KN
        return value_kn

    def parse(self, value_display: float, quantity: str) -> float:
        """Display value → engine value (kN based)."""
        if self.mks and quantity in _CONVERTED:
            return value_display * T_TO_KN
        return value_display

    def fmt(self, value_kn: float, quantity: str, digits: int = 2) -> str:
        return f"{self.show(value_kn, quantity):.{digits}f} {self.label(quantity)}"

    def text(self, s: str) -> str:
        """Replace kN unit symbols in a label/header with the display units."""
        if not self.mks:
            return s
        for si, mks in (
            ("kN·m", "t·m"),
            ("kNm", "t·m"),
            ("kN/m³", "t/m³"),
            ("kN/m²", "t/m²"),
            ("kN/m2", "t/m²"),
            ("kN/m", "t/m"),
            ("kN", "t"),
        ):
            s = s.replace(si, mks)
        return s


SI = Units("SI")
MKS = Units("MKS")

#: units of the running application (the GUI sets this from the user's preference)
current: Units = SI


def set_system(system: str) -> Units:
    global current
    current = MKS if str(system).upper() == "MKS" else SI
    return current
