"""The home_control module (D-132, D-139, D-140), against a pretend Tuya cloud, a pretend table of the devices on Pip's
own network and pretend plugs: signed requests, which device people mean (by name, nickname or group), switching
directly or else through the cloud, what Claude reads, the card and the Home page, and keys kept private."""
import hashlib
import hmac
import json

import httpx
import pytest

from pip_assistant.common.modules import ROOT, read
from pip_assistant.core.modules import Call, HostNotAllowed, Invalid, Registry

ROOM = {"r": ("robo-lamp", True, True), "b": ("big-lamp", True, True), "t": ("tv-lamp", False, True),
        "f": ("Fan", True, True)}


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
        if path.startswith("/v1.0/devices/factory-infos"):
            ids = request.url.params["device_ids"].split(",")
            return httpx.Response(200, json={"success": True, "result": [
                {"id": i, "mac": "D8FC92" + i.encode().hex().upper().rjust(6, "0")} for i in ids]})
        if path.endswith("/commands"):
            pid = path.split("/")[-2]
            [cmd] = json.loads(body)["commands"]
            n, _, o = self.plugs[pid]
            self.plugs[pid] = (n, cmd["value"], o)
            return httpx.Response(200, json={"success": True, "result": True})
        return httpx.Response(404)


def mac_of(pid):
    return "d8:fc:92:" + ":".join(pid.encode().hex().rjust(6, "0")[i:i + 2] for i in (0, 2, 4))


class FakeLocal:
    """The plugs on Pip's own network, answering directly (protocol 3.5)."""

    def __init__(self):
        self.near = {}         # id → on (a plug that answers directly)
        self.sent = []         # (id, on, key) of each direct switch

    async def ask(self, plug, on=None):
        if plug["id"] not in self.near:
            raise OSError("no answer")
        if on is not None:
            self.near[plug["id"]] = on
            self.sent.append((plug["id"], on, plug["key"]))
        return self.near[plug["id"]], 3.5


def on_network(tmp_path, mod, plugs: dict):
    """Put these plugs on pip-plugs: in the Pi's table of devices (by MAC) and answering directly."""
    rows = ["IP address       HW type     Flags       HW address            Mask     Device"]
    for n, (pid, on) in enumerate(plugs.items(), 20):
        rows.append(f"10.47.0.{n}       0x1         0x2         {mac_of(pid)}     *        wlan0")
        mod.local.near[pid] = on
    rows.append(f"10.64.0.9        0x1         0x2         {mac_of('x')}     *        eth0")   # campus: not a plug
    (tmp_path / "arp").write_text("\n".join(rows) + "\n")


def setup(tmp_path, monkeypatch, plugs, nicknames=""):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    (tmp_path / "secrets.toml").write_text('[home_control]\naccess_id = "AID"\naccess_secret = "SECRET"\nregion = "us"\n')
    fake = FakeTuya(dict(plugs))
    reg = Registry.load({}, infos=[read(ROOT / "home_control", {"nicknames": nicknames})], db_path=tmp_path / "pip.db",
                        transport=httpx.MockTransport(fake))
    mod = reg.modules["home_control"]
    mod.local, mod.ARP = FakeLocal(), tmp_path / "arp"
    return reg, mod, fake


async def say(reg, name, on):
    act = reg.action("plug")
    return await act.do(act.check({"do": "plug", "name": name, "on": on}, Call()), Call())


async def test_the_only_plug_is_the_light_and_switches(tmp_path, monkeypatch):
    reg, mod, fake = setup(tmp_path, monkeypatch, {"p1": ("Smart plug", True, True)})
    await mod.refresh()
    assert reg.summary(Call()) == "Home devices: Smart plug (plug): on."           # the camera isn't a device
    assert await say(reg, "the light", False) == "The light's off." and fake.plugs["p1"][1] is False
    assert "Smart plug (plug): off" in reg.summary(Call())
    assert all(r.url.host == "openapi.tuyaus.com" for r in fake.seen)
    assert sum(r.url.path == "/v1.0/token" for r in fake.seen) == 1          # the token is kept


