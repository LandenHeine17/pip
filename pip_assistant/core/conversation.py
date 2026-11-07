"""The conversation part of the conductor (M1a steps 3–6).

Pip listens like Ziggletron (D-92): everything said in the room is transcribed and kept in a short
room transcript (core/transcript.py), with who said it. Each line is then one of:

    direct      the wake word, called by name ("Pip, …", "hey Pippletron"), the follow-up window
                after he spoke, or talking over him → he chimes and answers
    mentioned   his name came up but nobody called him ("ask Pip later") → Claude decides whether a
                person would expect him to answer, and may stay quiet (PASS)
    neither     → after a pause, he may join in (core/joining.py)

Answering something said to him:
      → pip-ears sends the audio of what was said (heard.utterance)
      → is there a human voice in it? (noise never reaches speech-to-text: no phantom "Thank you.", D-85)
      → speech-to-text (Groq), with household names spelled right ("Landon" → "Landen", D-93)
      → "was that really for Pip?" check (e.g. it was "Ziggletron")
      → pip-thinker asks Claude (a soft thinking tone plays), whatever the wording (D-94), streaming the answer
        sentence by sentence; if Claude replies with an ACTION (set/change/cancel a timer, alarm, reminder or
        event, stop or snooze a ringing alarm, remember/forget, learn a voice), Pip's code checks it, does it
        and says the confirmation (D-84); Claude may also stay quiet (PASS)
      → each sentence goes to pip-ears to be spoken
      → afterwards, a short follow-up window: talk again without the name

What people say and what Pip answers travel only in messages' `private` part, so they are never
written to the decision trace (D-34). The room transcript is kept in memory only (PIP-NEW-028 category 5).
When listening is switched off (dashboard or two claps, core/mic.py, D-91) nothing is heard at all.

Offline (Groq can't be reached): Whisper on the Pi transcribes instead, and Pip's own fixed-phrasing
commands (core/commands.py) do the basics; anything else gets "I'm offline, so I can only do the basics
right now" (PIP-NEW-044, D-83). They're also the fallback if Claude fails to answer.
While an alarm rings, the obvious "stop", "okay", "snooze" or Pip's name silence it instantly; anything
else said to Pip goes to Claude, which can stop or snooze it.

Pip's state (idle / listening / thinking / speaking / ringing / offline / mic_off) is published as `pip.state`
whenever it changes: the dashboard shows it, and the face (step 7) will react to it (D-86).
"""
import asyncio
import base64
import json
import logging
import re
import time

from ..common.messages import Message, new_id
from .joining import Joining, in_quiet_hours
from .transcript import Transcript

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


GREETINGS = frozenset("hey hi hello yo oi ok okay oh so um uh and but well alright thanks thank you".split())


def _is_pip(words: list[str]) -> bool:
    """These words, run together, are one of Pip's names (or a close mishearing of Pippletron)."""
    import difflib
    joined = "".join(words)
    if not joined:
        return False
    if PIP_NAMES.fullmatch(" ".join(words)) or PIP_NAMES.fullmatch(joined):
        return True
    return len(joined) >= 7 and not joined.startswith("zig") and \
        difflib.SequenceMatcher(None, joined, "pippletron").ratio() >= 0.82


