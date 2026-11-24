"""Trivia (D-144): multiple-choice questions, A to D, shown on the monitor; the first answer counts.

Questions come from Open Trivia DB (opentdb.com: free, no key, thousands of checked questions in 24 categories), or,
for a topic it doesn't have ("trivia about Minecraft"), from Claude, who writes them into the action itself.
An answer is a letter ("B", "bee", "I think it's C") or the choice's own words ("Canberra"), said to Pip or in the
room while a question is up. Whoever answers first gets the point or the miss, then the next question."""
import random
import re
from urllib.parse import unquote

from .common import Game, bare, mentions, plain, who

API = "https://opentdb.com/api.php"
CATEGORIES = {   # Open Trivia DB's categories: id → name (as Claude is told them in module.toml)
    9: "General Knowledge", 10: "Books", 11: "Film", 12: "Music", 13: "Musicals & Theatres", 14: "Television",
    15: "Video Games", 16: "Board Games", 17: "Science & Nature", 18: "Computers", 19: "Mathematics", 20: "Mythology",
    21: "Sports", 22: "Geography", 23: "History", 24: "Politics", 25: "Art", 26: "Celebrities", 27: "Animals",
    28: "Vehicles", 29: "Comics", 30: "Gadgets", 31: "Anime & Manga", 32: "Cartoons"}
OTHER_NAMES = {"general": 9, "movies": 11, "movie": 11, "films": 11, "tv": 14, "tv shows": 14, "games": 15,
               "gaming": 15, "science": 17, "nature": 17, "tech": 18, "technology": 18, "computer": 18, "math": 19,
               "maths": 19, "myths": 20, "sport": 21, "places": 22, "countries": 22, "anime": 31, "manga": 31,
               "cars": 28, "animal": 27, "musicals": 13, "theatre": 13, "theater": 13, "cartoon": 32,
               "animation": 32, "comic books": 29, "celebrity": 26, "famous people": 26, "art": 25}
LETTERS = "ABCD"
SAID_AS = {"a": 0, "ay": 0, "eh": 0, "aye": 0, "b": 1, "be": 1, "bee": 1, "bea": 1, "c": 2, "see": 2, "sea": 2,
           "si": 2, "cee": 2, "d": 3, "dee": 3}
DIFFICULTIES = ("easy", "medium", "hard")


def category_id(name: str) -> int | None:
    """Open Trivia DB's category for what Claude (or they) called it, or None."""
    p = plain(name)
    if not p:
        return None
    for cid, cname in CATEGORIES.items():
        if p == plain(cname) or p == plain(cname).split(" and ")[0]:
            return cid
    return OTHER_NAMES.get(p)


def from_opentdb(results: list[dict]) -> list[dict]:
    """Open Trivia DB's questions (asked for url-encoded) → {"q", "right", "wrong"}."""
    out = []
    for r in results:
        q, right = unquote(r.get("question", "")), unquote(r.get("correct_answer", ""))
        wrong = [unquote(w) for w in r.get("incorrect_answers", [])]
        if q and right and len(wrong) == 3:
            out.append({"q": q, "right": right, "wrong": wrong})
    return out


def checked(questions) -> list[dict]:
    """Questions Claude wrote: each needs a question, a right answer and three wrong ones (others are dropped)."""
    out = []
    for x in questions if isinstance(questions, list) else []:
        if not isinstance(x, dict):
            continue
        q = re.sub(r"\s+", " ", str(x.get("q") or "")).strip()[:250]
        right = re.sub(r"\s+", " ", str(x.get("right") or "")).strip()[:80]
        wrong = [re.sub(r"\s+", " ", str(w)).strip()[:80] for w in (x.get("wrong") or []) if str(w).strip()]
        if q and right and len(wrong) >= 3 and plain(right) not in {plain(w) for w in wrong[:3]}:
            out.append({"q": q, "right": right, "wrong": wrong[:3]})
    return out[:15]


