"""File formats, legacy import, AI assistant and licensing."""

import base64
import glob
import json
import os

import pytest

from planwin_ai.ai import providers
from planwin_ai.ai.actions import Session, execute
from planwin_ai.ai.assistant import Assistant
from planwin_ai.ai.offline import parse
from planwin_ai.ai.templates import build_template, legacy_samples, load_legacy_sample
from planwin_ai.core.frame import FrameModel
from planwin_ai.core.model import Project
from planwin_ai.core.plan_engine import PlanEngine
from planwin_ai.io import dxf_io, etabs, project_io, reports, staad
from planwin_ai.io.legacy_plw import read_plw

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "planwin_ai", "data", "legacy_samples")


def test_project_roundtrip(tmp_path):
    prj = build_template("bungalow")
    path = project_io.save_project(prj, str(tmp_path / "b"))
    assert path.endswith(".pwai")
    assert project_io.load_project(path).to_dict() == prj.to_dict()


def test_newer_schema_rejected(tmp_path):
    d = build_template("bungalow").to_dict()
    d["schema"] = 999
    p = tmp_path / "x.pwai"
    p.write_text(json.dumps(d))
    with pytest.raises(project_io.ProjectFormatError):
        project_io.load_project(str(p))


@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(SAMPLES, "*.plw"))))
def test_legacy_samples_import(path):
    plan, rep = read_plw(path)
    assert rep.slabs > 0 and rep.columns > 0 and rep.beams > 0
    r = PlanEngine(plan).run()
    assert abs(r.imbalance_pct) < 5.0  # residual only where the legacy drawing itself is incomplete


def test_legacy_sample_project_loads():
    assert "Box.plw" in legacy_samples()
    prj = load_legacy_sample("Saputara.plw")
    assert prj.plans and prj.levels


def test_exports(tmp_path):
    prj = build_template("residential_g4")
    fm = FrameModel(prj).build()
    fa = fm.analyze()
    std = staad.write_staad(fm, str(tmp_path / "m.std"))
    txt = open(std).read()
    assert txt.startswith("STAAD SPACE") and "PERFORM ANALYSIS" in txt and "FINISH" in txt
    from planwin_ai.core.frame import is_combinations

    n_ult = len([c for c in is_combinations(True, True, True) if c.kind == "ultimate"])
    assert n_ult == 37 and txt.count("LOAD COMB") == n_ult
    assert "MEMBER RELEASE" not in txt and "PRIS AX" in txt
    njoints = txt.split("JOINT COORDINATES")[1].split("MEMBER INCIDENCES")[0].count(";")
    assert njoints == len(fm.nodes)
    e2k = open(etabs.write_etabs(fm, str(tmp_path / "m.e2k"))).read()
    assert "$ STORIES - IN SEQUENCE FROM TOP" in e2k and e2k.count("LINEASSIGN") == len(fm.members)
    plan = prj.plan("Typical")
    d = dxf_io.export_plan_dxf(plan, str(tmp_path / "p.dxf"), PlanEngine(plan).run())
    back, notes = dxf_io.import_dxf(d)
    assert len(back.slabs) == len(plan.slabs) and len(back.columns) == len(plan.columns)
    dxf_io.export_frame_dxf(fm, str(tmp_path / "f.dxf"))
    from planwin_ai.design.runner import design_all

    rep = design_all(fa, prj)
    prs = {p.name: PlanEngine(p).run() for p in prj.plans}
    assert os.path.getsize(reports.write_excel(str(tmp_path / "r.xlsx"), prj, prs, fm, fa, rep)) > 5000
    assert os.path.getsize(reports.write_pdf(str(tmp_path / "r.pdf"), prj, prs, fm, rep, "TRIAL")) > 5000


# ----------------------------------------------------------------- AI
def test_offline_parser_building():
    reply, acts = parse("Create a G+4 residential building in Pune, 3x2 bays of 4.5 m with mumty", has_model=False)
    a = acts[0]
    assert a["action"] == "new_building" and a["upper_floors"] == 4 and a["bays_x"] == [4.5] * 3
    assert a["city"] == "Pune" and a["mumty"] and a["occupancy"] == "residential"


def test_offline_parser_commands():
    _, acts = parse("zone IV soft soil, M30 Fe500, SBC 250, analyze and design then export staad")
    names = [a["action"] for a in acts]
    assert names == ["set_seismic", "set_materials", "set_sbc", "analyze", "design", "export"]


def test_assistant_end_to_end(tmp_path):
    a = Assistant(Session(Project(), out_dir=str(tmp_path)))
    reply, res = a.ask("G+2 office in Mumbai, 3 by 2 bays of 6 m")
    assert res.changed and a.session.project.plans
    reply, res = a.ask("make it G+3")
    assert len(a.session.project.levels) == 1 + 3 + 1
    reply, res = a.ask("export etabs")
    assert res.files and os.path.exists(res.files[0])


def test_llm_provider_json(monkeypatch):
    class R:
        status_code = 200

        def json(self):
            return {
                "content": [
                    {"type": "text", "text": '```json\n{"reply": "ok", "actions": [{"action": "analyze"}]}\n```'}
                ]
            }

    monkeypatch.setattr(providers, "get_key", lambda p: "sk-test")
    monkeypatch.setattr(providers.requests, "post", lambda *a, **k: R())
    out = providers.chat(providers.ProviderConfig("claude"), [{"role": "user", "content": "run"}], "{}")
    assert out["reply"] == "ok" and out["actions"] == [{"action": "analyze"}]


def test_provider_failure_falls_back_offline(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise providers.requests.ConnectionError("offline")

    monkeypatch.setattr(providers, "get_key", lambda p: "sk-test")
    monkeypatch.setattr(providers.requests, "post", boom)
    a = Assistant(Session(build_template("bungalow"), out_dir=str(tmp_path)), providers.ProviderConfig("claude"))
    reply, res = a.ask("live load 3")
    assert "offline" in reply and res.changed


def test_unknown_action_reported():
    res = execute(Session(Project()), [{"action": "format_c_drive"}])
    assert res.errors


def test_exports_blocked_when_trial_expired(tmp_path):
    s = Session(build_template("bungalow"), out_dir=str(tmp_path), exports_allowed=False)
    res = execute(s, [{"action": "export", "format": "staad"}])
    assert res.errors and not res.files


# ----------------------------------------------------------------- licensing
def test_license_sign_verify_and_trial(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from planwin_ai.licensing import license as L

    key = Ed25519PrivateKey.generate()
    pub = base64.b64encode(
        key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
    lic = {
        "name": "A",
        "company": "B",
        "email": "c@d",
        "edition": "pro",
        "issued": "2026-01-01",
        "expires": "2099-01-01",
        "seats": 1,
    }
    lic["sig"] = base64.b64encode(key.sign(L.canonical(lic))).decode()
    assert L.verify(lic, pub)
    tampered = dict(lic, expires="2199-01-01")
    assert not L.verify(tampered, pub)
    assert not L.verify(lic)  # not signed by the shipped key
    st = L.current_state()
    assert st.mode == "trial" and st.days_left == L.TRIAL_DAYS and st.watermark
    import datetime as dt

    assert L.current_state(dt.date.today() + dt.timedelta(days=40)).mode == "expired"
