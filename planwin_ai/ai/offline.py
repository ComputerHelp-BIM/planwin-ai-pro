"""Offline natural-language command parser (no API key, no internet).

Understands common engineering phrasing, e.g.::

    "G+4 residential building in Pune, 3x2 bays of 4.5 m, mumty"
    "set live load 3 on typical"   "zone IV medium soil"   "use M30 Fe500"
    "auto size columns"   "analyze"   "design"   "export staad"
"""

from __future__ import annotations

import re

from ..io.cities import city_names
from .templates import TEMPLATES

_NUM = r"(\d+(?:\.\d+)?)"

_OCC = [
    (r"\b(residential|apartment|flat|house|bungalow|villa|hostel)\b", "residential"),
    (r"\b(office|corporate)\b", "office"),
    (r"\b(commercial|shop|shopping|mall|retail|showroom)\b", "commercial"),
    (r"\b(school|college|classroom|institut\w*)\b", "school"),
    (r"\b(hospital|clinic|health)\b", "hospital"),
    (r"\b(industrial|factory|warehouse|godown|workshop)\b", "industrial"),
    (r"\b(it ?park|data ?cent\w*|software)\b", "it_park"),
]

HELP = (
    "I can build and edit models for you. Try:\n"
    "• 'G+4 residential in Pune, 3x2 bays of 4.5 m with mumty'\n"
    "• 'office G+6, bays 6 x 6 m, 4 by 3 bays, Bengaluru'\n"
    "• 'make it G+7' / 'floor height 3.3' / 'add 1.2 m balcony'\n"
    "• 'live load 3 kN/m2', 'zone IV soft soil', 'M30 Fe500', 'SBC 250'\n"
    "• 'auto size columns', 'analyze', 'design', 'optimise sizes', 'export staad | etabs | dxf | excel | pdf'\n"
    "• 'template office' – templates: " + ", ".join(t.key for t in TEMPLATES) + "\n"
    "Connect Claude, OpenAI or Ollama in Settings › AI for free-form requests and engineering Q&A."
)


def _bays(count: int, span: float) -> list[float]:
    return [round(span, 3)] * max(count, 1)


