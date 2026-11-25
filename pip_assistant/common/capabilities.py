"""What Pip can do, for Claude: so "what can you do?" and "can you…?" are answered truthfully (M2, PIP-NEW-064, D-128).

The built-in features are listed here; each plugged-in module adds its own one-line `summary` and an example
from `says_examples` (its module.toml), so a new module shows up without changing this file (D-120).
pip-thinker adds the text to the fixed part of what Claude is told (`Thinker.system_prompt`), which stays the
same between requests, so the warm Claude process stays usable.

    text(infos)  →  "What you can do (…): timers, alarms and reminders …; Calendar: … (e.g. "what's on Friday")"
"""

BUILT_IN = [
    "timers, alarms and reminders, in any words (snooze, move, change, cancel; while ringing just say stop or snooze)",
    "remembering lasting things people tell you to remember, and forgetting or correcting them",
    "knowing people by their voice (they learn their voice on the dashboard)",
    "listening to the room's conversation and joining in now and then (how often is set by voice: "
    "\"set chattiness to low, normal or high\")",
    "answering what's typed in the dashboard's chat box, like the same words said out loud",
    "the monitor: your face, captions, and boxes people can ask to show",
    "the weather where you live",
    "being switched off: the dashboard's Listening switch, or two claps",
    "changing your own code: when someone asks for a change or complains about something you do, offering to write it "
    "up as a change; the owner then works it through with Claude in Claude Hub and approves it there",
    "answering questions about yourself: what you can do, why you did or didn't answer, your settings, "
    "how you're running, why you remember something, and what was talked about earlier",
]


def text(infos) -> str:
    """The capability list for Claude, from the built-in features and the loaded modules' infos
    (common/modules.ModuleInfo: name, label, summary, says_examples)."""
    parts = list(BUILT_IN)
    for i in infos or ():
        name = getattr(i, "label", "") or getattr(i, "name", "")
        summary = (getattr(i, "summary", "") or "").strip().rstrip(".")
        if not summary:
            continue
        eg = next((e for e in (getattr(i, "says_examples", None) or []) if e), "")
        parts.append(f"{name}: {summary}" + (f' (e.g. "{eg}")' if eg else ""))
    return ("What you can do (answer \"what can you do?\" or \"can you...?\" from this, in a sentence or two, not "
            "the whole list; for anything else, say you can't yet, and that they could ask you to learn it): "
            + "; ".join(parts) + ".")
