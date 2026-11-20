"""The tuya module (D-132, D-139), against a pretend Tuya cloud and pretend plugs on Pip's own network: signed
requests, finding a plug by what people call it (or all of them), switching it directly or else through the
cloud, what Claude reads, and nothing but Tuya's API hosts."""
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
            devs = [{"id": i, "name": n, "online": o, "local_key": "KEY-" + i, "status": [{"code": "switch_1", "value": on}]}
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


class FakeLocal:
    """The plugs on Pip's own network: their announcements and direct messages."""

    def __init__(self):
        self.near = {}         # id → on (a plug that answers directly)
        self.sent = []         # (id, on, key) of each direct switch

    @staticmethod
    def decrypt(data):
        return json.loads(data)

    async def switch(self, plug, on):
        if plug["id"] not in self.near:
            raise OSError("no answer")
        self.near[plug["id"]] = on
        self.sent.append((plug["id"], on, plug["key"]))

    async def state(self, plug):
        if plug["id"] not in self.near:
            raise OSError("no answer")
        return self.near[plug["id"]]


def announce(mod, pid, ip="10.47.0.20"):
    mod.heard(json.dumps({"gwId": pid, "ip": ip, "version": "3.4"}).encode(), ip)


def setup(tmp_path, monkeypatch, plugs, nicknames=""):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    (tmp_path / "secrets.toml").write_text('[tuya]\naccess_id = "AID"\naccess_secret = "SECRET"\nregion = "us"\n')
    fake = FakeTuya(plugs)
    reg = Registry.load({}, infos=[read(ROOT / "tuya", {"nicknames": nicknames})], db_path=tmp_path / "pip.db",
                        transport=httpx.MockTransport(fake))
    reg.modules["tuya"].local = FakeLocal()
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


async def test_a_plug_on_pips_network_is_switched_directly_and_the_rest_through_the_cloud(tmp_path, monkeypatch):
    reg, mod, fake = setup(tmp_path, monkeypatch, {"a": ("Desk plug", False, True), "b": ("Fan plug", False, True)})
    await mod.refresh()
    commands = lambda: sum(r.url.path.endswith("/commands") for r in fake.seen)  # noqa: E731
    mod.local.near["a"] = False
    announce(mod, "a")
    announce(mod, "b", ip="10.65.1.9")                  # not on Pip's network (the campus): ignored
    mod.heard(b"not a plug", "10.47.0.30")
    assert set(mod.found) == {"a"} and mod.found["a"] == {"ip": "10.47.0.20", "version": "3.4"}
    await mod.switch("a", True)
    assert mod.local.sent == [("a", True, "KEY-a")] and commands() == 0 and mod.plugs["a"]["on"] is True
    await mod.switch("b", True)                         # not found on Pip's network: the cloud
    assert commands() == 1 and fake.plugs["b"][1] is True
    del mod.local.near["a"]                             # unplugged, or moved away: the cloud, until heard again
    await mod.switch("a", False)
    assert commands() == 2 and "a" not in mod.found


async def test_everything_switches_every_plug_and_says_which_it_couldnt_reach(tmp_path, monkeypatch):
    reg, mod, fake = setup(tmp_path, monkeypatch, {"a": ("Desk plug", True, True), "b": ("Fan plug", True, True),
                                                   "c": ("Heater", True, True)})
    await mod.refresh()
    mod.local.near["a"] = True
    announce(mod, "a")
    act = reg.action("plug")
    for said in ("everything", "all the plugs", "all the lights"):
        assert act.check({"do": "plug", "name": said, "on": False}, Call())[0] == "*"
    said = await act.do(act.check({"do": "plug", "name": "everything", "on": False}, Call()), Call())
    assert said == "All 3 plugs are off." and not any(p["on"] for p in mod.plugs.values())
    del fake.plugs["c"]                                 # the cloud now fails for the heater
    said = await act.do(act.check({"do": "plug", "name": "all", "on": True}, Call()), Call())
    assert said == "The rest are on, but I couldn't reach Heater."


async def test_plugs_on_pips_network_are_checked_directly_and_keys_stay_private(tmp_path, monkeypatch, caplog):
    reg, mod, fake = setup(tmp_path, monkeypatch, {"a": ("Desk plug", False, True)})
    published = []

    async def publish(kind, data=None, private=None):
        published.append((kind, data, private))
    mod.ctx.publish = publish
    await mod.refresh()
    mod.local.near["a"] = True                          # switched by its own button
    announce(mod, "a")
    monkeypatch.setattr("asyncio.sleep", _once())
    with pytest.raises(StopAsyncIteration):
        await mod._check()
    assert mod.plugs["a"]["on"] is True and published[-1][0] == "changed"
    fake.plugs["a"] = ("Desk plug", False, True)        # the cloud's list lags behind: the direct check wins
    await mod.refresh()
    assert mod.plugs["a"]["on"] is True
    everything = repr(published) + caplog.text + repr(reg.summary(Call())) + repr(mod.home())
    assert "KEY-" not in everything


def _once():
    """asyncio.sleep that lets a background loop run once, then stops it."""
    calls = []

    async def sleep(_):
        calls.append(1)
        if len(calls) > 1:
            raise StopAsyncIteration
    return sleep


def test_a_real_announcement_is_read_with_tinytuya():
    import tinytuya
    from tinytuya.core import udp_helper
    from pip_assistant.modules.tuya.module import Local
    raw = json.dumps({"gwId": "abc", "ip": "10.47.0.5", "version": "3.3"}).encode()
    sealed = tinytuya.AESCipher(udp_helper.udpkey).encrypt(raw, False)
    msg = tinytuya.pack_message(tinytuya.TuyaMessage(0, 0x13, 0, b"\0\0\0\0" + sealed, 0, True,
                                                     tinytuya.PREFIX_55AA_VALUE, False))
    assert Local.decrypt(msg)["gwId"] == "abc"
