"""Would you rather (D-144): Pip asks one (from data/rather.txt, or one Claude made up), the two choices go up on the
monitor, everyone answers, and Claude roasts the answers (what Claude reads says what's being asked and who chose
what). "Next" asks another. No score: it's for fun."""
import random
import re
from pathlib import Path

from .common import Game, bare, mentions, who

PAIRS = Path(__file__).parent / "data" / "rather.txt"
FIRST = re.compile(r"^(?:the )?(?:first|1st|one|number one|option one|left|a)(?: one)?$")
SECOND = re.compile(r"^(?:the )?(?:second|2nd|two|number two|option two|right|b)(?: one)?$")


def pairs() -> list[tuple[str, str]]:
    out = []
    for line in PAIRS.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "|" in line:
            x, _, y = line.partition("|")
            if x.strip() and y.strip():
                out.append((x.strip(), y.strip()))
    return out


class Rather(Game):
    kind, name, scored = "rather", "would you rather", False

    def __init__(self, rng: random.Random | None = None, first: tuple[str, str] | None = None):
        super().__init__()
        self.rng = rng or random.Random()
        self.left = pairs()
        self.rng.shuffle(self.left)
        self.round = 0
        self.pair: tuple[str, str] = ("", "")
        self.votes: dict[str, int] = {}   # name → 0 or 1
        self.next(first)

    def next(self, pair: tuple[str, str] | None = None) -> None:
        if pair is None:
            if not self.left:
                self.left = pairs()
                self.rng.shuffle(self.left)
            pair = self.left.pop()
        self.pair, self.votes, self.round = pair, {}, self.round + 1

    def ask(self) -> str:
        return f"Would you rather {self.pair[0]}, or {self.pair[1]}?"

    def start(self) -> str:
        return "Would you rather! " + self.ask()

    def heard(self, text: str, call, said_to_pip: bool) -> str | None:
        """A choice is noted for the monitor (who chose which); Claude still answers it (the roast)."""
        t = bare(text)
        side = 0 if FIRST.search(t) else 1 if SECOND.search(t) else None
        if side is None:
            hits = [i for i, option in enumerate(self.pair) if mentions(t, option, close=0.75)]
            side = hits[0] if len(hits) == 1 else None
        if side is not None:
            self.votes[who(call)] = side
        return None

    def skip(self) -> str:
        self.next()
        return self.ask()

    def made_up(self, a: str, b: str) -> str:
        self.next((a, b))
        return self.ask()

    def repeat(self) -> str:
        return self.ask()

    def box(self) -> dict:
        return {"kind": self.kind, "round": self.round, "a": self.pair[0], "b": self.pair[1],
                "votes": [[n, v] for n, v in self.votes.items()], "over": self.over}

    def summary(self) -> str:
        chose = "; ".join(f"{n} chose \"{self.pair[v]}\"" for n, v in self.votes.items())
        return (f"Would you rather is on: you asked them to choose \"{self.pair[0]}\" or \"{self.pair[1]}\". "
                + (chose + ". " if chose else "")
                + "When they answer, react in a sentence or two: tease their choice playfully (roast them), maybe say "
                  "which you'd pick. When they want another, Pip asks it himself; to ask one you made up, reply with "
                  "ONLY: ACTION {\"do\": \"game\", \"op\": \"next\", \"a\": \"...\", \"b\": \"...\"}.")
