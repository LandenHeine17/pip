"""The conversation part of the conductor (M1a step 3).

    wake word / follow-up / interruption
      → pip-ears sends the audio of what was said (heard.utterance)
      → speech-to-text (Groq)
      → "was that really for Pip?" check (e.g. it was "Ziggletron")
      → one of Pip's own commands (timers, alarms, reminders, calendar, time)? Done on the Pi (step 4)
      → otherwise pip-thinker asks Claude, streaming the answer sentence by sentence
      → each sentence goes to pip-ears to be spoken
      → afterwards, a short follow-up window: talk again without the name

What people say and what Pip answers travel only in messages' `private` part, so they are never
written to the decision trace (D-34). The conversation itself is kept in memory only, for a few
minutes, so follow-up questions make sense (PIP-NEW-028 category 5).

Offline (Groq can't be reached): Whisper on the Pi transcribes instead, and only Pip's own commands
work; anything else gets "I'm offline, so I can only do the basics right now" (PIP-NEW-044, D-83).
While an alarm rings, everything heard is checked for "stop", "okay", "snooze" or Pip's name first.
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


class Conversation:
    def __init__(self, bus, cfg: dict | None = None, stt=None, schedule=None, local_stt=None):
        from .commands import Commands
        self.bus = bus
        self.schedule = schedule
        self.commands = Commands(schedule, clock=schedule.clock) if schedule is not None else None
        self.local_stt = local_stt
        c = (cfg or {}).get("conversation", {})
        self.follow_up_s = c.get("follow_up_seconds", 8.0)
        self.filler_after = (cfg or {}).get("thinker", {}).get("filler_after_seconds", 3.0)
        self.filler_said = None
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
        bus.core_listen("answer.sentence", self.on_sentence)
        bus.core_listen("answer.searching", self.on_searching)

    # ---------------------------------------------------------------- helpers
    async def command(self, target, msg_type, data=None, private=None):
        await self.bus.route(Message("command", msg_type, source="core", target=target,
                                     data=data or {}, private=private or {}))

    async def say(self, text: str, reason: str) -> None:
        if text:
            await self.command("ears", "ears.say", {"reason": reason}, {"text": text})

    async def show(self, who: str, text: str, **data) -> None:
        """Live conversation feed for the dashboard (private: shown, never logged)."""
        await self.bus.publish(Message("event", "conversation.line", source="core",
                                       data={"who": who, **data}, private={"text": text}))

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
            return await self.local_stt.transcribe(pcm), True

    # ---------------------------------------------------------------- events
    async def on_wake(self, msg: Message) -> None:
        if self.ringing:
            await self.schedule.acknowledge("name")  # saying his name answers an alarm (FEAT-037)
        self.answer_id = None  # a new request replaces anything in progress
        await self.command("thinker", "thinker.cancel")
        await self.command("ears", "ears.chime")

    async def on_started(self, msg: Message) -> None:
        """They started talking again in the window: keep waiting for the rest of a paused sentence."""
        if self.pending_task and msg.data.get("reason") == "follow_up":
            self.pending_task.cancel()
            self.pending_task = None

    async def on_interrupted(self, msg: Message) -> None:
        if self.answer_id:
            await self.command("thinker", "thinker.cancel", {"answer_id": self.answer_id})
            self.answer_id = None

    async def on_speech_finished(self, msg: Message) -> None:
        self.speech_done.set()
        if self.answer_id is None and not msg.data.get("interrupted"):
            await self._maybe_follow_up()

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
        self.filler_said = self.filler_said or "first sentence"  # too late for a filler now
        await self.say(text, "answer")

    async def on_searching(self, msg: Message) -> None:
        if msg.data.get("answer_id") == self.answer_id and not self.filler_said:
            self.filler_said = "searching"
            await self.say("Let me look that up.", "searching")

    async def _filler(self, aid: str) -> None:
        await asyncio.sleep(self.filler_after)
        if self.answer_id == aid and not self.filler_said:
            self.filler_said = "slow"
            await self.say("One sec.", "slow answer")

    async def on_utterance(self, msg: Message) -> None:
        reason = msg.data.get("reason")
        if not msg.data.get("had_speech"):
            return
        if reason == "room":
            return await self.on_room(msg)
        if self.stt is None and self.local_stt is None:
            if reason == "wake":
                await self.say(SAYINGS["stt_failed"], "error")
            return
        pcm = base64.b64decode(msg.private.get("audio_b64", ""))
        t0 = time.perf_counter()
        try:
            text, offline = await self.transcribe(pcm, reason)
        except Exception as e:
            log.warning("speech-to-text failed: %s", e)
            await self.bus.publish(Message("event", "stt.failed", data={"error": type(e).__name__}))
            if reason == "wake":
                await self.say(SAYINGS["stt_failed"], "error")
            return
        stt_s = round(time.perf_counter() - t0, 2)
        await self.bus.publish(Message("event", "stt.done", data={"seconds": stt_s, "chars": len(text),
                                                                   "reason": reason, "offline": offline}))
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
            return
        if looks_unfinished(text):
            self.pending = text
            self.expect_until = time.monotonic() + self.unfinished_wait + 1.0
            await self.command("ears", "ears.listen", {"seconds": self.unfinished_wait})
            self.pending_task = asyncio.create_task(self._answer_pending(reason))
            return
        self.expect_until = 0.0
        await self.show("you", text, reason=reason, offline=offline, **data)
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

    async def _answer_pending(self, reason: str) -> None:
        await asyncio.sleep(self.unfinished_wait + 1.0)  # nothing more came: answer what we have
        text, self.pending, self.pending_task = self.pending, "", None
        if text:
            await self.show("you", text, reason=reason)
            await self.answer(text, reason)

    async def on_room(self, msg: Message) -> None:
        """Room speech: transcribe it and check whether it called Pip (the wake detector may have missed it)."""
        if self.stt is None and not (self.ringing and self.local_stt):
            return
        try:
            text, offline = await self.transcribe(base64.b64decode(msg.private.get("audio_b64", "")), "room")
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
        await self.addressed(text, "expected" if expected else "transcript", offline=offline)

    # ---------------------------------------------------------------- answering
    async def answer(self, text: str, reason: str) -> None:
        aid = new_id()
        self.answer_id = aid
        self.sentences = 0
        self.long_ok = bool(WANTS_DETAIL.search(text))
        self.filler_said = None
        filler = asyncio.create_task(self._filler(aid))
        context = self._context()
        self.history.append({"role": "user", "text": text, "ts": time.time()})
        t0 = time.perf_counter()
        try:
            schedule = self.schedule.summary() if self.schedule else ""
            reply = await self.bus_request("thinker", "thinker.ask", {"answer_id": aid, "reason": reason},
                                           {"text": text, "context": context, "schedule": schedule}, timeout=60)
        except Exception as e:
            reply = Message("reply", "thinker.ask", data={"error": f"{type(e).__name__}"})
        filler.cancel()
        if self.answer_id != aid:
            return  # cancelled meanwhile
        self.answer_id = None
        if reply.data.get("error"):
            await self.bus.publish(Message("event", "answer.failed", data={"error": reply.data["error"]}))
            await self.say(SAYINGS["thinker_failed"], "error")
            return
        answer = reply.private.get("text", "")
        self.history.append({"role": "pip", "text": answer, "ts": time.time()})
        await self.show("pip", answer, total_s=round(time.perf_counter() - t0, 2),
                        first_s=reply.data.get("first_sentence_s"))
        if self.speech_done.is_set():
            await self._maybe_follow_up()

    async def _maybe_follow_up(self) -> None:
        if self.follow_up_s > 0 and self.history and self.history[-1]["role"] == "pip" and not self.ringing:
            await self.command("ears", "ears.listen", {"seconds": self.follow_up_s})

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
