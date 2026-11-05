"""The conversation part of the conductor (M1a step 3).

    wake word / follow-up / interruption
      → pip-ears sends the audio of what was said (heard.utterance)
      → is there a human voice in it? (noise never reaches speech-to-text: no phantom "Thank you.", D-85)
      → speech-to-text (Groq)
      → "was that really for Pip?" check (e.g. it was "Ziggletron")
      → one of Pip's own commands (timers, alarms, reminders, calendar, time)? Done on the Pi (step 4)
      → otherwise pip-thinker asks Claude (a soft thinking tone plays), streaming the answer sentence by sentence;
        if Claude replies with an ACTION (a timer/calendar request the rules missed), Pip carries it out (D-84)
      → each sentence goes to pip-ears to be spoken
      → afterwards, a short follow-up window: talk again without the name

What people say and what Pip answers travel only in messages' `private` part, so they are never
written to the decision trace (D-34). The conversation itself is kept in memory only, for a few
minutes, so follow-up questions make sense (PIP-NEW-028 category 5).

Offline (Groq can't be reached): Whisper on the Pi transcribes instead, and only Pip's own commands
work; anything else gets "I'm offline, so I can only do the basics right now" (PIP-NEW-044, D-83).
While an alarm rings, everything heard is checked for "stop", "okay", "snooze" or Pip's name first.

Pip's state (idle / listening / thinking / speaking / ringing / offline) is published as `pip.state`
whenever it changes: the dashboard shows it, and the face (step 7) will react to it (D-86).
"""
import asyncio
import base64
import logging
import re
import time

from ..common.messages import Message, new_id

log = logging.getLogger(__name__)

PIP_NAMES = re.compile(r"\b(pip|pipp|pips|pippy|pippie|pippletron|pipple ?tron|pippa ?tron|piple ?tron|pipletron)\b", re.I)
NOT_PIP = re.compile(r"\b(zig+le ?tron|zig+y|zig+)\b", re.I)


FILLER = re.compile(r"\b(hey|hi|ok|okay|so|yo|um+|uh+|hmm+)\b", re.I)
DANGLING = re.compile(r"\b(the|a|an|and|or|but|of|my|your|his|her|their|what's|whats|um+|uh+)\W*$", re.I)
WANTS_DETAIL = re.compile(r"\b(story|explain|details?|in depth|describe|tell me (more|everything|all)|"
                          r"step by step|steps|recipe|list|longer)\b", re.I)


def only_name(text: str) -> bool:
    return not FILLER.sub("", PIP_NAMES.sub("", text)).strip(" .,?!-")


def looks_unfinished(text: str) -> bool:
    """'Pippletron, what's the...' : they paused mid-sentence and are about to say more."""
    t = text.strip()
    return t.endswith(("...", "…", ",", "-")) or bool(DANGLING.search(t.rstrip(".")) and not t.endswith("?"))


def names_pip(text: str) -> bool:
    """Does this transcript call Pip? Exact names, or close mishearings of 'Pippletron' (never 'Ziggletron')."""
    import difflib
    if PIP_NAMES.search(text):
        return True
    words = re.findall(r"[a-z]+", text.lower())
    joined = words + [a + b for a, b in zip(words, words[1:])]
    return any(len(w) >= 7 and not w.startswith("zig") and
               difflib.SequenceMatcher(None, w, "pippletron").ratio() >= 0.82 for w in joined)
SAYINGS = {
    "not_caught": "Sorry, I didn't catch that. Could you say it again?",
    "stt_failed": "Sorry, I couldn't make out what you said. My hearing service isn't answering.",
    "thinker_failed": "Sorry, my brain isn't answering right now. Try me again in a moment.",
    "no_speech": "",  # wake word but then nothing: stay quiet
    "offline": "I'm offline, so I can only do the basics right now, like timers and alarms.",
    "command_failed": "Sorry, that didn't work. Nothing was set.",
}


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


