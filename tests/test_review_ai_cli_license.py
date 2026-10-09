"""Review regressions: AI actions / offline parser / providers, CLI and licensing.

Network calls are always mocked – no test here talks to a real API.
"""

from __future__ import annotations

import base64
import datetime as dt
import importlib.util
import json
import os

import pytest

from planwin_ai import cli
from planwin_ai.ai import providers
from planwin_ai.ai.actions import Session, execute
from planwin_ai.ai.assistant import Assistant
from planwin_ai.ai.offline import parse
from planwin_ai.ai.templates import build_template
from planwin_ai.core.model import Project
from planwin_ai.licensing import license as L


def _first(text: str, name: str, has_model: bool = True) -> dict:
    acts = [a for a in parse(text, has_model)[1] if a["action"] == name]
    assert acts, f"{text!r} gave no {name}: {parse(text, has_model)[1]}"
    return acts[0]


@pytest.fixture
def parametric():
    s = Session(Project())
    r = execute(s, [{"action": "new_building", "bays_x": [4, 4], "bays_y": [4], "upper_floors": 2}])
    assert not r.errors
    return s


# ------------------------------------------------------------------ offline parser
@pytest.mark.parametrize(
    "text",
    ["set ground floor height 4.2", "G+3 building, ground floor height 4.5 m", "make ground storey height 4.2"],
)
def test_ground_floor_height_does_not_change_the_typical_floor_height(text):
    act = parse(text)[1][0]
    assert "ground_height" in act and "floor_height" not in act


def test_typical_and_ground_floor_height_together():
    act = parse("make it floor height 3.1 and ground floor height 4.2")[1][0]
    assert act["floor_height"] == 3.1 and act["ground_height"] == 4.2


@pytest.mark.parametrize(
    "text, action, key, value",
    [
        ("set live load to 4", "set_loads", "live", 4.0),
        ("change slab thickness to 125 mm", "set_loads", "thickness", 0.125),
        ("set sbc to 200", "set_sbc", "sbc", 200.0),
        ("set wind speed to 50", "set_wind", "basic_speed", 50.0),
        ("set floor height to 3.2", "modify_building", "floor_height", 3.2),
        ("change foundation depth to 2 m", "modify_building", "foundation_depth", 2.0),
    ],
)
def test_set_value_to_number(text, action, key, value):
    assert _first(text, action)[key] == pytest.approx(value)


@pytest.mark.parametrize("text", ["ground + 3 residential building", "ground plus 3 building", "G + 3 building"])
def test_ground_plus_n(text):
    assert _first(text, "new_building", has_model=False)["upper_floors"] == 3


@pytest.mark.parametrize(
    "text, bx, by",
    [
        ("G+3 building 3x2 bays of 5000 mm", [5.0] * 3, [5.0] * 2),
        ("G+3 building 3x2 bays of 450 cm", [4.5] * 3, [4.5] * 2),
        ("G+3 building, 3 bays of 5 m by 2 bays of 4 m", [5.0] * 3, [4.0] * 2),
        ("4 bays x 3 bays building", [4.5] * 4, [4.5] * 3),
        ("G+3 building, 3 by 2 bays, with 5 m bays", [5.0] * 3, [5.0] * 2),
    ],
)
def test_bay_phrasings(text, bx, by):
    act = _first(text, "new_building", has_model=False)
    assert act["bays_x"] == bx and act["bays_y"] == by


def test_lengths_in_mm_and_cm():
    assert _first("set floor height 3300 mm", "modify_building")["floor_height"] == pytest.approx(3.3)
    assert _first("slab thickness 15 cm", "set_loads")["thickness"] == pytest.approx(0.15)


@pytest.mark.parametrize("text", ["G+2 building without balcony", "G+2 building, no balcony, with mumty"])
def test_negated_balcony_is_not_added(text):
    act = _first(text, "new_building", has_model=False)
    assert not act.get("balcony")


def test_negated_mumty_and_removals():
    assert _first("G+2 building without mumty", "new_building", has_model=False).get("mumty") is not True
    assert _first("remove the mumty", "modify_building")["mumty"] is False
    act = _first("remove the balcony", "modify_building")
    assert "balcony" in act and act["balcony"] is None


def test_existing_phrases_unchanged():
    act = parse("G+4 residential building in Pune, 3x2 bays of 4.5m with mumty and 1.5 m balcony")[1][0]
    assert act["upper_floors"] == 4 and act["bays_x"] == [4.5] * 3 and act["mumty"] is True
    assert act["balcony"] == {"side": "south", "depth": 1.5}
    w = _first("add shear wall W1 from 0,0 to 3,0 thickness 230", "add_wall")
    assert (w["x1"], w["y1"], w["x2"], w["y2"]) == (0, 0, 3, 0)


