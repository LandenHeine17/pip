"""Home control (D-132, D-139, D-140): the lamps and the fan, each on a smart plug (Eightree, Tuya inside).

    "Pip, turn on the lights"  → Claude: ACTION {"do": "plug", "name": "lights", "on": true}
    "turn off the desk lamp"   → Claude: ACTION {"do": "plug", "name": "robo-lamp", "on": false}
                               → check_plug finds the devices (`find`): a device's name or nickname, or a group
                                 ("lights", "fans", "everything")
                               → do_plug switches them (directly, else through the cloud) and says
                                 "The lights are on." / "The desk lamp's off."

Each device has a kind, from its name and nicknames (`kind`): a light (lamp, light, bulb), a fan, or a plug.
"The lights" are all the lights, not the fan; "everything" is every device. Claude reads the list with each
message (`summary`) and picks the device that fits what was said; the code's own matching is the backstop.

How Pip reaches a device: the plugs join the Wi-Fi network the Pi hosts for them ("pip-plugs",
deploy/plug-network.sh), where Pip switches each one directly (`Local`, the tinytuya library: one encrypted
message, a few hundredths of a second, no internet needed). Any other (not on pip-plugs yet, or not answering)
is switched through Tuya's cloud instead (Pi → Tuya → plug, about 0.2 s).

Two background jobs (`tasks`):
  - every `refresh_minutes` (10), Tuya's cloud lists the devices: their names, whether they're on, each one's
    local key (what its direct messages are encrypted with; a new one when a plug is paired again) and, once,
    its MAC address. Keys and MACs are kept in memory only: never logged, published, shown or saved.
  - every `check_seconds` (30): `locate` finds each plug's address on pip-plugs by its MAC in the Pi's table of
    devices it talks to (/proc/net/arp; these plugs don't announce themselves), and each one found is asked
    directly whether it's on, so one switched by its own button or the Smart Life app shows within half a minute.

On the dashboard: a Home card on the front page with an On/Off button for each (`home()`), and the module's own
Home page (static/index.html, `dashboard` "list" and "switch"), both switching the same way as by voice.

Tuya's API (https://developer.tuya.com/en/docs/cloud/): every request is signed with HMAC-SHA256 of the
project's access secret (`sign`); a token (`/v1.0/token`) is fetched first and kept until it expires. The keys
are the module's own section of ~/pip-data/secrets.toml ([home_control]); only Tuya's API hosts may be reached over
the internet (module.toml permissions), and directly only the plugs inside `plug_network` ("lan:plugs").
Nothing is saved: no tables.
"""
import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import time
from pathlib import Path

from pip_assistant.core.modules import Action, Call, Invalid, Module

log = logging.getLogger(__name__)

HOSTS = {"us": "openapi.tuyaus.com", "us-e": "openapi-ueaz.tuyaus.com", "eu": "openapi.tuyaeu.com",
         "eu-w": "openapi-weaz.tuyaeu.com", "cn": "openapi.tuyacn.com", "in": "openapi.tuyain.com"}
SWITCHES = {"switch_1": 1, "switch": 1, "switch_led": 20}   # the status code that turns a plug (or a bulb) on
                                                             # and off → its number in the plug's direct messages
VERSIONS = (3.5, 3.4, 3.3)                                   # Tuya's protocol versions, newest first
LIGHT = re.compile(r"\b(lights?|lamps?|bulbs?)\b")
FAN = re.compile(r"\bfans?\b")
MANY = re.compile(r"\b(all|every|everything|each|lights|lamps|bulbs|fans)\b")   # a group, not one device
ANY_ONE = re.compile(r"\b(plug|outlet|socket|light|lights|lamp|it|that)\b")     # with only one device: it
GROUP_NAMES = {"light": "the lights", "fan": "the fans"}


