"""How Claude makes things happen (D-84, D-94).

Online, everything said to Pip goes to Claude in natural language, whatever the wording ("set a
thing on the calendar in two days for the dentist", "give me ten more minutes"). Claude doesn't set
anything itself: it replies with one line

    ACTION {"do": "add", "kind": "event", "title": "dentist", "at": "2026-11-07T14:00"}

(or a list of them), and Pip's own code checks it, carries it out and says the confirmation, so
Pip only says something is set once it really is (PIP-NEW-039). If anything doesn't check out,
nothing is changed and Pip says so.

    add     kind = timer (seconds) | alarm (at) | reminder (label, at); other kinds belong to modules
            (kind event → the calendar module)
            optional: repeat ('', daily, weekdays, weekends, weekly, weekly:0-6), label, heads_up_minutes
    change  id ('#a1b2c3' from the summary Claude sees), and at | shift_seconds | seconds (timers)
    cancel  id                 (an id belonging to a module's item goes to that module: "cancel:event")
    other   any verb a plugged-in module declares ("add_task" → modules/todos), see core/modules.py
    look    kind (+ the module's fields): a look-up, not a change. Pip fetches it and asks Claude again
            with the result, once ("what's on next Friday?" → the calendar for that day). Handled in
            core/conversation.py.

Ringing alarms and voices (handled in core/conversation.py, which has the alarm and the audio):
    stop_alarm     turn off what's ringing
    snooze         minutes (optional; default from settings)
    learn_voice    name: start learning that household member's voice (or remember a visitor for the visit)

Changing Pip himself (M1b, PIP-NEW-069, AC-SM-01; handled in core/conversation.py):
    request_change summary: a short paraphrase of what to change. Only for someone Pip recognises for sure
                   (not a guest); it goes to pip-builder, which writes a plan for the dashboard's Changes page.
                   Voice can only ask: approving and undoing happen on the dashboard.

Memory:
    remember       text (third person, with names), category, about [names], sensitive, private
    forget         id ('#m1a2b3')
    update_memory  id, text
    approve_memory id: a memory waiting in the inbox, approved by the person it's about (recognised for sure, D-107)
Sensitive memories, memories about someone other than the speaker, and private ones from a voice Pip
doesn't recognise wait in the memory inbox instead of being saved straight away.
"""
import inspect
import json
import logging
import re
from datetime import datetime, timedelta

from .memory import CREDENTIAL

REPEATS = re.compile(r"^(|daily|weekdays|weekends|weekly|weekly:[0-6])$")
FAILED = "Sorry, that didn't work. Nothing was changed."
NO_SECRETS = "I can't keep passwords, card or phone numbers in my memory."
NOTHING = "I didn't catch what to remember."
log = logging.getLogger(__name__)


class Invalid(ValueError):
    pass


HERE = ("stop_alarm", "snooze", "learn_voice", "request_change")   # done by Conversation, not apply()
SUMMARY_MAX = 300


def check_here(items: list[dict]) -> list[tuple]:
    """Check the actions in HERE (Conversation carries them out). Raises Invalid if one doesn't check out."""
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
        elif do == "request_change":
            summary = re.sub(r"\s+", " ", str(a.get("summary") or "")).strip()
            if len(summary) < 5 or len(summary) > SUMMARY_MAX:
                raise Invalid("no change summary")
            if CREDENTIAL.search(summary):
                raise Invalid("credential")
            plan.append(("request_change", summary))
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
    """Validate everything first, so a bad action means nothing at all is changed.
    `cmds`: Pip's Commands (core/commands.py); timers, alarms and reminders are its `alerts` (core/alerts.py)."""
    now = cmds.now()
    ctx = ctx or {}
    plan = []
    for a in items:
        do = a.get("do")
        if do in ("remember", "forget", "update_memory", "approve_memory"):
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
                from .commands import timer_name
                plan.append(("timer", secs, timer_name(label)))
            elif kind in ("alarm", "reminder"):
                at = _time(a.get("at"), now)
                if kind == "reminder" and not label:
                    raise Invalid("reminder without text")
                plan.append((kind, at, label, rep))
            else:
                plan.append(check_module_action(f"add:{kind}", a, ctx, now))   # e.g. calendar events (modules/calendar)
        elif do == "look":
            plan.append(check_module_action(f"look:{a.get('kind')}", a, ctx, now))   # normally handled by Conversation.look_up
        elif do in ("change", "cancel") and not a.get("id") and a.get("kind") not in (None, "", "timer", "alarm", "reminder"):
            plan.append(check_module_action(f"{do}:{a['kind']}", a, ctx, now))   # e.g. a whole calendar, by name
        elif do in ("change", "cancel"):
            found = cmds.alerts.s.find(str(a.get("id") or ""))
            if not found:
                reg = ctx.get("modules")
                kind = reg.kind_of(str(a.get("id") or "")) if reg else None
                if not kind:
                    raise Invalid("unknown id")
                plan.append(check_module_action(f"{do}:{kind}", a, ctx, now))
                continue
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
            row = found[1]
            if shift is not None and at is None and length is None and row["kind"] != "timer" and not row["repeat"] \
                    and row["due"] + shift <= cmds.alerts.clock():
                raise Invalid("moved into the past")   # (timers stop at "now"; a repeat moves on to its next day)
            plan.append(("change", *found, at, shift, length))
        else:
            plan.append(check_module_action(str(do), a, ctx, now))   # an action a module added ("add_task", …)
    if not plan or len(plan) > 10:
        raise Invalid("too many or no actions")
    return plan


