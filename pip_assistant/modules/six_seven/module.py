"""The six-seven module (D-35, D-121): when someone says six-seven, Pip chants it and his face on the monitor
freaks out, at any hour.

It uses one part only, `room_heard(text, call)`: pip-core offers everything heard, room talk and what's said to
Pip alike. The rule:
  - said to Pip (or about him): "six seven" together ("six seven", "6-7", "67"; not "6:07", "167" or "6.7"),
    so ordinary requests like "a timer for 6 minutes and 7 seconds" still reach Claude. Pip chants instead of
    asking Claude.
  - room talk: a six and a seven anywhere in the sentence ("it was seven, then six").
Either way the face reacts (it asks pip-web for the "sixseven" reaction in web/static/tv.html) and Pip chants
if he's free to talk. Then it rests `pause_seconds` (module.toml). It reads the words and forgets them: nothing
is stored or logged.
"""
import asyncio
import re

from pip_assistant.core.modules import Call, Module

CHANT = "six seven, six seven, six fucking seven"
TOGETHER = re.compile(r"(?<![\d.:])\b(?:six[\s,.!?\-–—]*seven|6[ \-–—]?7)\b(?![.:]\d)", re.I)
SIX = re.compile(r"\b(six|6)\b", re.I)
SEVEN = re.compile(r"\b(seven|7)\b", re.I)


def heard(text: str, kind: str = "room") -> bool:
    """Does this set it off? Said to Pip: six-seven together. Room talk: a six and a seven anywhere."""
    if TOGETHER.search(text or ""):
        return True
    return kind == "room" and bool(SIX.search(text or "") and SEVEN.search(text or ""))


class SixSeven(Module):
    def __init__(self, ctx):
        self.ctx = ctx
        self.quiet_until = 0.0

    def room_heard(self, text: str, call: Call) -> str | None:
        now = self.ctx.clock()
        if now < self.quiet_until or not heard(text, call.kind):
            return None
        self.quiet_until = now + float(self.ctx.settings["pause_seconds"])
        try:   # the face freaks out on the monitor, at the same moment
            asyncio.get_running_loop().create_task(self.ctx.publish("reaction", {"reaction": "sixseven"}))
        except RuntimeError:   # no loop running (tests)
            pass
        return CHANT


def setup(ctx) -> Module:
    return SixSeven(ctx)
