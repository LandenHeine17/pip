"""The smart plugs (Eightree, Tuya inside): "Pip, turn off the light", "turn everything off" (D-132, D-139).

The plugs join the Wi-Fi network the Pi hosts for them ("pip-plugs", deploy/plug-network.sh), where Pip switches
each one directly (`Local`, the tinytuya library: one encrypted message to the plug, no internet needed). A plug
Pip can't reach there (not moved to pip-plugs yet, or just unplugged) is switched through Tuya's cloud instead,
as before D-139 (Pi → Tuya's cloud → plug, about 0.2 s).

    "Pip, turn off the light"  → Claude: ACTION {"do": "plug", "name": "light", "on": false}
                               → check_plug finds the plug (by its name in the Smart Life app, a nickname from
                                 the module's settings, "all"/"everything" for every plug, or, with only one
                                 plug, any plug/light/lamp)
                               → do_plug switches it (directly, else through the cloud) and says "The light's off."

The dashboard's front page has a "Smart plugs" card with an On/Off button for each (`home`), which switches it
the same way (`dashboard` "switch" → switch).

Three background jobs (`tasks`):
  - every `refresh_minutes` (10), Tuya's cloud lists the plugs: their names, whether they're on, and each one's
    local key (what its direct messages are encrypted with; it changes when a plug is paired again). The keys
    are kept in memory only: never logged, published or saved.
  - the plugs announce themselves on the network every few seconds (UDP broadcasts on 6666/6667/7000, `Heard`);
    those from inside `plug_network` give each plug's address (`found`).
  - every `check_seconds` (30), each plug found there is asked directly whether it's on, so one switched by its
    own button or the Smart Life app shows within half a minute.

What Claude reads with every message (`summary`): each plug's name and whether it's on, from the last check.
After Pip switches one through the cloud, he keeps what he set for 30 s, as Tuya's list takes a few seconds to
catch up.

Tuya's API (https://developer.tuya.com/en/docs/cloud/): every request is signed with HMAC-SHA256 of the
project's access secret (`sign`); a token (`/v1.0/token`) is fetched first and kept until it expires. The keys
are the module's own section of ~/pip-data/secrets.toml ([tuya]); only Tuya's API hosts may be reached over
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

from pip_assistant.core.modules import Action, Call, Invalid, Module

log = logging.getLogger(__name__)

HOSTS = {"us": "openapi.tuyaus.com", "us-e": "openapi-ueaz.tuyaus.com", "eu": "openapi.tuyaeu.com",
         "eu-w": "openapi-weaz.tuyaeu.com", "cn": "openapi.tuyacn.com", "in": "openapi.tuyain.com"}
SWITCHES = {"switch_1": 1, "switch": 1, "switch_led": 20}   # the status code that turns a plug (or a bulb) on
                                                             # and off → its number in the plug's direct messages
ANY_ONE = re.compile(r"\b(plug|outlet|socket|light|lights|lamp|it|that)\b")
EVERY = re.compile(r"\b(all|every|everything|each)\b")       # "all the plugs", "everything"
PORTS = (6666, 6667, 7000)                                   # where the plugs announce themselves


def words(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower())).strip()


class Local:
    """Talking to a plug directly, on Pip's own network (tinytuya; it waits on the network, so in a thread)."""

    @staticmethod
    def decrypt(data: bytes) -> dict:
        import tinytuya
        return json.loads(tinytuya.decrypt_udp(data))

    @staticmethod
    def _ask(plug: dict, on: bool | None) -> bool:
        import tinytuya
        d = tinytuya.OutletDevice(plug["id"], plug["ip"], plug["key"], version=float(plug["version"]),
                                  connection_timeout=2, connection_retry_limit=1, connection_retry_delay=0)
        try:
            got = d.status() if on is None else d.set_status(on, switch=plug["dp"])
        finally:
            d.close()
        if not isinstance(got, dict) or "Error" in got:
            raise OSError("the plug didn't answer")
        return bool((got.get("dps") or {}).get(str(plug["dp"]), on))

    async def switch(self, plug: dict, on: bool) -> None:
        await asyncio.to_thread(self._ask, plug, on)

    async def state(self, plug: dict) -> bool:
        return await asyncio.to_thread(self._ask, plug, None)


class Heard(asyncio.DatagramProtocol):
    def __init__(self, heard):
        self.heard = heard

    def datagram_received(self, data, addr):
        self.heard(data, addr[0])


