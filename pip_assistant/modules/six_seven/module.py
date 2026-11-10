"""The six-seven module: when a six and a seven both come up in something said in the room, Pip chants.

It uses one part only, `room_heard(text, call)`: pip-core offers every sentence that wasn't said to Pip, and the
module may answer with something for Pip to say. It reads the words and forgets them: nothing is stored or logged.
After a chant it stays quiet for `pause_seconds` (module.toml). Quiet hours and "is Pip free to talk" are pip-core's
business (core/conversation.py asks only when he may speak).
"""
import re

from pip_assistant.core.modules import Call, Module

CHANT = "six seven, six seven, six fucking seven"
SIX = re.compile(r"\b(six|6)\b", re.I)
SEVEN = re.compile(r"\b(seven|7)\b", re.I)
SIX_SEVEN = re.compile(r"\b67\b")          # speech-to-text often writes the meme as "67"


def mentions_six_and_seven(text: str) -> bool:
    return bool(SIX_SEVEN.search(text) or (SIX.search(text) and SEVEN.search(text)))


class SixSeven(Module):
    def __init__(self, ctx):
        self.ctx = ctx
        self.quiet_until = 0.0

    def room_heard(self, text: str, call: Call) -> str | None:
        now = self.ctx.clock()
        if now < self.quiet_until or not mentions_six_and_seven(text):
            return None
        self.quiet_until = now + float(self.ctx.settings["pause_seconds"])
        return CHANT


def setup(ctx) -> Module:
    return SixSeven(ctx)
