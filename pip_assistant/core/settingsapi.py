"""The Settings page's requests (step 8, D-117), answered in pip-core (`core.settings`, from /api/settings).

    get             every setting (grouped as on the page), module settings, the live preferences, and
                    which programs are still waiting to restart
    set             check every change first (nothing is saved if one is wrong), download a new voice,
                    save ~/pip-data/settings.toml, then restart only the programs that read what changed,
                    once Pip is quiet
    reset           one setting back to its default (the same as set)
    try_ringtone    play a ringtone for ~3 s (pressed by the owner)
    try_voice       say a sample sentence in the voice Pip has now (a newly chosen one plays after it applies)

Settings are read once when a program starts, so a change applies by restarting the programs that read
it (settings.SCHEMA "programs"). "Quiet" means Pip is idle: not talking, not holding a turn, nothing
ringing. He waits at most 2 minutes for that, then restarts anyway. pip-core restarts itself last.
Restarting only happens when systemd runs Pip (like core/health.py); otherwise the page says a restart is needed.
"""
import asyncio
import inspect
import logging
import os
import subprocess
import time

from ..common import settings, voices
from . import states
from ..common.settings import SettingsError

log = logging.getLogger("pip-core")

ORDER = ("ears", "thinker", "web", "core")      # pip-core last: it's the one doing the restarting
SAMPLE = "Hi, I'm {name}. This is how I sound at the moment."
TRY_SECONDS = 3.0


def systemd_restart(name: str) -> bool:
    """Restart one of Pip's services. pip-core's own restart is queued (--no-block): it ends this process."""
    cmd = ["systemctl", "--user"] + (["--no-block"] if name == "core" else []) + ["restart", f"pip-{name}.service"]
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    return r.returncode == 0