async def fetch(http, amount: int, category: int | None, difficulty: str | None) -> list[dict]:
    """Questions from Open Trivia DB. Too few at that difficulty: any difficulty."""
    for diff in ([difficulty, None] if difficulty else [None]):
        params = {"amount": amount, "type": "multiple", "encode": "url3986"}
        if category:
            params["category"] = category
        if diff:
            params["difficulty"] = diff
        r = await http.get(API, params=params)
        r.raise_for_status()
        got = r.json()
        if got.get("response_code") == 0 and got.get("results"):
            return from_opentdb(got["results"])
    return []


class Trivia(Game):
    kind, name = "trivia", "trivia"

    def __init__(self, questions: list[dict], topic: str = "", rng: random.Random | None = None):
        super().__init__()
        rng = rng or random.Random()
        self.topic = topic
        self.questions = []
        for x in questions:
            choices = [x["right"], *x["wrong"]]
            rng.shuffle(choices)
            self.questions.append({"q": x["q"], "choices": choices, "right": choices.index(x["right"])})
        self.i = 0

    @property
    def now(self) -> dict:
        return self.questions[self.i]

    def ask(self) -> str:
        q = self.now
        c = q["choices"]
        ask = q["q"].rstrip()
        ask = ask if ask.endswith(("?", ".", "!")) else ask + "?"
        return (f"Question {self.i + 1} of {len(self.questions)}. {ask} "
                f"A: {c[0]}. B: {c[1]}. C: {c[2]}. Or D: {c[3]}?")

    def start(self) -> str:
        about = f" about {self.topic}" if self.topic else ""
        return f"Trivia{about}! Answer with the letter or the answer; first answer counts. " + self.ask()

    def pick(self, text: str) -> int | None:
        """Which choice they said, or None if it isn't an answer."""
        t = bare(text)
        if t in SAID_AS:
            return SAID_AS[t]
        hits = [i for i, c in enumerate(self.now["choices"]) if mentions(t, c, close=0.85)]
        if len(hits) > 1:   # "New York" and "York": the longest one they said
            hits = sorted(hits, key=lambda i: -len(self.now["choices"][i]))[:1]
        return hits[0] if hits else None

    def heard(self, text: str, call, said_to_pip: bool) -> str | None:
        if self.over:
            return None
        got = self.pick(text)
        if got is None:
            return None
        q, name = self.now, who(call)
        right = got == q["right"]
        if right:
            n = self.point(name)
            line = (f"Right, {name}! " if name != "Guest" else "Right! ") + (f"That's {n}. " if n > 1 else "")
        else:
            self.scores.setdefault(name, 0)
            line = f"Nope, it was {LETTERS[q['right']]}, {q['choices'][q['right']]}. "
        self.last = {"who": name, "right": right, "said": LETTERS[got], "answer": LETTERS[q["right"]],
                     "text": q["choices"][q["right"]]}
        return line + self.advance()

    def advance(self) -> str:
        self.i += 1
        if self.i >= len(self.questions):
            self.i = len(self.questions) - 1
            self.over = True
            return self.final()
        return self.ask()

    def skip(self) -> str:
        q = self.now
        self.last = {"who": "", "right": False, "said": "", "answer": LETTERS[q["right"]], "text": q["choices"][q["right"]]}
        return f"It was {LETTERS[q['right']]}, {q['choices'][q['right']]}. " + self.advance()

    def repeat(self) -> str:
        return self.ask()

    def box(self) -> dict:
        q = self.now
        return {"kind": self.kind, "n": self.i + 1, "of": len(self.questions), "topic": self.topic,
                "question": q["q"], "choices": q["choices"], "scores": self.scores, "last": self.last,
                "over": self.over, "answer": q["right"] if self.over else None}

    def summary(self) -> str:
        q = self.now
        return (f"A trivia game is on: question {self.i + 1} of {len(self.questions)}, \"{q['q']}\" "
                f"(A {q['choices'][0]}; B {q['choices'][1]}; C {q['choices'][2]}; D {q['choices'][3]}; the answer is "
                f"{LETTERS[q['right']]}). {self.score_line()} Pip judges answers himself; if they talk to you about it, "
                f"never say the answer, though you may give a small hint if they ask for one.")