async def test_which_device_they_mean(tmp_path, monkeypatch):
    reg, mod, fake = setup(tmp_path, monkeypatch, ROOM, nicknames="robo-lamp = desk lamp")
    await mod.refresh()
    assert [mod.kind(i) for i in "rbtf"] == ["light", "light", "light", "fan"]
    assert mod.find("the desk lamp") == ["r"]                                   # a nickname
    assert mod.find("tv lamp") == ["t"] and mod.find("the TV-lamp") == ["t"]     # hyphens don't matter
    assert mod.find("big lamp") == ["b"] and mod.find("robo lamp") == ["r"] and mod.find("the fan") == ["f"]
    for group in ("the lights", "lights", "all the lamps", "all the lights"):
        assert mod.find(group) == ["r", "b", "t"], group                        # every light, never the fan
    for everything in ("everything", "all", "all the plugs", "all the devices"):
        assert mod.find(everything) == ["r", "b", "t", "f"], everything
    assert mod.find("fans") == ["f"]
    assert mod.find("the lamp") is None and mod.find("the heater") is None    # not clear: Claude has to say
    assert "robo-lamp (light; also desk lamp): on" in reg.summary(Call()) and "Fan (fan): on" in reg.summary(Call())
    with pytest.raises(Invalid):
        reg.action("plug").check({"do": "plug", "name": "the heater", "on": True}, Call())
    with pytest.raises(Invalid):
        reg.action("plug").check({"do": "plug", "name": "fan", "on": "yes"}, Call())


async def test_the_lights_and_everything_are_switched_and_said(tmp_path, monkeypatch):
    reg, mod, fake = setup(tmp_path, monkeypatch, ROOM, nicknames="robo-lamp = desk lamp")
    await mod.refresh()
    assert await say(reg, "lights", False) == "The lights are off."
    assert [fake.plugs[i][1] for i in "rbtf"] == [False, False, False, True]   # the fan stays on
    assert await say(reg, "desk lamp", True) == "The desk lamp's on."
    assert await say(reg, "robo-lamp", False) == "The robo lamp's off."
    assert await say(reg, "everything", False) == "Everything's off." and not any(p[1] for p in fake.plugs.values())
    del fake.plugs["t"]                                                          # the cloud now fails for tv-lamp
    assert await say(reg, "the lights", True) == "The lights are on, but I couldn't reach the tv lamp."
    assert await say(reg, "tv lamp", True) == "I couldn't reach the tv lamp."


async def test_a_plug_on_pips_network_is_switched_directly_and_the_rest_through_the_cloud(tmp_path, monkeypatch):
    reg, mod, fake = setup(tmp_path, monkeypatch, ROOM)
    commands = lambda: sum(r.url.path.endswith("/commands") for r in fake.seen)  # noqa: E731
    on_network(tmp_path, mod, {"r": True, "b": True, "f": True})               # tv-lamp isn't on pip-plugs
    await mod.refresh()                                                          # MACs from the cloud → addresses
    assert set(mod.found) == {"r", "b", "f"} and mod.found["r"]["ip"] == "10.47.0.20"
    assert await say(reg, "the lights", False) == "The lights are off."
    assert sorted(mod.local.sent) == [("b", False, "KEY-b"), ("r", False, "KEY-r")] and commands() == 1   # tv-lamp
    assert mod.found["r"]["version"] == 3.5 and mod.switched["r"][1] == "direct" and mod.switched["t"][1] == "cloud"
    del mod.local.near["f"]                                    # the fan unplugged: through the cloud this time
    assert await say(reg, "fan", False) == "The fan's off." and commands() == 2 and mod.found["f"]["missed"]
    assert mod.near("f") is None                               # ...and until a check finds it answering again
    mod.local.near["f"] = False
    await mod.check()
    assert not mod.found["f"]["missed"] and mod.near("f") is not None


