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
commands (core/commands.py) do the basics; for anything else he stays quiet (the face and dashboard show
he's offline; D-83, D-115). They're also the fallback if Claude fails to answer. Groq's daily limit running
out isn't offline: what's said to him is heard on the Pi and answered by Claude as usual (D-115).
While an alarm rings, the obvious "stop", "okay", "snooze" or Pip's name silence it instantly; anything
else said to Pip goes to Claude, which can stop or snooze it.

Pip's state is published as `pip.state` {state, detail, words, ends_in, quiet} whenever it changes: one of 8 states
(idle / listening / thinking / speaking / interrupted / ringing / mic_off / offline), and within it what exactly
he's doing (waiting to see if you're done, following the conversation, …) (D-108, D-112). The rules are in
core/states.py; the fields they read are grouped under "What the face shows" in Conversation.__init__, and
refresh_state() is called whenever one of them changes. The dashboard's conversation feed also gets every line
heard (`conversation.line`, words private) and small event bars (`conversation.event`: chime, waiting, passed,
interrupted, joined, mic_off/mic_on, and state changes if switched on; no words).

Where each part lives. This file keeps the face side (the "What the face shows" fields in __init__,
refresh_state, situation, state_message, say) and the main flow from hearing to answering:
on_wake → on_utterance (or on_room) → addressed → respond → answer → on_sentence → on_speech_finished.
`Conversation` takes in the methods of these files as if they were its own (each is a small class it is
built from, so `self.…` works the same everywhere):

    core/hearing.py     Hearing       is there a voice, speech-to-text, online/offline, who's talking
    core/micctl.py      MicControl    the Listening switches, claps, the chime and the dashboard's preferences
    core/turns.py       TurnTaking    holding what's said until they've finished their turn (addressed, D-106)
    core/enrolment.py   VoiceLearning guided voice learning
    core/context.py     Context       what Claude is told (schedule, modules, people, memories)
    core/acting.py      Acting        Pip's own commands and Claude's ACTIONs (stop/snooze, request_change, look-ups)
    core/feed.py        Feed          the room transcript and the dashboard's conversation feed
