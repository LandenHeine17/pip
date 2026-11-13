"""Knowing when someone has finished talking to Pip (D-106, timed as one budget since D-122).

Part of the conversation (core/conversation.py): `Conversation` takes these methods in as `TurnTaking`, so
they read and set its fields like its own. What's said to Pip is held (`pending`) for a moment in case
they're not finished; if they keep talking the pieces are joined, and once nothing more comes it's
answered (`respond`).

How long he waits is a budget **counted from when they stopped talking** (D-122). The pause pip-ears already
waited before sending the speech (0.7 s, its `quiet_s`) and the time speech-to-text took both count towards
it, so he only holds for what's left (`hold`). Before D-122 the wait started after speech-to-text, so every
request paid the pause twice (the owner's spec, 2026-11-12: 1.55 s typical hold, 5.4 s to answer).

    sounds unfinished ("what's the...", "remind me to")   unfinished_wait_seconds    (3.0 s)
    two sentences or more, or a long stretch              turn_wait_long_seconds     (2.0 s)
    a question, or 8 words or fewer                       turn_wait_short_seconds    (1.0 s)
    anything else                                         turn_wait_statement_seconds (1.6 s)
    (plus turn_wait_after_interrupt_seconds for the next turn after someone talked over him)

The face shows "Waiting to see if you're done" with a countdown while a turn is held (`turn_until`). Each
answered turn is traced as `turn.decision` (decision "answered", why, how long since they stopped talking):
tools/latency.py reads those.
"""
import asyncio
import re
import time

from ..common.messages import Message
from .names import only_name

# Words a sentence doesn't end on: "what's the…", "remind me to…", "set a timer for…", "it's at the…", "um"
DANGLING = re.compile(r"\b(the|a|an|and|or|but|of|my|your|his|her|their|what's|whats|to|for|with|at|about|from|in|"
                      r"on|into|is|are|was|that|than|if|because|so|then|um+|uh+|er+|like)\W*$", re.I)
# Short replies that only show someone's listening: talking over Pip with one of these isn't asking him to stop
BACKCHANNEL = re.compile(r"^(yeah|yep|yes|yup|mhm+|mm+|hm+|uh[ -]?huh|okay|ok|right|sure|cool|nice|wow|oh|ah|"
                         r"ha(ha)+|lol|true|got it|i see)( (yeah|okay|ok|right|sure))?$", re.I)
# Laughing, as speech-to-text writes it: "haha", "Ha ha ha.", "hehe", "lol"
LAUGH = re.compile(r"\b(?:(?:ha|he|ho)h?){2,}\b|\b(?:ha|hah|heh|lol|lmao|lmfao)\b", re.I)
# What it writes for a sound that isn't words: "(laughs)", "[Laughter]", "*chuckles*", "♪"
SOUND_NOTE = re.compile(r"[\[(*♪][^\])*♪]*[\])*♪]?")
# Its phantom words for a sound without words in it (D-85). A laugh, a cough or a chair often comes back as
# "Thank you." (2026-11-12: 1 in 10 of everything transcribed was exactly 10 letters long, mostly from no known voice)
PHANTOM = re.compile(r"^(thank you( (so|very) much)?( for watching)?|thanks for watching|you|bye|bye bye)$")
# After someone talks over him, how long he waits (on top of the longest they could talk, max_utterance_seconds)
# for speech-to-text to say what it was, before giving the turn up as theirs
RESUME_WITHIN_S = 4.0


def looks_unfinished(text: str) -> bool:
    """'Pippletron, what's the...' or 'remind me to': they paused mid-sentence and are about to say more.
    A question mark wins: 'what's that for?' is finished."""
    t = text.strip()
    if t.endswith("?"):
        return False
    return t.endswith(("...", "…", ",", "-")) or bool(DANGLING.search(t.rstrip(".")))


def is_backchannel(text: str) -> bool:
    """'yeah', 'mhm', 'okay', a laugh: listening along, not taking the turn (CB-020)."""
    return listening_only(text) != ""


def is_phantom(text: str) -> bool:
    """Is this only what speech-to-text writes for a sound without words ("Thank you.", "you", "Bye.")?"""
    return bool(PHANTOM.match(" ".join(re.sub(r"[^\w' ]", " ", text).lower().split())))


def listening_only(text: str) -> str:
    """What talking over Pip was, if it wasn't taking the turn: "laugh" ("haha", "(laughs)", the phantom
    "Thank you." Whisper writes for a laugh), "backchannel" ("yeah", "mhm", "haha okay"), or "" for real words.
    A laugh has a voice in it, so pip-ears can't tell it from speech and stops him; this is how he knows to carry on."""
    t = SOUND_NOTE.sub(" ", text)
    t = re.sub(r"[^\w' -]", " ", t).lower()
    t = " ".join(t.replace("-", " ").split())
    if not t or PHANTOM.match(t):
        return "laugh"
    rest = " ".join(LAUGH.sub(" ", t).split())
    if not rest:
        return "laugh"
    return "backchannel" if BACKCHANNEL.match(rest) else ""


