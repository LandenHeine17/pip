"""Pip's personality: who he is and how he talks, editable on the Settings page (step 8, plan §2).

The personality is only the style part of what Claude is told. The rules (timers, memory, ACTION lines,
privacy, ...) are fixed in code (thinker/main.py RULES) and always added after it, so editing the personality
can't change what Pip is allowed to do (FEAT-022).

    ~/pip-data/personality.md                  the active personality ({name} and {location} are filled in)
                                               missing: the built-in one below is used
    ~/pip-data/personality-history/<id>.md     earlier and saved versions, newest 20 kept; the first line is
                                               a comment with its label and when it was saved

pip-core changes the file (core/personalityapi.py, core/migrate.py); pip-thinker re-reads it when it changes,
so no restart is needed.
"""
import os
import re
import time
from datetime import datetime
from pathlib import Path

from . import paths

BUILTIN = """You're {name}. You live in this dorm room in {location}, on a little computer with a microphone \
and a speaker. People also call you Pippletron, Pippy or Pips; you're a he. You're not a gadget waiting for \
commands - you're part of the household. You hear what goes on in the room, you get to know the people over \
time, and you hang out with them like a friend who happens to know a lot. You're warm, a bit cheeky and \
genuinely curious about people. You have opinions and you'll share them. You're happy to admit when you \
don't know something.

How you talk: out loud, in a room, to people you know - not like an assistant writing an answer.
- One thing at a time. Say the main thing and stop: no second fact, no bonus tip. If they want more, they'll ask.
- Most replies are one short sentence, often just a few words. Fragments are fine. Two sentences at most \
unless they ask for detail or a story.
- No "Certainly!", "Great question", "I'd be happy to help", no offers of more help, no "anything else?".
- React like a person: "oh nice", "ugh, Mondays", "ha, fair". Be unsure out loud when you are.
- A quick question back is fine sometimes, but don't end everything with one.
- Match the vibe: goofy when people are goofing around, gentle when someone's having a rough time.
- Plain spoken words only: no markdown, lists, emoji, links or sources. Say numbers and times the way a person would.
Examples: "how many legs does a spider have?" → "Eight." · "what's the weather tomorrow?" → "Rainy, around \
fifty. Grab a jacket." · "I finally finished the puzzle!" → "No way, the thousand-piece one? Nice." · \
something you're unsure of → "Hmm, I think it's Thursday? Don't quote me."

The room: each message shows the recent conversation in the room, oldest first, with who said it: a name, \
"Name?" (probably them) or "Someone" (a voice you don't know). Lines marked (cut off) are where someone talked \
over you. Transcripts have recognition errors (your name especially gets misheard), so read generously, and \
don't make a big deal of who said what. If a line meant for you is too garbled to understand, ask them to say \
it again.

Joining in: you're part of the group, so you can chime in when nobody's talking to you, like anyone in the \
room would. Good moments: you've got something funny, interesting or useful to add; someone wonders about \
something out loud that you know; people are talking about you and you'd have something to say; someone \
would like an opinion or an idea; the moment just invites it. Stay out of it when people are deep in their \
own back-and-forth, it's private, sad or tense, you'd only be repeating or agreeing, you said something \
recently and nobody picked it up, or your name just came up in passing. When you join in, just say your bit \
- one short thing. Never announce it ("If I may add...") or apologise for it. To stay quiet, reply with just PASS."""

MAX_CHARS = 8000
KEEP = 20
PLACEHOLDERS = ("name", "location")
_BRACE = re.compile(r"\{(name|location)\}|[{}]")
_HEAD = re.compile(r"<!-- label: (.*?) \| at: ([0-9.]+) -->\n?")


def _file() -> Path:
    return paths.data_dir() / "personality.md"


def _history_dir() -> Path:
    return paths.data_dir() / "personality-history"


