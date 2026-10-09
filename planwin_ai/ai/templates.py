"""Pre-built model library (parametric templates + bundled legacy samples)."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources

from ..core.generator import GridSpec, grid_building
from ..core.model import Level, Project


@dataclass(frozen=True)
class Template:
    key: str
    title: str
    description: str
    spec: dict


TEMPLATES: list[Template] = [
    Template(
        "bungalow",
        "Bungalow G+1 with balcony",
        "2 x 2 bays of 4.0 m, 1.2 m cantilever balcony, residential.",
        dict(
            name="Bungalow G+1",
            bays_x=[4.0, 4.0],
            bays_y=[4.0, 3.5],
            upper_floors=1,
            occupancy="residential",
            balcony={"side": "south", "depth": 1.2},
            foundation_depth=1.5,
        ),
    ),
    Template(
        "residential_g4",
        "Residential apartment G+4",
        "3 x 2 bays (4.5/4.0/4.5 x 4.0 m), mumty, Pune.",
        dict(
            name="Residential G+4",
            bays_x=[4.5, 4.0, 4.5],
            bays_y=[4.0, 4.0],
            upper_floors=4,
            occupancy="residential",
            mumty=True,
            city="Pune",
        ),
    ),
    Template(
        "residential_g7",
        "Residential tower G+7",
        "4 x 3 bays of 4.5 m with balconies, Mumbai.",
        dict(
            name="Residential G+7",
            bays_x=[4.5] * 4,
            bays_y=[4.5, 4.0, 4.5],
            upper_floors=7,
            occupancy="residential",
            balcony={"side": "north", "depth": 1.5},
            city="Mumbai",
            column=(0.3, 0.6),
            grade="M30",
        ),
    ),
    Template(
        "office_g5",
        "Office block G+5",
        "4 x 3 bays of 6.0 m, office loads, Bengaluru.",
        dict(
            name="Office G+5",
            bays_x=[6.0] * 4,
            bays_y=[6.0] * 3,
            upper_floors=5,
            occupancy="office",
            floor_height=3.6,
            ground_height=4.2,
            city="Bengaluru",
            beam_int=(0.3, 0.6),
            beam_ext=(0.3, 0.6),
            column=(0.4, 0.6),
            grade="M30",
        ),
    ),
    Template(
        "commercial_g2",
        "Shopping centre G+2",
        "5 x 4 bays of 7.5 m, commercial loads, Indore (PlanWin classic).",
        dict(
            name="Shopping Centre G+2",
            bays_x=[7.5] * 5,
            bays_y=[7.5] * 4,
            upper_floors=2,
            occupancy="commercial",
            floor_height=4.5,
            ground_height=5.0,
            city="Indore",
            beam_int=(0.3, 0.75),
            beam_ext=(0.3, 0.75),
            column=(0.45, 0.6),
            grade="M30",
        ),
    ),
    Template(
        "it_park",
        "IT park G+6",
        "6 x 3 bays of 8.0 m, IT/office loads, Hyderabad.",
        dict(
            name="IT Park G+6",
            bays_x=[8.0] * 6,
            bays_y=[8.0] * 3,
            upper_floors=6,
            occupancy="it_park",
            floor_height=3.9,
            ground_height=4.5,
            city="Hyderabad",
            beam_int=(0.35, 0.75),
            beam_ext=(0.35, 0.75),
            column=(0.5, 0.75),
            grade="M35",
        ),
    ),
    Template(
        "school",
        "School block G+2",
        "5 bays of 7 m with a 2.5 m corridor, school loads, Nagpur.",
        dict(
            name="School G+2",
            bays_x=[7.0] * 5,
            bays_y=[7.0, 2.5],
            upper_floors=2,
            occupancy="school",
            floor_height=3.6,
            ground_height=3.9,
            city="Nagpur",
            beam_int=(0.3, 0.6),
            beam_ext=(0.3, 0.6),
        ),
    ),
    Template(
        "hospital",
        "Hospital wing G+4",
        "4 x 3 bays of 6 m, hospital loads, importance 1.5, Delhi.",
        dict(
            name="Hospital G+4",
            bays_x=[6.0] * 4,
            bays_y=[6.0, 6.0, 6.0],
            upper_floors=4,
            occupancy="hospital",
            floor_height=3.6,
            ground_height=4.2,
            city="Delhi",
            beam_int=(0.3, 0.6),
            beam_ext=(0.3, 0.6),
            column=(0.45, 0.6),
            grade="M30",
        ),
    ),
    Template(
        "industrial",
        "Industrial building G+1",
        "3 x 2 bays of 9 m, 5 kN/m² floor, Ahmedabad.",
        dict(
            name="Industrial G+1",
            bays_x=[9.0] * 3,
            bays_y=[9.0] * 2,
            upper_floors=1,
            occupancy="industrial",
            floor_height=5.0,
            ground_height=6.0,
            city="Ahmedabad",
            beam_int=(0.35, 0.9),
            beam_ext=(0.35, 0.9),
            column=(0.5, 0.75),
        ),
    ),
    Template(
        "tutorial",
        "Tutorial building (Manual Part I)",
        "14 x 5 m, ground + 2 floors + roof + mumty like the PlanWin manual.",
        dict(
            name="Tutorial Building",
            bays_x=[5.0, 5.0, 4.0],
            bays_y=[5.0],
            upper_floors=2,
            occupancy="residential",
            ground_height=4.0,
            floor_height=3.5,
            foundation_depth=3.5,
            mumty=True,
            city="Mumbai",
        ),
    ),
]


def template_names() -> list[tuple[str, str, str]]:
    return [(t.key, t.title, t.description) for t in TEMPLATES]


def _find(key: str) -> Template:
    k = str(key).strip().lower()  # "Bungalow", " office_g5 " (LLM / CLI input)
    for t in TEMPLATES:
        if t.key == k or t.title.lower() == k:
            return t
    raise KeyError(f"Unknown template '{key}'")


def build_template(key: str) -> Project:
    """Load a pre-optimised template (fast); falls back to generating it."""
    t = _find(key)
    res = resources.files("planwin_ai.data").joinpath("templates", f"{t.key}.pwai")
    if res.is_file():
        from ..io.project_io import project_from_json

        return project_from_json(res.read_text(encoding="utf-8"))
    return generate_template(t.key)


def generate_template(key: str) -> Project:
    """Generate a template parametrically (auto-sized, not yet optimised)."""
    for t in TEMPLATES:
        if t.key == key or t.title.lower() == key.lower():
            spec = dict(t.spec)
            if "column" in spec:
                spec["column"] = tuple(spec["column"])
            for k in ("beam_int", "beam_ext"):
                if k in spec:
                    spec[k] = tuple(spec[k])
            return grid_building(GridSpec(**spec))
    raise KeyError(f"Unknown template '{key}'")


def legacy_samples() -> list[str]:
    root = resources.files("planwin_ai.data").joinpath("legacy_samples")
    return sorted(p.name for p in root.iterdir() if p.name.lower().endswith(".plw"))


def load_legacy_sample(name: str) -> Project:
    from ..io.legacy_plw import read_plw

    root = resources.files("planwin_ai.data").joinpath("legacy_samples")
    with resources.as_file(root.joinpath(name)) as path:
        plan, rep = read_plw(str(path))
    prj = Project(name=f"{plan.name} (legacy sample)")
    prj.plans.append(plan)
    prj.levels = [Level("Plinth", plan.name, 1.5, "M20"), Level("Floor 1", plan.name, 3.0, "M20")]
    prj.meta["import_notes"] = rep.notes
    return prj