def words(name: str) -> str:
    """"Robo-Lamp" → "robo lamp": how a name is compared with what was said."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower())).strip()


def mac(text: str) -> str:
    """"D8FC925B073F" or "d8:fc:92:5b:07:3f" → "d8:fc:92:5b:07:3f"."""
    h = re.sub(r"[^0-9a-f]", "", str(text or "").lower())
    return ":".join(h[i:i + 2] for i in range(0, 12, 2)) if len(h) == 12 else ""


class Local:
    """Talking to a plug directly, on Pip's own network (tinytuya; it waits on the network, so in a thread)."""

    @staticmethod
    def _ask(plug: dict, on: bool | None) -> tuple[bool, float]:
        """Switch it (on True/False) or ask whether it's on (None) → (on?, the protocol version it answered)."""
        import tinytuya

        def answer(version, switching):
            d = tinytuya.OutletDevice(plug["id"], plug["ip"], plug["key"], version=version, connection_timeout=2,
                                      connection_retry_limit=1, connection_retry_delay=0)
            try:
                got = d.set_status(on, switch=plug["dp"]) if switching else d.status()
            finally:
                d.close()
            return got if isinstance(got, dict) and "Error" not in got else None

        version = plug.get("version")
        if not version:   # first contact: the version it speaks (asking, not switching, while trying)
            version = next((v for v in VERSIONS if answer(v, False) is not None), None)
            if version is None:
                raise OSError("the plug didn't answer")
        got = answer(version, on is not None)
        if got is None:
            raise OSError("the plug didn't answer")
        return bool((got.get("dps") or {}).get(str(plug["dp"]), on)), version

    async def ask(self, plug: dict, on: bool | None = None) -> tuple[bool, float]:
        return await asyncio.to_thread(self._ask, plug, on)