# ------------------------------------------------------------------ actions: atomicity
def test_modify_with_unknown_city_leaves_the_model_alone(parametric):
    s = parametric
    s.last["fa"] = "cached"
    before = s.project
    r = execute(s, [{"action": "modify_building", "floor_height": 3.6, "city": "Atlantis"}])
    assert r.errors and not r.changed
    assert s.project is before and s.project.meta["grid_spec"]["floor_height"] == 3.0
    assert s.last == {"fa": "cached"}


@pytest.mark.parametrize(
    "act, check",
    [
        ({"action": "set_seismic", "zone": "V", "soil": "clay"}, lambda p: p.seismic.zone == "III"),
        ({"action": "set_seismic", "zone": "V", "importance": "high"}, lambda p: p.seismic.zone == "III"),
        ({"action": "set_wind", "basic_speed": 50, "terrain": "open"}, lambda p: p.wind.basic_speed == 44.0),
        ({"action": "set_materials", "concrete": "M30", "steel": "abc"}, lambda p: p.levels[0].grade != "M30"),
        (
            {"action": "set_loads", "live": 7, "thickness": "thick"},
            lambda p: all(sl.live != 7 for pl in p.plans for sl in pl.slabs),
        ),
    ],
)
def test_invalid_parameter_changes_nothing(act, check):
    s = Session(build_template("bungalow"))
    s.last["fa"] = "cached"
    r = execute(s, [act])
    assert r.errors and not r.changed and check(s.project)
    assert s.last == {"fa": "cached"}


@pytest.mark.parametrize(
    "act, msg",
    [
        ({"action": "modify_building", "floor_height": -3}, "floor_height"),
        ({"action": "modify_building", "floor_height": 0}, "floor_height"),
        ({"action": "modify_building", "ground_height": 40}, "ground_height"),
        ({"action": "modify_building", "foundation_depth": -1}, "foundation_depth"),
        ({"action": "modify_building", "column": [0.3]}, "column"),
        ({"action": "modify_building", "column": 0.3}, "column"),
        ({"action": "modify_building", "balcony": {"side": "east", "depth": 1.2}}, "balcony"),
        ({"action": "modify_building", "balcony": {"side": "south", "depth": -1}}, "balcony"),
        ({"action": "modify_building", "upper_floors": "4.5"}, "upper_floors"),
        ({"action": "modify_building", "grade": "M7"}, "grade"),
        ({"action": "set_sbc", "sbc": 0}, "sbc"),
        ({"action": "set_sbc"}, "sbc"),
        ({"action": "set_seismic", "importance": 0}, "importance"),
        ({"action": "set_seismic", "response_reduction": 0}, "response_reduction"),
        ({"action": "set_wind", "basic_speed": -5}, "basic_speed"),
        ({"action": "set_materials", "concrete": "M7"}, "concrete"),
        ({"action": "set_materials", "concrete": "strong"}, "concrete"),
        ({"action": "set_loads", "live": -5}, "live"),
        ({"action": "set_loads", "thickness": 0}, "thickness"),
        ({"action": "autosize_columns", "steel_pct": 0}, "steel_pct"),
    ],
)
def test_invalid_values_are_rejected_with_a_message(parametric, act, msg):
    r = execute(parametric, [act])
    assert r.errors and msg in r.errors[0] and not r.changed, r.errors


def test_millimetre_values_and_grade_without_m(parametric):
    s = parametric
    r = execute(s, [{"action": "modify_building", "floor_height": 3300, "column": [300, 450], "grade": "30"}])
    assert not r.errors
    spec = s.project.meta["grid_spec"]
    assert spec["floor_height"] == pytest.approx(3.3) and spec["grade"] == "M30"
    assert spec["column"][0] >= 0.3 and spec["column"][0] < 1  # auto-sizing may enlarge, never 300 m
    r = execute(s, [{"action": "set_loads", "thickness": 150}])
    assert not r.errors
    assert {sl.thickness for pl in s.project.plans for sl in pl.slabs if sl.distribution != "on_grade"} == {0.15}


def test_modify_can_remove_a_balcony(parametric):
    s = parametric
    execute(s, [{"action": "modify_building", "balcony": {"side": "north", "depth": 1.5}}])
    assert any(sl.room == "Balcony" for pl in s.project.plans for sl in pl.slabs)
    r = execute(s, [{"action": "modify_building", "balcony": None}])
    assert not r.errors and r.changed
    assert not any(sl.room == "Balcony" for pl in s.project.plans for sl in pl.slabs)
    r = execute(s, [{"action": "modify_building", "balcony": True}])
    assert not r.errors and any(sl.room == "Balcony" for pl in s.project.plans for sl in pl.slabs)