class TurnTaking:
    async def addressed(self, text: str, reason: str, offline: bool = False, chime: bool = False, **data) -> None:
        """Someone said something to Pip. Hold it until they've finished their turn (D-106): if they keep
        talking, the pieces are joined. Then chime (if they called him by name) and answer.
        data["ended_at"]: when they stopped talking (time.monotonic()); the wait counts from then (D-122)."""
        ended = data.pop("ended_at", None) or time.monotonic()
        merged = bool(self.pending)
        if self.pending:
            text = f"{self.pending} {text}"
            self.pending = ""
            chime = chime or self.pending_how[3]
            data["kind"] = self.pending_how[2].get("kind") or data.get("kind")   # the turn is still who it was for
        if self.pending_task:
            self.pending_task.cancel()
            self.pending_task = None
        if only_name(text):
            # Just the name (often followed by a pause): the chime says he's listening. Don't talk over them.
            if chime:
                await self.chime()
            self.expect_until = time.monotonic() + self.wait_after_name
            await self.command("ears", "ears.listen", {"seconds": self.wait_after_name})
            self.listening_for(self.wait_after_name)
            return
        why = self.turn_kind(text, float(data.pop("heard_s", 0) or 0))
        budget = self.turn_wait(text, why=why)
        wait = round(max(0.0, budget - (time.monotonic() - ended)), 2)
        if wait > 1.0:
            await self.note("waiting", seconds=round(wait, 1))
        if merged:
            await self.decided("held_more_speech", why)
        elif why == "unfinished":
            await self.decided("waited_unfinished", why, wait_s=wait)
        self.pending, self.pending_how = text, (reason, offline, data, chime)
        self.pending_ended, self.pending_why = ended, why
        self.turn_until = time.monotonic() + wait
        self.refresh_state()
        self.expect_until = time.monotonic() + wait + 1.0
        await self.command("ears", "ears.listen", {"seconds": wait + 0.5})   # more speech now = the same turn
        if self.last_started > ended:
            # They started talking again while this was with speech-to-text (heard.started isn't held up behind it,
            # D-122): keep holding until that speech arrives, as on_started does.
            wait = self.max_utterance + 4.0
        self.pending_task = asyncio.create_task(self._answer_pending(wait=wait))

    def turn_kind(self, text: str, heard_s: float = 0.0) -> str:
        """What kind of turn it sounds like: unfinished / long / short / statement."""
        t = text.strip()
        if looks_unfinished(t):
            return "unfinished"
        if len(re.findall(r"[.!?](?:\s|$)", t)) >= 2 or heard_s >= self.max_utterance - 0.5:
            return "long"
        if t.endswith("?") or len(t.split()) <= 8:
            return "short"
        return "statement"

    def turn_wait(self, text: str, heard_s: float = 0.0, why: str = "") -> float:
        """How long after they stop talking before taking it that they've finished (D-106, D-122): the whole
        budget, counted from the end of their speech."""
        why = why or self.turn_kind(text, heard_s)
        wait = {"unfinished": self.unfinished_wait, "long": self.turn_long, "short": self.turn_short,
                "statement": self.turn_statement}[why]
        if time.monotonic() - self.last_cut < 60:
            wait += self.turn_after_cut   # just talked over: he takes the hint, for this turn only
        return wait

    async def decided(self, decision: str, why: str = "", **data) -> None:
        """A turn-taking decision, for the trace (labels and times only, never words): tools/latency.py."""
        await self.bus.publish(Message("event", "turn.decision", data={"decision": decision, "why": why, **data}))

    async def _finish_pending(self) -> None:
        """They seemed to start talking again, but it was only noise: answer the held turn now."""
        if self.pending:
            if self.pending_task:
                self.pending_task.cancel()
            self.pending_task = asyncio.create_task(self._answer_pending(wait=0.0))

    async def _answer_pending(self, wait: float) -> None:
        self.refresh_state()
        await asyncio.sleep(wait)  # nothing more came: their turn is over
        text, self.pending, self.pending_task = self.pending, "", None
        reason, offline, data, chime = self.pending_how
        if text:
            self.last_cut = -1e9   # the after-interrupt wait was for this turn only (D-122)
            if chime:
                await self.chime()
            self.turn_since_speech = round(time.monotonic() - self.pending_ended, 2)
            await self.respond(text, reason, offline=offline, **data)
        # No longer "waiting to see if you're done", even if nothing was said back ("never mind" offline).
        # (Not before respond(): it would flash idle before thinking.)
        self.refresh_state()
