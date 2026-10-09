"""Offline licensing: Ed25519-signed licence files + tamper-resistant 30-day trial.

Licences
--------
* Computer Help signs licences with a **private key that never ships**
  (``tools/keygen.py``).  The application only embeds the public key, so
  licences cannot be forged by inspecting the executable.
* A licence is a small JSON document::

      {"name": "...", "company": "...", "email": "...", "edition": "pro",
       "issued": "2027-01-01", "expires": "2028-01-01", "seats": 1, "sig": "<base64>"}

* Optional machine binding: a licence may carry ``"machine": "XXXX-XXXX-XXXX"``
  – the customer's :func:`machine_code` (shown in Help ▸ Licence).  The field
  is covered by the signature *only when present*, so licences issued before
  machine binding existed verify exactly as before, and the field cannot be
  stripped from a bound licence without breaking the signature.  A bound
  licence is only accepted on the computer with that code.

Trial
-----
Without a licence the app runs as a 30-day trial; exports carry a
"TRIAL – not for construction" watermark and are disabled after expiry
(modelling keeps working).

The trial record is JSON ``{"version", "start", "last_seen", "last_clock", "tag"}``
where ``tag`` is HMAC-SHA256 over the other fields, keyed by a secret derived
from the machine id (Windows ``HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid``;
elsewhere ``/etc/machine-id``, falling back to the MAC address / host name) mixed
with an application salt.  It is stored in several independent places
(:func:`trial_stores`):

* ``<app_data_dir()>/.trial`` (``%APPDATA%\\PlanWinAIPro\\.trial`` on Windows),
* Windows: registry value ``HKCU\\Software\\Computer Help\\PlanWinAIPro`` → ``Trial``
  and ``%LOCALAPPDATA%\\PlanWinAIPro\\trial.dat``,
* other platforms: ``$XDG_DATA_HOME/PlanWinAIPro/.trial`` (default ``~/.local/share``).

Rules applied on every :func:`current_state` call:

* The **earliest** start date across all valid copies wins, and all copies are
  re-written (self-healing) – deleting one copy does not restart the trial.
* A copy that parses as a record but whose tag does not verify (edited dates,
  copied from another computer) is treated as tampering: the trial is reported
  expired with the label :data:`TAMPER_MESSAGE`.  Tampered copies are never
  overwritten, so the state persists until the modified data is removed.
* Unreadable/garbled copies are treated like missing ones (that is no weaker
  than deleting them).  A pre-1.1 plain-date ``.trial`` file is migrated (its
  date is clamped to today, so it can only shorten the trial).
* Clock rollback: ``last_seen`` is the latest date ever seen and the effective
  date is ``max(today, last_seen + days the clock advanced since the last run)``,
  so setting the clock back neither adds trial days nor stops the countdown.
  If today is more than :data:`CLOCK_TOLERANCE_DAYS` behind the effective date
  the state is flagged ``clock_rollback`` (modelling still works).
* The records are written at most once per process run.

**The only way to restart a trial is to delete every copy** (all files and the
registry value above).  Support can do this deliberately, e.g. for an evaluator
whose trial was spoiled by a wrong system clock.

Threat model
------------
This is offline copy protection in a Python application and **can be bypassed
by a determined attacker**: the salt and the verification logic ship inside the
executable, so someone who decompiles it can recompute tags, patch the checks
out, or find and wipe every storage location.  A clock kept permanently at a
fixed date also freezes the countdown.  The goal is only to stop *casual*
resets – deleting a file, editing a date, re-installing, or setting the clock
back – while never bothering licensed users.  Licence signatures, in contrast,
cannot be forged without Computer Help's private key (but the check itself can
still be patched out of the binary).

Honest-user caveats: a changed machine id (Windows re-install, cloned VM) makes
existing trial copies look tampered, and running once with the clock far in the
future consumes trial days.  Both only affect the *trial*; a licence (or the
support reset above) resolves them.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import hmac
import importlib
import json
import os
import socket
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass

PUBLIC_KEY_B64 = "V0T+AFyLGYzG0uLeNrXmhmv5rrhXSW8sUWGDr18eDu8="
TRIAL_DAYS = 30
SIGNED_FIELDS = ("name", "company", "email", "edition", "issued", "expires", "seats")
MACHINE_FIELD = "machine"
TRIAL_RECORD_VERSION = 1
CLOCK_TOLERANCE_DAYS = 2
TAMPER_MESSAGE = "Trial data was modified – please activate a licence"
REGISTRY_KEY = r"Software\Computer Help\PlanWinAIPro"
REGISTRY_VALUE = "Trial"

_APP_SALT = bytes.fromhex("f7f66bc876c31b6f0f8a12331ea05695d5ecb418e224fb66")
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # base32 without I, L, O, U


def app_data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(base, "PlanWinAIPro")
    os.makedirs(d, exist_ok=True)
    return d


# ----------------------------------------------------------------- machine identity
def _system_machine_id() -> str:
    """Stable identifier of this computer (not secret, but not shown to the user)."""
    if sys.platform == "win32":
        try:
            import winreg

            access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, access) as k:
                guid = str(winreg.QueryValueEx(k, "MachineGuid")[0]).strip()
            if guid:
                return "win:" + guid.lower()
        except OSError:
            pass
    else:
        for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                with open(path, encoding="ascii") as f:
                    mid = f.read().strip()
                if mid:
                    return "mid:" + mid
            except (OSError, UnicodeDecodeError):
                pass
    node = uuid.getnode()
    if not (node >> 40) & 1:  # multicast bit set => random per process, unusable
        return f"mac:{node:012x}"
    return "host:" + socket.gethostname().lower()


def machine_id() -> str:
    """Machine id used for trial keys and licence binding (tests monkeypatch this)."""
    return _system_machine_id()


def _format_code(raw: str) -> str:
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}"


def machine_code(mid: str | None = None) -> str:
    """Short, human-typeable code identifying this computer, e.g. ``"7K2M-Q9XD-4HBR"``.

    It is a hash of the machine id (60 bits, Crockford base32), so it reveals
    nothing about the machine itself.  Customers quote it when ordering a
    machine-bound licence (``tools/keygen.py issue --machine``).
    """
    digest = hashlib.sha256(b"PlanWinAIPro/machine-code/v1\0" + (mid if mid is not None else machine_id()).encode())
    n = int.from_bytes(digest.digest()[:8], "big") >> 4
    return _format_code("".join(_CROCKFORD[(n >> (5 * i)) & 31] for i in reversed(range(12))))


def normalize_machine_code(code: str) -> str:
    """Canonical ``XXXX-XXXX-XXXX`` form; tolerant of case, spaces and O/I/L typos."""
    s = "".join(str(code).split()).replace("-", "").upper().translate(str.maketrans("OIL", "011"))
    if len(s) != 12 or any(c not in _CROCKFORD for c in s):
        raise ValueError(f"Invalid machine code {code!r} (expected XXXX-XXXX-XXXX)")
    return _format_code(s)


# ----------------------------------------------------------------- licences
def canonical(lic: dict) -> bytes:
    fields = SIGNED_FIELDS + ((MACHINE_FIELD,) if MACHINE_FIELD in lic else ())
    return json.dumps({k: lic.get(k) for k in fields}, sort_keys=True, separators=(",", ":")).encode()


def verify(lic: dict, public_key_b64: str | None = None) -> bool:
    """True if ``lic`` carries a valid signature (by the shipped key unless one is given)."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64 or PUBLIC_KEY_B64))
        key.verify(base64.b64decode(lic["sig"]), canonical(lic))
        return True
    except (InvalidSignature, KeyError, ValueError, TypeError):
        return False
    except Exception:
        return False


