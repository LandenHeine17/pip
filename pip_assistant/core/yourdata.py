"""Your data (PIP-NEW-034, D-128): download a copy of everything Pip knows, or delete it all. Settings page.

    export   one JSON file: who lives here (names and roles), every memory, each module's own data (the
             conversation notes…), your settings (what you changed; secrets.toml is never included) and the
             personality. Voiceprints are **not** exported: they never leave the Pi (D-89).
    wipe     needs {"confirm": "DELETE"}. Deletes every memory, every voiceprint (people stay, by name, and can
             learn their voice again), this visit's guests, each module's own data (Module.wipe_data) and the
             room transcript in memory. Settings, the personality, timers and the calendar stay.

Requests and replies travel in `private` (core/main.py `privately`), so none of it reaches the trace.
"""
import time

from ..common import paths, personality


class YourData:
    def __init__(self, memory, people, modules, conv=None):
        self.memory, self.people, self.modules, self.conv = memory, people, modules, conv

    async def handle(self, data: dict) -> dict:
        action = (data or {}).get("action", "")
        if action == "export":
            return self.export()
        if action == "wipe":
            if (data or {}).get("confirm") != "DELETE":
                return {"error": "Type DELETE to confirm"}
            return await self.wipe()
        if action == "get":
            return {"memories": len(self.memory.items()), "people": len(self.people.all())}
        return {"error": f"unknown action {action}"}

    def export(self) -> dict:
        names = {p["id"]: p["name"] for p in self.people.all()}
        memories = [{"text": m["text"], "category": m["category"], "about": [names.get(i, "?") for i in m["about"]],
                     "visibility": m["visibility"], "status": m["status"], "source": m["source"],
                     "created": time.strftime("%Y-%m-%d %H:%M", time.localtime(m["created"]))}
                    for m in self.memory.items()]
        settings_file = paths.user_settings()
        text, builtin = personality.read()
        return {"pip_export": 1, "exported_at": time.strftime("%Y-%m-%d %H:%M"),
                "people": [{"name": p["name"], "role": p.get("role", "")} for p in self.people.all()],
                "memories": memories,
                "modules": self.modules.export_data() if self.modules is not None else {},
                "settings_toml": settings_file.read_text() if settings_file.is_file() else "",
                "personality": "" if builtin else text,
                "not_included": "voiceprints (they never leave the Pi), secrets.toml, the decision trace"}

    async def wipe(self) -> dict:
        n_mem = 0
        for m in self.memory.items():
            self.memory.delete(m["id"])
            n_mem += 1
        n_voices = 0
        for p in self.people.all():
            self.people.forget_voice(p["id"])
            n_voices += 1
        self.people.guests.clear()
        n_mod = await self.modules.wipe_data() if self.modules is not None else 0
        if self.conv is not None:
            self.conv.transcript.lines.clear()
            self.conv.__dict__.pop("recent_heard", None)
        return {"ok": True, "memories": n_mem, "voices": n_voices, "other": n_mod}