def test_new_building_with_unknown_city_says_so():
    s = Session(Project())
    r = execute(s, [{"action": "new_building", "city": "Atlantis"}])
    assert not r.errors and r.changed
    assert any("Atlantis" in m and "not in the database" in m for m in r.messages)
    assert "Atlantis" not in s.project.name


def test_load_template_key_is_case_insensitive():
    s = Session(Project())
    r = execute(s, [{"action": "load_template", "key": "Bungalow"}])
    assert not r.errors and s.project.plans


# ------------------------------------------------------------------ actions: export paths
def test_relative_export_path_goes_to_the_session_folder(tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    s = Session(build_template("bungalow"), out_dir=str(tmp_path / "out"))
    r = execute(s, [{"action": "export", "format": "staad", "path": "model.std"}])
    assert not r.errors, r.errors
    assert os.path.abspath(r.files[0]) == str(tmp_path / "out" / "model.std")
    assert not (cwd / "model.std").exists()


def test_export_path_that_is_a_folder_gets_the_default_name(tmp_path):
    s = Session(build_template("bungalow"), out_dir=str(tmp_path))
    folder = tmp_path / "results"
    folder.mkdir()
    r = execute(s, [{"action": "export", "format": "staad", "path": str(folder)}])
    assert not r.errors, r.errors
    assert os.path.dirname(os.path.abspath(r.files[0])) == str(folder) and r.files[0].endswith(".std")


# ------------------------------------------------------------------ providers (mocked)
class _Resp:
    def __init__(self, payload=None, status=200, text=""):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _claude(text):
    return _Resp({"content": [{"type": "text", "text": text}]})


@pytest.fixture
def fake_post(monkeypatch):
    calls = []
    box = {"resp": _claude('{"reply": "ok", "actions": []}')}

    def post(url, **kw):
        calls.append((url, kw))
        return box["resp"]

    monkeypatch.setattr(providers, "get_key", lambda p: "sk-secret-123456")
    monkeypatch.setattr(providers.requests, "post", post)
    return calls, box


@pytest.mark.parametrize(
    "text, reply, actions",
    [
        ('[{"action": "analyze"}]', "", [{"action": "analyze"}]),
        (
            'Sure!\n```json\n{"reply": "ok", "actions": [{"action": "design"}]}\n```\nLet me know {if} needed.',
            "ok",
            [{"action": "design"}],
        ),
        ('{"reply": null, "actions": [{"action": "analyze"}]}', "", [{"action": "analyze"}]),
        ('"just a string"', "just a string", []),
        ('{"reply": "a"} and {"reply": "b"}', "a", []),
    ],
)
def test_model_reply_json_variants(fake_post, text, reply, actions):
    _, box = fake_post
    box["resp"] = _claude(text)
    out = providers.chat(providers.ProviderConfig("claude"), [{"role": "user", "content": "x"}], "{}")
    assert out["reply"] == reply and out["actions"] == actions


@pytest.mark.parametrize("payload", [{"unexpected": 1}, {"choices": []}, None])
def test_malformed_provider_payload_is_a_provider_error(fake_post, payload):
    _, box = fake_post
    box["resp"] = _Resp(payload)
    with pytest.raises(providers.ProviderError):
        providers.chat(providers.ProviderConfig("openai"), [{"role": "user", "content": "x"}], "{}")


def test_malformed_payload_falls_back_to_the_offline_parser(fake_post, tmp_path):
    _, box = fake_post
    box["resp"] = _Resp({"choices": []})
    a = Assistant(Session(build_template("bungalow"), out_dir=str(tmp_path)), providers.ProviderConfig("openai"))
    reply, res = a.ask("live load 3")
    assert "offline" in reply and res.changed


@pytest.mark.parametrize(
    "status, words", [(401, "API key"), (403, "API key"), (404, "model"), (429, "rate"), (503, "unavailable")]
)
def test_http_errors_are_friendly_and_never_echo_the_key(fake_post, status, words):
    _, box = fake_post
    box["resp"] = _Resp(None, status, '{"error": {"message": "bad key sk-secret-123456"}}')
    with pytest.raises(providers.ProviderError) as ei:
        providers.chat(providers.ProviderConfig("claude"), [{"role": "user", "content": "x"}], "{}")
    msg = str(ei.value)
    assert words.lower() in msg.lower() and str(status) in msg
    assert "sk-secret-123456" not in msg


def test_conversation_sent_to_the_model_starts_with_a_user_turn(fake_post):
    calls, _ = fake_post
    history = []
    for i in range(7):
        history += [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"a{i}"}]
    history.append({"role": "user", "content": "last"})
    for prov in ("claude", "openai", "ollama"):
        calls.clear()
        if prov != "claude":
            fake_post[1]["resp"] = _Resp(
                {"choices": [{"message": {"content": "{}"}}]} if prov == "openai" else {"message": {"content": "{}"}}
            )
        providers.chat(providers.ProviderConfig(prov), history, "{}")
        msgs = [m for m in calls[0][1]["json"]["messages"] if m["role"] != "system"]
        assert msgs[0]["role"] == "user" and msgs[-1]["content"] == "last"