def license_machine_ok(lic: dict, code: str | None = None) -> bool:
    """True if ``lic`` is not machine-bound or is bound to this computer (or ``code``)."""
    bound = lic.get(MACHINE_FIELD)
    if bound in (None, ""):
        return True
    try:
        return normalize_machine_code(bound) == normalize_machine_code(code or machine_code())
    except ValueError:
        return False


@dataclass
class LicenseState:
    mode: str  # "pro" | "trial" | "expired"
    holder: str = ""
    expires: str | None = None
    days_left: int = 0
    tampered: bool = False  # trial data failed its integrity check
    clock_rollback: bool = False  # system clock is behind the latest date seen

    @property
    def watermark(self) -> str:
        return "" if self.mode == "pro" else "TRIAL – not for construction"

    @property
    def exports_allowed(self) -> bool:
        return self.mode != "expired"

    def label(self) -> str:
        if self.mode == "pro":
            return f"Licensed to {self.holder}" + (f" (until {self.expires})" if self.expires else "")
        if self.tampered:
            return TAMPER_MESSAGE
        clock = " (system clock appears to have been set back)" if self.clock_rollback else ""
        if self.mode == "trial":
            return f"Trial – {self.days_left} day(s) left" + clock
        return "Trial expired – exports disabled" + clock


