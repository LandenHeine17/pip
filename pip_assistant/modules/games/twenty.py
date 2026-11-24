"""20 questions (D-144): Pip thinks of something (from data/things.txt) and says what kind of thing it is; they
ask yes-or-no questions to guess it.

Claude answers the questions: while the game is on, what Claude reads says what the secret is and how many questions
are left (`summary`). This module counts the questions (anything said to Pip that sounds like one) and spots the
guess that names it ("is it a penguin?"), which wins. After the last question, Claude answers it and says what it
was; the game ends a little later (module.py)."""
import random
import re
from pathlib import Path

from .common import Game, mentions, plain, who

THINGS = Path(__file__).parent / "data" / "things.txt"
QUESTION = re.compile(r"^(?:is|are|was|were|does|do|did|can|could|would|will|should|has|have|had|am|may|might|"
                      r"if|so is|so does|and is|but is|ok is|okay is|what about|how about)\b")


def things() -> list[tuple[str, list[str]]]:
    """(what kind of thing, as Pip says it; [its name, other names]) from data/things.txt
    ("an animal: penguin | penguins")."""
    out = []
    for line in THINGS.read_text().splitlines():
        if not line.strip() or line.startswith("#") or ":" not in line:
            continue
        kind, _, names = line.partition(":")
        names = [n.strip() for n in names.split("|") if n.strip()]
        if names:
            out.append((kind.strip(), names))
    return out


COUNTED = ("an animal", "a thing", "a vehicle", "a job")   # "a penguin"; but "pizza", "soccer", "coffee"


def a(name: str, kind: str = "a thing") -> str:
    """"penguin" → "a penguin"; "pizza", "soccer", "the Eiffel Tower" and "Paris" stay as they are."""
    if not kind.startswith(COUNTED) or name.lower().startswith(("the ", "a ", "an ")) or name[:1].isupper():
        return name
    return ("an " if name[:1].lower() in "aeiou" else "a ") + name


class Twenty(Game):
    kind, name = "twenty", "20 questions"

    def __init__(self, limit: int = 20, rng: random.Random | None = None, pick: tuple | None = None):
        super().__init__()
        rng = rng or random.Random()
        self.thing_kind, self.names = pick or rng.choice(things())
        self.limit = max(5, int(limit))
        self.asked: list[str] = []      # the questions (shown on the monitor; gone with the game)
        self.final = False              # the last question's been asked: Claude says what it was
        self.found_by = ""

    @property
    def secret(self) -> str:
        return self.names[0]

    def start(self) -> str:
        return (f"Okay, I'm thinking of {self.thing_kind}. You've got {self.limit} yes-or-no questions. "
                f"What's your first question?")

    def guessed(self, text: str) -> bool:
        return any(mentions(text, n, close=0.85) for n in self.names)

    def heard(self, text: str, call, said_to_pip: bool) -> str | None:
        if self.over or self.final or not said_to_pip:
            return None
        if self.guessed(text):
            self.over, self.found_by = True, who(call)
            self.point(self.found_by)
            n = len(self.asked) + 1
            by = f"{self.found_by} got it" if self.found_by != "Guest" else "You got it"
            self.last = {"found": True, "who": self.found_by}
            return f"Yes! It's {a(self.secret, self.thing_kind)}! {by} in {n} question{'s' if n != 1 else ''}."
        if QUESTION.search(plain(text)) or text.strip().endswith("?"):
            self.asked.append(text.strip()[:120])
            if len(self.asked) >= self.limit:
                self.final = True
        return None   # Claude answers it, knowing the secret (summary)

    def skip(self) -> str:
        self.over = True
        self.last = {"found": False, "who": ""}
        return f"Giving up? It was {a(self.secret, self.thing_kind)}!"

    def repeat(self) -> str:
        left = self.limit - len(self.asked)
        return f"I'm thinking of {self.thing_kind}. {left} question{'s' if left != 1 else ''} left."

    def box(self) -> dict:
        return {"kind": self.kind, "thing_kind": self.thing_kind, "asked": len(self.asked), "limit": self.limit,
                "questions": self.asked[-8:], "over": self.over or self.final,
                "secret": self.secret if self.over or self.final else None, "found_by": self.found_by}

    def summary(self) -> str:
        left = self.limit - len(self.asked)
        text = (f"20 questions is on: you're thinking of \"{self.secret}\" (they know it's {self.thing_kind}); "
                f"never say it unless they guess it. Answer their yes-or-no question honestly about it, in a few words "
                f"(yes, no, sometimes, sort of, not really), in character; a hint only if they ask. ")
        if self.final:
            return text + (f"That was their last question: answer it, then tell them it was {a(self.secret, self.thing_kind)} and "
                           f"the game's over.")
        return text + f"Questions asked so far: {len(self.asked)} of {self.limit} ({left} left)."

    def winner(self) -> str:
        return self.found_by if self.found_by and self.found_by != "Guest" else ("Guest" if self.found_by else "Pip")