def parse(text: str, has_model: bool = True) -> tuple[str, list[dict]]:
    """Return (reply, actions)."""
    t = " " + text.lower().replace("×", "x").replace("*", "x") + " "
    actions: list[dict] = []
    if re.search(r"\b(help|what can you do|commands)\b", t):
        return HELP, []

    for tpl in TEMPLATES:
        if re.search(r"\btemplate\b", t) and (tpl.key.replace("_", " ") in t or tpl.key in t or tpl.title.lower() in t):
            return f"Loading template {tpl.title}.", [{"action": "load_template", "key": tpl.key}]
    if re.search(r"\btemplate\b", t):
        m = re.search(r"template\s+(\w+)", t)
        if m:
            for tpl in TEMPLATES:
                if tpl.key.startswith(m.group(1)) or m.group(1) in tpl.title.lower():
                    return f"Loading template {tpl.title}.", [{"action": "load_template", "key": tpl.key}]

    spec: dict = {}
    m = re.search(r"\bg\s*\+\s*(\d+)", t)
    if m:
        spec["upper_floors"] = int(m.group(1))
    m = re.search(r"(\d+)\s*(?:storey|storeys|story|stories|floor building|floors? building)", t)
    if m and "upper_floors" not in spec:
        spec["upper_floors"] = max(int(m.group(1)) - 1, 0)
    m = re.search(r"(\d+)\s*(?:x|by)\s*(\d+)\s*bays?", t)
    nx = ny = None
    if m:
        nx, ny = int(m.group(1)), int(m.group(2))
    m = re.search(r"bays?\s*(?:of|@|=)?\s*" + _NUM + r"\s*m?\s*(?:x|by)\s*" + _NUM, t)
    m1 = re.search(r"(?:bays?|spans?|grid)\s*(?:of|@|=)?\s*" + _NUM + r"\s*m\b", t)
    sx = sy = None
    if m:
        sx, sy = float(m.group(1)), float(m.group(2))
    elif m1:
        sx = sy = float(m1.group(1))
    m = re.search(r"(?:plot|building|footprint|size)\s*(?:of|is|=)?\s*" + _NUM + r"\s*m?\s*(?:x|by)\s*" + _NUM, t)
    if m and not (nx or sx):
        L, B = float(m.group(1)), float(m.group(2))
        nx, ny = max(round(L / 4.5), 1), max(round(B / 4.5), 1)
        sx, sy = L / nx, B / ny
    if nx or sx:
        nx, ny = nx or 3, ny or 2
        sx, sy = sx or 4.5, sy or 4.5
        spec["bays_x"], spec["bays_y"] = _bays(nx, sx), _bays(ny, sy)
    for pat, occ in _OCC:
        if re.search(pat, t):
            spec["occupancy"] = occ
            break
    m = re.search(r"(?:typical\s*)?floor\s*height\s*(?:of|=|is)?\s*" + _NUM, t) or re.search(
        r"storey\s*height\s*" + _NUM, t
    )
    if m:
        spec["floor_height"] = float(m.group(1))
    m = re.search(r"ground\s*(?:floor|storey)?\s*height\s*(?:of|=|is)?\s*" + _NUM, t)
    if m:
        spec["ground_height"] = float(m.group(1))
    m = re.search(r"(?:foundation|footing)\s*depth\s*(?:of|=|is)?\s*" + _NUM, t)
    if m:
        spec["foundation_depth"] = float(m.group(1))
    if re.search(r"\bbalcon(y|ies)\b", t):
        m = re.search(_NUM + r"\s*m\s*(?:wide\s*)?balcon", t) or re.search(r"balcon\w*\s*(?:of)?\s*" + _NUM, t)
        side = "north" if "north" in t else "south"
        spec["balcony"] = {"side": side, "depth": float(m.group(1)) if m else 1.2}
    if re.search(r"\b(mumty|mumti|stair ?cabin|head ?room)\b", t):
        spec["mumty"] = True
    m = re.search(r"\bm\s?(15|20|25|30|35|40|45|50)\b", t)
    if m and spec:
        spec["grade"] = f"M{m.group(1)}"
    city = None
    for c in sorted(city_names(), key=len, reverse=True):
        if re.search(r"\b" + re.escape(c.lower()) + r"\b", t):
            city = c
            break
    if city and spec:
        spec["city"] = city

    building_words = re.search(
        r"\b(building|bungalow|block|tower|apartment|g\s*\+|storey|bays?|create|make|generate|design a)\b", t
    )
    structural_spec = {
        k
        for k in spec
        if k
        in (
            "upper_floors",
            "bays_x",
            "bays_y",
            "floor_height",
            "ground_height",
            "foundation_depth",
            "balcony",
            "mumty",
            "occupancy",
        )
    }
    modify = re.search(r"\b(make it|change|increase|decrease|modify|add|set|update)\b", t) and has_model
    if structural_spec and (building_words or modify):
        if modify and not re.search(r"\b(create|new|generate)\b", t):
            actions.append({"action": "modify_building", **spec})
        else:
            actions.append({"action": "new_building", **spec})
    elif city:
        actions.append({"action": "set_location", "city": city})

    # loads
    ld = {}
    m = re.search(r"live\s*load\s*(?:of|=|is)?\s*" + _NUM, t)
    if m:
        ld["live"] = float(m.group(1))
    m = re.search(r"floor\s*finish\w*\s*(?:of|=|is)?\s*" + _NUM, t)
    if m:
        ld["floor_finish"] = float(m.group(1))
    m = re.search(r"slab\s*(?:thickness|thk|depth)\s*(?:of|=|is)?\s*" + _NUM, t)
    if m:
        v = float(m.group(1))
        ld["thickness"] = v / 1000 if v > 2 else v
    if ld:
        m = re.search(r"\bon\s+(typical|roof|ground|all)\b", t)
        ld["plan"] = m.group(1).title() if m and m.group(1) != "all" else "all"
        actions.append({"action": "set_loads", **ld})
    # seismic / wind / materials / sbc
    sz = {}
    m = re.search(r"zone\s*(ii|iii|iv|v|2|3|4|5)\b", t)
    if m:
        sz["zone"] = m.group(1).upper()
    m = re.search(r"\b(hard|rock|medium|soft)\s*(?:soil|strata|rock)?\b", t)
    if m and ("soil" in t or "rock" in t):
        sz["soil"] = "hard" if m.group(1) in ("hard", "rock") else m.group(1)
    m = re.search(r"importance\s*(?:factor)?\s*" + _NUM, t)
    if m:
        sz["importance"] = float(m.group(1))
    if re.search(r"\b(omrf|ordinary moment)\b", t):
        sz["response_reduction"] = 3.0
    if re.search(r"\b(smrf|special moment)\b", t):
        sz["response_reduction"] = 5.0
    if re.search(r"\b(no|without|disable)\s+(seismic|earthquake)\b", t):
        sz["enabled"] = False
    if sz:
        actions.append({"action": "set_seismic", **sz})
    wd = {}
    m = re.search(r"(?:wind\s*speed|vb)\s*(?:of|=|is)?\s*" + _NUM, t)
    if m:
        wd["basic_speed"] = float(m.group(1))
    m = re.search(r"terrain\s*(?:category)?\s*(\d)", t)
    if m:
        wd["terrain"] = int(m.group(1))
    if re.search(r"\b(no|without|disable)\s+wind\b", t):
        wd["enabled"] = False
    if wd:
        actions.append({"action": "set_wind", **wd})
    mat = {}
    m = re.search(r"\bm\s?(15|20|25|30|35|40|45|50)\b", t)
    if m and not any(a["action"] == "new_building" for a in actions):
        mat["concrete"] = f"M{m.group(1)}"
    m = re.search(r"\bfe\s?(415|500|550)\b", t)
    if m:
        mat["steel"] = m.group(1)
    if mat:
        actions.append({"action": "set_materials", **mat})
    m = re.search(r"\bsbc\s*(?:of|=|is)?\s*" + _NUM, t)
    if m:
        actions.append({"action": "set_sbc", "sbc": float(m.group(1))})
    # workflow
    if re.search(r"\bauto\s*-?\s*size|size (the )?columns\b", t):
        m = re.search(_NUM + r"\s*%", t)
        actions.append({"action": "autosize_columns", "steel_pct": float(m.group(1)) if m else 1.0})
    if re.search(r"\b(optimi[sz]e|iterate|resize|fix (the )?(failures|failing))\b", t):
        actions.append({"action": "optimize_sizes"})
    if re.search(r"\b(analy[sz]e|analysis|run|solve)\b", t):
        actions.append({"action": "analyze"})
    if re.search(r"\bdesign\b", t) and not re.search(r"design a\b", t):
        actions.append({"action": "design"})
    for key, fmt in (
        ("bar bending", "bbs"),
        ("bbs", "bbs"),
        ("calc", "calc"),
        ("detail", "details"),
        ("reinforcement drawing", "details"),
        ("staad", "staad"),
        (".std", "staad"),
        ("etabs", "etabs"),
        ("e2k", "etabs"),
        ("3d dxf", "dxf3d"),
        ("dxf", "dxf"),
        ("cad", "dxf"),
        ("excel", "excel"),
        ("xlsx", "excel"),
        ("spreadsheet", "excel"),
        ("pdf", "pdf"),
        ("report", "pdf"),
    ):
        if re.search(r"\b(export|generate|create|save|give|make)\b", t) and key in t:
            actions.append({"action": "export", "format": fmt})
            break
    if not actions:
        return ("I didn't catch a command there. " + HELP), []
    return "OK – " + ", ".join(a["action"].replace("_", " ") for a in actions) + ".", actions
