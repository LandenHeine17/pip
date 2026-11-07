"""How Claude makes things happen (D-84, D-94).

Online, everything said to Pip goes to Claude in natural language, whatever the wording ("set a
thing on the calendar in two days for the dentist", "give me ten more minutes"). Claude doesn't set
anything itself: it replies with one line

    ACTION {"do": "add", "kind": "event", "title": "dentist", "at": "2026-11-07T14:00"}

(or a list of them), and Pip's own code checks it, carries it out and says the confirmation, so
Pip only says something is set once it really is (PIP-NEW-039). If anything doesn't check out,
nothing is changed and Pip says so.

    add     kind = timer (seconds) | alarm (at) | reminder (label, at) | event (title, at or date, all_day)
            optional: repeat ('', daily, weekdays, weekends, weekly, weekly:0-6), label, heads_up_minutes
    change  id ('#a1b2c3' from the summary Claude sees), and at | shift_seconds | seconds (timers)
    cancel  id

Ringing alarms and voices (handled in core/conversation.py, which has the alarm and the audio):
    stop_alarm     turn off what's ringing
    snooze         minutes (optional; default from settings)
    learn_voice    name: start learning that household member's voice (or remember a visitor for the visit)

Memory (step 5):
    remember       text (third person, with names), category, about [names], sensitive, private
    forget         id ('#m1a2b3')
    update_memory  id, text
Sensitive memories, memories about someone other than the speaker, and private ones from a voice Pip
doesn't recognise wait in the memory inbox instead of being saved straight away.
"""
import json
import re
from datetime import date, datetime, timedelta

REPEATS = re.compile(r"^(|daily|weekdays|weekends|weekly|weekly:[0-6])$")
FAILED = "Sorry, that didn't work. Nothing was changed."


class Invalid(ValueError):
    pass


HERE = ("stop_alarm", "snooze", "learn_voice")   # done by Conversation, not apply()


def check_here(items: list[dict]) -> list[tuple]:
    plan = []
    for a in items:
        do = a.get("do")
        if do == "stop_alarm":
            plan.append(("stop_alarm",))
        elif do == "snooze":
            mins = a.get("minutes")
            mins = float(mins) if mins not in (None, "") else None
            if mins is not None and not 1 <= mins <= 180:
                raise Invalid("snooze length")
            plan.append(("snooze", mins))
        elif do == "learn_voice":
            name = str(a.get("name") or "").strip()
            if not name or len(name) > 30:
                raise Invalid("no name")
            plan.append(("learn_voice", name.split()[0].title() if name.islower() else name.split()[0]))
    return plan


def parse(raw: str) -> list[dict]:
    """The JSON after 'ACTION' (one object or a list)."""
    text = raw.strip()
    text = re.sub(r"^ACTION\s*:?\s*", "", text)
    text = text.strip().strip("`").strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise Invalid("no JSON")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    items = obj if isinstance(obj, list) else [obj]
    if not items or not all(isinstance(i, dict) for i in items):
        raise Invalid("not a list of actions")
    return items


def _time(value, now: datetime) -> datetime:
    try:
        at = datetime.fromisoformat(str(value)).replace(tzinfo=None, second=0, microsecond=0)
    except ValueError:
        raise Invalid("bad time")
    if not now < at < now + timedelta(days=366):
        raise Invalid("time not in the coming year")
    return at


def check(cmds, items: list[dict], ctx: dict | None = None) -> list[tuple]:
    """Validate everything first, so a bad action means nothing at all is changed."""
    now = cmds.now()
    ctx = ctx or {}
    plan = []
    for a in items:
        do = a.get("do")
        if do in ("remember", "forget", "update_memory"):
            plan.append(_check_memory(a, ctx))
            continue
        if do == "add":
            kind = a.get("kind")
            rep = str(a.get("repeat") or "")
            if not REPEATS.match(rep):
                raise Invalid("bad repeat")
            label = str(a.get("label") or a.get("title") or "").strip()[:60]
            if kind == "timer":
                secs = float(a.get("seconds") or 0)
                if not 1 <= secs <= 24 * 3600:
                    raise Invalid("timer length")
                plan.append(("timer", secs, label))
            elif kind in ("alarm", "reminder"):
                at = _time(a.get("at"), now)
                if kind == "reminder" and not label:
                    raise Invalid("reminder without text")
                plan.append((kind, at, label, rep))
            elif kind == "event":
                if not label:
                    raise Invalid("event without title")
                if a.get("all_day") or (a.get("date") and not a.get("at")):
                    try:
                        d = date.fromisoformat(str(a.get("date") or a.get("at"))[:10])
                    except ValueError:
                        raise Invalid("bad date")
                    if not now.date() <= d <= now.date() + timedelta(days=366):
                        raise Invalid("date not in the coming year")
                    at, all_day = datetime.combine(d, datetime.min.time()), True
                else:
                    at, all_day = _time(a.get("at"), now), False
                heads = a.get("heads_up_minutes")
                plan.append(("event", label, at, all_day, rep, float(heads) * 60 if heads is not None else None))
            else:
                raise Invalid("unknown kind")
        elif do in ("change", "cancel"):
            found = cmds.s.find(str(a.get("id") or ""))
            if not found:
                raise Invalid("unknown id")
            if do == "cancel":
                plan.append(("cancel", *found))
                continue
            at = _time(a["at"], now) if a.get("at") else None
            shift = float(a["shift_seconds"]) if a.get("shift_seconds") else None
            length = float(a["seconds"]) if a.get("seconds") else None
            if at is None and shift is None and length is None:
                raise Invalid("change without a new time")
            if length is not None and not 1 <= length <= 24 * 3600:
                raise Invalid("timer length")
            plan.append(("change", *found, at, shift, length))
        else:
            raise Invalid("unknown action")
    if not plan or len(plan) > 10:
        raise Invalid("too many or no actions")
    return plan


