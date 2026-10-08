"""Tamper resistance of the offline trial and machine-bound licences.

``conftest.py`` points the trial stores at two tmp files and fixes the machine
id, so nothing here touches the real profile or registry.  A "new run" of the
application is simulated by resetting the once-per-run write guard.
"""

import base64
import datetime as dt
import importlib.util
import json
import os
import re
import sys

import pytest

from planwin_ai.licensing import license as L

D0 = dt.date(2026, 3, 1)


def run(today):
    """Simulate starting the application on ``today``."""
    L._written_this_run = False
    return L.current_state(today)


def stores():
    return L.trial_stores()


def records():
    return [json.loads(s.read()) if s.read() else None for s in stores()]


# ----------------------------------------------------------------- fresh trial / storage
def test_fresh_trial_is_30_days_and_written_everywhere():
    st = run(D0)
    assert st.mode == "trial" and st.days_left == L.TRIAL_DAYS == 30
    assert not st.tampered and not st.clock_rollback and st.exports_allowed and st.watermark
    assert st.label() == "Trial – 30 day(s) left"
    recs = records()
    assert len(recs) == 2 and recs[0] == recs[1]
    assert recs[0]["start"] == recs[0]["last_seen"] == D0.isoformat()
    assert recs[0]["version"] == L.TRIAL_RECORD_VERSION and len(recs[0]["tag"]) == 64


def test_trial_counts_down_and_expires():
    run(D0)
    assert run(D0 + dt.timedelta(days=12)).days_left == 18
    st = run(D0 + dt.timedelta(days=30))
    assert st.mode == "expired" and st.days_left == 0 and not st.exports_allowed
    assert st.label() == "Trial expired – exports disabled"


@pytest.mark.parametrize("victim", [0, 1])
def test_deleting_one_copy_keeps_original_start_and_self_heals(victim):
    run(D0)
    stores()[victim].delete()
    assert stores()[victim].read() is None
    st = run(D0 + dt.timedelta(days=10))
    assert st.mode == "trial" and st.days_left == 20
    healed = records()
    assert healed[victim] is not None and healed[victim]["start"] == D0.isoformat()
    assert healed[0] == healed[1]


def test_deleting_all_copies_is_the_only_reset_path():
    run(D0)
    later = D0 + dt.timedelta(days=40)
    assert run(later).mode == "expired"
    stores()[0].delete()
    assert run(later).mode == "expired"  # one copy left -> still expired (and re-healed)
    for s in stores():
        s.delete()
    st = run(later)  # documented support reset: every copy removed
    assert st.mode == "trial" and st.days_left == 30


def test_garbled_copy_is_treated_as_missing_and_repaired():
    run(D0)
    stores()[0].write("\x00not a record")
    st = run(D0 + dt.timedelta(days=5))
    assert st.mode == "trial" and st.days_left == 25 and not st.tampered
    assert records()[0]["start"] == D0.isoformat()


def test_records_written_at_most_once_per_run():
    run(D0)
    stores()[1].delete()
    L.current_state(D0 + dt.timedelta(days=1))  # same process: no second write
    assert stores()[1].read() is None
    run(D0 + dt.timedelta(days=1))  # next start of the app heals it
    assert records()[1]["start"] == D0.isoformat()


# ----------------------------------------------------------------- tampering
@pytest.mark.parametrize("victim", [0, 1])
def test_edited_start_date_is_tamper_expired(victim):
    run(D0)
    rec = records()[victim]
    rec["start"] = (D0 + dt.timedelta(days=25)).isoformat()
    stores()[victim].write(json.dumps(rec))
    st = run(D0 + dt.timedelta(days=26))
    assert st.mode == "expired" and st.tampered and not st.exports_allowed
    assert st.label() == L.TAMPER_MESSAGE == "Trial data was modified – please activate a licence"
    # tampered evidence is not overwritten by self-healing
    assert records()[victim]["start"] == (D0 + dt.timedelta(days=25)).isoformat()


def test_stripped_or_forged_tag_is_tampering():
    run(D0)
    rec = records()[0]
    del rec["tag"]
    stores()[0].write(json.dumps(rec))
    assert run(D0).tampered
    stores()[0].write(json.dumps(dict(rec, tag="0" * 64)))
    assert run(D0).tampered


def test_trial_copied_from_another_machine_is_rejected(monkeypatch):
    run(D0)
    monkeypatch.setattr(L, "machine_id", lambda: "some-other-pc")
    assert run(D0 + dt.timedelta(days=1)).tampered