def check_module_action(verb: str, a: dict, ctx: dict, now: datetime) -> tuple:
    """An action a plugged-in module handles (core/modules.py): checked now, done with the rest."""
    from .modules import Call
    reg = ctx.get("modules")
    act = reg.action(verb) if reg else None
    if act is None:
        raise Invalid("unknown action")
    call = ctx.get("call") or Call(speaker=ctx.get("speaker"), now=now)
    return ("module", act, act.check(a, call), call)


def _check_memory(a: dict, ctx: dict) -> tuple:
    """Check a memory action. For a new memory, also works out whether it must wait in the inbox, and why."""
    mem, people, speaker = ctx.get("memory"), ctx.get("people"), ctx.get("speaker")
    if mem is None:
        raise Invalid("no memory")
    if a["do"] in ("forget", "update_memory", "approve_memory"):
        row = mem.find(str(a.get("id") or ""))
        if not row:
            raise Invalid("unknown memory")
        if a["do"] == "approve_memory":
            # Only the person it's about, recognised for sure, may approve it by voice (D-107)
            me = speaker.person_id if speaker and speaker.level == "sure" and not speaker.guest else None
            if row["status"] != "pending" or not me or not mem.is_about(row, me, speaker.name) or not mem.may_see(row, me):
                raise Invalid("only the person it's about may approve it")
            return ("approve_memory", row)
        if a["do"] == "forget":
            return ("forget", row)
        text = str(a.get("text") or "").strip()
        if len(text) < 3:
            raise Invalid("no text")
        if CREDENTIAL.search(text):
            raise Invalid("credential")   # checked now, so nothing else in the list is done either (D-84)
        return ("update_memory", row, text)
    text = str(a.get("text") or "").strip()
    if len(text) < 3:
        raise Invalid("no text")
    if CREDENTIAL.search(text):
        raise Invalid("credential")
    me = speaker.person_id if speaker and speaker.level == "sure" and not speaker.guest else None
    about, strangers = [], False
    for name in a.get("about") or []:
        p = people.by_name(str(name)) if people else None
        if p:
            about.append(p["id"])
        else:
            strangers = True   # someone Pip doesn't know ("Jordan from next door"): still someone else
    category = str(a.get("category") or "household")
    personal = category in ("preference", "personal")      # household facts and rules can come from anyone
    own = bool(me) and personal and about == [me] and not strangers   # only about the speaker, and Pip is sure it's them
    why = ""
    if a.get("sensitive") and not own:                      # your own sensitive facts are saved straight away (D-88)
        why = "sensitive"
    elif personal and (strangers or any(pid != me for pid in about)):
        why = "about someone else"
    elif a.get("private") and not me:
        why = "private, but I didn't recognise the voice"
    elif personal and not about and not me:
        why = "I didn't recognise the voice"
    if not personal:
        about = []
    who = speaker.name if me else "someone"
    subject = me if me and (me in about or speaker.name.lower() in text.lower()) else None   # may confirm a waiting one
    return ("remember", text, category, about, me or "", bool(a.get("private")) and bool(me),
            bool(a.get("sensitive")), why, f"{who}, by voice", own, subject, speaker.name if me else "")


