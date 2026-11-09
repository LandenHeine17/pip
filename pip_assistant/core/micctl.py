"""The Listening switches, the clap microphone and the dashboard's preferences (D-91, D-105).

Part of the conversation (core/conversation.py): `Conversation` takes these methods in as `MicControl`, so
they read and set its fields like its own. The switches themselves (dashboard and two claps) are in
core/mic.py and the preferences in core/prefs.py; this is what Pip does when they change: tell pip-ears what
to listen for, put a bar on the conversation feed, and update the face (refresh_state()).
"""
import time

from ..common.messages import Message


class MicControl:
    # ---------------------------------------------------------------- listening on/off (D-91)
    async def apply_mic(self, tone: str | None = None, how: str = "") -> None:
        """Tell pip-ears what to listen for, and everyone (dashboard, face) whether Pip is listening."""
        if self.mic is None:
            return
        if not self.mic.listening and self.mic_off_since is None:
            self.mic_off_since = time.monotonic()
            await self.note("mic_off", how=how)
        elif self.mic.listening and self.mic_off_since is not None:
            await self.note("mic_on", how=how, off_for=round(time.monotonic() - self.mic_off_since))
            self.mic_off_since = None
        if not self.mic.listening:
            # Off just means he stops hearing anything new. What he'd already heard is still answered (owner,
            # D-114); only the things that need him to keep listening end: listening windows and voice learning.
            self.expect_until = self.listen_until = 0.0
            if self.enrolling:
                self.cancel_enrolment()
        await self.command("ears", "ears.mic", {"mode": self.mic.mode})
        if tone:
            await self.command("ears", "ears.tone", {"name": tone})
        await self.bus.publish(Message("event", "mic.changed", data=self.mic.snapshot()))
        self.refresh_state()

    async def on_clapped_trial(self, msg: Message) -> None:
        """Two claps heard while the clap switch isn't ticked: only shown on the feed, to try it out (D-91)."""
        await self.note("claps_trial")

    async def on_clapped(self, msg: Message) -> None:
        if self.mic is not None and self.mic.clapped():
            await self.apply_mic("mic_on" if self.mic.listening else "mic_off", how="claps")

    async def set_mic(self, on: bool) -> dict:
        """The dashboard's Listening switch. Turning it on also clears a clap switch-off, so "on" means on."""
        if self.mic is None:
            return {}
        was = self.mic.listening
        self.mic.set_dashboard(on)
        if on and not self.mic.claps:
            self.mic.clapped()
        await self.apply_mic(None if was == self.mic.listening else "mic_on" if self.mic.listening else "mic_off", how="dashboard")
        return self.mic.snapshot()

    async def on_program_connected(self, name: str) -> None:
        if name == "ears":
            await self.apply_mic()
            await self.tell_clap_mic()

    async def chime(self) -> None:
        """The chime that says "I heard my name" (can be switched off on the dashboard, D-105)."""
        if self.prefs is None or self.prefs.get("chime"):
            await self.command("ears", "ears.chime")
            await self.note("chime")

    async def tell_clap_mic(self) -> None:
        """pip-ears listens for claps only on the chosen microphone, and only if claps are switched on (D-91)."""
        if self.prefs is not None:
            live = bool(self.prefs.get("claps") and self.prefs.get("clap_mic"))
            if not live and self.mic is not None and not self.mic.claps:
                # Switched off by claps, but claps can't switch him back on now: don't leave him deaf (D-91)
                self.mic.clapped()
                await self.apply_mic("mic_on" if self.mic.listening else None, how="claps")
            await self.command("ears", "ears.clap_mic", {"device": self.prefs.get("clap_mic"), "on": self.prefs.get("claps")})

    async def prefs_snapshot(self) -> dict:
        """The preferences, plus the microphones pip-ears can see (to choose the clap microphone)."""
        out = {"ok": True, **(self.prefs.snapshot() if self.prefs else {}), "mics": [], "speakerphone": ""}
        try:
            r = await self.bus_request("ears", "ears.mics", {}, {}, timeout=6)
            out.update(mics=r.data.get("mics", []), speakerphone=r.data.get("speakerphone", ""))
        except Exception:
            pass   # pip-ears not running: the list is just empty
        return out

    async def set_pref(self, key: str, value) -> dict:
        """A preference changed on the dashboard (core/prefs.py)."""
        if self.prefs is None:
            return {"error": "no preferences here"}
        if key == "clap_mic" and "Speaker_Phone" in str(value):
            return {"error": "The speakerphone can't hear claps (its noise filter removes them): plug in a plain USB microphone"}
        try:
            self.prefs.set(key, value)
        except KeyError:
            return {"error": f"unknown preference {key}"}
        if key in ("clap_mic", "claps"):
            await self.tell_clap_mic()
        await self.bus.publish(Message("event", "prefs.changed", data=self.prefs.snapshot()))
        return await self.prefs_snapshot()