def calls_pip(text: str) -> bool:
    """Is Pip being *called* ("Pip, what's the time?", "thanks, Pip", "hey Pippletron what's up")
    rather than just mentioned ("Pip's being weird", "ask Pip later")? Ziggletron's rule: a clause
    that's only the name, or a greeting followed by it."""
    for clause in re.split(r"[,.!?;:—]+", text):
        words = re.findall(r"[a-z']+", clause.lower())
        greeted = False
        while words and words[0] in GREETINGS:
            words, greeted = words[1:], True
        if words and _is_pip(words):
            return True
        if greeted and any(_is_pip(words[:n]) for n in (1, 2, 3)):
            return True
    return False


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
    def __init__(self, bus, cfg: dict | None = None, stt=None, schedule=None, local_stt=None, people=None, memory=None,
                 mic=None, modules=None):
        from .commands import Commands
        from .peoplecmds import PeopleCommands
        self.bus = bus
        self.people, self.memory = people, memory
        self.mic = mic               # the listening switches (core/mic.py); None = always on
        self.net = None              # the online check (core/net.py), set by main
        self.own_commands = (cfg or {}).get("conversation", {}).get("own_commands", "offline")  # D-94
        self.transcript = Transcript()
        self.joining = Joining(self, cfg)
        self.answer_kind = "direct"  # what the current answer is: direct / mentioned / join
        self.join_spoke = False      # a join-in started speaking (too late to drop it)
        self.join_count = 0          # transcript.count when the join-in was asked for
        so = (cfg or {}).get("social", {})
        self.welcome_after = so.get("welcome_back_minutes", 20) * 60   # 0 = never
        self.quiet_hours = so.get("quiet_hours", "23:00-07:00")
        self.welcomed: dict[str, float] = {}   # person id → when Pip last said welcome back
        self.last_voice_at = time.time()  # when anyone last said something (no welcome-back right after a restart)
        self.people_cmds = PeopleCommands(people, memory) if people is not None else None
        self.speaker = None          # who's talking in the current request (people.Match)
        self.last_pcm = b""
        self.enrolling: dict | None = None   # guided voice learning in progress
        self.last_started = 0.0              # when ears last started capturing someone
        self.schedule = schedule
        self.modules = modules       # plugged-in modules (core/modules.py): calendar, to-dos, …
        self.commands = Commands(schedule, clock=schedule.clock, calendar=modules.get("calendar") if modules else None) \
            if schedule is not None else None
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
        self.stt = stt
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
        bus.core_listen("mic.clapped", self.on_clapped)
        bus.on_connect.append(self.on_program_connected)

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
        elif self.mic is not None and not self.mic.listening:
            state = "mic_off"
        elif self.transcribing or now < self.say_pending_until or \
                (self.answer_id and self.answer_kind == "direct" and self.filler_said in (None, "searching")):
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

    def label(self, speaker=None) -> str:
        sp = speaker if speaker is not None else self.speaker
        return sp.label if sp is not None else "Someone"

    def hear(self, text: str, speaker=None) -> None:
        """A person said this (to Pip or not): into the room transcript."""
        self.transcript.add(self.label(speaker), text)
        self.last_voice_at = time.time()

    def said(self, text: str) -> None:
        """Pip said this."""
        if text:
            self.transcript.add("Pip", text, ours=True)

    def fix_names(self, text: str) -> str:
        if self.people is None:
            return text
        try:
            return self.people.fix_names(text)
        except Exception:
            return text

    @property
    def listening(self) -> bool:
        return self.mic is None or self.mic.listening

    @property
    def ringing(self) -> bool:
        return bool(self.schedule and self.schedule.ringing)

    async def transcribe(self, pcm: bytes, reason: str) -> tuple[str, bool]:
        """(text, offline). Groq first; if it can't be reached, Whisper on the Pi."""
        names = []
        if self.people is not None:
            try:
                names = self.people.names()
            except Exception:
                names = []
        for s in (self.stt, self.local_stt):
            if s is not None:
                s.names = names   # so Groq spells "Landen", not "Landon" (D-93)
        try:
            if self.stt is None:
                raise RuntimeError("no speech-to-text service")
            text = await self.stt.transcribe(pcm)
            if self.net is not None and not self.net.online:
                await self.net.set(True)
            return text, False
        except Exception as e:
            if self.net is not None and self.stt is not None:
                await self.net.failed()
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

    # ---------------------------------------------------------------- listening on/off (D-91)
    async def apply_mic(self, tone: str | None = None) -> None:
        """Tell pip-ears what to listen for, and everyone (dashboard, face) whether Pip is listening."""
        if self.mic is None:
            return
        if not self.mic.listening:  # anything half-done from what was heard is dropped
            self.cancel_answer("listening switched off")
            self.joining.cancel_wait()
            self.pending, self.expect_until, self.listen_until = "", 0.0, 0.0
            if self.pending_task:
                self.pending_task.cancel()
                self.pending_task = None
        await self.command("ears", "ears.mic", {"mode": self.mic.mode})
        if tone:
            await self.command("ears", "ears.tone", {"name": tone})
        await self.bus.publish(Message("event", "mic.changed", data=self.mic.snapshot()))
        self.refresh_state()

    async def on_clapped(self, msg: Message) -> None:
        if self.mic is not None and self.mic.clapped():
            await self.apply_mic("mic_on" if self.mic.listening else "mic_off")

    async def set_mic(self, on: bool) -> dict:
        """The dashboard's Listening switch. Turning it on also clears a clap switch-off, so "on" means on."""
        if self.mic is None:
            return {}
        was = self.mic.listening
        self.mic.set_dashboard(on)
        if on and not self.mic.claps:
            self.mic.clapped()
        await self.apply_mic(None if was == self.mic.listening else "mic_on" if self.mic.listening else "mic_off")
        return self.mic.snapshot()

    async def on_program_connected(self, name: str) -> None:
        if name == "ears":
            await self.apply_mic()

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
        self.joining.someone_started()
        if msg.data.get("reason") == "follow_up":
            self.listening_for(12.0)
        if self.pending_task and msg.data.get("reason") == "follow_up":
            self.pending_task.cancel()
            self.pending_task = None

    async def on_interrupted(self, msg: Message) -> None:
        self.transcript.mark_cut()
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
        if self.answer_kind == "join" and not self.join_spoke:
            if self.transcript.count != self.join_count or not self.can_join(joining=True):
                return self.cancel_answer("the conversation moved on")
            self.join_spoke = True
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
        if not self.listening:
            return  # switched off (D-91): pip-ears shouldn't send anything, and nothing is used if it does
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
        text = self.fix_names(text)
        self.hear(text)
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
        if offline or self.own_commands == "always":
            # No Claude without the internet: Pip's own fixed-phrasing commands do the basics (D-83, D-94)
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
        self.said(reply)
        self.speech_done.clear()
        await self.say(reply, "command")
        await self.show("pip", reply, total_s=round(time.perf_counter() - t0, 3), local=True)
        return True

    async def people_command(self, text: str, offline: bool = False) -> bool:
        """Offline (or if Claude fails): 'I'm Sam', 'learn my voice', 'who am I?', 'remember…' (core/peoplecmds.py)."""
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
            self.said(reply.say)
            await self.show("pip", reply.say, local=True)
            await self.start_enrolment(reply.person_id, first=self.last_pcm, greeting=reply.say)
            return True
        self.said(reply)
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
        """Room speech: everything is transcribed and kept in the room transcript, like Ziggletron (D-92).
        Then: called by name or in the follow-up window → answer; name mentioned → Claude decides;
        otherwise → maybe join in after a pause."""
        if self.stt is None and not (self.ringing and self.local_stt):
            return
        pcm = base64.b64decode(msg.private.get("audio_b64", ""))
        if not await self.has_voice(pcm, "room"):
            return
        ident = asyncio.ensure_future(self.identify(pcm)) if not self.ringing else None
        try:
            text, offline = await self.transcribe(pcm, "room")
        except Exception:
            if ident:
                ident.cancel()
            return
        speaker = await ident if ident else None
        if self.ringing and self.commands:
            reply = await self.commands.while_ringing(text, "room")
            await self.bus.publish(Message("event", "room.heard", data={"seconds": msg.data.get("seconds"),
                                                                         "during_alarm": True, "answered": reply is not None and not self.ringing}))
            if reply is not None:
                await self.say(reply, "alarm answered")
                return
        if not text.strip(" .?!,") or not self.listening:
            return
        text = self.fix_names(text)
        quiet_for = time.time() - self.last_voice_at
        self.hear(text, speaker)
        expected = time.monotonic() < self.expect_until
        how = "direct" if expected or calls_pip(text) else "mentioned" if names_pip(text) else "room"
        await self.bus.publish(Message("event", "room.heard", data={"seconds": msg.data.get("seconds"),
                                                                     "addressed": how != "room", "how": how,
                                                                     "speaker": speaker.level if speaker else None}))
        if how == "room":
            if not await self.welcome_back(speaker, quiet_for):
                self.joining.heard()
            return
        self.joining.cancel_wait()
        self.speaker, self.last_pcm = speaker, pcm
        if how == "mentioned":
            if self.answer_id is None and not self.speaking and not offline:
                await self.answer(text, "mentioned", kind="mentioned")
            return
        if not expected:
            self.answer_id = None
            await self.command("thinker", "thinker.cancel")
            await self.command("ears", "ears.chime")
        await self.addressed(text, "expected" if expected else "transcript", offline=offline)

    async def welcome_back(self, speaker, quiet_for: float) -> bool:
        """Someone Pip knows speaks after the room was quiet a while: "Hey Landen, welcome back." (once an hour)"""
        if not self.welcome_after or speaker is None or speaker.level != "sure" or speaker.guest or \
                quiet_for < self.welcome_after or in_quiet_hours(self.quiet_hours) or not self.can_join():
            return False
        now = time.time()
        if now - self.welcomed.get(speaker.person_id, 0) < 3600:
            return False
        self.welcomed[speaker.person_id] = now
        line = f"Hey {speaker.name}, welcome back."
        self.said(line)
        self.speech_done.clear()
        await self.say(line, "welcome")
        await self.show("pip", line, local=True)
        return True

    # ---------------------------------------------------------------- answering
    async def answer(self, text: str, reason: str, kind: str = "direct", mood: str = "") -> None:
        """Ask Claude. kind: "direct" (said to Pip), "mentioned" (his name came up: he may PASS) or "join"
        (nobody spoke to him: he may join in with "SAY: …" or PASS). Claude sees the room transcript."""
        aid = new_id()
        self.answer_id, self.answer_kind, self.join_spoke = aid, kind, False
        self.join_count = self.transcript.count
        self.sentences = 0
        self.long_ok = bool(WANTS_DETAIL.search(text or ""))
        self.filler_said = None
        self.refresh_state()  # thinking (direct answers only)
        filler = asyncio.create_task(self._thinking_tone(aid)) if kind == "direct" else None
        t0 = time.perf_counter()
        try:
            sp = self.speaker if kind != "join" else None
            schedule = self.schedule.summary() if self.schedule else ""
            if self.modules is not None:   # each module's part: the calendar, to-dos, … (only what this speaker may hear)
                from .modules import Call
                schedule = " ".join(x for x in (schedule, self.modules.summary(Call(speaker=sp))) if x)
            people = self.people.summary(sp) if self.people is not None else ""
            if kind == "join":
                people = people.split(". ", 1)[-1]   # nobody spoke to him: just who lives here
            memories = self.memory.for_claude(sp.person_id if sp else None, sp.level if sp else "unknown") \
                if self.memory is not None else ""
            reply = await self.bus_request("thinker", "thinker.ask", {"answer_id": aid, "reason": reason, "kind": kind},
                                           {"text": text or "", "transcript": self.transcript.render(), "mood": mood,
                                            "schedule": schedule, "people": people, "memories": memories}, timeout=60)
        except Exception as e:
            reply = Message("reply", "thinker.ask", data={"error": f"{type(e).__name__}"})
        if filler:
            filler.cancel()
        if self.answer_id != aid:
            self.refresh_state()
            return  # cancelled meanwhile
        self.answer_id = None
        self.refresh_state()
        if reply.data.get("error"):
            await self.bus.publish(Message("event", "answer.failed", data={"error": reply.data["error"], "kind": kind}))
            if kind == "direct":
                # Claude didn't answer: Pip's own commands may still manage it ("timer ten minutes")
                if text and (await self.people_command(text) or await self.local_command(text)):
                    return
                await self.say(SAYINGS["thinker_failed"], "error")
            return
        if reply.private.get("action") and self.commands:
            if kind == "join":
                return  # joining in never acts on anything (nothing overheard is acted on)
            return await self.claude_action(reply.private["action"], t0)
        answer = reply.private.get("text", "")
        if not answer:  # PASS: he decided it wasn't for him / nothing to add
            await self.bus.publish(Message("event", "answer.passed", data={"kind": kind}))
            return
        self.said(answer)
        await self.show("pip", answer, total_s=round(time.perf_counter() - t0, 2),
                        first_s=reply.data.get("first_sentence_s"), kind=kind)
        if self.speech_done.is_set():
            await self._maybe_follow_up()

    def can_join(self, joining: bool = False) -> bool:
        """Free to join in (or still free, for a join-in being decided)."""
        return (self.listening and not self.ringing and not self.enrolling and not self.pending
                and not self.transcribing and self.stt is not None and time.monotonic() >= self.offline_until
                and (joining or (self.answer_id is None and not self.speaking)))

    async def join_in(self, mood: str) -> None:
        await self.bus.publish(Message("event", "join.considered", data={}))
        await self.answer("", "join", kind="join", mood=mood)

    def cancel_answer(self, why: str = "") -> None:
        if self.answer_id:
            aid, self.answer_id = self.answer_id, None
            if _loop_running():
                asyncio.ensure_future(self.command("thinker", "thinker.cancel", {"answer_id": aid}))
                asyncio.ensure_future(self.bus.publish(Message("event", "answer.dropped", data={"why": why})))
            self.refresh_state()

    async def claude_action(self, raw: str, t0: float) -> None:
        """Claude turned a request into actions; Pip checks them, does them and confirms (D-84, D-94).
        Ringing alarms and voice learning are handled here (they need the alarm and the audio);
        the rest by core/actions.py. Anything invalid means nothing at all is done."""
        from . import actions
        from .peoplecmds import StartLearning
        said, done, learning = [], 0, None
        try:
            items = actions.parse(raw)
            here = [a for a in items if a.get("do") in actions.HERE]
            rest = [a for a in items if a.get("do") not in actions.HERE]
            plan = actions.check_here(here)
            if rest:
                actions.check(self.commands, rest, self._action_ctx())   # validate before doing anything
            for step in plan:
                if step[0] == "stop_alarm":
                    n = await self.schedule.acknowledge("claude")
                    said.append("" if n else "Nothing's ringing.")
                elif step[0] == "snooze":
                    n = await self.schedule.snooze(step[1], how="claude")
                    from . import when
                    said.append(f"Snoozed for {when.say_duration((step[1] or self.schedule.snooze_min) * 60)}." if n
                                else "Nothing's ringing.")
                elif step[0] == "learn_voice":
                    r = await self.people_cmds.introduced(step[1], True, self.speaker, self.last_pcm) \
                        if self.people_cmds else "I can't recognise voices yet."
                    if isinstance(r, StartLearning):
                        learning = r
                    else:
                        said.append(r)
                done += 1
            if rest:
                s, n = actions.apply(self.commands, json.dumps(rest), self._action_ctx())
                said.append(s)
                done += n
        except Exception as e:
            if not isinstance(e, (actions.Invalid, ValueError, TypeError, KeyError)):
                log.exception("action failed")
            said, done, learning = [actions.FAILED], 0, None
        await self.bus.publish(Message("event", "action.done", data={"count": done, "ok": bool(done)}))
        text = " ".join(s for s in said if s)
        if text:
            self.said(text)
            self.speech_done.clear()
            await self.say(text, "command")
            await self.show("pip", text, total_s=round(time.perf_counter() - t0, 2), local=True)
        if learning:
            self.said(learning.say)
            await self.show("pip", learning.say, local=True)
            await self.start_enrolment(learning.person_id, first=self.last_pcm, greeting=learning.say)

    def _action_ctx(self) -> dict:
        from .modules import Call
        return {"memory": self.memory, "people": self.people, "speaker": self.speaker, "modules": self.modules,
                "call": Call(speaker=self.speaker, now=self.commands.now()) if self.commands else Call(speaker=self.speaker)}

    async def _maybe_follow_up(self) -> None:
        last = self.transcript.last()
        if self.follow_up_s > 0 and last is not None and last["ours"] and not self.ringing and self.listening:
            secs = self.follow_up_s * (1.5 if last["text"].rstrip().endswith("?") else 1.0)  # he asked something
            self.expect_until = time.monotonic() + secs
            await self.command("ears", "ears.listen", {"seconds": secs})
            self.listening_for(secs)

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
