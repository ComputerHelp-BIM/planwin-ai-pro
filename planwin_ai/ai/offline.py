"""Offline natural-language command parser (no API key, no internet).

Understands common engineering phrasing, e.g.::

    "G+4 residential building in Pune, 3x2 bays of 4.5 m, mumty"
    "set live load 3 on typical"   "zone IV medium soil"   "use M30 Fe500"
    "auto size columns"   "analyze"   "design"   "export staad"
    "use response spectrum"   "rigid diaphragm off"   "staircase on beams B3 B4 width 1.2"
    "water tank 10000 litres on C5 C6 C9 C10 at roof"   "save revision R1"   "show BOQ by floor"
    "export boq" (Excel cost estimate)   "column schedule" / "export schedules"
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
    "• 'use response spectrum' / 'static method', 'rigid diaphragm off'\n"
    "• 'add shear wall W1 from 0,0 to 3,0 thickness 230', 'staircase on beams B3 B4 width 1.2'\n"
    "• 'water tank 10000 litres on C5 C6 C9 C10 at roof', 'add grids'\n"
    "• 'save revision R1', 'compare revisions', 'show BOQ by floor', 'units tonnes' / 'use kN'\n"
    "• 'export boq' (editable Excel cost estimate), 'export schedules' / 'column schedule' / 'beam schedule'\n"
    "• 'template office' – templates: " + ", ".join(t.key for t in TEMPLATES) + "\n"
    "Connect Claude, OpenAI or Ollama in Settings › AI for free-form requests and engineering Q&A."
)


def _bays(count: int, span: float) -> list[float]:
    return [round(span, 3)] * max(count, 1)


_SNUM = r"(-?\d+(?:\.\d+)?)"
_PT = r"\(?\s*" + _SNUM + r"\s*,\s*" + _SNUM + r"\s*\)?"
_RSA = r"\b(response\s*-?\s*spectrum|rsa|dynamic(?:\s+(?:analysis|method))?|modal\s+(?:analysis|method))\b"
_STATIC = (
    r"\b(equivalent\s+static|static\s+(?:method|analysis|only|seismic)|seismic\s+coefficient|(?:use|only)\s+static)\b"
)
_AUTO_METHOD = r"\b(auto(?:matic)?\s+(?:seismic\s+)?method|method\s+(?:to\s+)?auto)\b"
#: "<method> analysis" names a seismic method, not a request to run the analysis
_METHOD_WORDS = (
    r"\b(response\s*-?\s*spectrum|dynamic|modal|equivalent\s+static|static|seismic\s+coefficient)\s+(analysis|method)\b"
)
_DIAPHRAGM_OFF = (
    r"\b(?:no|without|disable|remove|turn\s+off|switch\s+off)\s+(?:the\s+)?rigid\s*-?\s*diaphragms?\b"
    r"|\brigid\s*-?\s*diaphragms?\s*(?:=|:|to|is)?\s*(?:off|false|no|disabled?|none)\b"
    r"|\b(?:flexible|semi\s*-?\s*rigid)\s+(?:floor\s+)?diaphragms?\b"
)
_MKS = (
    r"\b(?:units?\s*(?:to|=|:|in)?\s*|use\s+|switch\s+to\s+|show\s+(?:in\s+)?|display\s+(?:in\s+)?)"
    r"(?:tonnes?|tons?|mks|metric\s+tonnes?)\b|\b(?:mks|tonnes?)\s+units?\b"
)
_SI = (
    r"\b(?:units?\s*(?:to|=|:|in)?\s*|use\s+|switch\s+to\s+|show\s+(?:in\s+)?|display\s+(?:in\s+)?)"
    r"(?:kn|si|kilo\s*newtons?)\b(?!\s*/)|\b(?:si|kn)\s+units?\b"
)
_EXPORT_VERB = r"\b(?:export|download|generate|create|save|give|make|write)\b"
_BOQ_WORDS = r"(?:boq|bill\s+of\s+quantit(?:y|ies)|quantit(?:y|ies)|(?:cost|material)\s+estimate|estimate)"
_FILE_WORDS = r"(?:excel|xlsx|spreadsheet|workbook|file)"
_LABEL_STOP = {"and", "then", "with", "of", "the", "to", "for", "now", "please", "revision", "revisions"}


def _seismic_extras(t: str) -> dict:
    out: dict = {}
    if re.search(_AUTO_METHOD, t):
        out["method"] = "auto"
    elif re.search(_STATIC, t):
        out["method"] = "static"
    elif re.search(_RSA, t):
        out["method"] = "response_spectrum"
    if re.search(_DIAPHRAGM_OFF, t):
        out["rigid_diaphragm"] = False
    elif re.search(r"\brigid\s*-?\s*diaphragms?\b", t):
        out["rigid_diaphragm"] = True
    return out


def _wall(t: str) -> dict | None:
    m = re.search(
        r"\bwalls?\s*(w\d+)?\s*(?:from|between|at)?\s*" + _PT + r"\s*(?:to|and|–)\s*" + _PT,
        t,
    )
    if not m:
        return None
    act: dict = {"action": "add_wall"}
    mark = m.group(1) or (re.search(r"\b(w\d+)\b", t) or [None, None])[1]
    if mark:
        act["mark"] = mark.upper()
    act.update(x1=float(m.group(2)), y1=float(m.group(3)), x2=float(m.group(4)), y2=float(m.group(5)))
    th = re.search(r"\b(?:thickness|thick|thk)\s*(?:of|=|is)?\s*" + _NUM, t) or re.search(
        _NUM + r"\s*(?:mm|m)?\s*thick\b", t
    )
    if th:
        act["thickness"] = float(th.group(1))
    pl = re.search(r"\bon\s+(?:the\s+)?(typical|roof|ground|mumty|all)\b", t)
    if pl:
        act["plan"] = "all" if pl.group(1) == "all" else pl.group(1).title()
    g = re.search(r"\bm\s?(15|20|25|30|35|40|45|50)\b", t)
    if g:
        act["grade"] = f"M{g.group(1)}"
    return act


def _staircase(t: str) -> dict | None:
    if not re.search(r"\bstair(?:case|way)?s?\b(?!\s*-?\s*cabin)", t):
        return None
    act: dict = {"action": "add_staircase"}
    m = re.search(r"\b(st\d+)\b", t)
    if m:
        act["name"] = m.group(1).upper()
    beams = re.findall(r"\bb(\d+)\b", t)
    if not beams:  # "mumty with staircase" etc. – no flight to place
        return None
    act["support_beams"] = [f"B{b}" for b in dict.fromkeys(beams)]
    pl = re.search(r"\bon\s+(?:the\s+)?(typical|roof|ground|mumty)\b", t)
    if pl:
        act["plan"] = pl.group(1).title()
    for key, pat in (
        ("width", r"\bwidth\s*(?:of|=|is)?\s*" + _NUM),
        ("width", _NUM + r"\s*m\s*wide\b"),
        ("going", r"\bgoing\s*(?:of|=|is)?\s*" + _NUM),
        ("landing", r"\blanding\s*(?:width)?\s*(?:of|=|is)?\s*" + _NUM),
        ("riser", r"\brisers?\s*(?:of|=|is)?\s*" + _NUM),
        ("tread", r"\btreads?\s*(?:of|=|is)?\s*" + _NUM),
        ("waist", r"\bwaist\s*(?:slab)?\s*(?:thickness)?\s*(?:of|=|is)?\s*" + _NUM),
        ("start", r"\bstart(?:ing|s)?\s*(?:at|from)?\s*" + _NUM),
    ):
        m = re.search(pat, t)
        if m and key not in act:
            act[key] = float(m.group(1))
    return act


def _water_tank(t: str) -> dict | None:
    if not re.search(r"\b(?:water\s*tanks?|overhead\s*tanks?|ohwt|tanks?)\b", t):
        return None
    tt = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)  # 10,000 litres
    act: dict = {"action": "add_water_tank"}
    m = re.search(r"\b(t\d+)\b", tt)
    if m:
        act["name"] = m.group(1).upper()
    m = re.search(_NUM + r"\s*(?:l|lit|litre|litres|liter|liters|ltr|ltrs|lts)\b", tt)
    k = re.search(_NUM + r"\s*(?:kl|kilo\s*lit\w*|k\s*l|k\s*lit\w*|m3|m³|cum|cu\.?\s*m)\b", tt)
    if m:
        act["capacity_l"] = float(m.group(1))
    elif k:
        act["capacity_l"] = float(k.group(1)) * 1000
    cols = re.findall(r"\bc(\d+)\b", tt)
    if cols:
        act["columns"] = [f"C{c}" for c in dict.fromkeys(cols)]
    m = re.search(r"\b(?:at|on)\s+(?:the\s+)?(roof|terrace|mumty|top)\b", tt)
    lv = re.search(r"\b(?:at|on)\s+(?:the\s+)?level\s*(\d+)\b", tt)
    fl = re.search(r"\b(?:at|on)\s+(?:the\s+)?floor\s*(\d+)\b", tt)
    if lv:
        act["level"] = int(lv.group(1))
    elif fl:
        act["level"] = f"Floor {fl.group(1)}"
    elif m:
        act["level"] = {"terrace": "Roof", "top": "top"}.get(m.group(1), m.group(1).title())
    m = re.search(r"(?<!foundation )(?<!footing )\b(?:water\s*)?depth\s*(?:of\s*water)?\s*(?:of|=|is)?\s*" + _NUM, tt)
    m = m or re.search(_NUM + r"\s*m\s*(?:of\s*water|water\s*depth|deep)\b", tt)
    if m:
        act["water_depth"] = float(m.group(1))
    return act if ("capacity_l" in act or "columns" in act) else None


def _named_exports(t: str) -> list[str]:
    """Exports named by their content rather than a file type: the BOQ workbook ("export boq", "boq excel",
    "cost estimate") and the member schedules ("column schedule", "export schedules").  "show boq" stays
    the chat table and "bar bending schedule" stays the BBS."""
    if re.search(r"\bbar\s+bending\b|\bbbs\b", t):
        return []
    out = []
    lead = r"(?:(?:the|a|an|me|my|full|complete|editable|detailed)\s+)*"
    if (
        re.search(_EXPORT_VERB + r"\s+" + lead + _BOQ_WORDS + r"\b", t)
        or re.search(r"\b" + _FILE_WORDS + r"\s+(?:(?:of|for)\s+(?:the\s+)?)?" + _BOQ_WORDS + r"\b", t)
        or re.search(r"\b" + _BOQ_WORDS + r"\s+(?:(?:in|as|to|into)\s+(?:an?\s+)?)?" + _FILE_WORDS + r"\b", t)
        or (re.search(r"\bcost\s+estimate\b", t) and not re.search(r"\b(?:show|display|list|print|tell|what)\b", t))
    ):
        out.append("boq")
    if re.search(r"\b(?:column|beam|footing|slab|wall|member)s?\s+schedules?\b", t) or (
        re.search(_EXPORT_VERB, t) and re.search(r"\bschedules?\b", t)
    ):
        out.append("schedules")
    return out


def _label(m: re.Match | None, group: int) -> str | None:
    if not m or not m.group(group):
        return None
    lab = m.group(group).strip("'\".,;")
    return None if not lab or lab.lower() in _LABEL_STOP else lab


def _revisions(text: str, t: str) -> list[dict]:
    out: list[dict] = []
    m = re.search(
        r"\b(?:save|store|record|snapshot|take|keep)\s+(?:a\s+|the\s+|this\s+(?:as\s+)?)?(?:revision|rev)\b"
        r"(?:\s+(?:as\s+|named\s+|called\s+)?(['\"]?)([\w.\-]+)\1)?",
        text,
        re.I,
    )
    if m:
        act = {"action": "save_revision"}
        lab = _label(m, 2)
        if lab:
            act["label"] = lab
        out.append(act)
    if re.search(r"\bcompare\b", t):
        m = re.search(
            r"\bcompare\s+(?:revisions?\s+|revs?\s+)?(['\"]?)([\w.\-]+)\1\s+(?:and|with|vs\.?|versus|to|&)\s+"
            r"(?:revision\s+|rev\s+)?(['\"]?)([\w.\-]+)\3",
            text,
            re.I,
        )
        a, b = _label(m, 2), _label(m, 4)
        if m and a and b and ((m.group(1) and m.group(3)) or (re.search(r"\d", a) and re.search(r"\d", b))):
            out.append({"action": "compare_revisions", "a": a, "b": b})
        elif re.search(r"\b(revisions?|revs?)\b", t):
            out.append({"action": "compare_revisions"})
    return out


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

    wall, stair, tank = _wall(t), _staircase(t), _water_tank(t)
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
    if stair:  # "staircase … live load 5" is the stair's own load, not the slabs'
        for k, sk in (("live", "live"), ("floor_finish", "finish")):
            if k in ld:
                stair[sk] = ld.pop(k)
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
    sz.update(_seismic_extras(t))
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
    if m and not any(a["action"] == "new_building" for a in actions) and not (wall and "grade" in wall):
        mat["concrete"] = f"M{m.group(1)}"
    m = re.search(r"\bfe\s?(415|500|550)\b", t)
    if m:
        mat["steel"] = m.group(1)
    if mat:
        actions.append({"action": "set_materials", **mat})
    m = re.search(r"\bsbc\s*(?:of|=|is)?\s*" + _NUM, t)
    if m:
        actions.append({"action": "set_sbc", "sbc": float(m.group(1))})
    actions += [a for a in (wall, stair, tank) if a]
    if re.search(
        r"\b(auto\s*-?\s*grids?|(?:add|create|generate|make|draw|put)\s+(?:the\s+)?(?:auto(?:matic)?\s+)?grids?"
        r"(?:\s*lines?)?|grid\s*lines?)\b",
        t,
    ):
        actions.append({"action": "add_grids", "grids": "auto"})
    # workflow
    if re.search(r"\bauto\s*-?\s*size|size (the )?columns\b", t):
        m = re.search(_NUM + r"\s*%", t)
        actions.append({"action": "autosize_columns", "steel_pct": float(m.group(1)) if m else 1.0})
    if re.search(r"\b(optimi[sz]e|iterate|resize|fix (the )?(failures|failing))\b", t):
        actions.append({"action": "optimize_sizes"})
    if re.search(r"\b(analy[sz]e|analysis|run|solve)\b", re.sub(_METHOD_WORDS, " ", t)):
        actions.append({"action": "analyze"})
    if re.search(r"\bdesign\b", t) and not re.search(r"design a\b", t):
        actions.append({"action": "design"})
    actions += _revisions(text, t)
    named = _named_exports(t)
    if "boq" not in named and re.search(
        r"\b(boq|bill\s+of\s+quantit(?:y|ies)|quantit(?:y|ies)|quantity\s+take\s*-?\s*off|(?:material|cost)\s+estimate)\b",
        t,
    ):
        by_type = re.search(r"\bby\s+(?:member\s+)?types?\b|\b(?:member|type)\s*-?\s*wise\b|\bby\s+members?\b", t)
        actions.append({"action": "boq", "by": "type" if by_type else "floor"})
    actions += [{"action": "export", "format": f} for f in named]
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
        if not named and re.search(_EXPORT_VERB, t) and key in t:
            actions.append({"action": "export", "format": fmt})
            break
    if re.search(_MKS, t):
        actions.insert(0, {"action": "set_units", "system": "MKS"})
    elif re.search(_SI, t):
        actions.insert(0, {"action": "set_units", "system": "SI"})
    if not actions:
        return ("I didn't catch a command there. " + HELP), []
    return "OK – " + ", ".join(a["action"].replace("_", " ") for a in actions) + ".", actions
