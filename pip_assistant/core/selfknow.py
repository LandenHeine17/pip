"""Pip answering questions about himself (M2: PIP-NEW-035, 065–067, D-128).

Claude asks for one of these like any other look-up (`ACTION {"do": "look", "kind": "why"}`, D-102), and
`Acting.look_up` (core/acting.py) answers it here instead of asking a module:

    why          what he heard and decided in the last 10 minutes, from the decision trace (core/trace.py):
                 labels and times only, never the words (D-34), for "why didn't you answer me?"
    settings     the conversation settings as they are now, for "how long do you wait before answering?"
    health       his programs, the internet, speech-to-text and today's Claude use, for "are you okay?"
    memory_why   where a memory came from (who, when, how), for "why do you remember that?" ("id": "#m1a2b3")
    docs         the parts of his own docs (PIP-SPEC, HOW-PIP-WORKS) that match "query", for "how does your memory work?"
    code         which of his own code files match "query" (file and what it's for), for "where's that in your code?"
                 (read-only, from the version that's running; only pip_assistant/ and docs/, never ~/pip-data)

The answers are plain sentences for Claude to read; Claude then answers out loud in its own words.
"""
import ast
import re
import time
from datetime import date
from pathlib import Path

KINDS = ("why", "settings", "health", "memory_why", "docs", "code")
ROOT = Path(__file__).resolve().parents[2]      # the running release (~/pip-app/current) or ~/pip
DOCS = ("docs/PIP-SPEC.md", "docs/HOW-PIP-WORKS.md")
DOCS_CHARS = 1200
STOP = {"how", "does", "your", "you", "the", "what", "work", "works", "where", "is", "are", "do", "and", "about", "pip"}
WHY_SECONDS = 600
WHY_LINES = 25

WHO = {"sure": "someone he knows", "probably": "probably someone he knows", "unknown": "a voice he doesn't know"}
DECIDED = {
    "answered": "answered",
    "fast_command": "did it at once without Claude (a clearly worded command)",
    "held_more_speech": "waited: they kept talking, so he took it as one turn",
    "reopened": "dropped the answer he was working on: they kept talking, so he took it all as one turn (D-137)",
    "dropped_reply": "dropped a reply said without his name: they kept talking among themselves, so it wasn't "
                     "for him (D-145)",
    "taken_as_room_talk": "took it as room talk: it began after his follow-up window had closed (D-145)",
    "waited_unfinished":"waited longer: it sounded unfinished",
    "held_on": "waited, because someone said \"hold on\"",
    "discarded": "dropped an answer that was no longer wanted",
    "join_suppressed": "stayed out of the conversation for a while (told \"not now\" or talked over)",
    "resumed_after_backchannel": "carried on after only a \"yeah\", a laugh or nothing",
}