def _license_path() -> str:
    return os.path.join(app_data_dir(), "license.json")


def _trial_path() -> str:
    return os.path.join(app_data_dir(), ".trial")


def install_license(text: str) -> LicenseState:
    lic = json.loads(text)
    if not isinstance(lic, dict) or not verify(lic):
        raise ValueError("Invalid licence signature")
    if not license_machine_ok(lic):
        raise ValueError(f"This licence is registered to another computer (this computer's code is {machine_code()})")
    exp = lic.get("expires")
    if exp:  # never replace the installed licence with one that current_state() would ignore
        try:
            exp_date = _dt.date.fromisoformat(exp)
        except (TypeError, ValueError):
            raise ValueError(f"This licence has an unreadable expiry date {exp!r} – please contact support") from None
        if exp_date < _dt.date.today():
            raise ValueError(f"This licence expired on {exp_date.isoformat()}")
    with open(_license_path(), "w", encoding="utf-8") as f:
        json.dump(lic, f, indent=1)
    return current_state()


def current_state(today: _dt.date | None = None) -> LicenseState:
    today = today or _dt.date.today()
    try:
        with open(_license_path(), encoding="utf-8") as f:
            lic = json.load(f)
        if isinstance(lic, dict) and verify(lic) and license_machine_ok(lic):
            exp = lic.get("expires")
            if not exp or _dt.date.fromisoformat(exp) >= today:
                return LicenseState("pro", lic.get("company") or lic.get("name", ""), exp)
    except (OSError, ValueError, TypeError):
        pass
    return _trial_state(today)


# ----------------------------------------------------------------- trial storage
class FileStore:
    """Trial record in a file; ``path`` may be a callable so it is resolved lazily."""

    def __init__(self, path: str | Callable[[], str]):
        self._path = path

    @property
    def path(self) -> str:
        return self._path() if callable(self._path) else self._path

    def read(self) -> str | None:
        try:
            with open(self.path, encoding="utf-8") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return None

    def write(self, data: str) -> None:
        path = self.path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)

    def delete(self) -> None:
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass

    def __repr__(self) -> str:
        return f"FileStore({self.path!r})"


class RegistryStore:
    """Trial record as a REG_SZ value under HKEY_CURRENT_USER.

    ``winreg_module`` lets tests supply a fake implementation so the real
    registry is never touched.
    """

    def __init__(self, key: str = REGISTRY_KEY, value: str = REGISTRY_VALUE, winreg_module=None):
        self.key, self.value, self._winreg = key, value, winreg_module

    def _wr(self):
        return self._winreg if self._winreg is not None else importlib.import_module("winreg")

    def read(self) -> str | None:
        wr = self._wr()
        try:
            with wr.OpenKey(wr.HKEY_CURRENT_USER, self.key, 0, wr.KEY_READ) as k:
                val = wr.QueryValueEx(k, self.value)[0]
        except OSError:
            return None
        return val if isinstance(val, str) else None

    def write(self, data: str) -> None:
        wr = self._wr()
        with wr.CreateKeyEx(wr.HKEY_CURRENT_USER, self.key, 0, wr.KEY_WRITE) as k:
            wr.SetValueEx(k, self.value, 0, wr.REG_SZ, data)

    def delete(self) -> None:
        wr = self._wr()
        try:
            with wr.OpenKey(wr.HKEY_CURRENT_USER, self.key, 0, wr.KEY_SET_VALUE) as k:
                wr.DeleteValue(k, self.value)
        except OSError:
            pass

    def __repr__(self) -> str:
        return f"RegistryStore(HKCU\\{self.key}\\{self.value})"


#: Override for the trial storage locations (tests point this at tmp dirs / a fake registry).
TRIAL_STORES: list | None = None


