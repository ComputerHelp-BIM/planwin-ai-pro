"""Indian city table: basic wind speed (IS 875-3) and seismic zone (IS 1893-1).

Values for major cities follow IS 875 (Part 3):2015 Annex A and IS 1893
(Part 1):2016 Annex E; the remainder come from the legacy PlanWin table.
Engineers must verify site-specific values before issuing drawings.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from importlib import resources
from typing import Optional


@lru_cache(maxsize=1)
def load_cities() -> dict[str, dict]:
    out: dict[str, dict] = {}
    path = resources.files("planwin_ai.data").joinpath("cities.csv")
    with path.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            vb = float(row["basic_wind_speed_mps"]) if row["basic_wind_speed_mps"] else None
            out[row["city"].lower()] = {"city": row["city"], "vb": vb, "zone": row["seismic_zone"], "source": row["source"]}
    return out


def city_names() -> list[str]:
    return sorted(v["city"] for v in load_cities().values())


def lookup_city(name: str) -> Optional[dict]:
    if not name:
        return None
    data = load_cities()
    key = " ".join(name.strip().lower().split())
    if key in data:
        return data[key]
    if len(key) < 3:  # "a" must not silently become "Agra"
        return None
    # "Thane West" -> Thane: the longest table name that starts the query (whole words only)
    longer = [k for k in data if key.startswith(k + " ")]
    if longer:
        return data[max(longer, key=len)]
    # "pun" -> Pune: a unique (or shortest) table name that the query is the start of
    shorter = sorted((k for k in data if k.startswith(key)), key=lambda k: (len(k), k))
    return data[shorter[0]] if shorter else None