def test_legacy_plain_date_trial_file_is_migrated():
    stores()[0].write((D0 - dt.timedelta(days=10)).isoformat())
    st = run(D0)
    assert st.mode == "trial" and st.days_left == 20
    assert all(r["start"] == (D0 - dt.timedelta(days=10)).isoformat() for r in records())


def test_legacy_future_date_cannot_extend_trial():
    stores()[0].write("2099-01-01")
    st = run(D0)
    assert st.days_left == 30 and not st.clock_rollback
    assert records()[0]["start"] == D0.isoformat()


# ----------------------------------------------------------------- clock rollback
def test_clock_set_back_does_not_add_days_and_is_flagged():
    run(D0)
    st = run(D0 + dt.timedelta(days=5))
    assert st.days_left == 25 and not st.clock_rollback
    back = D0 - dt.timedelta(days=5)  # clock set back 10 days
    st = run(back)
    assert st.days_left == 25 and st.clock_rollback and st.mode == "trial"
    assert "clock" in st.label()
    assert records()[0]["last_seen"] == (D0 + dt.timedelta(days=5)).isoformat()
    # days still elapse on the rolled-back clock
    st = run(back + dt.timedelta(days=3))
    assert st.days_left == 22 and st.clock_rollback


def test_small_clock_drift_is_tolerated():
    run(D0 + dt.timedelta(days=2))
    st = run(D0)
    assert not st.clock_rollback and st.days_left == 30


def test_clock_forward_before_first_run_does_not_freeze_trial():
    run(dt.date(2099, 1, 1))  # trial started with the clock far in the future
    st = run(D0)
    assert st.clock_rollback and st.days_left == 30
    assert run(D0 + dt.timedelta(days=31)).mode == "expired"


# ----------------------------------------------------------------- registry store (fake winreg)
class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ, KEY_WRITE, KEY_SET_VALUE, REG_SZ = 1, 2, 4, 1

    def __init__(self):
        self.data = {}

    class _Key:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(self, hive, sub, reserved=0, access=0):
        if (hive, sub) not in {k[:2] for k in self.data} and (hive, sub, None) not in self.data:
            raise FileNotFoundError(sub)
        return self._Key((hive, sub))

    def CreateKeyEx(self, hive, sub, reserved=0, access=0):
        self.data.setdefault((hive, sub, None), None)
        return self._Key((hive, sub))

    def QueryValueEx(self, key, name):
        try:
            return self.data[key.path + (name,)], self.REG_SZ
        except KeyError:
            raise FileNotFoundError(name) from None

    def SetValueEx(self, key, name, reserved, typ, value):
        self.data[key.path + (name,)] = value

    def DeleteValue(self, key, name):
        if self.data.pop(key.path + (name,), None) is None:
            raise FileNotFoundError(name)


def test_registry_store_roundtrip_and_self_healing(monkeypatch, tmp_path):
    fake = FakeWinreg()
    reg = L.RegistryStore(winreg_module=fake)
    assert reg.read() is None
    monkeypatch.setattr(L, "TRIAL_STORES", [L.FileStore(str(tmp_path / "a" / ".trial")), reg])
    run(D0)
    assert json.loads(fake.data[("HKCU", L.REGISTRY_KEY, L.REGISTRY_VALUE)])["start"] == D0.isoformat()
    L.trial_stores()[0].delete()  # user deletes the AppData file
    assert run(D0 + dt.timedelta(days=7)).days_left == 23
    assert json.loads(L.trial_stores()[0].read())["start"] == D0.isoformat()
    reg.delete()
    assert reg.read() is None
    assert run(D0 + dt.timedelta(days=8)).days_left == 22
    assert json.loads(reg.read())["start"] == D0.isoformat()


def test_default_stores_are_independent_locations(monkeypatch):
    monkeypatch.setattr(L, "TRIAL_STORES", None)
    default = L.trial_stores()
    assert len(default) >= 2
    if sys.platform == "win32":
        assert any(isinstance(s, L.RegistryStore) for s in default)
    else:
        paths = [s.path for s in default]
        assert len(set(paths)) == len(paths)
        assert paths[1].startswith(os.environ["XDG_DATA_HOME"])  # redirected to tmp by conftest