def _default_stores() -> list:
    stores: list = [FileStore(_trial_path)]
    if sys.platform == "win32":
        stores.append(RegistryStore())
        local = os.environ.get("LOCALAPPDATA")
        if local:
            stores.append(FileStore(os.path.join(local, "PlanWinAIPro", "trial.dat")))
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
        stores.append(FileStore(os.path.join(base, "PlanWinAIPro", ".trial")))
    return stores


def trial_stores() -> list:
    """The places the trial record is kept (objects with ``read``/``write``/``delete``)."""
    return list(TRIAL_STORES) if TRIAL_STORES is not None else _default_stores()


# ----------------------------------------------------------------- trial records
_MISSING, _VALID, _LEGACY, _TAMPERED = "missing", "valid", "legacy", "tampered"
_written_this_run = False  # records are persisted at most once per process


@dataclass
class _Record:
    status: str
    start: _dt.date | None = None
    last_seen: _dt.date | None = None
    last_clock: _dt.date | None = None


def _trial_key() -> bytes:
    return hmac.new(_APP_SALT, b"PlanWinAIPro/trial-key/v1\0" + machine_id().encode(), hashlib.sha256).digest()


def _tag(body: dict, key: bytes) -> str:
    msg = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _encode_record(start: _dt.date, last_seen: _dt.date, last_clock: _dt.date, key: bytes) -> str:
    body = {
        "version": TRIAL_RECORD_VERSION,
        "start": start.isoformat(),
        "last_seen": last_seen.isoformat(),
        "last_clock": last_clock.isoformat(),
    }
    return json.dumps(dict(body, tag=_tag(body, key)), sort_keys=True)


def _decode_record(text: str | None, key: bytes, today: _dt.date) -> _Record:
    if text is None or not text.strip():
        return _Record(_MISSING)
    text = text.strip()
    try:
        data = json.loads(text)
    except ValueError:
        try:  # pre-1.1 format: a bare ISO start date, untrusted -> never later than today
            d = min(_dt.date.fromisoformat(text), today)
            return _Record(_LEGACY, d, d, d)
        except ValueError:
            return _Record(_MISSING)  # garbled: no weaker than a deleted copy
    if not isinstance(data, dict):
        return _Record(_TAMPERED)
    body = {k: v for k, v in data.items() if k != "tag"}
    tag = data.get("tag")
    if not isinstance(tag, str) or not hmac.compare_digest(tag, _tag(body, key)):
        return _Record(_TAMPERED)
    try:
        start = _dt.date.fromisoformat(body["start"])
        last_seen = _dt.date.fromisoformat(body["last_seen"])
        last_clock = _dt.date.fromisoformat(body.get("last_clock", body["last_seen"]))
    except (KeyError, TypeError, ValueError):
        return _Record(_TAMPERED)
    if start > last_seen:
        return _Record(_TAMPERED)
    return _Record(_VALID, start, last_seen, last_clock)


def _safe_read(store) -> str | None:
    try:
        return store.read()
    except Exception:
        return None


def _trial_state(today: _dt.date) -> LicenseState:
    global _written_this_run
    stores = trial_stores()
    key = _trial_key()
    texts = [_safe_read(s) for s in stores]
    records = [_decode_record(t, key, today) for t in texts]
    if any(r.status == _TAMPERED for r in records):
        return LicenseState("expired", days_left=0, tampered=True)

    good = [r for r in records if r.status in (_VALID, _LEGACY)]
    if good:
        start = min(r.start for r in good)
        last_seen = max(r.last_seen for r in good)
        last_clock = max(r.last_clock for r in good)
        # days the (possibly wrong) clock moved forward since the last run still count
        advanced = max((today - last_clock).days, 0)
        effective = max(today, last_seen + _dt.timedelta(days=advanced))
    else:
        start = effective = today
    rollback = (effective - today).days > CLOCK_TOLERANCE_DAYS

    new_text = _encode_record(start, effective, today, key)
    if not _written_this_run and any((t or "").strip() != new_text for t in texts):
        for store in stores:
            try:
                store.write(new_text)
            except Exception:
                pass  # read-only location / registry unavailable: other copies still count
        _written_this_run = True

    left = min(TRIAL_DAYS - (effective - start).days, TRIAL_DAYS)
    return LicenseState("trial" if left > 0 else "expired", days_left=max(left, 0), clock_rollback=rollback)