class Home(Module):
    ARP = Path("/proc/net/arp")   # the Pi's table of the devices it talks to: address, MAC, network

    def __init__(self, ctx, local=None):
        self.ctx = ctx
        self.local = local or Local()
        self.plugs: dict[str, dict] = {}       # id → {"name", "on" (True/False/None), "online", "switch"}
        self.keys: dict[str, str] = {}           # id → its local key (from the cloud; memory only)
        self.macs: dict[str, str] = {}           # id → its MAC address (from the cloud; memory only)
        self.found: dict[str, dict] = {}         # id → {"ip", "version", "checked", "missed"}: on Pip's own network
        self.checked = 0.0                       # when the cloud's list was last fetched (ctx.clock)
        self.token, self.token_until = "", 0.0
        self.switched: dict[str, tuple] = {}     # id → (when Pip last switched it, "direct" or "cloud")
        self.actions = {"plug": Action(self.check_plug, self.do_plug)}

    # ---------------------------------------------------------------- Tuya's API
    @property
    def host(self) -> str:
        return HOSTS.get(str(self.ctx.secrets.get("region") or "us"), HOSTS["us"])

    def sign(self, method: str, path: str, body: str, t: str, token: str = "") -> str:
        """Tuya's request signature: HMAC-SHA256(secret, id + token + t + method\\nsha256(body)\\n\\npath)."""
        text = "\n".join([method, hashlib.sha256(body.encode()).hexdigest(), "", path])
        msg = self.ctx.secrets.get("access_id") + token + t + text
        return hmac.new(self.ctx.secrets.get("access_secret").encode(), msg.encode(), hashlib.sha256).hexdigest().upper()

    async def call(self, http, method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
        """One signed request; `path` includes any query (keys in alphabetical order, as Tuya signs them)."""
        token = self.token if token is None else token
        data = json.dumps(body) if body is not None else ""
        t = str(int(time.time() * 1000))
        headers = {"client_id": self.ctx.secrets.get("access_id"), "t": t, "sign_method": "HMAC-SHA256",
                   "sign": self.sign(method, path, data, t, token)}
        if token:
            headers["access_token"] = token
        if body is not None:
            headers["Content-Type"] = "application/json"
        r = await http.request(method, f"https://{self.host}{path}", headers=headers, content=data or None)
        r.raise_for_status()
        out = r.json()
        if not out.get("success"):
            raise RuntimeError(f"Tuya said no: {out.get('code')} {str(out.get('msg', ''))[:80]}")
        return out.get("result")

    async def ensure_token(self, http) -> None:
        if self.token and time.time() < self.token_until:
            return
        got = await self.call(http, "GET", "/v1.0/token?grant_type=1", token="")
        self.token = got["access_token"]
        self.token_until = time.time() + int(got.get("expire_time", 3600)) - 120

    async def request(self, method: str, path: str, body: dict | None = None):
        if not (self.ctx.secrets.get("access_id") and self.ctx.secrets.get("access_secret")):
            raise Invalid("no Tuya keys in secrets.toml [home_control]")
        async with self.ctx.http(timeout=6.0) as http:
            await self.ensure_token(http)
            try:
                return await self.call(http, method, path, body)
            except RuntimeError as e:
                if "1010" not in str(e):           # 1010: the token expired early; fetch a new one, once
                    raise
                self.token = ""
                await self.ensure_token(http)
                return await self.call(http, method, path, body)

    async def refresh(self) -> None:
        """Every device linked to the project, whether it's on, its local key and (once) its MAC address."""
        got = await self.request("GET", "/v1.0/iot-01/associated-users/devices?size=50")
        plugs = {}
        for d in (got or {}).get("devices", []):
            status = {s.get("code"): s.get("value") for s in d.get("status") or []}
            switch = next((c for c in SWITCHES if c in status), None)
            if switch is None:
                continue   # not something that switches on and off
            pid, on = d["id"], bool(status[switch])
            near = pid in self.found and not self.found[pid]["missed"]   # checked directly: fresher than the cloud
            when, how = self.switched.get(pid, (-1e9, ""))
            if pid in self.plugs and (near or (how == "cloud" and self.ctx.clock() - when < 30)):
                on = self.plugs[pid]["on"]   # (and just switched through the cloud: its list may not show it yet)
            plugs[pid] = {"name": d.get("name") or "plug", "on": on, "online": near or bool(d.get("online", True)),
                          "switch": switch}
            if d.get("local_key"):
                self.keys[pid] = d["local_key"]
        self.plugs, self.checked = plugs, self.ctx.clock()
        missing = [pid for pid in plugs if pid not in self.macs]
        if missing:
            try:
                for f in await self.request("GET", "/v1.0/devices/factory-infos?device_ids=" + ",".join(missing)) or []:
                    if mac(f.get("mac")):
                        self.macs[f["id"]] = mac(f["mac"])
            except Exception as e:
                log.warning("couldn't fetch the plugs' network addresses: %s", type(e).__name__)
        self.locate()
        await self.ctx.publish("changed", {"plugs": len(plugs)})   # numbers only

    def tasks(self) -> list:
        return [self._loop(), self._check()]

    async def _loop(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception as e:   # no internet, no keys, Tuya down: try again later
                log.warning("couldn't fetch the smart plugs: %s", type(e).__name__)
            await asyncio.sleep(max(1.0, float(self.ctx.settings.get("refresh_minutes", 10))) * 60)

    # ---------------------------------------------------------------- the plugs on Pip's own network
    def network(self):
        return ipaddress.ip_network(str(self.ctx.settings.get("plug_network") or "10.47.0.0/24"), strict=False)

    def neighbours(self) -> dict[str, str]:
        """MAC → address of each device the Pi talks to on the plugs' network (from its ARP table)."""
        out, net = {}, self.network()
        try:
            lines = self.ARP.read_text().splitlines()[1:]
        except OSError:
            return out
        for line in lines:   # IP address, HW type, Flags (0x0: not reached), HW address, Mask, Device
            f = line.split()
            try:
                if len(f) >= 4 and f[2] != "0x0" and mac(f[3]) and ipaddress.ip_address(f[0]) in net:
                    out[mac(f[3])] = f[0]
            except ValueError:
                continue
        return out

    def locate(self) -> None:
        """Where each plug is on pip-plugs, by its MAC address."""
        table = self.neighbours()
        for pid, m in self.macs.items():
            ip = table.get(m)
            if ip and self.found.get(pid, {}).get("ip") != ip:
                if pid not in self.found:
                    log.info("a smart plug is on Pip's own network")
                self.found[pid] = {"ip": ip, "version": (self.found.get(pid) or {}).get("version"),
                                   "checked": 0.0, "missed": False}

    def near(self, pid: str, retry: bool = False) -> dict | None:
        """What's needed to talk to this plug directly, if it's on Pip's network and Pip knows its key (one that
        just didn't answer is left to the cloud until the next check finds it again, unless `retry`)."""
        f = self.found.get(pid)
        if not f or pid not in self.keys or pid not in self.plugs or (f["missed"] and not retry):
            return None
        return {"id": pid, "key": self.keys[pid], "dp": SWITCHES[self.plugs[pid]["switch"]], "ip": f["ip"],
                "version": f["version"]}

    async def direct(self, pid: str, on: bool | None = None, retry: bool = False) -> bool:
        """Switch it (or ask whether it's on) directly; raises if it isn't on pip-plugs or doesn't answer."""
        plug = self.near(pid, retry)
        if plug is None:
            raise LookupError("not on Pip's network")
        try:
            state, version = await self.local.ask(plug, on)
        except Exception:
            if pid in self.found:
                self.found[pid]["missed"] = True
            raise
        if pid in self.found:
            self.found[pid].update(version=version, checked=self.ctx.clock(), missed=False)
        return state

    async def _check(self) -> None:
        while True:
            await self.check()
            await asyncio.sleep(max(5.0, float(self.ctx.settings.get("check_seconds", 30))))

    async def check(self) -> None:
        """Ask each plug on pip-plugs whether it's on (one switched by its button or the app shows within 30 s)."""
        self.locate()
        changed = False
        for pid in list(self.found):
            try:
                on = await self.direct(pid, retry=True)
            except Exception:
                continue   # gone from the network (unplugged?): the cloud until it answers again
            if pid in self.plugs and (self.plugs[pid]["on"], self.plugs[pid]["online"]) != (on, True):
                self.plugs[pid].update(on=on, online=True)
                changed = True
        if changed:
            await self.ctx.publish("changed", {"plugs": len(self.plugs)})

    # ---------------------------------------------------------------- which device they mean
    def nicknames(self) -> dict[str, str]:
        """"robo-lamp = desk lamp; Fan = fan" → {"desk lamp": "robo lamp", "fan": "fan"}."""
        out = {}
        for part in str(self.ctx.settings.get("nicknames") or "").split(";"):
            name, _, others = part.partition("=")
            for other in others.split(","):
                if words(other) and words(name):
                    out[words(other)] = words(name)
        return out

    def also(self, pid: str) -> list[str]:
        """Its nicknames."""
        return [k for k, v in self.nicknames().items() if v == words(self.plugs[pid]["name"])]

    def kind(self, pid: str) -> str:
        """"light", "fan" or "plug", from its name and nicknames."""
        text = " ".join([words(self.plugs[pid]["name"])] + self.also(pid))
        return "light" if LIGHT.search(text) else "fan" if FAN.search(text) else "plug"

    def find(self, said: str) -> list[str] | None:
        """The devices they mean: one by name or nickname ("desk lamp", "tv lamp"), a group ("the lights": every
        light, not the fan; "the fans"; "everything"), or None if it isn't clear."""
        said = re.sub(r"^(the|my) ", "", words(said))
        name = self.nicknames().get(said, said)
        exact = [i for i, p in self.plugs.items() if words(p["name"]) == name]
        if exact:
            return exact[:1]
        if MANY.search(said):
            kind = "light" if LIGHT.search(said) else "fan" if FAN.search(said) else ""
            group = [i for i in self.plugs if not kind or self.kind(i) == kind]
            if group and (kind or re.search(r"\b(all|every|everything|each)\b", said)):
                return group
        part = [i for i, p in self.plugs.items() if name and (name in words(p["name"]) or words(p["name"]) in name)]
        if len(part) == 1:
            return part
        if len(self.plugs) == 1 and (not said or ANY_ONE.search(said)):
            return list(self.plugs)   # only one device: "the light", "the plug", "it" all mean it
        for kind, pattern in (("light", LIGHT), ("fan", FAN)):
            one = [i for i in self.plugs if self.kind(i) == kind]
            if pattern.search(said) and len(one) == 1:
                return one              # "the fan", with one fan
        return None

    # ---------------------------------------------------------------- Claude
    def summary(self, call: Call) -> str:
        if call.kind == "join" or not self.plugs:
            return ""
        parts = []
        for pid, p in self.plugs.items():
            also = self.also(pid)
            state = "offline" if not p["online"] else "on" if p["on"] else "off"
            parts.append(f"{p['name']} ({self.kind(pid)}{'; also ' + ', '.join(also) if also else ''}): {state}")
        return "Home devices: " + "; ".join(parts) + "."

    def check_plug(self, a: dict, call: Call) -> tuple[list[str], bool, str]:
        if not isinstance(a.get("on"), bool):
            raise Invalid("on: true or false")
        if not self.plugs:
            raise Invalid("no home devices found yet (are the Tuya keys in secrets.toml?)")
        ids = self.find(a.get("name", ""))
        if not ids:
            raise Invalid("which device: " + ", ".join(p["name"] for p in self.plugs.values())
                          + '; or "lights", "fans", "everything"')
        return ids, a["on"], words(a.get("name", ""))

    async def switch(self, pid: str, on: bool, publish: bool = True) -> None:
        """Switch one device, directly if it's on Pip's network, else through Tuya's cloud (raises if neither
        can), and remember it's now on/off."""
        try:
            await self.direct(pid, on)
            how = "direct"
        except Exception as e:
            if pid in self.found:
                log.warning("couldn't switch a plug directly (%s): through the cloud", type(e).__name__)
            await self.request("POST", f"/v1.0/iot-03/devices/{pid}/commands",
                               {"commands": [{"code": self.plugs[pid]["switch"], "value": on}]})
            how = "cloud"
        self.switched[pid] = (self.ctx.clock(), how)
        if pid in self.plugs:   # (a refresh may have replaced the list meanwhile: update the one in use now)
            self.plugs[pid]["on"] = on
        if publish:
            await self.ctx.publish("changed", {"plugs": len(self.plugs)})   # the card and the page redraw

    def called(self, pid: str, said: str) -> str:
        """How to name it back: as they called it ("the desk lamp"), else by its name ("the tv lamp")."""
        name = said if said and said not in ("it", "that") and self.find(said) == [pid] else words(self.plugs[pid]["name"])
        return name if name.startswith(("the ", "my ")) else "the " + (name or "plug")

    async def do_plug(self, plan: tuple[list[str], bool, str], call: Call) -> str:
        ids, on, said = plan
        done = await asyncio.gather(*(self.switch(pid, on, publish=False) for pid in ids), return_exceptions=True)
        await self.ctx.publish("changed", {"plugs": len(self.plugs)})
        missed = [pid for pid, r in zip(ids, done) if isinstance(r, BaseException)]
        for pid in missed:
            log.warning("switching a plug failed: %s", type(done[ids.index(pid)]).__name__)
        state = "on" if on else "off"
        if len(ids) == 1:
            name = self.called(ids[0], said)
            if not missed:
                return f"{name[0].upper() + name[1:]}'s {state}."
            return f"I couldn't reach {name}." if self.plugs[ids[0]]["online"] else f"{name[0].upper() + name[1:]} is offline."
        kinds = {self.kind(pid) for pid in ids}
        group = "everything" if set(ids) == set(self.plugs) else GROUP_NAMES.get(kinds.pop(), "they") if len(kinds) == 1 else "they"
        if len(missed) == len(ids):
            return "I couldn't reach any of them."
        line = f"Everything's {state}" if group == "everything" else f"{group[0].upper() + group[1:]} are {state}"
        if not missed:
            return line + "."
        return line + ", but I couldn't reach " + " or ".join(self.called(pid, "") for pid in missed) + "."

    # ---------------------------------------------------------------- the dashboard
    def home(self) -> dict | None:
        """A card on the front page with an On/Off button for each device (index.html draws it; the button sends
        "switch" below). No card until the devices have been fetched (no Tuya keys, no card)."""
        if not self.plugs:
            return None
        return {"title": "Home", "link": "", "empty": "No devices found.", "switch": "switch",
                "items": [{"id": pid, "title": p["name"], "on": p["on"] if p["online"] else None,
                           "sub": "" if p["online"] else "offline"} for pid, p in self.plugs.items()]}

    def page(self) -> dict:
        """What the Home page shows: each device, how Pip reaches it, the groups (no keys, MACs)."""
        devices = []
        for pid, p in self.plugs.items():
            f = self.found.get(pid) or {}
            direct = bool(f) and not f["missed"]
            devices.append({"id": pid, "name": p["name"], "kind": self.kind(pid), "also": self.also(pid),
                            "on": p["on"], "online": p["online"], "direct": direct, "ip": f.get("ip") if direct else "",
                            "version": f.get("version") if direct else None,
                            "checked": f.get("checked") if direct and f.get("checked") else self.checked,
                            "switched": (self.switched.get(pid) or (None,))[0]})
        groups = {"everything": [p["name"] for p in self.plugs.values()]}
        for kind, label in GROUP_NAMES.items():
            if any(d["kind"] == kind for d in devices):
                groups[label] = [d["name"] for d in devices if d["kind"] == kind]
        return {"devices": devices, "groups": groups, "network": str(self.network()), "cloud_checked": self.checked,
                "direct": sum(d["direct"] for d in devices), "now": self.ctx.clock()}

    async def dashboard(self, action: str, args: dict) -> dict:
        if action == "list":
            return self.page()
        if action != "switch":
            return {"error": f"unknown action {action}"}
        pid, on = str(args.get("id", "")), args.get("on")
        if pid not in self.plugs or not isinstance(on, bool):
            return {"error": "Which device, on or off?"}
        try:
            await self.switch(pid, on)   # the same switching as by voice
        except Exception as e:
            log.warning("switching a plug failed: %s", type(e).__name__)
            return {"error": "Couldn't reach it." if self.plugs[pid]["online"] else "It's offline."}
        return {"ok": True}


def setup(ctx) -> Module:
    return Home(ctx)
