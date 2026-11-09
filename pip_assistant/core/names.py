"""Is Pip being called, or mentioned? His names and their common mishearings, in one place.

Used by core/conversation.py (deciding whether something was said to him) and core/commands.py (taking
his name off a command: "Pip, set a timer"). To teach Pip a new mishearing, add it to WORDS.

    calls_pip("thanks, Pip")          True: said to him
    names_pip("Pip's being weird")    True: he's mentioned (calls_pip is False)
    only_name("hey Pip")              True: just his name, the rest is still coming
"""
import difflib
import re

# His names and how speech-to-text tends to write them.
WORDS = r"pip|pipp|pips|pippy|pippie|pippletron|pipple ?tron|pippa ?tron|piple ?tron|pipletron"
PIP_NAMES = re.compile(rf"\b({WORDS})\b", re.I)
NOT_PIP = re.compile(r"\b(zig+le ?tron|zig+y|zig+)\b", re.I)    # Ziggletron's names: never Pip
# His name with a greeting before it, as taken off the start of a command ("hey Pip, …").
NAME_WITH_GREETING = re.compile(rf"\b(hey |hi |ok |okay )?({WORDS})\b[,.!]?", re.I)

FILLER = re.compile(r"\b(hey|hi|ok|okay|so|yo|um+|uh+|hmm+)\b", re.I)
GREETINGS = frozenset("hey hi hello yo oi ok okay oh so um uh and but well alright thanks thank you".split())


def sounds_like_pippletron(word: str) -> bool:
    """A close mishearing of 'Pippletron' (but never 'Ziggletron')."""
    return len(word) >= 7 and not word.startswith("zig") and \
        difflib.SequenceMatcher(None, word, "pippletron").ratio() >= 0.82


def only_name(text: str) -> bool:
    return not FILLER.sub("", PIP_NAMES.sub("", text)).strip(" .,?!-")


def is_pip(words: list[str]) -> bool:
    """These words, run together, are one of Pip's names (or a close mishearing of Pippletron)."""
    joined = "".join(words)
    if not joined:
        return False
    return bool(PIP_NAMES.fullmatch(" ".join(words)) or PIP_NAMES.fullmatch(joined)) or sounds_like_pippletron(joined)


def calls_pip(text: str) -> bool:
    """Is Pip being *called* ("Pip, what's the time?", "thanks, Pip", "hey Pippletron what's up")
    rather than just mentioned ("Pip's being weird", "ask Pip later")? Ziggletron's rule: a clause
    that's only the name, or a greeting followed by it."""
    for clause in re.split(r"[,.!?;:—]+", text):
        words = re.findall(r"[a-z']+", clause.lower())
        greeted = False
        while words and words[0] in GREETINGS:
            words, greeted = words[1:], True
        if words and is_pip(words):
            return True
        if greeted and any(is_pip(words[:n]) for n in (1, 2, 3)):
            return True
    return False


def names_pip(text: str) -> bool:
    """Does this transcript call Pip? Exact names, or close mishearings of 'Pippletron' (never 'Ziggletron')."""
    if PIP_NAMES.search(text):
        return True
    words = re.findall(r"[a-z]+", text.lower())
    return any(sounds_like_pippletron(w) for w in words + [a + b for a, b in zip(words, words[1:])])