async def test_devices_on_pips_network_are_checked_directly_and_keys_stay_private(tmp_path, monkeypatch, caplog):
    reg, mod, fake = setup(tmp_path, monkeypatch, {"a": ("Desk plug", False, True)})
    published = []

    async def publish(kind, data=None, private=None):
        published.append((kind, data, private))
    mod.ctx.publish = publish
    on_network(tmp_path, mod, {"a": True})                     # switched by its own button
    await mod.refresh()
    await mod.check()
    assert mod.plugs["a"]["on"] is True and published[-1][0] == "changed"
    fake.plugs["a"] = ("Desk plug", False, True)               # the cloud's list lags behind: the direct check wins
    await mod.refresh()
    assert mod.plugs["a"]["on"] is True
    page = await mod.dashboard("list", {})
    everything = repr(published) + caplog.text + reg.summary(Call()) + json.dumps(page)
    assert "KEY-" not in everything and mac_of("a") not in everything.lower()


async def test_the_home_page(tmp_path, monkeypatch):
    reg, mod, fake = setup(tmp_path, monkeypatch, {**ROOM, "f": ("Fan", True, False)}, nicknames="robo-lamp = desk lamp")
    on_network(tmp_path, mod, {"r": True})
    await mod.refresh()
    await mod.check()
    assert reg.home_cards() == []                                             # D-148: no switches on the Overview
    page = await mod.dashboard("list", {})
    assert [(d["name"], d["online"]) for d in page["devices"]][-1] == ("Fan", False)
    robo = page["devices"][0]
    assert (robo["name"], robo["kind"], robo["also"], robo["direct"], robo["ip"], robo["version"]) == \
        ("robo-lamp", "light", ["desk lamp"], True, "10.47.0.20", 3.5)
    assert page["devices"][2]["direct"] is False and page["direct"] == 1
    assert page["groups"] == {"everything": ["robo-lamp", "big-lamp", "tv-lamp", "Fan"],
                              "the lights": ["robo-lamp", "big-lamp", "tv-lamp"], "the fans": ["Fan"]}
    assert await mod.dashboard("switch", {"id": "t", "on": True}) == {"ok": True}
    assert fake.plugs["t"][1] is True and mod.plugs["t"]["on"] is True
    assert "error" in await mod.dashboard("switch", {"id": "t", "on": "yes"})
    assert "error" in await mod.dashboard("switch", {"id": "nope", "on": True})
    fake.plugs.clear()                                                        # the cloud now fails the command
    r = await mod.dashboard("switch", {"id": "t", "on": False})
    assert r == {"error": "Couldn't reach it."} and mod.plugs["t"]["on"] is True   # not shown as switched