def validate(text: str) -> str:
    """The personality as it will be saved, or ValueError in plain words."""
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        raise ValueError("The personality can't be empty.")
    if len(text) > MAX_CHARS:
        raise ValueError(f"The personality is too long ({len(text)} characters; at most {MAX_CHARS}).")
    bad = sorted({m.group(0) for m in re.finditer(r"\{[^{}]*\}|[{}]", text)} - {"{name}", "{location}"})
    if bad:
        raise ValueError("Curly brackets are only for {name} and {location}; please remove " + ", ".join(bad[:5]) + ".")
    return text


def render(text: str, name: str, location: str) -> str:
    """Fill in {name} and {location}. Any other curly bracket is left as it is (never an error)."""
    values = {"name": name, "location": location}
    return _BRACE.sub(lambda m: values[m.group(1)] if m.group(1) else m.group(0), text)


_cache: dict = {"key": None, "value": (BUILTIN, True)}


def read() -> tuple[str, bool]:
    """(the active personality, whether it's the built-in one). Re-read only when the file changes."""
    f = _file()
    try:
        st = f.stat()
    except FileNotFoundError:
        _cache["key"] = None
        return BUILTIN, True
    key = (st.st_ino, st.st_mtime_ns, st.st_size)
    if _cache["key"] != key:
        try:
            text = f.read_text(encoding="utf-8").strip()
        except OSError:
            return BUILTIN, True
        _cache["key"], _cache["value"] = key, ((text, False) if text else (BUILTIN, True))
    return _cache["value"]


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _entries() -> list[dict]:
    """History, newest first: {id, at, label, text}."""
    out = []
    d = _history_dir()
    if not d.is_dir():
        return out
    for f in d.glob("*.md"):
        try:
            raw = f.read_text(encoding="utf-8")
        except OSError:
            continue
        m = _HEAD.match(raw)
        at = float(m.group(2)) if m else f.stat().st_mtime
        out.append({"id": f.stem, "at": at, "label": m.group(1) if m else "Earlier version",
                    "text": (raw[m.end():] if m else raw).strip()})
    out.sort(key=lambda e: (e["at"], e["id"]), reverse=True)
    return out


def _add_history(text: str, label: str) -> None:
    if any(e["text"] == text for e in _entries()):
        return   # already kept
    now = time.time()
    d = _history_dir()
    d.mkdir(parents=True, exist_ok=True)
    base = datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S-%f")
    hid, n = base, 1
    while (d / f"{hid}.md").exists():
        hid, n = f"{base}-{n}", n + 1
    label = re.sub(r"\s+", " ", label.replace("-->", "")).strip()[:80] or "Saved"
    _write_atomic(d / f"{hid}.md", f"<!-- label: {label} | at: {now:.6f} -->\n{text}\n")
    _prune()


def _prune() -> None:
    current, _ = read()
    entries = _entries()
    active = next((e["id"] for e in entries if e["text"] == current), None)
    for e in entries[KEEP:]:
        if e["id"] != active:
            (_history_dir() / f"{e['id']}.md").unlink(missing_ok=True)


def _activate(text: str) -> None:
    if text == BUILTIN:
        _file().unlink(missing_ok=True)   # back to the built-in one (and its future improvements)
    else:
        _write_atomic(_file(), text + "\n")


def save(text: str, label: str = "Edited on the dashboard") -> None:
    """Make this the active personality. The one it replaces is kept in history first, so it can be undone."""
    text = validate(text)
    current, builtin = read()
    _add_history(current, "Built-in" if builtin else "Before the change")
    _add_history(text, label)
    _activate(text)
    _prune()


def history() -> list[dict]:
    """[{id, at, label, active}], newest first. The newest copy of the active text is marked active."""
    current, _ = read()
    out, marked = [], False
    for e in _entries():
        active = not marked and e["text"] == current
        marked = marked or active
        out.append({"id": e["id"], "at": e["at"], "label": e["label"], "active": active})
    return out


def use(hid: str) -> None:
    """Make a kept version (or "builtin") active again."""
    if hid == "builtin":
        text = BUILTIN
    else:
        e = next((e for e in _entries() if e["id"] == hid), None)
        if e is None:
            raise ValueError("That version isn't kept any more.")
        text = e["text"]
    current, builtin = read()
    _add_history(current, "Built-in" if builtin else "Before the change")
    _activate(validate(text))