def test_base_url_with_trailing_slash(fake_post):
    calls, box = fake_post
    box["resp"] = _Resp({"message": {"content": "{}"}})
    providers.chat(providers.ProviderConfig("ollama", base_url="http://localhost:11434/"), [], "{}")
    assert calls[0][0] == "http://localhost:11434/api/chat"


# ------------------------------------------------------------------ CLI
def test_cli_ask_that_understood_nothing_fails(tmp_path, capsys):
    out = tmp_path / "x.pwai"
    assert cli.main(["ask", "hello there", "--out", str(out)]) == 1
    assert "didn't catch" in capsys.readouterr().out


def test_cli_ask_success_still_exits_zero(tmp_path, capsys):
    out = tmp_path / "x.pwai"
    assert cli.main(["ask", "G+1 building 2x1 bays of 4 m", "--out", str(out)]) == 0
    assert out.exists()


# ------------------------------------------------------------------ licensing
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


def test_expired_licence_is_rejected_and_keeps_the_installed_one(signer):
    assert L.install_license(json.dumps(signer())).mode == "pro"
    old = signer(expires=(dt.date.today() - dt.timedelta(days=1)).isoformat())
    with pytest.raises(ValueError, match="expired"):
        L.install_license(json.dumps(old))
    assert L.current_state().mode == "pro"


def test_licence_with_unreadable_expiry_is_rejected(signer):
    with pytest.raises(ValueError, match="expiry"):
        L.install_license(json.dumps(signer(expires="31/03/2099")))


def test_licence_expiry_day_boundary(signer):
    lic = signer(expires="2030-06-30")
    with open(L._license_path(), "w", encoding="utf-8") as f:
        json.dump(lic, f)
    assert L.current_state(dt.date(2030, 6, 30)).mode == "pro"
    assert L.current_state(dt.date(2030, 7, 1)).mode != "pro"


def test_tampered_signature_and_garbage_never_raise(signer):
    lic = signer()
    assert not L.verify(dict(lic, company="Other"))
    assert not L.verify(dict(lic, sig="!!!"))
    assert not L.verify(dict(lic, sig=12))
    assert not L.verify({"sig": lic["sig"]})
    for bad in ("[]", "null", '"x"', "{"):
        with pytest.raises(ValueError):
            L.install_license(bad)


def _keygen():
    path = os.path.join(os.path.dirname(__file__), "..", "tools", "keygen.py")
    spec = importlib.util.spec_from_file_location("planwin_keygen_review", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("bad", ["2028-3-31", "31/03/2028", "2028-02-30"])
def test_keygen_rejects_an_unreadable_expiry(tmp_path, capsys, bad):
    kg = _keygen()
    pem = str(tmp_path / "k.pem")
    kg.main(["init", "--out", pem])
    with pytest.raises(SystemExit):
        kg.main(["issue", "--key", pem, "--name", "X", "--expires", bad, "--out", str(tmp_path / "x.lic")])
    assert not (tmp_path / "x.lic").exists()


def test_keygen_licence_installs(tmp_path, capsys, monkeypatch):
    import re

    kg = _keygen()
    pem, out = str(tmp_path / "k.pem"), str(tmp_path / "x.lic")
    kg.main(["init", "--out", pem])
    pub = re.search(r"PUBLIC_KEY_B64 = (\S+)", capsys.readouterr().out).group(1)
    monkeypatch.setattr(L, "PUBLIC_KEY_B64", pub)
    kg.main(["issue", "--key", pem, "--name", "X", "--expires", "20990331", "--out", out])
    lic = json.loads(open(out, encoding="utf-8").read())
    assert lic["expires"] == "2099-03-31"
    assert L.install_license(json.dumps(lic)).mode == "pro"
