"""Claude fills in what Pip's own rules didn't understand (D-84).

Pip's rules handle everyday phrasings instantly and offline. Anything else ("set a thing on the
calendar in two days for the dentist") goes to Claude. Claude doesn't set anything itself: it
replies with one line

    ACTION {"do": "add", "kind": "event", "title": "dentist", "at": "2026-11-07T14:00"}

(or a list of them), and Pip's own code checks it, carries it out and says the confirmation, so
Pip only says something is set once it really is (PIP-NEW-039). If anything doesn't check out,
nothing is changed and Pip says so.

    add     kind = timer (seconds) | alarm (at) | reminder (label, at) | event (title, at or date, all_day)
            optional: repeat ('', daily, weekdays, weekends, weekly, weekly:0-6), label, heads_up_minutes
    change  id ('#a1b2c3' from the summary Claude sees), and at | shift_seconds | seconds (timers)
    cancel  id
"""
import json
import re
from datetime import date, datetime, timedelta

REPEATS = re.compile(r"^(|daily|weekdays|weekends|weekly|weekly:[0-6])$")
FAILED = "Sorry, that didn't work. Nothing was changed."


class Invalid(ValueError):
    pass


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


def check(cmds, items: list[dict]) -> list[tuple]:
    """Validate everything first, so a bad action means nothing at all is changed."""
    now = cmds.now()
    plan = []
    for a in items:
        do = a.get("do")
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


def apply(cmds, raw: str) -> tuple[str, int]:
    """Check and carry out Claude's actions. Returns (what Pip says, how many were done)."""
    try:
        plan = check(cmds, parse(raw))
    except (Invalid, ValueError, TypeError, KeyError):
        return FAILED, 0
    said = []
    for step in plan:
        kind = step[0]
        if kind == "timer":
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