async def test_no_keys_means_nothing_to_switch_and_only_tuya_can_be_reached(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    reg = Registry.load({}, infos=[read(ROOT / "home_control")], db_path=tmp_path / "pip.db",
                        transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(Invalid):
        await reg.modules["home_control"].refresh()
    assert reg.summary(Call()) == ""
    async with reg.modules["home_control"].ctx.http() as http:
        with pytest.raises(HostNotAllowed):
            await http.get("https://example.com/")


def test_the_pis_table_of_devices_is_read_for_the_plug_network_only(tmp_path, monkeypatch):
    reg, mod, fake = setup(tmp_path, monkeypatch, {})
    (tmp_path / "arp").write_text(
        "IP address       HW type     Flags       HW address            Mask     Device\n"
        "10.47.0.63       0x1         0x2         d8:fc:92:5b:0d:8c     *        wlan0\n"
        "10.47.0.99       0x1         0x0         00:00:00:00:00:00     *        wlan0\n"   # never answered
        "10.64.255.254    0x1         0x2         00:11:22:33:44:55     *        eth0\n")
    assert mod.neighbours() == {"d8:fc:92:5b:0d:8c": "10.47.0.63"}
    mod.ARP = tmp_path / "missing"
    assert mod.neighbours() == {}


def test_tinytuya_is_asked_for_the_version_first_then_switches(monkeypatch):
    """Local: on first contact it finds the plug's protocol version by asking (never switching while trying)."""
    import tinytuya
    from pip_assistant.modules.home_control.module import Local
    calls = []

    class Device:
        def __init__(self, pid, ip, key, version, **kw):
            self.version = version

        def status(self):
            calls.append(("status", self.version))
            return {"dps": {"1": True}} if self.version == 3.4 else {"Error": "no"}

        def set_status(self, on, switch):
            calls.append(("set", self.version, on))
            return {"dps": {"1": on}}

        def close(self):
            pass
    monkeypatch.setattr(tinytuya, "OutletDevice", Device)
    plug = {"id": "a", "ip": "10.47.0.5", "key": "k", "dp": 1, "version": None}
    assert Local._ask(plug, False) == (False, 3.4)
    assert calls == [("status", 3.5), ("status", 3.4), ("set", 3.4, False)]


# ---------------------------------------------------------------- the disco (D-142: six seven)
async def disco_room(tmp_path, monkeypatch):
    from pip_assistant.modules.home_control import module as hc
    monkeypatch.setattr(hc, "FLASH_S", 0.01)
    reg, mod, fake = setup(tmp_path, monkeypatch, ROOM)
    await mod.refresh()
    on_network(tmp_path, mod, {"r": True, "b": True, "t": False, "f": True})
    await mod.check()
    return reg, mod, fake


async def test_the_disco_flashes_only_the_lights_directly_then_puts_them_back(tmp_path, monkeypatch):
    import asyncio
    reg, mod, fake = await disco_room(tmp_path, monkeypatch)
    calls = len(fake.seen)
    reg.get("home_control").disco(0.1)
    reg.get("home_control").disco(0.1)                      # a second one while it flashes is ignored
    await mod.party
    flashed = [(pid, on) for pid, on, _ in mod.local.sent]
    assert {pid for pid, _ in flashed} == {"r", "b", "t"}   # never the fan
    assert ("t", True) in flashed and ("r", False) in flashed   # each went both ways
    assert mod.local.near == {"r": True, "b": True, "t": False, "f": True}   # back as they were
    assert [mod.plugs[i]["on"] for i in "rbtf"] == [True, True, False, True]
    assert len(fake.seen) == calls                          # the cloud was never asked
    assert mod.party.done()
    await asyncio.sleep(0)


async def test_a_light_switched_during_the_disco_stays_as_switched(tmp_path, monkeypatch):
    import asyncio
    reg, mod, fake = await disco_room(tmp_path, monkeypatch)
    reg.get("home_control").disco(0.2)
    await asyncio.sleep(0.05)
    assert await say(reg, "tv lamp", True) == "The tv lamp's on."   # someone wants it on
    await mod.party
    assert mod.local.near["t"] is True and mod.local.near["r"] is True and mod.local.near["b"] is True


async def test_no_disco_without_lights_on_pips_network(tmp_path, monkeypatch):
    reg, mod, fake = setup(tmp_path, monkeypatch, ROOM)
    await mod.refresh()                                     # only through the cloud: too slow for a disco
    calls = len(fake.seen)
    reg.get("home_control").disco(1.0)
    await mod.party
    assert len(fake.seen) == calls and mod.local.sent == []


async def test_other_modules_switch_by_name_and_hear_what_didnt(tmp_path, monkeypatch):
    """The api routines uses (D-143): a device or group by what it's called; back come the ones that didn't switch."""
    reg, mod, fake = setup(tmp_path, monkeypatch, ROOM, nicknames="robo-lamp = desk lamp")
    home = mod.api
    with pytest.raises(LookupError):
        await home.switch("lights", False)                                     # nothing fetched yet
    await mod.refresh()
    assert await home.switch("lights", False) == []
    assert [fake.plugs[i][1] for i in "rbtf"] == [False, False, False, True]
    assert await home.switch("fans", False) == [] and fake.plugs["f"][1] is False
    del fake.plugs["t"]
    assert await home.switch("everything", True) == ["the tv lamp"]
    with pytest.raises(LookupError):
        await home.switch("the heater", True)
    assert [d["kind"] for d in home.devices()] == ["light", "light", "light", "fan"]
    assert home.devices()[0] == {"name": "robo-lamp", "kind": "light", "on": True}
