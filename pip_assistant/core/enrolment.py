"""Guided voice learning: Pip learns someone's voice from five sentences they read out.

Part of the conversation (core/conversation.py): `Conversation` takes these methods in as `VoiceLearning`,
so they read and set its fields (`self.enrolling`, …) like its own. Started by voice ("Pip, learn my
voice", core/peoplecmds.py, or Claude's ACTION learn_voice, core/acting.py) or from the dashboard
(core/dashapi.py: start_enrolment, cancel_enrolment). While it runs, what's heard goes to `_enrol_sample`
instead of being answered (see Conversation.on_utterance); the face shows "listening: learning_voice".
"""
import asyncio
import time

from ..common.messages import Message


class VoiceLearning:
    # ---------------------------------------------------------------- learning a voice
    # Started by saying "Pip, I'm Landen" / "Pip, learn my voice", or with Learn my voice on the dashboard.
    # The sentences show up on the dashboard; Pip beeps before each one. What's read is never transcribed:
    # it's only turned into a voiceprint on the Pi.
    ENROL_PHRASES = ["The quick brown fox jumps over the lazy dog.",
                     "What's the weather going to be like tomorrow afternoon?",
                     "Set a timer for ten minutes and remind me to call home.",
                     "My favourite food is pizza, but I like tacos too.",
                     "Put the dentist on the calendar for Friday at three."]
    ENROL_MIN_VOICE = 1.0    # seconds of voice for a read sentence to count
    ENROL_WAIT = 15.0        # seconds Pip listens after each beep
    enrol_speech_wait = 15.0  # longest wait for Pip to finish talking before the beep

    async def start_enrolment(self, pid: str, first: bytes = b"", greeting: str = "") -> dict:
        person = self.people.get(pid) if self.people else None
        if not person:
            raise ValueError("No such person")
        if not self.people.embedder.available:
            raise ValueError("The voice model isn't installed yet (run deploy/install.sh)")
        self.enrolling = {"pid": pid, "name": person["name"], "got": [], "extra": [first] if first else [],
                          "misses": 0, "until": time.monotonic() + 120}
        self.speech_done.clear()
        await self.say(greeting or f"Hi {person['name']}! Let's learn your voice. Read the sentences on my dashboard "
                                   "out loud, one after each beep.", "enrol")
        await self._enrol_event()
        self._enrol_task(self._enrol_prompt(after_speech=True))
        return self.enrol_status()

    async def start_learning(self, learning, pcm: bytes) -> None:
        """A command or Claude's ACTION started voice learning (peoplecmds.StartLearning): say its greeting
        and start, with what they just said as a first sample."""
        self.said(learning.say)
        await self.show("pip", learning.say, local=True)
        await self.start_enrolment(learning.person_id, first=pcm, greeting=learning.say)

    def _enrol_task(self, coro) -> None:
        if (old := (self.enrolling or {}).get("task")) and not old.done():
            old.cancel()
        task = asyncio.create_task(coro)
        if self.enrolling is not None:
            self.enrolling["task"] = task

    def enrol_status(self) -> dict | None:
        e = self.enrolling
        if e and time.monotonic() > e["until"]:
            self.enrolling = e = None
        return {"person_id": e["pid"], "name": e["name"], "done": len(e["got"]), "of": len(self.ENROL_PHRASES),
                "phrases": self.ENROL_PHRASES, "retry": e["misses"] > 0} if e else None

    async def _enrol_event(self) -> None:
        e = self.enrolling
        await self.bus.publish(Message("event", "voice.enrol", data={"done": len(e["got"]) if e else 0,
                                                                     "of": len(self.ENROL_PHRASES), "active": bool(e)}))

    async def _enrol_prompt(self, after_speech: bool = False) -> None:
        if after_speech:  # beep only once Pip has finished talking, so the whole listening time is theirs
            try:
                await asyncio.wait_for(self.speech_done.wait(), self.enrol_speech_wait)
            except asyncio.TimeoutError:
                pass
        if not self.enrolling:
            return
        await self.command("ears", "ears.chime")
        await self.command("ears", "ears.listen", {"seconds": self.ENROL_WAIT})
        self.listening_for(self.ENROL_WAIT, "learning_voice")
        beeped, e = time.monotonic(), self.enrolling
        count = len(e["got"]) + e["misses"]
        await asyncio.sleep(self.ENROL_WAIT + 1.0)
        # Nobody read anything after the beep (ears sends nothing when nobody talks): ask again
        if self.enrolling is e and len(e["got"]) + e["misses"] == count and self.last_started < beeped:
            await self._enrol_retry("nothing")

    async def _enrol_retry(self, why: str) -> None:
        """A sentence came back too short, or nothing came: ask again; twice in a row means stop."""
        e = self.enrolling
        e["misses"] += 1
        if e["misses"] > 2:
            self.enrolling = None
            await self._enrol_event()
            await self.say("I'll stop there. Say \"Pip, learn my voice\" when you want to try again.", "enrol")
            return
        n = len(e["got"]) + 1
        e["until"] = time.monotonic() + 60
        await self._enrol_event()
        self.speech_done.clear()
        await self.say(f"I didn't get that one. Read sentence {n} again." if why == "short" else
                       f"Whenever you're ready, read sentence {n} on my dashboard.", "enrol")
        self._enrol_task(self._enrol_prompt(after_speech=True))

    async def _enrol_sample(self, pcm: bytes) -> None:
        e = self.enrolling
        if not e or time.monotonic() > e["until"]:
            self.enrolling = None
            return
        if await self._voice_seconds(pcm) < self.ENROL_MIN_VOICE:
            return await self._enrol_retry("short")
        e["got"].append(pcm)
        e["misses"] = 0
        await self._enrol_event()
        if len(e["got"]) < len(self.ENROL_PHRASES):
            e["until"] = time.monotonic() + 60
            self._enrol_task(self._enrol_prompt())
            return
        self.enrolling = None
        await self._enrol_event()
        n = await self.people.add_samples(e["pid"], e["got"] + e["extra"], "guided")
        await self.say(f"Thanks, {e['name']}. I know your voice now. Ask me \"who am I?\" to check." if n >= 3 else
                       "Hmm, those were too short. Say \"Pip, learn my voice\" to try again.", "enrol")

    async def _voice_seconds(self, pcm: bytes) -> float:
        if not self.voice_check:
            return len(pcm) / 32000
        from ..common.voicecheck import voice_seconds
        try:
            return await voice_seconds(pcm)
        except Exception:
            return len(pcm) / 32000

    def cancel_enrolment(self) -> None:
        if self.enrolling and (task := self.enrolling.get("task")):
            task.cancel()
        self.enrolling = None
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return   # not called from inside the event loop: no dashboard to tell
        asyncio.ensure_future(self._enrol_event())