class Tuya(Module):
    def __init__(self, ctx, local=None):
        self.ctx = ctx
        self.local = local or Local()
        self.plugs: dict[str, dict] = {}       # id → {"name", "on" (True/False/None), "online", "switch"}
        self.keys: dict[str, str] = {}           # id → its local key (from the cloud; memory only)
        self.found: dict[str, dict] = {}         # id → {"ip", "version"}: where it is on Pip's own network
        self.listening: dict[int, object] = {}   # port → the socket hearing the plugs announce themselves
        self.checked = 0.0                       # when the plugs were last fetched (ctx.clock)
        self.token, self.token_until = "", 0.0
        self.switched: dict[str, float] = {}     # id → when Pip last switched it (Tuya's list lags a few seconds)
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
            raise Invalid("no Tuya keys in secrets.toml [tuya]")
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
        """Every plug linked to the project, and whether it's on."""
        got = await self.request("GET", "/v1.0/iot-01/associated-users/devices?size=50")
        plugs = {}
        for d in (got or {}).get("devices", []):
            status = {s.get("code"): s.get("value") for s in d.get("status") or []}
            switch = next((c for c in SWITCHES if c in status), None)
            if switch is None:
                continue   # not something that switches on and off
            on = bool(status[switch])
            near = d["id"] in self.found   # checked directly every 30 s: that's fresher than the cloud's list
            if d["id"] in self.plugs and (near or self.ctx.clock() - self.switched.get(d["id"], -1e9) < 30):
                on = self.plugs[d["id"]]["on"]   # (and just switched: Tuya's list may not show it yet)
            plugs[d["id"]] = {"name": d.get("name") or "plug", "on": on,
                              "online": near or bool(d.get("online", True)), "switch": switch}
            if d.get("local_key"):
                self.keys[d["id"]] = d["local_key"]
        self.plugs, self.checked = plugs, self.ctx.clock()
        await self.ctx.publish("changed", {"plugs": len(plugs)})   # numbers only

    def tasks(self) -> list:
        return [self._loop(), self._listen(), self._check()]

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

    def heard(self, data: bytes, ip: str) -> None:
        """A plug announcing itself: where it is, if that's on Pip's own network (anything else is ignored)."""
        try:
            if ipaddress.ip_address(ip) not in self.network():
                return
            info = self.local.decrypt(data)
            pid = str(info["gwId"])
        except Exception:
            return   # not a plug's announcement
        new = pid not in self.found
        self.found[pid] = {"ip": ip, "version": str(info.get("version") or "3.3")}
        if new:
            log.info("a smart plug is on Pip's own network")

    async def _listen(self) -> None:
        if self.listening:
            return
        loop = asyncio.get_running_loop()
        for port in PORTS:
            try:
                self.listening[port], _ = await loop.create_datagram_endpoint(lambda: Heard(self.heard), local_addr=("0.0.0.0", port),
                                                    reuse_port=True, allow_broadcast=True)
            except OSError as e:
                log.warning("can't listen for the plugs on port %s: %s", port, type(e).__name__)

    def near(self, pid: str) -> dict | None:
        """What's needed to talk to this plug directly, if Pip has found it on his network and knows its key."""
        if pid not in self.found or pid not in self.keys or pid not in self.plugs:
            return None
        return {"id": pid, "key": self.keys[pid], "dp": SWITCHES[self.plugs[pid]["switch"]], **self.found[pid]}

    async def _check(self) -> None:
        while True:
            await asyncio.sleep(max(5.0, float(self.ctx.settings.get("check_seconds", 30))))
            changed = False
            for pid in list(self.plugs):
                plug = self.near(pid)
                if plug is None:
                    continue
                try:
                    on = await self.local.state(plug)
                except Exception:
                    self.found.pop(pid, None)   # gone from the network: the cloud until it's heard again
                    continue
                if pid in self.plugs and (self.plugs[pid]["on"], self.plugs[pid]["online"]) != (on, True):
                    self.plugs[pid].update(on=on, online=True)
                    changed = True
            if changed:
                await self.ctx.publish("changed", {"plugs": len(self.plugs)})

    # ---------------------------------------------------------------- finding a plug by what people call it
    def nicknames(self) -> dict[str, str]:
        """"Smart plug = light, lamp; Fan plug = fan" → {"light": "smart plug", "lamp": "smart plug", "fan": "fan plug"}."""
        out = {}
        for part in str(self.ctx.settings.get("nicknames") or "").split(";"):
            name, _, others = part.partition("=")
            for other in others.split(","):
                if words(other) and words(name):
                    out[words(other)] = words(name)
        return out

    def find(self, said: str) -> str | None:
        """The plug they mean (one id), "*" for all of them ("everything", "all the lights"), or None."""
        said = words(said).removeprefix("the ").removeprefix("my ")
        if EVERY.search(said) and len(self.plugs) > 1:
            return "*"
        said = self.nicknames().get(said, said)
        exact = [i for i, p in self.plugs.items() if words(p["name"]) == said]
        if exact:
            return exact[0]
        part = [i for i, p in self.plugs.items() if said and (said in words(p["name"]) or words(p["name"]) in said)]
        if len(part) == 1:
            return part[0]
        if len(self.plugs) == 1 and (not said or ANY_ONE.search(said)):
            return next(iter(self.plugs))   # only one plug: "the light", "the plug", "it" all mean it
        return None

    # ---------------------------------------------------------------- Claude
    def summary(self, call: Call) -> str:
        if call.kind == "join" or not self.plugs:
            return ""
        nick = self.nicknames()
        parts = []
        for p in self.plugs.values():
            also = [k for k, v in nick.items() if v == words(p["name"])]
            state = "offline" if not p["online"] else "on" if p["on"] else "off"
            parts.append(f"{p['name']}{' (also ' + ', '.join(also) + ')' if also else ''}: {state}")
        return "Smart plugs: " + "; ".join(parts) + "."

    def check_plug(self, a: dict, call: Call) -> tuple[str, bool, str]:
        if not isinstance(a.get("on"), bool):
            raise Invalid("on: true or false")
        if not self.plugs:
            raise Invalid("no smart plugs found yet (are the Tuya keys in secrets.toml?)")
        pid = self.find(a.get("name", ""))
        if pid is None:
            raise Invalid("which plug: " + ", ".join(p["name"] for p in self.plugs.values()) + ', or "all"')
        return pid, a["on"], words(a.get("name", ""))

    # ---------------------------------------------------------------- the dashboard's front page
    def home(self) -> dict | None:
        """A card on the front page with an On/Off button for each plug (index.html draws it; the button sends
        "switch" below). No card until the plugs have been fetched (no Tuya keys, no card)."""
        if not self.plugs:
            return None
        return {"title": "Smart plugs", "link": "", "empty": "No plugs found.", "switch": "switch",
                "items": [{"id": pid, "title": p["name"], "on": p["on"] if p["online"] else None,
                           "sub": "" if p["online"] else "offline"} for pid, p in self.plugs.items()]}

    async def dashboard(self, action: str, args: dict) -> dict:
        if action != "switch":
            return {"error": f"unknown action {action}"}
        pid, on = str(args.get("id", "")), args.get("on")
        if pid not in self.plugs or not isinstance(on, bool):
            return {"error": "Which plug, on or off?"}
        try:
            await self.switch(pid, on)   # the same switching as by voice
        except Exception as e:
            log.warning("switching a plug failed: %s", type(e).__name__)
            return {"error": "Couldn't reach the plug." if self.plugs[pid]["online"] else "That plug is offline."}
        return {"ok": True}

    async def switch(self, pid: str, on: bool, publish: bool = True) -> None:
        """Switch one plug, directly if it's on Pip's network, else through Tuya's cloud (raises if neither can),
        and remember it's now on/off."""
        p, plug = self.plugs[pid], self.near(pid)
        try:
            if plug is None:
                raise LookupError
            await self.local.switch(plug, on)
        except Exception as e:
            if plug is not None:
                log.warning("couldn't switch a plug directly (%s): through the cloud", type(e).__name__)
                self.found.pop(pid, None)
            await self.request("POST", f"/v1.0/iot-03/devices/{pid}/commands",
                               {"commands": [{"code": p["switch"], "value": on}]})
            self.switched[pid] = self.ctx.clock()
        if pid in self.plugs:   # (a refresh may have replaced the list meanwhile: update the one in use now)
            self.plugs[pid]["on"] = on
        if publish:
            await self.ctx.publish("changed", {"plugs": len(self.plugs)})   # the front page's card redraws

    async def do_plug(self, plan: tuple[str, bool, str], call: Call) -> str:
        pid, on, said = plan
        if pid == "*":
            return await self.do_all(on)
        p = self.plugs[pid]
        name = said if said and said not in ("it", "that") else words(p["name"]) or "the plug"   # as they called it
        name = name if name.startswith(("the ", "my ")) else "the " + name
        try:
            await self.switch(pid, on)
        except Exception as e:
            log.warning("switching a plug failed: %s", type(e).__name__)
            return f"I couldn't reach {name}." if p["online"] else f"{name[0].upper() + name[1:]} is offline."
        return f"{name[0].upper() + name[1:]}'s {'on' if on else 'off'}."

    async def do_all(self, on: bool) -> str:
        """Every plug at once: "All 4 plugs are off.", or which ones it couldn't reach."""
        pids = list(self.plugs)
        done = await asyncio.gather(*(self.switch(pid, on, publish=False) for pid in pids), return_exceptions=True)
        await self.ctx.publish("changed", {"plugs": len(self.plugs)})
        missed = [self.plugs[pid]["name"] for pid, r in zip(pids, done) if isinstance(r, BaseException)]
        for r in done:
            if isinstance(r, BaseException):
                log.warning("switching a plug failed: %s", type(r).__name__)
        state = "on" if on else "off"
        if not missed:
            return f"All {len(pids)} plugs are {state}."
        if len(missed) == len(pids):
            return "I couldn't reach any of the plugs."
        return f"The rest are {state}, but I couldn't reach {' or '.join(missed)}."


def setup(ctx) -> Module:
    return Tuya(ctx)
