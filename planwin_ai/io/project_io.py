"""Save / open PlanWin AI Pro projects (``.pwai`` – UTF-8 JSON).

Writes are atomic (temp file + replace) so a crash never corrupts a
project.  ``migrate`` upgrades older schema versions in place.
"""

from __future__ import annotations

import json
import os
import tempfile
import time

from .. import __version__
from ..core.model import SCHEMA_VERSION, Project

EXT = ".pwai"


class ProjectFormatError(ValueError):
    pass


def save_project(project: Project, path: str, stamp: bool = True) -> str:
    """Write ``project`` atomically.  ``stamp=False`` omits the app version and time so that
    generated files (the bundled templates) are byte-identical between rebuilds."""
    if not path.lower().endswith(EXT):
        path += EXT
    data = project.to_dict()
    if stamp:
        data["saved_with"] = __version__
        data["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".pwai-", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())  # the data must be on disk before the rename replaces the old file
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o644)  # mkstemp creates 0600 files
        except OSError:
            pass
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return path


def migrate(data: dict) -> dict:
    schema = int(data.get("schema", 1))
    if schema > SCHEMA_VERSION:
        raise ProjectFormatError(f"Project was saved by a newer PlanWin AI Pro (schema {schema}); please update")
    if schema < 2:
        # 1.0.x projects were analysed with distributed storey forces and the equivalent
        # static method only; keep their results unchanged until the user opts in
        seismic = data.setdefault("seismic", {})
        seismic.setdefault("method", "static")
        seismic.setdefault("rigid_diaphragm", False)
    data["schema"] = SCHEMA_VERSION
    return data


def load_project(path: str) -> Project:
    try:
        with open(path, encoding="utf-8-sig") as f:  # tolerate a BOM added by Windows editors
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProjectFormatError(f"Not a valid PlanWin AI Pro project: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("plans"), list):
        raise ProjectFormatError("Not a valid PlanWin AI Pro project (missing plans)")
    try:
        return Project.from_dict(migrate(data))
    except ProjectFormatError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:  # well-formed JSON, wrong structure
        raise ProjectFormatError(f"Not a valid PlanWin AI Pro project: {type(exc).__name__}: {exc}") from exc


def project_from_json(text: str) -> Project:
    return Project.from_dict(migrate(json.loads(text)))


def project_to_json(project: Project) -> str:
    return json.dumps(project.to_dict(), ensure_ascii=False)