def apply(cmds, raw: str, ctx: dict | None = None) -> tuple[str, int]:
    """Check and carry out Claude's actions. Returns (what Pip says, how many were done).
    For actions that must wait (a module talking to Google) use apply_async."""
    try:
        plan = check(cmds, parse(raw), ctx)
    except (Invalid, ValueError, TypeError, KeyError) as e:
        return _refused(e, raw), 0
    said = _carry_out(cmds, plan, ctx)
    for i, s in enumerate(said):
        if inspect.isawaitable(s):
            s.close()
            said[i] = FAILED
    return " ".join(said), _done(said)


async def apply_async(cmds, raw: str, ctx: dict | None = None) -> tuple[str, int]:
    """Like apply, but waits for actions that take time (a module talking to Google)."""
    try:
        plan = check(cmds, parse(raw), ctx)
    except (Invalid, ValueError, TypeError, KeyError) as e:
        return _refused(e, raw), 0
    said = _carry_out(cmds, plan, ctx)
    for i, s in enumerate(said):
        if inspect.isawaitable(s):
            try:
                said[i] = await s
            except Exception:
                log.exception("action failed")
                said[i] = FAILED
    return " ".join(said), _done(said)


def _refused(e: Exception, raw: str) -> str:
    """What Pip says when the actions don't check out (nothing was done)."""
    if str(e) != "credential":
        return FAILED
    try:
        alone = len(parse(raw)) == 1
    except (Invalid, ValueError):
        alone = True
    return NO_SECRETS if alone else f"{NO_SECRETS} So I didn't do any of it."


def _done(said: list[str]) -> int:
    """How many steps really happened: a step that failed while being done isn't counted."""
    from .alerts import PASSED
    return sum(s not in (FAILED, NO_SECRETS, NOTHING, PASSED) for s in said)


def _carry_out(cmds, plan: list[tuple], ctx: dict | None) -> list:
    """Do each checked step; returns what to say for each (a module's may still need awaiting)."""
    said = []
    mem = (ctx or {}).get("memory")
    for step in plan:
        kind = step[0]
        if kind == "remember":
            from .memory import Refused
            _, text, cat, about, owner, private, sensitive, why, source, own, subject, name = step
            try:
                # The person it's about saying it: that confirms one waiting in the inbox, not a second copy (D-107)
                row = mem.confirm_pending(text, subject, name) if subject else None
                if row is not None:
                    said.append(f"Saved: {row['text'].rstrip('.!')}.")
                    continue
                row, outcome = mem.add(text, cat, about, owner, private, sensitive, why, source, own=own)
            except Refused as e:
                said.append(NO_SECRETS if str(e) == "credential" else NOTHING)
                continue
            said.append({"saved": f"Saved: {row['text'].rstrip('.!')}.", "duplicate": "I already knew that.",
                         "waiting": "That's waiting in the memory inbox.",
                         "pending": "I'll remember that once it's approved in the memory inbox on the dashboard."}[outcome])
        elif kind == "approve_memory":
            row = mem.approve(step[1]["id"])
            said.append(f"Saved: {row['text'].rstrip('.!')}.")
        elif kind == "forget":
            mem.delete(step[1]["id"])
            said.append(f"Forgot: {step[1]['text'].rstrip('.!')}.")
        elif kind == "update_memory":
            from .memory import Refused
            try:
                row = mem.update(step[1]["id"], text=step[2])
                said.append(f"Updated: {row['text'].rstrip('.!')}.")
            except Refused:
                said.append(NO_SECRETS)
        elif kind == "timer":
            said.append(cmds.alerts.make_timer(step[1], step[2]))
        elif kind == "alarm":
            said.append(cmds.alerts.make_alarm(step[1], step[2], step[3]))
        elif kind == "reminder":
            said.append(cmds.alerts.make_reminder(step[1], step[2], step[3]))
        elif kind == "module":
            _, act, mplan, call = step
            said.append(act.do(mplan, call))
        elif kind == "cancel":
            said.append(cmds.alerts.cancel_item(step[1], step[2]))
        elif kind == "change":
            _, table, row, at, shift, length = step
            said.append(cmds.alerts.apply_change(table, row, at=at, shift=shift, length=length))
    return said