def test_unavailable_store_does_not_break_trial(monkeypatch, tmp_path):
    class Broken:
        def read(self):
            raise OSError("registry unavailable")

        def write(self, data):
            raise OSError("registry unavailable")

    monkeypatch.setattr(L, "TRIAL_STORES", [L.FileStore(str(tmp_path / "t")), Broken()])
    assert run(D0).days_left == 30
    assert run(D0 + dt.timedelta(days=3)).days_left == 27


# ----------------------------------------------------------------- licences
@pytest.fixture
def signer(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(L, "PUBLIC_KEY_B64", base64.b64encode(pub).decode())

    def sign(**extra):
        lic = {"name": "Ravi", "company": "ABC", "email": "r@abc.in", "edition": "pro", "issued": "2026-01-01"}
        lic.update({"expires": "2099-01-01", "seats": 1}, **extra)
        lic["sig"] = base64.b64encode(key.sign(L.canonical(lic))).decode()
        return lic

    return sign


def test_signed_licence_is_pro(signer):
    run(D0)
    st = L.install_license(json.dumps(signer()))
    assert st.mode == "pro" and st.holder == "ABC" and st.exports_allowed and not st.watermark
    assert L.current_state().label() == "Licensed to ABC (until 2099-01-01)"


def test_licence_overrides_tampered_trial(signer):
    run(D0)
    stores()[0].write('{"start": "2026-03-20"}')
    assert run(D0).tampered
    assert L.install_license(json.dumps(signer())).mode == "pro"


def test_licence_without_machine_field_keeps_old_canonical_form(signer):
    lic = signer()
    legacy = json.dumps({k: lic.get(k) for k in L.SIGNED_FIELDS}, sort_keys=True, separators=(",", ":")).encode()
    assert L.canonical(lic) == legacy  # licences issued before machine binding still verify
    assert L.verify(lic) and L.license_machine_ok(lic)
    assert L.install_license(json.dumps(lic)).mode == "pro"


def test_machine_bound_licence_for_this_machine(signer):
    lic = signer(machine=L.machine_code().lower())
    assert L.install_license(json.dumps(lic)).mode == "pro"


def test_machine_bound_licence_for_another_machine_rejected(signer):
    other = L.machine_code("another-computer")
    assert other != L.machine_code()
    lic = signer(machine=other)
    assert L.verify(lic)
    with pytest.raises(ValueError, match="another computer"):
        L.install_license(json.dumps(lic))
    # copied straight into the profile it is ignored, the app stays in trial
    with open(L._license_path(), "w", encoding="utf-8") as f:
        json.dump(lic, f)
    assert run(D0).mode == "trial"


def test_machine_field_cannot_be_stripped_or_changed(signer):
    lic = signer(machine=L.machine_code("another-computer"))
    stripped = {k: v for k, v in lic.items() if k != "machine"}
    assert not L.verify(stripped)
    assert not L.verify(dict(lic, machine=L.machine_code()))


def test_machine_code_format():
    code = L.machine_code()
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}", code)
    assert code == L.machine_code() and code != L.machine_code("x")
    assert L.normalize_machine_code(code.lower().replace("-", " ")) == code
    assert L.normalize_machine_code("o1il-0000-0000") == "0111-0000-0000"
    with pytest.raises(ValueError):
        L.normalize_machine_code("ABC")


@pytest.mark.skipif(sys.platform == "win32", reason="reads HKLM on Windows; keep tests off the real registry")
def test_system_machine_id_is_stable():
    assert L._system_machine_id() and L._system_machine_id() == L._system_machine_id()


def test_keygen_issue_with_machine(tmp_path, monkeypatch, capsys):
    path = os.path.join(os.path.dirname(__file__), "..", "tools", "keygen.py")
    spec = importlib.util.spec_from_file_location("planwin_keygen", path)
    keygen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(keygen)
    pem, out = str(tmp_path / "k.pem"), str(tmp_path / "x.lic")
    keygen.main(["init", "--out", pem])
    pub = re.search(r"PUBLIC_KEY_B64 = (\S+)", capsys.readouterr().out).group(1)
    code = L.machine_code()
    keygen.main(["issue", "--key", pem, "--name", "Ravi", "--machine", code.lower(), "--out", out])
    lic = json.loads(open(out, encoding="utf-8").read())
    assert lic["machine"] == code and L.verify(lic, pub)
    monkeypatch.setattr(L, "PUBLIC_KEY_B64", pub)
    assert L.install_license(json.dumps(lic)).mode == "pro"
    keygen.main(["issue", "--key", pem, "--name", "Any PC", "--out", out])
    assert "machine" not in json.loads(open(out, encoding="utf-8").read())
