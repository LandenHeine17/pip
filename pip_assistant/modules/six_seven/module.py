"""The six-seven module (D-35, D-121, D-142): when someone says six-seven, Pip freaks out, at any hour: he chants
it, his face on the monitor freaks out (the hands), and the lamps flash on and off like a disco.

It uses one part only, `room_heard(text, call)`: pip-core offers everything heard, room talk and what's said to
Pip alike, whatever he's doing (idle, listening for a reply, thinking, talking), so it's all done here on the Pi
at once, never waiting for Claude. The rule (D-142): a six straight followed by a seven, anywhere in a sentence
("six seven", "6-7", "67", "six, seven", "6'7"; not "6:07", "167", "6.7", "6/7", "sixty seven" or "seven six").

What happens (`room_heard`):
  - the face freaks out (it asks pip-web for the "sixseven" reaction in web/static/tv.html) and the lamps flash
    (`disco`, through the home_control module's api, if it's on), at once, whatever Pip is doing;
  - Pip chants if he's free to talk (pip-core decides). The chant is a `Done`: Claude is never asked about it,
    even when he's too busy to chant. Only a request to him (by name, or typed in the chat box: "Pip, set a timer
    for 67 minutes") gets no chant, so the request goes on as usual (the face and the lamps still freak out). A reply without his
    name right after he spoke ("bro that's so 67") gets the chant: it's the room talking, not a request.
Then it rests `pause_seconds` (module.toml; 0, every time, by default). It reads the words and forgets them:
nothing is stored or logged.
"""
import asyncio
import logging
import re

from pip_assistant.core.modules import Call, Done, Module

log = logging.getLogger(__name__)

CHANT = "six seven, six seven, six fucking seven"
# A six straight followed by a seven. Between them: nothing ("67"), spaces, or the marks speech-to-text writes for
# a little pause ("six, seven", "6-7", "6...7", "6'7"). Not part of a bigger number or a time ("167", "6:07",
# "6.7", "67.5", "6/7"), and whole words ("sixty seven" and "seventy" don't count).
TOGETHER = re.compile(r"(?<![\d.:,/])\b(?:six|6(?![.,]\d))(?:\s*[,.!?…'’\"\-–—]+\s*|\s*)(?:seven|7)\b(?![.:,/]\d)", re.I)
# Words that don't make it a request: his name, greetings, laughs, "say".
FILLER = re.compile(r"\b(?:pip|pipp|pips|pippy|pippie|pippletron|hey|hi|yo|oi|oh|ok|okay|so|say|it's|its|is|that's|"
                    r"thats|bro|bruh|dude|man|guys|ha|haha+|lol|lmao|yeah|yes|yay|and|um+|uh+|like|go|again)\b", re.I)


def heard(text: str) -> bool:
    """Does this set it off? A six straight followed by a seven, anywhere (D-142)."""
    return bool(TOGETHER.search(text or ""))


def only_six_seven(text: str) -> bool:
    """Is that all they said ("Pip, six seven!", "yo, 6 7 haha")? Then the chant answers it. Anything more is a
    request ("set a timer for 67 minutes") for Pip's usual answer."""
    rest = FILLER.sub(" ", TOGETHER.sub(" ", text or ""))
    return not re.sub(r"[\W_]+", "", rest)


class SixSeven(Module):
    def __init__(self, ctx):
        self.ctx = ctx
        self.quiet_until = 0.0

    def room_heard(self, text: str, call: Call) -> str | None:
        now = self.ctx.clock()
        if now < self.quiet_until or not heard(text):
            return None
        self.quiet_until = now + float(self.ctx.settings["pause_seconds"])
        self.freak_out()
        if call.kind == "direct" and not call.follow_up and not only_six_seven(text):
            return None   # a request to him (by name, or typed): the face and the lamps freak out, and it's answered as usual
        return Done(CHANT)   # said to Pip, it's his whole answer: never Claude's, even when he can't chant right now

    def freak_out(self) -> None:
        """The face freaks out on the monitor and the lamps flash, at the same moment (both run in the background)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:   # no loop running (tests)
            return
        loop.create_task(self.ctx.publish("reaction", {"reaction": "sixseven"}))
        seconds = float(self.ctx.settings["disco_seconds"])
        home = self.ctx.module("home_control") if seconds > 0 else None   # None when home control is off
        if home is not None:
            try:
                home.disco(seconds)
            except Exception:
                log.exception("the lamps' disco didn't start")


def setup(ctx) -> Module:
    return SixSeven(ctx)
