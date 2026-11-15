"""Smart plugs through Tuya's cloud (D-132): "Pip, turn off the light".

A stopgap so the plugs work now. The plan (D-36) is local control on a network Pip hosts for them; on the
campus Wi-Fi the Pi can't reach the plugs directly (test tuya-plug, 2026-11-15), so for now each switch goes
Pi → Tuya's cloud → plug (about 0.2 s). When Pip's own network is up, this module is replaced or reworked.

    "Pip, turn off the light"  → Claude: ACTION {"do": "plug", "name": "light", "on": false}
                               → check_plug finds the plug (by its name in the Smart Life app, a nickname from
                                 the module's settings, or, with only one plug, any plug/light/lamp)
                               → do_plug asks Tuya to switch it, then says "The light's off."

What Claude reads with every message (`summary`): each plug's name and whether it's on, from the last check.
The plugs and their states are fetched at startup, every `refresh_minutes` and right after switching.

Tuya's API (https://developer.tuya.com/en/docs/cloud/): every request is signed with HMAC-SHA256 of the
project's access secret (`sign`); a token (`/v1.0/token`) is fetched first and kept until it expires. The keys
are the module's own section of ~/pip-data/secrets.toml ([tuya]); only Tuya's API hosts may be reached
(module.toml permissions). Nothing is saved: no tables.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import re
import time

from pip_assistant.core.modules import Action, Call, Invalid, Module

log = logging.getLogger(__name__)

HOSTS = {"us": "openapi.tuyaus.com", "us-e": "openapi-ueaz.tuyaus.com", "eu": "openapi.tuyaeu.com",
         "eu-w": "openapi-weaz.tuyaeu.com", "cn": "openapi.tuyacn.com", "in": "openapi.tuyain.com"}
SWITCHES = ("switch_1", "switch", "switch_led")   # the status code that turns a plug (or a bulb) on and off
ANY_ONE = re.compile(r"\b(plug|outlet|socket|light|lights|lamp|it|that)\b")


def words(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower())).strip()


class Tuya(Module):
    def __init__(self, ctx):
        self.ctx = ctx
        self.plugs: dict[str, dict] = {}       # id → {"name", "on" (True/False/None), "online", "switch"}
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
            if self.ctx.clock() - self.switched.get(d["id"], -1e9) < 30 and d["id"] in self.plugs:
                on = self.plugs[d["id"]]["on"]   # just switched: Tuya's list may not show it yet
            plugs[d["id"]] = {"name": d.get("name") or "plug", "on": on,
                              "online": bool(d.get("online", True)), "switch": switch}
        self.plugs, self.checked = plugs, self.ctx.clock()
        await self.ctx.publish("changed", {"plugs": len(plugs)})   # numbers only

    def tasks(self) -> list:
        return [self._loop()]

    async def _loop(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception as e:   # no internet, no keys, Tuya down: try again later
                log.warning("couldn't fetch the smart plugs: %s", type(e).__name__)
            await asyncio.sleep(max(1.0, float(self.ctx.settings.get("refresh_minutes", 10))) * 60)

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
        said = words(said).removeprefix("the ").removeprefix("my ")
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
            raise Invalid("which plug: " + ", ".join(p["name"] for p in self.plugs.values()))
        return pid, a["on"], words(a.get("name", ""))

    async def do_plug(self, plan: tuple[str, bool, str], call: Call) -> str:
        pid, on, said = plan
        p = self.plugs[pid]
        name = said if said and said not in ("it", "that") else words(p["name"]) or "the plug"   # as they called it
        name = name if name.startswith(("the ", "my ")) else "the " + name
        try:
            await self.request("POST", f"/v1.0/iot-03/devices/{pid}/commands",
                               {"commands": [{"code": p["switch"], "value": on}]})
        except Exception as e:
            log.warning("switching a plug failed: %s", type(e).__name__)
            return f"I couldn't reach {name}." if p["online"] else f"{name[0].upper() + name[1:]} is offline."
        p["on"], self.switched[pid] = on, self.ctx.clock()
        await self.ctx.publish("changed", {"plugs": len(self.plugs)})
        return f"{name[0].upper() + name[1:]}'s {'on' if on else 'off'}."


def setup(ctx) -> Module:
    return Tuya(ctx)