def _check_memory(a: dict, ctx: dict) -> tuple:
    mem, people, speaker = ctx.get("memory"), ctx.get("people"), ctx.get("speaker")
    if mem is None:
        raise Invalid("no memory")
    if a["do"] in ("forget", "update_memory"):
        row = mem.find(str(a.get("id") or ""))
        if not row:
            raise Invalid("unknown memory")
        if a["do"] == "forget":
            return ("forget", row)
        text = str(a.get("text") or "").strip()
        if len(text) < 3:
            raise Invalid("no text")
        return ("update_memory", row, text)
    text = str(a.get("text") or "").strip()
    if len(text) < 3:
        raise Invalid("no text")
    me = speaker.person_id if speaker and speaker.level == "sure" and not speaker.guest else None
    about = []
    for name in a.get("about") or []:
        p = people.by_name(str(name)) if people else None
        if p:
            about.append(p["id"])
    category = str(a.get("category") or "household")
    personal = category in ("preference", "personal")      # household facts and rules can come from anyone
    why = ""
    if a.get("sensitive"):
        why = "sensitive"
    elif personal and any(pid != me for pid in about):
        why = "about someone else"
    elif a.get("private") and not me:
        why = "private, but I didn't recognise the voice"
    elif personal and not about and not me:
        why = "I didn't recognise the voice"
    if not personal:
        about = []
    who = speaker.name if me else "someone"
    return ("remember", text, category, about, me or "", bool(a.get("private")) and bool(me),
            bool(a.get("sensitive")), why, f"{who}, by voice")


def apply(cmds, raw: str, ctx: dict | None = None) -> tuple[str, int]:
    """Check and carry out Claude's actions. Returns (what Pip says, how many were done)."""
    try:
        plan = check(cmds, parse(raw), ctx)
    except (Invalid, ValueError, TypeError, KeyError):
        return FAILED, 0
    said = []
    mem = (ctx or {}).get("memory")
    for step in plan:
        kind = step[0]
        if kind == "remember":
            from .memory import Refused
            _, text, cat, about, owner, private, sensitive, why, source = step
            try:
                row, outcome = mem.add(text, cat, about, owner, private, sensitive, why, source)
            except Refused as e:
                said.append("I can't keep passwords, card or phone numbers in my memory." if str(e) == "credential"
                            else "I didn't catch what to remember.")
                continue
            said.append({"saved": f"Saved: {row['text'].rstrip('.!')}.", "duplicate": "I already knew that.",
                         "pending": "I'll remember that once it's approved in the memory inbox on the dashboard."}[outcome])
        elif kind == "forget":
            mem.delete(step[1]["id"])
            said.append(f"Forgot: {step[1]['text'].rstrip('.!')}.")
        elif kind == "update_memory":
            from .memory import Refused
            try:
                row = mem.update(step[1]["id"], text=step[2])
                said.append(f"Updated: {row['text'].rstrip('.!')}.")
            except Refused:
                said.append("I can't keep passwords, card or phone numbers in my memory.")
        elif kind == "timer":
            said.append(cmds._make_timer(step[1], step[2]))
        elif kind == "alarm":
            said.append(cmds._make_alarm(step[1], step[2], step[3]))
        elif kind == "reminder":
            said.append(cmds._make_reminder(step[1], step[2], step[3]))
        elif kind == "event":
            _, title, at, all_day, rep, heads = step
            said.append(cmds._add_event(title, at, all_day=all_day, rep=rep, heads=heads))
        elif kind == "cancel":
            said.append(cmds._cancel_item(step[1], step[2]))
        elif kind == "change":
            _, table, row, at, shift, length = step
            said.append(cmds.apply_change(table, row, at=at, shift=shift, length=length))
    return " ".join(said), len(plan)