class SettingsAPI:
    def __init__(self, bus, conv, module_infos=None, restarter=None, path=None,
                 poll_seconds: float = 1.0, max_wait_seconds: float = 120.0, clock=time.monotonic):
        self.bus, self.conv = bus, conv
        self.module_infos = list(module_infos or [])
        self.path = path                       # settings.toml (None = ~/pip-data/settings.toml)
        if restarter is None and os.environ.get("INVOCATION_ID"):   # set by systemd
            restarter = systemd_restart
        self.restarter = restarter             # None: can't restart, the page says "restart needed"
        self.poll, self.max_wait, self.clock = poll_seconds, max_wait_seconds, clock
        self.applying: list[str] = []          # programs waiting to restart
        self.restart_needed: list[str] = []    # programs that need restarting by hand (not run by systemd)
        self.task: asyncio.Task | None = None
        self._try_task: asyncio.Task | None = None

    # ---------------------------------------------------------------- requests
    async def handle(self, data: dict) -> dict:
        action = (data or {}).get("action", "get")
        try:
            if action == "get":
                return await self.get()
            if action == "set":
                return await self.set(data.get("changes"))
            if action == "reset":
                key = str(data.get("key", ""))
                default = self._defaults().get(key)
                if default is None:
                    return {"errors": {key: "There's no such setting."}}
                return await self.set({key: default})
            if action == "try_ringtone":
                return await self.try_ringtone(str(data.get("tone", "")))
            if action == "try_voice":
                return await self.try_voice(data.get("text"))
        except Exception as e:   # never leave the page hanging
            log.warning("settings %s failed: %s", action, type(e).__name__)
            return {"error": f"That didn't work ({type(e).__name__})."}
        return {"error": f"Unknown action '{action}'."}

    def _defaults(self) -> dict:
        return {**settings.defaults(), **{k: m["default"] for k, m in settings.module_settings().items()}}

    def _current(self) -> tuple[dict, dict]:
        """(settings as saved now, flat; [modules.*] as saved). Read fresh: a save shows straight away."""
        cfg = settings.load(self.path)
        return settings.flat(cfg), cfg.get("modules", {}) or {}

    async def get(self) -> dict:
        values, user_mods = self._current()
        defaults = settings.defaults()
        groups = {g: [] for g in settings.GROUPS}
        for key, e in settings.SCHEMA.items():
            groups[e["group"]].append(self._describe(key, e, values[key], defaults[key]))
        mods, names = settings.module_settings(), {}
        for info in self.module_infos:
            names[info.name] = info
        out_mods = []
        for name in dict.fromkeys(m["module"] for m in mods.values()):
            if self.module_infos and name not in names:
                continue
            info = names.get(name)
            items = []
            for key, e in mods.items():
                if e["module"] == name:
                    value = (user_mods.get(name) or {}).get(e["name"], e["default"])
                    items.append(self._describe(key, {**e, "label": e["name"].replace("_", " ").capitalize(),
                                                      "advanced": False}, value, e["default"]))
            out_mods.append({"name": (info.label if info and info.label else name.capitalize()), "module": name,
                             "summary": info.summary if info else "", "settings": items})
        prefs = {}
        try:
            prefs = await self.conv.prefs_snapshot()
        except Exception:
            pass
        return {"groups": [{"name": g, "settings": s} for g, s in groups.items() if s], "modules": out_mods,
                "applying": list(self.applying), "restart_needed": list(self.restart_needed), "prefs": prefs}

    @staticmethod
    def _describe(key: str, e: dict, value, default) -> dict:
        d = {"key": key, "label": e["label"], "explain": e["explain"], "type": e["type"],
             "advanced": bool(e.get("advanced")), "value": value, "default": default}
        c = settings.choices(key, e)
        if c is not None:
            d["choices"] = c
            if key == "ears.voice":
                d["choice_labels"] = {**voices.KNOWN_VOICES}
                d["installed"] = voices.installed_voices()
        for k in ("min", "max"):
            if e.get(k) is not None:
                d[k] = e[k]
        return d

    async def set(self, changes) -> dict:
        if not isinstance(changes, dict) or not changes:
            return {"error": "Nothing to save."}
        errors = settings.validate(changes, self.path)
        if errors:
            return {"errors": errors}
        cleaned = settings.clean(changes, self.path)
        before, before_mods = self._current()
        changed = [k for k, v in cleaned.items() if self._value(k, before, before_mods) != v]
        voice = cleaned.get("ears.voice")
        if voice and "ears.voice" in changed:
            try:
                await asyncio.to_thread(voices.ensure_voice, voice)
            except Exception as e:   # VoiceError or a disk problem: nothing is saved
                return {"errors": {"ears.voice": str(e) if isinstance(e, voices.VoiceError)
                                   else f"Couldn't save the voice ({type(e).__name__})."}}
        try:
            settings.save(cleaned, self.path)
        except SettingsError as e:
            return {"error": str(e)}
        programs = programs_for(changed)
        if programs:
            await self.apply(programs)
        return {"ok": True, "changed": changed, "applying": list(self.applying),
                "restart_needed": list(self.restart_needed)}

    @staticmethod
    def _value(key: str, flat: dict, mods: dict):
        if key.startswith("modules."):
            _, name, setting = key.split(".", 2)
            if setting in (mods.get(name) or {}):
                return mods[name][setting]
            return True if setting == "enabled" else settings.module_settings()[key]["default"]
        return flat.get(key)

    # ---------------------------------------------------------------- restarting when quiet
    def quiet(self) -> bool:
        return states.quiet(self.conv)   # the same rule installs use (release.py)

    async def apply(self, programs: list[str]) -> None:
        if self.restarter is None:
            self.restart_needed = [p for p in ORDER if p in set(self.restart_needed) | set(programs)]
            return
        self.applying = [p for p in ORDER if p in set(self.applying) | set(programs)]
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._restart_when_quiet())

    async def _restart_when_quiet(self) -> None:
        start = self.clock()
        while not self.quiet() and self.clock() - start < self.max_wait:
            await asyncio.sleep(self.poll)
        if not self.quiet():
            log.info("settings: Pip didn't go quiet in %.0f s; restarting anyway", self.max_wait)
        while self.applying:
            name = self.applying[0]
            if name == "core" and len(self.applying) > 1:   # anything added meanwhile goes first
                self.applying.append(self.applying.pop(0))
                continue
            log.info("settings changed: restarting pip-%s", name)
            try:
                r = self.restarter(name)
                if inspect.isawaitable(r):
                    r = await r
            except Exception as e:
                log.warning("restarting pip-%s failed: %s", name, type(e).__name__)
            self.applying.pop(0)

    # ---------------------------------------------------------------- try buttons (owner-pressed)
    async def try_ringtone(self, tone: str) -> dict:
        if tone not in settings.RINGTONES:
            return {"error": f"Unknown ringtone '{tone}'."}
        if getattr(self.conv, "ringing", False):
            return {"error": "Something's ringing right now: try again when it stops."}
        await self.conv.command("ears", "ears.ring", {"tone": tone, "max_seconds": TRY_SECONDS})
        if self._try_task and not self._try_task.done():
            self._try_task.cancel()
        self._try_task = asyncio.create_task(self._stop_try_ring())
        return {"ok": True}

    async def _stop_try_ring(self) -> None:
        """pip-ears stops starting new rounds after max_seconds but finishes the one playing: cut it at 3 s.
        Never when a real alert has started ringing meanwhile (that stop would silence it)."""
        await asyncio.sleep(TRY_SECONDS)
        if not getattr(self.conv, "ringing", False):
            await self.conv.command("ears", "ears.ring_stop", {})

    async def try_voice(self, text=None) -> dict:
        text = str(text or "").strip()[:200] or SAMPLE.format(name=settings.flat(settings.load(self.path))
                                                              .get("pip.name", "Pip"))
        await self.conv.command("ears", "ears.say", {"reason": "settings"}, {"text": text})
        return {"ok": True, "note": "That's the voice Pip uses now. A newly chosen voice plays once it's applied."}


def programs_for(keys) -> list[str]:
    """Which programs read these settings (in restart order, pip-core last). Modules run in pip-core;
    switching one on or off also changes what pip-thinker tells Claude."""
    out = set()
    for key in keys:
        if key.startswith("modules."):
            out.add("core")
            if key.endswith(".enabled"):
                out.add("thinker")
        elif key in settings.SCHEMA:
            out.update(settings.SCHEMA[key]["programs"])
    return [p for p in ORDER if p in out]
