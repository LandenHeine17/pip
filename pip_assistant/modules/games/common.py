"""What every game shares: hearing an answer in what was said, who said it, and the base class of a game.

Speech-to-text writes answers loosely ("bee" for B, "the weekend" for The Weeknd), so answers are compared
plainly (lower case, no punctuation, no "Pip", no "I think it's") and a little fuzzily (`mentions`)."""
import re
from difflib import SequenceMatcher

NAME = re.compile(r"\b(?:hey |hi |ok |okay )?(?:pip|pipp|pips|pippy|pippie)\b")
# Said before an answer without changing it: "I think it's B", "is it Canberra", "the answer is D"
LEAD = re.compile(r"^(?:(?:hey|ok|okay|um+|uh+|erm|so|well|oh|i think|i guess|i know|maybe|probably|is it|isnt it|"
                  r"it s|its|it is|is that|thats|that s|the answer is|my answer is|answer|letter|option|i ll say|"
                  r"ill say|i say|go with|i ll go with|ill go with|i choose|definitely|easy|obviously|ooh|oh oh|"
                  r"wait|hmm+)\s+)+")
TRAIL = re.compile(r"(?:\s+(?:please|final answer|for sure|right|maybe|i think|probably|obviously))+$")
# Controls, said to Pip during any game
STOP = re.compile(r"^(?:stop|end|quit|cancel|finish)(?: the| this)? game$|^game over$|^(?:i m|im|we re|were) done playing$|"
                  r"^(?:stop|quit) playing$")
SKIP = re.compile(r"^(?:skip(?: it| this one| that one| the question)?|next(?: one| question| song| round)?|pass|"
                  r"(?:i|we) give up|give up|another one|new one|ask (?:another|a new) one|i don t know|i dont know)$")
REPEAT = re.compile(r"^(?:repeat(?: that| the question| it)?|say (?:that|it) again|what was the question|"
                    r"(?:can you )?(?:read|say) (?:the|that) question again|again|one more time)$")
REPLAY = re.compile(r"^(?:play (?:it|that) again|replay(?: it)?|again|one more time|play (?:it|that) one more time|"
                    r"play the song again)$")
SCORE = re.compile(r"^(?:what s|whats|what is) the score$|^(?:the )?score$|^who s winning$|^whos winning$|"
                   r"^how many (?:do i have|points do i have)$")


def plain(text: str) -> str:
    """Lower case, no apostrophes or punctuation, no "Pip": how answers are compared."""
    t = (text or "").lower().replace("’", "'").replace("'", " ").replace("&", " and ")
    t = NAME.sub(" ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def bare(text: str) -> str:
    """The answer itself: "Pip, I think it's B!" → "b"."""
    t = LEAD.sub("", plain(text)).strip()
    return TRAIL.sub("", t).strip()


def control(text: str) -> str | None:
    """A control said during a game: "stop", "skip", "repeat", "replay", "score", or None."""
    t = bare(text)
    for name, pattern in (("stop", STOP), ("replay", REPLAY), ("repeat", REPEAT), ("skip", SKIP), ("score", SCORE)):
        if pattern.search(t):
            return name
    return None


def mentions(said: str, target: str, close: float = 0.8) -> bool:
    """Does what was said contain `target` (both plain), allowing for a slightly wrong spelling? Whole words only;
    very short targets ("a", "u2") must match exactly."""
    said, target = plain(said), plain(target)
    if not said or not target:
        return False
    if re.search(rf"(?:^| ){re.escape(target)}(?: |$)", said):
        return True
    if len(target) < 4:
        return False
    words, n = said.split(), len(target.split())
    for size in {max(1, n - 1), n, n + 1}:
        for i in range(0, max(1, len(words) - size + 1)):
            if SequenceMatcher(None, " ".join(words[i:i + size]), target).ratio() >= close:
                return True
    return False


def who(call) -> str:
    """Who scores: the person, if Pip is sure who's speaking; else "Guest"."""
    return call.speaker.name if call is not None and call.person_id and call.speaker.name else "Guest"


def join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


class Game:
    """One game being played. The module (module.py) keeps at most one, and hands it what's heard."""
    kind = ""
    name = ""          # how Pip says it ("trivia")
    scored = True      # has a winner (would you rather hasn't)

    def __init__(self):
        self.scores: dict[str, int] = {}
        self.over = False
        self.last: dict | None = None   # what just happened, for the monitor ("Right, Sam!")

    def point(self, name: str) -> int:
        self.scores[name] = self.scores.get(name, 0) + 1
        return self.scores[name]

    def start(self) -> str:
        raise NotImplementedError

    def heard(self, text: str, call, said_to_pip: bool) -> str | None:
        """Something said while it's on: a reply (the module marks it Done), or None (not for the game)."""
        return None

    def skip(self) -> str:
        return ""

    def repeat(self) -> str:
        return ""

    def replay(self) -> str | None:
        return None

    def box(self) -> dict:
        return {}

    def summary(self) -> str:
        return ""

    def winner(self) -> str:
        """The winner's name, "" for nobody (or a tie)."""
        if not self.scores:
            return ""
        best = max(self.scores.values())
        top = [n for n, s in self.scores.items() if s == best]
        return top[0] if len(top) == 1 and best > 0 else ""

    def score_line(self) -> str:
        if not self.scores:
            return "Nobody's scored yet."
        parts = [f"{n} {s}" for n, s in sorted(self.scores.items(), key=lambda x: -x[1])]
        return "The score: " + join(parts) + "."

    def final(self) -> str:
        """What Pip says at the end."""
        if not self.scored:
            return "That's the game!"
        if not self.scores or max(self.scores.values()) == 0:
            return "That's the game! Nobody scored. Tough crowd."
        best = max(self.scores.values())
        top = [n for n, s in self.scores.items() if s == best]
        if len(top) > 1:
            return f"That's the game! It's a tie between {join(top)}, with {best} each."
        rest = [f"{n} {s}" for n, s in sorted(self.scores.items(), key=lambda x: -x[1]) if n != top[0]]
        return f"That's the game! {top[0]} wins with {best}" + (f", then {join(rest)}." if rest else ".")