def _when(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def describe(row: dict) -> str | None:
    """One trace entry as a plain sentence, or None for the ones that don't explain anything."""
    ty, d = row["type"], row.get("data") or {}
    if ty == "stt.done":
        if not d.get("chars"):
            return "heard speech, but speech-to-text made out no words"
        return f"heard {WHO.get(d.get('speaker') or 'unknown', 'someone')} ({d.get('reason', '')})"
    if ty == "heard.noise":
        return "heard a sound with no voice in it: ignored"
    if ty == "heard.phantom":
        return "speech-to-text wrote only \"Thank you.\" (a cough or a laugh) from a voice he doesn't know: ignored"
    if ty == "turn.decision":
        what = DECIDED.get(d.get("decision", ""), d.get("decision", ""))
        why = f" ({d['why']})" if d.get("why") else ""
        after = f", {d['since_speech_s']} s after they stopped" if d.get("since_speech_s") is not None else ""
        return f"{what}{why}{after}"
    if ty == "answer.passed":
        return {"direct": "Claude decided it didn't need a reply (or it was \"never mind\" / \"stop\")",
                "mentioned": "his name came up, but Claude decided it wasn't said to him"}.get(d.get("kind"),
                                                                                       "Claude chose not to answer")
    if ty == "answer.failed":
        return "Claude didn't answer (an error), so he said so"
    if ty == "answer.offline":
        return "the internet was down, so he stayed quiet (only timers, alarms and commands work offline)"
    if ty == "speech.interrupted":
        return "someone talked over him, so he stopped"
    if ty == "speech.overlap_ignored":
        return "a loud sound over him with no voice: he kept talking"
    if ty == "stt.failed":
        return "speech-to-text failed, so he couldn't make it out"
    if ty == "mic.changed":
        return "listening switched " + ("on" if d.get("listening") else "off")
    if ty == "join.considered":
        return "thought about joining in"
    if ty == "action.looked":
        return f"looked something up ({d.get('count', 0)} found)"
    return None


def why(trace, now: float | None = None) -> str:
    now = now or time.time()
    rows = list(reversed(trace.recent(limit=600, since=now - WHY_SECONDS)))   # oldest first
    lines = [f"{_when(r['ts'])} {s}" for r in rows if (s := describe(r))]
    if not lines:
        return "What Pip heard and did in the last 10 minutes: nothing."
    return f"What Pip heard and did in the last 10 minutes (now {_when(now)}): " + "; ".join(lines[-WHY_LINES:]) + "."


def settings_text(cfg: dict | None = None) -> str:
    from ..common import settings
    from . import settings_schema
    cfg = cfg if cfg is not None else settings.load()
    flat = settings_schema.flat(cfg)
    groups = ("Taking turns", "Conversation & joining in")
    out = []
    for key, e in settings_schema.SCHEMA.items():
        if e["group"] in groups and key in flat:
            v = flat[key]
            v = ("on" if v else "off") if isinstance(v, bool) else v
            unit = " s" if key.endswith("_seconds") else " min" if key.endswith("_minutes") else ""
            out.append(f"{e['label']}: {v}{unit}")
    return "Pip's conversation settings now (changed on the dashboard's Settings page): " + "; ".join(out) + "."


def health_text(conv) -> str:
    parts = []
    h = getattr(conv.bus, "health", None)
    if h is not None:
        snap = h.snapshot()
        progs = [f"pip-{n} {p.get('state')}" for n, p in snap.items()]
        up = snap.get("core", {}).get("uptime_s")
        parts.append("programs: " + ", ".join(progs) + (f"; running {round(up / 3600, 1)} h" if up else ""))
    net = getattr(conv, "net", None)
    if net is not None:
        parts.append("internet: " + ("up" if net.online else "down"))
    parts.append("speech-to-text: " + ("Groq's daily limit used up, hearing on the Pi" if conv.stt_limited else "fine"))
    try:
        from ..common.usage import Usage
        t = Usage().today(date.today())
        parts.append(f"Claude today: {t['requests']} requests, {t['errors']} errors")
    except Exception:
        pass
    if conv.mic is not None:
        parts.append("listening: " + ("on" if conv.mic.listening else "switched off"))
    return "How Pip is running: " + "; ".join(parts) + "."


def memory_why(conv, a: dict, speaker) -> str:
    mem = conv.memory
    row = mem.find(str(a.get("id") or "")) if mem is not None else None
    me = speaker.person_id if speaker is not None and speaker.level == "sure" and not speaker.guest else None
    if not row or not mem.may_see(row, me):
        return "No such memory (or it's private to someone else)."
    when = time.strftime("%B %-d, %Y", time.localtime(row["created"]))
    how = "someone asked him to remember it" if row.get("confirmed", 1) else "he worked it out (not said directly)"
    src = row.get("source") or "unknown"
    return (f"Memory {row['id']} (\"{row['text']}\", {row['category']}): saved {when}; from {src}; {how}. "
            "It can be forgotten by asking, or on the dashboard's Memory page.")


def _words(query: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9_]+", (query or "").lower()) if len(w) > 2 and w not in STOP]


def sections(text: str) -> list[tuple[str, str]]:
    """A markdown file as (heading, body) pieces, by ## and ### headings."""
    out, head, body = [], "", []
    for line in text.split("\n"):
        if re.match(r"^#{2,3} ", line):
            if body:
                out.append((head, "\n".join(body).strip()))
            head, body = line.lstrip("# ").strip(), []
        else:
            body.append(line)
    if body:
        out.append((head, "\n".join(body).strip()))
    return out


def docs_text(query: str, root: Path = ROOT) -> str:
    """The best-matching sections of his own docs, plain and short."""
    words = _words(query)
    if not words:
        return "Say what to look for in your docs (a few words)."
    scored = []
    for rel in DOCS:
        f = root / rel
        if not f.is_file():
            continue
        for head, body in sections(f.read_text(errors="replace")):
            hay_head, hay = head.lower(), body.lower()
            score = sum(3 * hay_head.count(w) + min(hay.count(w), 5) for w in words)
            if score:
                scored.append((score, rel, head, body))
    if not scored:
        return f"Nothing in your docs about \"{query}\"."
    out, size = [], 0
    for _, rel, head, body in sorted(scored, key=lambda x: -x[0])[:3]:
        body = re.sub(r"[`*|]", "", re.sub(r"\s+", " ", body))
        piece = f"[{rel.split('/')[-1]}: {head}] {body}"[:DOCS_CHARS - size]
        out.append(piece)
        size += len(piece)
        if size >= DOCS_CHARS:
            break
    return "From your own docs (explain it simply, in a sentence or two): " + " … ".join(out)


def code_text(query: str, root: Path = ROOT) -> str:
    """Which of his code files match: path and the first line of what each says it does."""
    words = _words(query)
    if not words:
        return "Say what to look for in your code (a few words)."
    scored = []
    for f in sorted((root / "pip_assistant").rglob("*.py")):
        if "__pycache__" in f.parts or f.name.startswith("test_"):
            continue
        text = f.read_text(errors="replace")
        try:
            doc = (ast.get_docstring(ast.parse(text)) or "").strip().split("\n")[0]
        except SyntaxError:
            doc = ""
        rel = str(f.relative_to(root / "pip_assistant"))
        hay = text.lower()
        score = sum(5 * (w in rel.lower()) + 3 * (w in doc.lower()) + min(hay.count(w), 10) for w in words)
        if score:
            scored.append((score, rel, doc))
    if not scored:
        return f"No code of yours matches \"{query}\"."
    best = sorted(scored, key=lambda x: -x[0])[:4]
    return "Your code that matches (file: what it's for): " + "; ".join(f"{r}: {d}" for _, r, d in best) + "."


async def look(conv, a: dict, speaker) -> str:
    kind = a.get("kind")
    if kind == "why":
        return why(conv.bus.trace)
    if kind == "settings":
        return settings_text()
    if kind == "health":
        return health_text(conv)
    if kind == "memory_why":
        return memory_why(conv, a, speaker)
    if kind == "docs":
        return docs_text(str(a.get("query") or "")[:100])
    if kind == "code":
        return code_text(str(a.get("query") or "")[:100])
    raise KeyError(kind)