class Conversation:
    def __init__(self, bus, cfg: dict | None = None, stt=None, schedule=None, local_stt=None, people=None, memory=None):
        from .commands import Commands
        from .peoplecmds import PeopleCommands
        self.bus = bus
        self.people, self.memory = people, memory
        self.people_cmds = PeopleCommands(people, memory) if people is not None else None
        self.speaker = None          # who's talking in the current request (people.Match)
        self.last_pcm = b""
        self.enrolling: dict | None = None   # guided voice learning in progress
        self.last_started = 0.0              # when ears last started capturing someone
        self.schedule = schedule
        self.commands = Commands(schedule, clock=schedule.clock) if schedule is not None else None
        self.local_stt = local_stt
        c = (cfg or {}).get("conversation", {})
        self.follow_up_s = c.get("follow_up_seconds", 8.0)
        th = (cfg or {}).get("thinker", {})
        self.tone_mode = th.get("thinking_tone", "always")
        self.tone_after = th.get("thinking_tone_after_seconds", 1.5)
        self.filler_said = None
        st = (cfg or {}).get("stt", {})
        self.voice_check = st.get("voice_check", False) if cfg else False
        self.min_voice = st.get("min_voice_seconds", 0.15)
        self.state = "idle"
        self.speaking = False
        self.listen_until = 0.0     # chime / follow-up window: Pip is listening for someone
        self.offline_until = 0.0
        self.transcribing = False   # working out what was said: part of "thinking"
        self.say_pending_until = 0.0  # words sent to pip-ears but not playing yet (being turned into speech)
        self._state_timer = None
        if schedule is not None:
            schedule.watchers.append(self.refresh_state)
        self.wait_after_name = c.get("wait_after_name_seconds", 5.0)
        self.unfinished_wait = c.get("unfinished_wait_seconds", 2.0)
        self.max_sentences = c.get("max_sentences", 2)
        self.expect_until = 0.0     # Pip was just called: the next speech is for him, name or not
        self.pending = ""           # the start of a sentence they paused in the middle of
        self.pending_task = None
        self.sentences = 0
        self.long_ok = False
        self.keep_s = c.get("keep_minutes", 10.0) * 60
        self.max_turns = c.get("max_turns", 10)
        self.stt = stt
        self.history: list[dict] = []   # [{"role": "user"/"pip", "text": ..., "ts": ...}] RAM only
        self.answer_id = None
        self.answer_done = asyncio.Event()
        self.speech_done = asyncio.Event()
        bus.core_listen("heard.wake", self.on_wake)
        bus.core_listen("heard.utterance", self.on_utterance)
        bus.core_listen("heard.started", self.on_started)
        bus.core_listen("speech.interrupted", self.on_interrupted)
        bus.core_listen("speech.finished", self.on_speech_finished)
        bus.core_listen("speech.started", self.on_speech_started)
        bus.core_listen("answer.sentence", self.on_sentence)
        bus.core_listen("answer.searching", self.on_searching)

    # ---------------------------------------------------------------- helpers
    async def command(self, target, msg_type, data=None, private=None):
        await self.bus.route(Message("command", msg_type, source="core", target=target,
                                     data=data or {}, private=private or {}))

    # ---------------------------------------------------------------- Pip's state (for the dashboard and the face)
    def refresh_state(self) -> None:
        now = time.monotonic()
        if self.ringing:
            state = "ringing"
        elif self.speaking:
            state = "speaking"
        elif self.transcribing or now < self.say_pending_until or \
                (self.answer_id and self.filler_said in (None, "searching")):
            state = "thinking"  # until his first words
        elif now < self.listen_until:
            state = "listening"
        elif now < self.offline_until:
            state = "offline"
        else:
            state = "idle"
        if self._state_timer:
            self._state_timer.cancel()
            self._state_timer = None
        nxt = [t for t in (self.listen_until, self.offline_until, self.say_pending_until) if t > now]
        if nxt and _loop_running():
            self._state_timer = asyncio.get_running_loop().call_later(min(nxt) - now + 0.05, self.refresh_state)
        if state != self.state:
            self.state = state
            if _loop_running():
                asyncio.ensure_future(self.bus.publish(Message("event", "pip.state", source="core", data={"state": state})))

    def listening_for(self, seconds: float) -> None:
        self.listen_until = max(self.listen_until, time.monotonic() + seconds)
        self.refresh_state()

    async def say(self, text: str, reason: str) -> None:
        if text and not self.speaking:
            self.say_pending_until = time.monotonic() + 3.0
        if text:
            await self.command("ears", "ears.say", {"reason": reason}, {"text": text})

    async def show(self, who: str, text: str, **data) -> None:
        """Live conversation feed for the dashboard (private: shown, never logged)."""
        private = {"text": text}
        if who == "you" and self.speaker is not None and self.people is not None:
            private["speaker"] = self.speaker.label
        await self.bus.publish(Message("event", "conversation.line", source="core",
                                       data={"who": who, **data}, private=private))

    def _context(self) -> list[dict]:
        cutoff = time.time() - self.keep_s
        self.history = [h for h in self.history if h["ts"] >= cutoff][-2 * self.max_turns:]
        return [{"role": h["role"], "text": h["text"]} for h in self.history]

    @property
    def ringing(self) -> bool:
        return bool(self.schedule and self.schedule.ringing)

    async def transcribe(self, pcm: bytes, reason: str) -> tuple[str, bool]:
        """(text, offline). Groq first; if it can't be reached, Whisper on the Pi."""
        try:
            if self.stt is None:
                raise RuntimeError("no speech-to-text service")
            return await self.stt.transcribe(pcm), False
        except Exception as e:
            if self.local_stt is None or (reason == "room" and not self.ringing):
                raise  # room speech is never transcribed on the Pi (D-50), except to hear "stop" during an alarm
            await self.bus.publish(Message("event", "stt.offline", data={"error": type(e).__name__, "reason": reason}))
            self.offline_until = time.monotonic() + 120
            return await self.local_stt.transcribe(pcm), True

    async def has_voice(self, pcm: bytes, reason: str) -> bool:
        """Noise (a click, a cough, a hiss) never reaches speech-to-text, which would hear "Thank you." (D-85)."""
        if not self.voice_check or reason == "wake":
            return True  # right after the wake word, someone was certainly talking
        from .voicecheck import voice_seconds
        try:
            secs = await voice_seconds(pcm)
        except Exception as e:  # never lose speech because the check failed
            log.warning("voice check failed: %s", e)
            return True
        if secs < self.min_voice:
            await self.bus.publish(Message("event", "heard.noise", data={"reason": reason, "voice_s": round(secs, 2)}))
            return False
        return True

    # ---------------------------------------------------------------- events
    async def on_wake(self, msg: Message) -> None:
        if self.ringing:
            await self.schedule.acknowledge("name")  # saying his name answers an alarm (FEAT-037)
        self.answer_id = None  # a new request replaces anything in progress
        await self.command("thinker", "thinker.cancel")
        await self.command("ears", "ears.chime")
        self.listening_for(8.0)

    async def on_started(self, msg: Message) -> None:
        """They started talking again in the window: keep waiting for the rest of a paused sentence."""
        self.last_started = time.monotonic()
        if msg.data.get("reason") == "follow_up":
            self.listening_for(12.0)
        if self.pending_task and msg.data.get("reason") == "follow_up":
            self.pending_task.cancel()
            self.pending_task = None

    async def on_interrupted(self, msg: Message) -> None:
        if self.answer_id:
            await self.command("thinker", "thinker.cancel", {"answer_id": self.answer_id})
            self.answer_id = None

    async def on_speech_started(self, msg: Message) -> None:
        self.speaking, self.say_pending_until = True, 0.0
        self.refresh_state()

    async def on_speech_finished(self, msg: Message) -> None:
        self.speaking, self.say_pending_until = False, 0.0
        self.speech_done.set()
        if self.answer_id is None and not msg.data.get("interrupted"):
            await self._maybe_follow_up()
        self.refresh_state()

    async def on_sentence(self, msg: Message) -> None:
        if msg.data.get("answer_id") != self.answer_id:
            return  # from an answer that was cancelled
        text = msg.private.get("text", "")
        self.sentences += 1
        if self.sentences > self.max_sentences and not self.long_ok:
            if self.sentences == self.max_sentences + 1:
                await self.command("thinker", "thinker.cancel", {"answer_id": self.answer_id})
                await self.bus.publish(Message("event", "answer.trimmed", data={"after": self.max_sentences}))
            return  # roommates don't give speeches (D-79)
        self.speech_done.clear()
        self.filler_said = self.filler_said or "first sentence"  # he's answering: no longer "thinking"
        await self.say(text, "answer")
        self.refresh_state()

    async def on_searching(self, msg: Message) -> None:
        if msg.data.get("answer_id") == self.answer_id and not self.filler_said:
            self.filler_said = "searching"
            await self.say("Let me look that up.", "searching")
            self.refresh_state()

    async def _thinking_tone(self, aid: str) -> None:
        """Replaces "One sec." (D-86): a soft tone the moment Pip starts thinking (or only if slow; see settings)."""
        if self.tone_mode == "off":
            return
        if self.tone_mode == "slow":
            await asyncio.sleep(self.tone_after)
        if self.answer_id == aid and not self.filler_said:
            await self.command("ears", "ears.tone", {"name": "thinking"})

    async def on_utterance(self, msg: Message) -> None:
        reason = msg.data.get("reason")
        if reason != "room":
            self.listen_until = 0.0
            self.transcribing = bool(msg.data.get("had_speech"))
            self.refresh_state()
        if not msg.data.get("had_speech"):
            await self._finish_pending()
            return
        if reason == "room" and self.enrolling:
            pcm = base64.b64decode(msg.private.get("audio_b64", ""))
            if await self.has_voice(pcm, reason):
                return await self._enrol_sample(pcm)   # they started reading a little after the listening window
            return
        if reason == "room":
            return await self.on_room(msg)
        if self.stt is None and self.local_stt is None:
            if reason == "wake":
                await self.say(SAYINGS["stt_failed"], "error")
            return
        pcm = base64.b64decode(msg.private.get("audio_b64", ""))
        if not await self.has_voice(pcm, reason):
            self.transcribing = False
            self.refresh_state()
            await self._finish_pending()
            return
        if self.enrolling and reason in ("follow_up", "wake", "barge_in"):
            self.transcribing = False
            self.refresh_state()
            return await self._enrol_sample(pcm)
        t0 = time.perf_counter()
        self.transcribing = True
        self.refresh_state()
        ident = asyncio.ensure_future(self.identify(pcm)) if reason != "room" else None
        try:
            text, offline = await self.transcribe(pcm, reason)
        except Exception as e:
            if ident:
                ident.cancel()
            self.transcribing = False
            self.refresh_state()
            log.warning("speech-to-text failed: %s", e)
            await self.bus.publish(Message("event", "stt.failed", data={"error": type(e).__name__}))
            if reason == "wake":
                await self.say(SAYINGS["stt_failed"], "error")
            return
        stt_s = round(time.perf_counter() - t0, 2)
        if ident:
            self.speaker, self.last_pcm = await ident, pcm
        self.transcribing = False
        asyncio.get_running_loop().call_soon(self.refresh_state)  # by then he's answering (thinking) or done
        await self.bus.publish(Message("event", "stt.done", data={"seconds": stt_s, "chars": len(text),
                                                                   "reason": reason, "offline": offline,
                                                                   "speaker": self.speaker.level if self.speaker else None}))
        if self.ringing and self.commands:
            reply = await self.commands.while_ringing(text, reason)
            if reply is not None:
                await self.say(reply, "alarm answered")
                return
        if not text.strip(" .?!,"):
            if reason == "wake":
                await self.say(SAYINGS["not_caught"], "not caught")
            return
        if reason == "wake" and NOT_PIP.search(text) and not PIP_NAMES.search(text):
            await self.bus.publish(Message("event", "wake.rejected", data={"why": "transcript names Ziggletron"}))
            return
        await self.addressed(text, reason, stt_s=stt_s, offline=offline)

    async def addressed(self, text: str, reason: str, offline: bool = False, **data) -> None:
        """Someone said something to Pip. Wait if they aren't finished; otherwise answer."""
        if self.pending:
            text = f"{self.pending} {text}"
            self.pending = ""
        if self.pending_task:
            self.pending_task.cancel()
            self.pending_task = None
        if only_name(text):
            # Just the name (often followed by a pause): the chime says he's listening. Don't talk over them.
            self.expect_until = time.monotonic() + self.wait_after_name
            await self.command("ears", "ears.listen", {"seconds": self.wait_after_name})
            self.listening_for(self.wait_after_name)
            return
        if looks_unfinished(text):
            self.pending = text
            self.expect_until = time.monotonic() + self.unfinished_wait + 1.0
            await self.command("ears", "ears.listen", {"seconds": self.unfinished_wait})
            self.pending_task = asyncio.create_task(self._answer_pending(reason))
            return
        self.expect_until = 0.0
        await self.show("you", text, reason=reason, offline=offline, **data)
        if await self.people_command(text, offline):
            return
        if await self.local_command(text):
            return
        if offline:
            await self.bus.publish(Message("event", "answer.offline", data={}))
            await self.say(SAYINGS["offline"], "offline")
            return
        await self.answer(text, reason)

    async def local_command(self, text: str) -> bool:
        """Pip's own commands, done on the Pi with no AI. True if it was one."""
        if not self.commands:
            return False
        t0 = time.perf_counter()
        try:
            reply = await self.commands.handle(text)
        except Exception as e:
            log.exception("command failed")
            await self.bus.publish(Message("event", "command.failed", data={"error": type(e).__name__}))
            await self.say(SAYINGS["command_failed"], "error")
            return True
        if reply is None:
            return False
        self.answer_id = None
        await self.command("thinker", "thinker.cancel")
        await self.bus.publish(Message("event", "command.done", data={"seconds": round(time.perf_counter() - t0, 3),
                                                                      "spoken": bool(reply)}))
        if not reply:  # "stop", "never mind", or answering an alarm: just go quiet
            await self.command("ears", "ears.stop")
            return True
        self.history.append({"role": "user", "text": text, "ts": time.time()})
        self.history.append({"role": "pip", "text": reply, "ts": time.time()})
        self.speech_done.clear()
        await self.say(reply, "command")
        await self.show("pip", reply, total_s=round(time.perf_counter() - t0, 3), local=True)
        return True

    async def people_command(self, text: str, offline: bool = False) -> bool:
        """'Leave me out', 'I'm Sam', 'who am I?', offline 'remember…' (core/peoplecmds.py)."""
        if not self.people_cmds:
            return False
        try:
            reply = await self.people_cmds.handle(text, self.speaker, self.last_pcm, offline)
        except Exception:
            log.exception("people command failed")
            reply = SAYINGS["command_failed"]
        if reply is None:
            return False
        self.answer_id = None
        await self.command("thinker", "thinker.cancel")
        await self.bus.publish(Message("event", "command.done", data={"kind": "people", "spoken": True}))
        from .peoplecmds import StartLearning
        if isinstance(reply, StartLearning):
            self.history += [{"role": "user", "text": text, "ts": time.time()}, {"role": "pip", "text": reply.say, "ts": time.time()}]
            await self.show("pip", reply.say, local=True)
            await self.start_enrolment(reply.person_id, first=self.last_pcm, greeting=reply.say)
            return True
        self.history += [{"role": "user", "text": text, "ts": time.time()}, {"role": "pip", "text": reply, "ts": time.time()}]
        self.speech_done.clear()
        await self.say(reply, "command")
        await self.show("pip", reply, local=True)
        return True

    async def identify(self, pcm: bytes):
        if self.people is None:
            return None
        try:
            return await self.people.identify(pcm)
        except Exception:
            log.exception("voice recognition failed")
            return None

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
        self.listening_for(self.ENROL_WAIT)
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
        from .voicecheck import voice_seconds
        try:
            return await voice_seconds(pcm)
        except Exception:
            return len(pcm) / 32000

    def cancel_enrolment(self) -> None:
        if self.enrolling and (task := self.enrolling.get("task")):
            task.cancel()
        self.enrolling = None
        if _loop_running():
            asyncio.ensure_future(self._enrol_event())

    async def _finish_pending(self) -> None:
        """They seemed to start talking again, but it was only noise: answer the paused sentence now."""
        if self.pending and not self.pending_task:
            self.pending_task = asyncio.create_task(self._answer_pending("follow_up", wait=0.0))

    async def _answer_pending(self, reason: str, wait: float | None = None) -> None:
        await asyncio.sleep(self.unfinished_wait + 1.0 if wait is None else wait)  # nothing more came: answer what we have
        text, self.pending, self.pending_task = self.pending, "", None
        if text:
            await self.show("you", text, reason=reason)
            await self.answer(text, reason)

    async def on_room(self, msg: Message) -> None:
        """Room speech: transcribe it and check whether it called Pip (the wake detector may have missed it)."""
        if self.stt is None and not (self.ringing and self.local_stt):
            return
        pcm = base64.b64decode(msg.private.get("audio_b64", ""))
        if not await self.has_voice(pcm, "room"):
            return
        speaker = None
        if self.people is not None and self.people.has_opt_outs() and not self.ringing:
            speaker = await self.identify(pcm)
            if speaker and speaker.opted_out:
                # D-77: an opted-out person's room speech never leaves the Pi. They can still call Pip with the
                # wake word, which is heard on the Pi.
                await self.bus.publish(Message("event", "room.left_out", data={"level": speaker.level}))
                return
        try:
            text, offline = await self.transcribe(pcm, "room")
        except Exception:
            return
        if self.ringing and self.commands:
            reply = await self.commands.while_ringing(text, "room")
            await self.bus.publish(Message("event", "room.heard", data={"seconds": msg.data.get("seconds"),
                                                                         "during_alarm": True, "answered": reply is not None and not self.ringing}))
            if reply is not None:
                await self.say(reply, "alarm answered")
                return
        expected = time.monotonic() < self.expect_until
        addressed = bool(text.strip(" .?!,")) and (expected or names_pip(text))
        await self.bus.publish(Message("event", "room.heard", data={"seconds": msg.data.get("seconds"),
                                                                     "addressed": addressed}))
        if not addressed:
            return  # (Social-mode joining in uses this in step 6)
        if not expected:
            self.answer_id = None
            await self.command("thinker", "thinker.cancel")
            await self.command("ears", "ears.chime")
        self.speaker, self.last_pcm = (speaker or await self.identify(pcm)), pcm
        await self.addressed(text, "expected" if expected else "transcript", offline=offline)

    # ---------------------------------------------------------------- answering
    async def answer(self, text: str, reason: str) -> None:
        aid = new_id()
        self.answer_id = aid
        self.sentences = 0
        self.long_ok = bool(WANTS_DETAIL.search(text))
        self.filler_said = None
        self.refresh_state()  # thinking
        filler = asyncio.create_task(self._thinking_tone(aid))
        context = self._context()
        self.history.append({"role": "user", "text": text, "ts": time.time()})
        t0 = time.perf_counter()
        try:
            schedule = self.schedule.summary() if self.schedule else ""
            sp = self.speaker
            people = self.people.summary(sp) if self.people is not None else ""
            memories = self.memory.for_claude(sp.person_id if sp else None, sp.level if sp else "unknown") \
                if self.memory is not None else ""
            reply = await self.bus_request("thinker", "thinker.ask", {"answer_id": aid, "reason": reason},
                                           {"text": text, "context": context, "schedule": schedule,
                                            "people": people, "memories": memories}, timeout=60)
        except Exception as e:
            reply = Message("reply", "thinker.ask", data={"error": f"{type(e).__name__}"})
        filler.cancel()
        if self.answer_id != aid:
            self.refresh_state()
            return  # cancelled meanwhile
        self.answer_id = None
        self.refresh_state()
        if reply.data.get("error"):
            await self.bus.publish(Message("event", "answer.failed", data={"error": reply.data["error"]}))
            await self.say(SAYINGS["thinker_failed"], "error")
            return
        if reply.private.get("action") and self.commands:
            return await self.claude_action(reply.private["action"], t0)
        answer = reply.private.get("text", "")
        self.history.append({"role": "pip", "text": answer, "ts": time.time()})
        await self.show("pip", answer, total_s=round(time.perf_counter() - t0, 2),
                        first_s=reply.data.get("first_sentence_s"))
        if self.speech_done.is_set():
            await self._maybe_follow_up()

    async def claude_action(self, raw: str, t0: float) -> None:
        """Claude turned a request the rules missed into an action; Pip checks it, does it and confirms (D-84)."""
        from . import actions
        try:
            said, done = actions.apply(self.commands, raw, {"memory": self.memory, "people": self.people,
                                                           "speaker": self.speaker})
        except Exception:
            log.exception("action failed")
            said, done = actions.FAILED, 0
        await self.bus.publish(Message("event", "action.done", data={"count": done, "ok": bool(done)}))
        self.history.append({"role": "pip", "text": said, "ts": time.time()})
        self.speech_done.clear()
        await self.say(said, "command")
        await self.show("pip", said, total_s=round(time.perf_counter() - t0, 2), local=True)

    async def _maybe_follow_up(self) -> None:
        if self.follow_up_s > 0 and self.history and self.history[-1]["role"] == "pip" and not self.ringing:
            await self.command("ears", "ears.listen", {"seconds": self.follow_up_s})
            self.listening_for(self.follow_up_s)

    async def bus_request(self, target, msg_type, data, private, timeout):
        """Ask another program from inside core and wait for its reply."""
        msg = Message("request", msg_type, source="core", target=target, id=new_id(), data=data, private=private)
        fut = asyncio.get_running_loop().create_future()
        self.bus.core_waiting[msg.id] = fut
        try:
            await self.bus.route(msg)
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.bus.core_waiting.pop(msg.id, None)
