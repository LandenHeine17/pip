"""The tuya module (D-132), against a pretend Tuya cloud: signed requests, finding a plug by what people call
it, switching it, what Claude reads, and nothing but Tuya's API hosts."""
import hashlib
import hmac
import json

import httpx
import pytest

from pip_assistant.common.modules import ROOT, read
from pip_assistant.core.modules import Call, HostNotAllowed, Invalid, Registry


class FakeTuya:
    def __init__(self, plugs):
        self.plugs = plugs        # id → (name, on, online)
        self.seen = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        h, path = request.headers, request.url.raw_path.decode()
        body = request.content.decode()
        text = "\n".join([request.method, hashlib.sha256(body.encode()).hexdigest(), "", path])
        msg = "AID" + h.get("access_token", "") + h["t"] + text
        if h["sign"] != hmac.new(b"SECRET", msg.encode(), hashlib.sha256).hexdigest().upper():
            return httpx.Response(200, json={"success": False, "code": 1004, "msg": "sign invalid"})
        if path.startswith("/v1.0/token"):
            return httpx.Response(200, json={"success": True, "result": {"access_token": "TOK", "expire_time": 7200}})
        if path.startswith("/v1.0/iot-01/associated-users/devices"):
            devs = [{"id": i, "name": n, "online": o, "status": [{"code": "switch_1", "value": on}]}
                    for i, (n, on, o) in self.plugs.items()]
            devs.append({"id": "cam", "name": "Camera", "status": [{"code": "record", "value": True}]})
            return httpx.Response(200, json={"success": True, "result": {"devices": devs}})
        if path.endswith("/commands"):
            pid = path.split("/")[-2]
            [cmd] = json.loads(body)["commands"]
            n, _, o = self.plugs[pid]
            self.plugs[pid] = (n, cmd["value"], o)
            return httpx.Response(200, json={"success": True, "result": True})
        return httpx.Response(404)


def setup(tmp_path, monkeypatch, plugs, nicknames=""):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    (tmp_path / "secrets.toml").write_text('[tuya]\naccess_id = "AID"\naccess_secret = "SECRET"\nregion = "us"\n')
    fake = FakeTuya(plugs)
    reg = Registry.load({}, infos=[read(ROOT / "tuya", {"nicknames": nicknames})], db_path=tmp_path / "pip.db",
                        transport=httpx.MockTransport(fake))
    return reg, reg.modules["tuya"], fake


async def test_the_only_plug_is_the_light_and_switches(tmp_path, monkeypatch):
    reg, mod, fake = setup(tmp_path, monkeypatch, {"p1": ("Smart plug", True, True)})
    await mod.refresh()
    assert reg.summary(Call()) == "Smart plugs: Smart plug: on."           # the camera isn't a plug
    act = reg.action("plug")
    said = await act.do(act.check({"do": "plug", "name": "the light", "on": False}, Call()), Call())
    assert said == "The light's off." and fake.plugs["p1"][1] is False
    assert "Smart plug: off" in reg.summary(Call())
    assert all(r.url.host == "openapi.tuyaus.com" for r in fake.seen)
    assert sum(r.url.path == "/v1.0/token" for r in fake.seen) == 1          # the token is kept


async def test_plugs_are_found_by_name_or_nickname(tmp_path, monkeypatch):
    reg, mod, fake = setup(tmp_path, monkeypatch, {"a": ("Desk plug", False, True), "b": ("Fan plug", True, True)},
                           nicknames="Desk plug = lamp, light; Fan plug = fan")
    await mod.refresh()
    assert mod.find("the lamp") == "a" and mod.find("fan") == "b" and mod.find("desk") == "a"
    assert mod.find("the light") == "a" and mod.find("the heater") is None
    assert "Desk plug (also lamp, light): off" in reg.summary(Call())
    act = reg.action("plug")
    with pytest.raises(Invalid):
        act.check({"do": "plug", "name": "the heater", "on": True}, Call())   # two plugs: "it" isn't enough
    with pytest.raises(Invalid):
        act.check({"do": "plug", "name": "fan", "on": "yes"}, Call())


async def test_a_failure_is_said_and_only_tuya_can_be_reached(tmp_path, monkeypatch):
    reg, mod, fake = setup(tmp_path, monkeypatch, {"p1": ("Smart plug", True, True)})
    await mod.refresh()
    fake.plugs.clear()                                                        # Tuya now fails the command
    act = reg.action("plug")
    said = await act.do(act.check({"do": "plug", "name": "light", "on": False}, Call()), Call())
    assert said == "I couldn't reach the light."
    async with mod.ctx.http() as http:
        with pytest.raises(HostNotAllowed):
            await http.get("https://example.com/")


async def test_no_keys_means_nothing_to_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    reg = Registry.load({}, infos=[read(ROOT / "tuya")], db_path=tmp_path / "pip.db",
                        transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(Invalid):
        await reg.modules["tuya"].refresh()
    assert reg.summary(Call()) == ""


async def test_the_front_page_card_switches_a_plug(tmp_path, monkeypatch):
    reg, mod, fake = setup(tmp_path, monkeypatch, {"p1": ("Smart plug", False, True), "p2": ("Fan", True, False)})
    assert reg.home_cards() == []                                             # nothing fetched yet: no card
    await mod.refresh()
    [card] = reg.home_cards()
    assert card["switch"] == "switch" and card["module"] == "tuya"
    assert [(i["title"], i["on"], i["sub"]) for i in card["items"]] == [("Smart plug", False, ""), ("Fan", None, "offline")]
    assert await mod.dashboard("switch", {"id": "p1", "on": True}) == {"ok": True}
    assert fake.plugs["p1"][1] is True and mod.plugs["p1"]["on"] is True
    assert "error" in await mod.dashboard("switch", {"id": "p1", "on": "yes"})
    assert "error" in await mod.dashboard("switch", {"id": "nope", "on": True})
    fake.plugs.clear()                                                        # Tuya now fails the command
    r = await mod.dashboard("switch", {"id": "p1", "on": False})
    assert r == {"error": "Couldn't reach the plug."} and mod.plugs["p1"]["on"] is True   # not shown as switched
