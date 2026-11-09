"""Knowing when someone has finished talking to Pip (D-106).

Part of the conversation (core/conversation.py): `Conversation` takes these methods in as `TurnTaking`, so
they read and set its fields like its own. What's said to Pip is held (`pending`) for a moment in case
they're not finished; if they keep talking the pieces are joined, and once nothing more comes it's
answered (`respond`). How long he waits (`turn_wait`) depends on what was said:

    sounds unfinished ("what's the...")        unfinished_wait + 1 s
    two sentences or more, or a long stretch   turn_wait_long_seconds
    a question, or 8 words or fewer            turn_wait_short_seconds
    anything else                              turn_wait_statement_seconds
    (plus turn_wait_after_interrupt_seconds for a minute after someone talked over him)

The face shows "Waiting to see if you're done" with a countdown while a turn is held (`turn_until`).
"""
import asyncio
import re
import time

from .names import only_name

DANGLING = re.compile(r"\b(the|a|an|and|or|but|of|my|your|his|her|their|what's|whats|um+|uh+)\W*$", re.I)


def looks_unfinished(text: str) -> bool:
    """'Pippletron, what's the...' : they paused mid-sentence and are about to say more."""
    t = text.strip()
    return t.endswith(("...", "…", ",", "-")) or bool(DANGLING.search(t.rstrip(".")) and not t.endswith("?"))


class TurnTaking:
    async def addressed(self, text: str, reason: str, offline: bool = False, chime: bool = False, **data) -> None:
        """Someone said something to Pip. Hold it until they've finished their turn (D-106): if they keep
        talking, the pieces are joined. Then chime (if they called him by name) and answer."""
        if self.pending:
            text = f"{self.pending} {text}"
            self.pending = ""
            chime = chime or self.pending_how[3]
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
        wait = self.turn_wait(text, float(data.pop("heard_s", 0) or 0))
        if wait > 1.0:
            await self.note("waiting", seconds=round(wait, 1))
        self.pending, self.pending_how = text, (reason, offline, data, chime)
        self.turn_until = time.monotonic() + wait
        self.refresh_state()
        self.expect_until = time.monotonic() + wait + 1.0
        await self.command("ears", "ears.listen", {"seconds": wait + 0.5})   # more speech now = the same turn
        self.pending_task = asyncio.create_task(self._answer_pending(wait=wait))

    def turn_wait(self, text: str, heard_s: float = 0.0) -> float:
        """How long to wait, after they pause, before taking it that they've finished (D-106)."""
        t = text.strip()
        if looks_unfinished(t):
            wait = self.unfinished_wait + 1.0
        elif len(re.findall(r"[.!?](?:\s|$)", t)) >= 2 or heard_s >= self.max_utterance - 0.5:
            wait = self.turn_long
        elif t.endswith("?") or len(t.split()) <= 8:
            wait = self.turn_short
        else:
            wait = self.turn_statement
        if time.monotonic() - self.last_cut < 60:
            wait += self.turn_after_cut
        return wait

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
            if chime:
                await self.chime()
            await self.respond(text, reason, offline=offline, **data)
        # No longer "waiting to see if you're done", even if nothing was said back ("never mind" offline).
        # (Not before respond(): it would flash idle before thinking.)
        self.refresh_state()