"""
import asyncio
import base64
import logging
import re
import time

from ..common.messages import Message, new_id
from .acting import Acting
from .context import Context
from .enrolment import VoiceLearning
from .feed import Feed
from .hearing import Hearing
from .joining import Joining, in_quiet_hours
from .micctl import MicControl
from .names import NOT_PIP, PIP_NAMES, calls_pip, names_pip
from . import states
from .states import DETAILS, situation
from .transcript import Transcript
from .turns import TurnTaking, looks_unfinished  # noqa: F401  (looks_unfinished: tests import it from here)

log = logging.getLogger(__name__)

WANTS_DETAIL = re.compile(r"\b(story|explain|details?|in depth|describe|tell me (more|everything|all)|"
                          r"step by step|steps|recipe|list|longer)\b", re.I)

SAYINGS = {
    "not_caught": "Sorry, I didn't catch that. Could you say it again?",
    "stt_failed": "Sorry, I couldn't make out what you said. My hearing service isn't answering.",
    "thinker_failed": "Sorry, my brain isn't answering right now. Try me again in a moment.",
    "no_speech": "",  # wake word but then nothing: stay quiet
    "command_failed": "Sorry, that didn't work. Nothing was set.",
    # Asking Pip to change himself (M1b, PIP-NEW-069): only people he recognises for sure may ask by voice
    "change_asked": "Okay, I'll write up a plan. It'll be on the dashboard to approve.",
    "change_not_you": "Ask whoever looks after me to put that in on the dashboard's Changes page.",
    "change_unavailable": "I can't take change requests right now. Try again later.",
}


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


class Conversation(Hearing, MicControl, TurnTaking, VoiceLearning, Context, Acting, Feed):
    SAYINGS = SAYINGS   # so the parts in other files (core/acting.py) can use them as self.SAYINGS
    TURN_WAITS = {"short": 0.6, "statement": 1.2, "long": 1.5, "after_interrupt": 1.0}   # as in defaults.toml

    def __init__(self, bus, cfg: dict | None = None, stt=None, schedule=None, local_stt=None, people=None, memory=None,
                 mic=None, modules=None, prefs=None):
        from .commands import Commands
        from .peoplecmds import PeopleCommands
        self.bus = bus
        self.people, self.memory = people, memory
        self.mic = mic               # the listening switches (core/mic.py); None = always on
        self.prefs = prefs           # dashboard preferences (core/prefs.py): chime on/off, clap microphone
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
        # Pip's own offline commands, with what modules add to them (the calendar's phrases and events)
        self.commands = Commands(schedule, clock=schedule.clock, items=modules.offline_items() if modules else ()) \
            if schedule is not None else None
        self.local_stt = local_stt
        c = (cfg or {}).get("conversation", {})
        self.follow_up_s = c.get("follow_up_seconds", 8.0)
        th = (cfg or {}).get("thinker", {})
        self.tone_mode = th.get("thinking_tone", "always")
        self.tone_after = th.get("thinking_tone_after_seconds", 1.5)
        st = (cfg or {}).get("stt", {})
        self.voice_check = st.get("voice_check", False) if cfg else False
        self.min_voice = st.get("min_voice_seconds", 0.15)
        # --- What the face shows: core/states.py reads these to work out his state (call refresh_state()
        # after changing one). The *_until fields are time.monotonic() deadlines; 0 = not happening.
        self.state, self.detail = "idle", "quiet"   # what was last published
        self.quiet = True                            # a good moment to restart his programs (states.quiet)
        self.speaking = False          # his voice is playing (speech.started → speech.finished)
        self.say_reason = ""           # why he's talking: answer / welcome / command / error / … (see say())
        self.voice_kind = "direct"     # the answer he's speaking: direct / mentioned / join
        self.say_pending_until = 0.0   # words sent to pip-ears but not playing yet (being turned into speech)
        self.cut_until = 0.0           # someone talked over him: "interrupted" until then
        self.transcribing = False      # speech-to-text on something said to him: "listening: hearing"
        self.listen_until = 0.0        # a listening window (after his name, after answering, learning a voice)
        self.listen_why = "called"     # what that window is for (see listening_for)
        self.turn_until = 0.0          # when a held turn will be answered, if nobody says more (D-106)
        self.offline_until = 0.0       # speech-to-text fell back to the Pi: "offline" until then
        self.room_until = 0.0          # people are talking in the room (not to him): he's following along
        self.looking_up = False        # the second round of an answer, after a look-up
        self.filler_said = None        # None until his first words of an answer ("searching" if he said so)
        # (also read: answer_id / answer_kind below, pending below, ringing and mic)
        self._state_timer = None       # re-checks the state when the next *_until deadline passes
        if schedule is not None:
            schedule.watchers.append(self.refresh_state)
        self.wait_after_name = c.get("wait_after_name_seconds", 5.0)
        self.unfinished_wait = c.get("unfinished_wait_seconds", 2.0)
        t = self.TURN_WAITS                                             # knowing when you're done (D-106)
        self.turn_short = c.get("turn_wait_short_seconds", t["short"])
        self.turn_statement = c.get("turn_wait_statement_seconds", t["statement"])
        self.turn_long = c.get("turn_wait_long_seconds", t["long"])
        self.turn_after_cut = c.get("turn_wait_after_interrupt_seconds", t["after_interrupt"])
        self.max_utterance = (cfg or {}).get("listen", {}).get("max_utterance_seconds", 12.0)
        self.last_cut = -1e9         # when someone last talked over Pip (he waits longer for a minute, D-106)
        self.mic_off_since = None    # for "mic was off for 12 s" on the feed
        self.max_sentences = c.get("max_sentences", 2)
        self.expect_until = 0.0     # Pip was just called: the next speech is for him, name or not
        self.pending = ""           # what they've said so far, held until they've finished their turn (D-106)
        self.pending_task = None    # answers it once the wait is over
        self.pending_how = ("direct", False, {}, False)   # (reason, offline, data, chime) for the held turn
        self.sentences = 0
        self.spoken: list[str] = []  # sentences of the current answer actually sent to be spoken
        self.trimmed = False         # the current answer was cut short after max_sentences
        self.long_ok = False
        self.stt = stt
        self.stt_limited = False     # Groq's daily limit is used up (D-115): not offline, just heard on the Pi
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
        bus.core_listen("mic.clapped_trial", self.on_clapped_trial)
        bus.on_connect.append(self.on_program_connected)
        bus.on_subscribe.append(self.on_program_subscribed)

    # ---------------------------------------------------------------- helpers
    async def command(self, target, msg_type, data=None, private=None):
        await self.bus.route(Message("command", msg_type, source="core", target=target,
                                     data=data or {}, private=private or {}))

    # ---------------------------------------------------------------- Pip's state (for the dashboard and the face)
    def situation(self) -> tuple[str, str, float]:
        """(state, detail, ends_at): what the face and the dashboard show. The rules are in core/states.py."""
        return situation(self)

    def refresh_state(self) -> None:
        """Work out the state again and, if it changed, tell the dashboard and the face (`pip.state`).
        Also sets a timer to look again when the next deadline (listen_until, cut_until, …) passes."""
        now = time.monotonic()
        state, detail, ends_at = self.situation()
        old = (self.state, self.detail)
        if self._state_timer:
            self._state_timer.cancel()
            self._state_timer = None
        nxt = [t for t in (self.listen_until, self.offline_until, self.say_pending_until, self.cut_until,
                           self.room_until) if t > now]
        if nxt and _loop_running():
            self._state_timer = asyncio.get_running_loop().call_later(min(nxt) - now + 0.05, self.refresh_state)
        was_quiet, (self.state, self.detail) = self.quiet, (state, detail)
        self.quiet = states.quiet(self)
        if (state, detail, self.quiet) != (*old, was_quiet):
            if _loop_running():
                data = self.state_message(ends_at, now)
                asyncio.ensure_future(self.bus.publish(Message("event", "pip.state", source="core", data=data)))
                if self.prefs is not None and self.prefs.get("state_bars"):   # the feed's state bars (D-112)
                    asyncio.ensure_future(self.note("state", **data))

    def state_message(self, ends_at: float = 0.0, now: float = 0.0) -> dict:
        """What `pip.state` carries: {state, detail, words, ends_in (seconds left of a wait, or None), quiet (a
        good moment to restart his programs: states.quiet)}."""
        return {"state": self.state, "detail": self.detail, "words": DETAILS.get(self.detail, ""), "quiet": self.quiet,
                "ends_in": round(ends_at - now, 1) if ends_at > now else None}

    def listening_for(self, seconds: float, why: str = "called") -> None:
        """Listening for someone for a while. why: called (heard his name), follow_up (after answering),
        hearing (they've started talking to him), learning_voice."""
        self.listen_until = max(self.listen_until, time.monotonic() + seconds)
        self.listen_why = why
        self.refresh_state()

    async def say(self, text: str, reason: str) -> None:
        """Speak `text` through pip-ears. `reason` says why (answer, welcome, command, error, …); the face
        shows it while he talks (states.SPEAKING)."""
        if text:
            self.say_reason = reason
        if text and not self.speaking:
            self.say_pending_until = time.monotonic() + 3.0
        if text:
            await self.command("ears", "ears.say", {"reason": reason}, {"text": text})

    async def speak_reply(self, text: str, reason: str, t0: float | None = None, digits: int = 2) -> None:
        """Say a reply Pip made himself (a command's confirmation, a welcome back…): into the room transcript,
        spoken, and onto the dashboard's feed (with how long it took since `t0`, if given)."""
        self.said(text)
        self.speech_done.clear()
        await self.say(text, reason)
        shown = {"total_s": round(time.perf_counter() - t0, digits)} if t0 is not None else {}
        await self.show("pip", text, **shown, local=True)

    @property
    def listening(self) -> bool:
        return self.mic is None or self.mic.listening

    @property
    def ringing(self) -> bool:
        return bool(self.schedule and self.schedule.ringing)

    async def on_program_subscribed(self, name: str) -> None:
        if name == "web":   # the dashboard (re)started and is listening: tell it what Pip is doing now
            await self.bus.publish(Message("event", "pip.state", source="core", data=self.state_message()))

    # ---------------------------------------------------------------- events
    async def on_wake(self, msg: Message) -> None:
        if self.ringing:
            await self.schedule.acknowledge("name")  # saying his name answers an alarm (FEAT-037)
        self.answer_id = None  # a new request replaces anything in progress
        await self.command("thinker", "thinker.cancel")
        await self.chime()
        self.listening_for(8.0)

    async def on_started(self, msg: Message) -> None:
        """They started talking again in the window: keep waiting for the rest of a paused sentence."""
        self.last_started = time.monotonic()
        self.joining.someone_started()
        if msg.data.get("reason") == "follow_up":
            self.listening_for(12.0, "hearing")
        elif msg.data.get("reason") == "room":
            self.room_until = time.monotonic() + self.max_utterance
            self.refresh_state()
        if self.pending:   # they're still going: keep holding their turn (until that speech arrives, D-106)
            if self.pending_task:
                self.pending_task.cancel()
            self.pending_task = asyncio.create_task(self._answer_pending(wait=self.max_utterance + 4.0))

    async def on_interrupted(self, msg: Message) -> None:
        self.last_cut = time.monotonic()   # talked over: wait a bit longer for a while (D-106)
        self.cut_until = self.last_cut + 1.5   # the face shows it for a moment
        self.refresh_state()
        await self.note("interrupted")
        self.transcript.mark_cut()
        if self.answer_id:
            await self.command("thinker", "thinker.cancel", {"answer_id": self.answer_id})
            self.answer_id = None

    async def on_speech_started(self, msg: Message) -> None:
        self.speaking, self.say_pending_until = True, 0.0
        self.refresh_state()

    async def on_speech_finished(self, msg: Message) -> None:
        was_talking = self.speaking and msg.data.get("spoken", True)   # not just a chime or a tone
        self.speaking, self.say_pending_until = False, 0.0
        self.speech_done.set()
        if was_talking and self.answer_id is None and not msg.data.get("interrupted"):
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
            await self.note("joined")
        self.sentences += 1
        if self.sentences > self.max_sentences and not self.long_ok:
            self.trimmed = True
            if self.sentences == self.max_sentences + 1:
                await self.command("thinker", "thinker.cancel", {"answer_id": self.answer_id})
                await self.bus.publish(Message("event", "answer.trimmed", data={"after": self.max_sentences}))
            return  # roommates don't give speeches (D-79)
        self.speech_done.clear()
        self.spoken.append(text)
        self.filler_said = self.filler_said or "first sentence"  # he's answering: no longer "thinking"
        self.voice_kind = self.answer_kind   # what he's saying is an answer, or him joining in
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
            await self._finish_pending()
            return
        stt_s = round(time.perf_counter() - t0, 2)
        speaker = await ident if ident else self.speaker
        self.transcribing = False
        self.speaker, self.last_pcm = speaker, pcm
        asyncio.get_running_loop().call_soon(self.refresh_state)  # by then he's answering (thinking) or done
        await self.bus.publish(Message("event", "stt.done", data={"seconds": stt_s, "chars": len(text),
                                                                   "reason": reason, "offline": offline,
                                                                   "speaker": self.speaker.level if self.speaker else None}))
        if self.ringing and self.commands:
            reply = await self.commands.while_ringing(text, reason)
            if reply is not None:
                await self.say(reply, "alarm answered")
                await self._finish_pending()
                return
        if not text.strip(" .?!,"):
            if reason == "wake":
                await self.say(SAYINGS["not_caught"], "not caught")
            await self._finish_pending()   # it was only a breath or an "um": a held turn is answered now
            return
        text = self.fix_names(text)
        self.hear(text)
        await self.feed(text, "direct", self.speaker, stt_s=stt_s, offline=offline, reason=reason)
        if reason == "wake" and NOT_PIP.search(text) and not PIP_NAMES.search(text):
            await self.bus.publish(Message("event", "wake.rejected", data={"why": "transcript names Ziggletron"}))
            await self._finish_pending()
            return
        await self.addressed(text, reason, stt_s=stt_s, offline=offline, heard_s=msg.data.get("seconds") or 0)

    async def respond(self, text: str, reason: str, offline: bool = False, **data) -> None:
        """Their turn is over: answer it."""
        self.expect_until = 0.0
        if offline or self.own_commands == "always":
            # No Claude without the internet: Pip's own fixed-phrasing commands do the basics (D-83, D-94)
            if await self.people_command(text, offline):
                return
            if await self.local_command(text, self.speaker):
                return
        if offline:
            # Offline and not one of the basics: he stays quiet (no repeated "I'm offline" line, owner D-115).
            # The face and the dashboard show he's offline.
            await self.bus.publish(Message("event", "answer.offline", data={}))
            await self.note("offline_quiet")
            return
        await self.answer(text, reason)

    async def on_room(self, msg: Message) -> None:
        """Room speech: everything is transcribed and kept in the room transcript, like Ziggletron (D-92).
        Then: called by name or in the follow-up window → answer; name mentioned → Claude decides;
        otherwise → maybe join in after a pause."""
        if self.stt is None and not (self.ringing and self.local_stt):
            return
        pcm = base64.b64decode(msg.private.get("audio_b64", ""))
        if not await self.has_voice(pcm, "room"):
            await self._finish_pending()   # it was only noise: a held turn can be answered now
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
        if not text.strip(" .?!,"):
            await self._finish_pending()
            return
        text = self.fix_names(text)
        quiet_for = time.time() - self.last_voice_at
        self.hear(text, speaker)
        expected = time.monotonic() < self.expect_until or bool(self.pending)   # more of a held turn
        how = "direct" if expected or calls_pip(text) else "mentioned" if names_pip(text) else "room"
        await self.feed(text, how, speaker, offline=offline)
        await self.bus.publish(Message("event", "room.heard", data={"seconds": msg.data.get("seconds"),
                                                                     "addressed": how != "room", "how": how,
                                                                     "speaker": speaker.level if speaker else None}))
        if how == "room":
            self.room_until = time.monotonic() + 4.0   # following the conversation (D-112)
            self.refresh_state()
            if not await self.welcome_back(speaker, quiet_for):
                self.joining.heard()
            return
        self.joining.cancel_wait()
        if how == "mentioned":
            if self.answer_id is None and not self.speaking and not offline:
                self.speaker, self.last_pcm = speaker, pcm
                await self.answer(text, "mentioned", kind="mentioned")
            return
        self.speaker, self.last_pcm = speaker, pcm
        if not expected:
            self.answer_id = None
            await self.command("thinker", "thinker.cancel")
        await self.addressed(text, "expected" if expected else "transcript", offline=offline, chime=not expected,
                             heard_s=msg.data.get("seconds") or 0)

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
        await self.speak_reply(line, "welcome")
        return True

    # ---------------------------------------------------------------- answering
    async def answer(self, text: str, reason: str, kind: str = "direct", mood: str = "", looked_up: str = "",
                     asker: tuple | None = None) -> None:
        """Ask Claude. kind: "direct" (said to Pip), "mentioned" (his name came up: he may PASS) or "join"
        (nobody spoke to him: he may join in with "SAY: …" or PASS). Claude sees the room transcript.
        `looked_up`: what Claude asked Pip to look up first (ACTION look), for the second round.
        `asker`: (speaker, audio) of who asked. It's kept with this answer, so if someone else talks while
        Claude works, the actions and look-ups are still done as the person who asked."""
        asker = asker or (self.speaker, self.last_pcm)
        aid = new_id()
        self.answer_id, self.answer_kind, self.join_spoke = aid, kind, False
        self.join_count = self.transcript.count
        self.sentences, self.spoken, self.trimmed = 0, [], False   # this answer's sentences actually said
        self.long_ok = bool(WANTS_DETAIL.search(text or ""))
        self.filler_said = None
        self.looking_up = bool(looked_up)
        self.refresh_state()  # thinking (direct answers only)
        filler = asyncio.create_task(self._thinking_tone(aid)) if kind == "direct" and not looked_up else None
        t0 = time.perf_counter()
        try:
            told = self.for_claude(asker[0] if kind != "join" else None, kind)   # core/context.py
            reply = await self.bus_request("thinker", "thinker.ask", {"answer_id": aid, "reason": reason, "kind": kind},
                                           {"text": text or "", "transcript": self.transcript.render(), "mood": mood,
                                            **told, "looked_up": looked_up}, timeout=60)
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
                if text and (await self.people_command(text, asker=asker) or await self.local_command(text, asker[0])):
                    return
                await self.say(SAYINGS["thinker_failed"], "error")
            return
        if reply.private.get("action") and self.commands:
            if kind == "join":
                return  # joining in never acts on anything (nothing overheard is acted on)
            return await self.claude_action(reply.private["action"], t0,
                                            again=None if looked_up else (text, reason, kind, mood), asker=asker)
        answer = reply.private.get("text", "")
        if self.trimmed:   # cut short (D-79): the transcript and the dashboard get only what he really said
            answer = " ".join(self.spoken)
        if not answer:  # PASS: he decided it wasn't for him / nothing to add
            await self.bus.publish(Message("event", "answer.passed", data={"kind": kind}))
            if kind in ("direct", "mentioned"):
                await self.note("passed", about=kind)
            if kind == "direct":   # e.g. "shut up" / "never mind": quiet, and stop listening for more (D-105)
                await self.go_quiet()
            return
        self.said(answer)
        await self.show("pip", answer, total_s=round(time.perf_counter() - t0, 2),
                        first_s=reply.data.get("first_sentence_s"), kind=kind)
        if self.speech_done.is_set():
            await self._maybe_follow_up()

    async def go_quiet(self) -> None:
        """Told to stop or be quiet: stop talking, close the follow-up window, back to idle."""
        self.expect_until = self.listen_until = 0.0
        await self.command("ears", "ears.stop")
        await self.command("ears", "ears.listen", {"seconds": 0})
        self.refresh_state()

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

    async def _maybe_follow_up(self) -> None:
        last = self.transcript.last()
        if self.follow_up_s > 0 and last is not None and last["ours"] and not self.ringing and self.listening:
            secs = self.follow_up_s * (1.5 if last["text"].rstrip().endswith("?") else 1.0)  # he asked something
            self.expect_until = time.monotonic() + secs
            await self.command("ears", "ears.listen", {"seconds": secs})
            self.listening_for(secs, "follow_up")

    async def bus_request(self, target, msg_type, data, private, timeout):
        """Ask another program and wait for its reply (Bus.request). One place, so tests can stand in for it."""
        return await self.bus.request(target, msg_type, data, private, timeout)
