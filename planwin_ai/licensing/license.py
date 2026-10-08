"""Offline licensing: Ed25519-signed licence files + 30-day trial.

* Computer Help signs licences with a **private key that never ships**
  (``tools/keygen.py``).  The application only embeds the public key, so
  licences cannot be forged by inspecting the executable.
* A licence is a small JSON document::

      {"name": "...", "company": "...", "email": "...", "edition": "pro",
       "issued": "2027-01-01", "expires": "2028-01-01", "seats": 1, "sig": "<base64>"}

* Without a licence the app runs as a 30-day trial; exports carry a
  "TRIAL – not for construction" watermark and are disabled after expiry.
"""

from __future__ import annotations

import base64
import datetime as _dt
import json
import os
from dataclasses import dataclass
from typing import Optional

PUBLIC_KEY_B64 = "V0T+AFyLGYzG0uLeNrXmhmv5rrhXSW8sUWGDr18eDu8="
TRIAL_DAYS = 30
SIGNED_FIELDS = ("name", "company", "email", "edition", "issued", "expires", "seats")


def app_data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(base, "PlanWinAIPro")
    os.makedirs(d, exist_ok=True)
    return d


def canonical(lic: dict) -> bytes:
    return json.dumps({k: lic.get(k) for k in SIGNED_FIELDS}, sort_keys=True, separators=(",", ":")).encode()


def verify(lic: dict, public_key_b64: str = PUBLIC_KEY_B64) -> bool:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        key.verify(base64.b64decode(lic["sig"]), canonical(lic))
        return True
    except (InvalidSignature, KeyError, ValueError, TypeError):
        return False
    except Exception:
        return False


@dataclass
class LicenseState:
    mode: str  # "pro" | "trial" | "expired"
    holder: str = ""
    expires: Optional[str] = None
    days_left: int = 0

    @property
    def watermark(self) -> str:
        return "" if self.mode == "pro" else "TRIAL – not for construction"

    @property
    def exports_allowed(self) -> bool:
        return self.mode != "expired"

    def label(self) -> str:
        if self.mode == "pro":
            return f"Licensed to {self.holder}" + (f" (until {self.expires})" if self.expires else "")
        if self.mode == "trial":
            return f"Trial – {self.days_left} day(s) left"
        return "Trial expired – exports disabled"


def _license_path() -> str:
    return os.path.join(app_data_dir(), "license.json")


def _trial_path() -> str:
    return os.path.join(app_data_dir(), ".trial")


def install_license(text: str) -> LicenseState:
    lic = json.loads(text)
    if not verify(lic):
        raise ValueError("Invalid licence signature")
    with open(_license_path(), "w", encoding="utf-8") as f:
        json.dump(lic, f, indent=1)
    return current_state()


def current_state(today: Optional[_dt.date] = None) -> LicenseState:
    today = today or _dt.date.today()
    try:
        with open(_license_path(), "r", encoding="utf-8") as f:
            lic = json.load(f)
        if verify(lic):
            exp = lic.get("expires")
            if not exp or _dt.date.fromisoformat(exp) >= today:
                return LicenseState("pro", lic.get("company") or lic.get("name", ""), exp)
    except (OSError, ValueError):
        pass
    tp = _trial_path()
    try:
        with open(tp, "r", encoding="utf-8") as f:
            start = _dt.date.fromisoformat(f.read().strip())
    except (OSError, ValueError):
        start = today
        try:
            with open(tp, "w", encoding="utf-8") as f:
                f.write(start.isoformat())
        except OSError:
            pass
    left = TRIAL_DAYS - (today - start).days
    return LicenseState("trial" if left > 0 else "expired", days_left=max(left, 0))
