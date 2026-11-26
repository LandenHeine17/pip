"""Pip-core's answers to the dashboard's requests (`core.*`, sent by pip-web); main.py connects each to its name.

    alerts_snapshot   the timers card (core.alerts), with the cards modules add (the calendar's next two weeks)
    tv_snapshot       what the face on the monitor shows (core.tv): timers, and each module's boxes
    alert_action      the timers card's buttons and forms (core.alert_action)
    chat              the Overview's chat box: a message typed to Pip, answered like speech (core.chat)
    people_action     people and voices (core.people_action)
    memory_snapshot   the Memory Center's list (core.memory)
    memory_action     the Memory Center's buttons (core.memory_action); memory_move its "Move to …" (D-147)
    mic_action        the Listening switch (core.mic)

Anything personal (names, memories, labels, what was typed) travels in `private`, so it's never logged.
Modules' parts come from their hooks (core/modules.py: home, tv, room_may_see, people_links); nothing here
names a module.
"""
import re

from ..common.messages import Message
from .conversation import Conversation
from .memory import CATEGORIES, Memory, Refused
from .modules import Registry
from .people import Match, People, m0_owner_clips
from .schedule import Schedule


def alerts_snapshot(schedule: Schedule, modules: Registry) -> dict:
    """The dashboard's timers card, plus the cards modules add to the Overview (the calendar's next two weeks).
    Heads-ups (kind "event") are listed only for the next 24 hours; the rest are just counted (D-109)."""
    snap = schedule.snapshot()
    soon = snap["now"] + 86400
    later = [a for a in snap["alerts"] if a["kind"] == "event" and a["state"] != "ringing" and a["due"] > soon]
    snap["alerts"] = [a for a in snap["alerts"] if a not in later]
    return {**snap, "later_heads_ups": len(later), "cards": modules.home_cards()}


def tv_snapshot(schedule: Schedule, modules: Registry) -> dict:
    """Only what the TV page shows (D-110): timers, alarms and reminders (heads-ups due within the hour) and
    what's ringing, each only if the room may see it (a heads-up for an event Pip mayn't mention to everyone
    stays off: the calendar decides); the screen's layout and what each module's boxes show (D-111; the
    calendar: today's and tomorrow's shared events; a to-do box: its list). Sent privately (labels and titles
    are personal), never logged."""
    snap = schedule.snapshot()
    now = snap["now"]
    alerts = [{"kind": a["kind"], "label": a["label"], "due": a["due"], "length": a.get("length"), "state": a["state"],
               "repeat": a["repeat"]}
              for a in snap["alerts"] if (a["kind"] != "event" or a["state"] == "ringing" or a["due"] - now < 3600)
              and modules.room_may_see(a)]
    return {"now": now, "alerts": alerts, **modules.tv()}


def face_status(conv: Conversation, net) -> dict:
    """The monitor's small status icons (PIP-NEW-062, D-128): listening off, no internet, quiet hours, Claude's usage
    limit reached, speech-to-text's daily limit used up. Only yes/no: nothing personal."""
    import time as _time
    from .joining import in_quiet_hours
    j = conv.joining
    return {"mic_off": not conv.listening, "offline": net is not None and not net.online,
            "quiet_hours": bool(j.quiet_hours) and in_quiet_hours(j.quiet_hours),
            "claude_limited": j.paused_until > _time.time(), "stt_limited": bool(conv.stt_limited)}


def people_snapshot(people: People, modules: Registry, conv: Conversation) -> dict:
    """The People page: everyone, voice learning in progress, and the links modules add for each person
    ("Connect Google"). Sent privately."""
    snap = people.snapshot()
    links = {p["id"]: ls for p in snap.get("people", []) if (ls := modules.people_links(p["id"]))}
    return {**snap, "enrolling": conv.enrol_status(), "links": links}


def memory_snapshot(memory: Memory, people: People, modules: Registry | None = None) -> dict:
    """The Memory Center's list, with people's names filled in (sent privately), and the modules a memory can be
    moved to (`takers`: a "Move to …" button each, D-147)."""
    names = {p["id"]: p["name"] for p in people.all()}
    rows = [{**r, "about_names": [names.get(i, "?") for i in r["about"]], "owner_name": names.get(r["owner"], "")}
            for r in memory.items()]
    return {"memories": rows, "categories": CATEGORIES, "takers": modules.memory_takers() if modules is not None else []}


async def people_action(people: People, memory: Memory, conv: Conversation, m: Message):
    """People and voices on the dashboard. Names travel privately (they're personal data)."""
    act, pid = m.data.get("action"), str(m.data.get("id") or "")
    try:
        if act == "add":
            p = people.add_person(m.private.get("name", ""), m.data.get("role", "member"))
            return {"ok": True, "id": p["id"]}
        if act == "rename":
            name = m.private.get("name", "").strip()[:40]
            if not name:
                return {"error": "A name is needed"}
            people.update_person(pid, name=name)
            return {"ok": True}
        if act == "role":
            people.update_person(pid, role=m.data.get("role"))
            return {"ok": True}
        if act == "remove":
            person = people.get(pid) or {}
            n = memory.forget_person(pid)
            other = await conv.modules.forget_person(pid, person.get("name", "")) if conv.modules is not None else 0
            people.remove_person(pid)
            return {"ok": True, "memories_removed": n, "other_removed": other}
        if act == "forget_voice":
            people.forget_voice(pid)
            return {"ok": True}
        if act == "voice_from_m0":
            n = await people.add_samples(pid, m0_owner_clips(), "m0 recordings")
            return {"ok": bool(n), "samples": n} if n else {"error": "No usable M0 recordings found"}
        if act == "guest_join":
            p = people.promote_guest(pid)
            return {"ok": True, "id": p["id"]}
        if act == "enrol_start":
            return {"ok": True, "enrolling": await conv.start_enrolment(pid)}
        if act == "enrol_cancel":
            conv.cancel_enrolment()
            return {"ok": True}
    except ValueError as e:
        return {"error": str(e)}
    return {"error": f"unknown action {act}"}


async def mic_action(conv: Conversation, m: Message) -> dict:
    """The dashboard's Listening switch (D-91). No "value": just report the state."""
    if "value" in m.data:
        return {"ok": True, **await conv.set_mic(bool(m.data["value"]))}
    return {"ok": True, **conv.mic.snapshot()}


async def memory_move(memory: Memory, people: People, modules: Registry, m: Message) -> dict:
    """The Memory Center's "Move to …" (D-147): a memory that's really something else (a funny moment) is handed
    to the module that takes them (Module.take_memory), and deleted here only once that module has kept it."""
    mid = str(m.data.get("id") or "")
    row = memory.get(mid)
    if row is None:
        return {"error": "That memory is gone."}
    names = {p["id"]: p["name"] for p in people.all()}
    row = {**row, "about_names": [names.get(i, "?") for i in row["about"]]}
    if not await modules.take_memory(str(m.data.get("to") or ""), row):
        return {"error": "It couldn't be moved, so it's still a memory."}
    memory.delete(mid)
    return {"ok": True}


def memory_action(memory: Memory, people: People, m: Message):
    """The Memory Center: add, edit, private/household, archive, delete (PIP-NEW-029/030)."""
    act, mid = m.data.get("action"), str(m.data.get("id") or "")
    try:
        if act == "add":
            about = [p["id"] for n in m.private.get("about", "").split(",") if (p := people.by_name(n.strip()))]
            owner = about[0] if m.data.get("private") and about else ""
            row, outcome = memory.add(m.private.get("text", ""), m.data.get("category", "household"), about, owner,
                                      bool(m.data.get("private")) and bool(owner), source="added on the dashboard")
            return {"ok": True, "outcome": outcome}
        if act == "edit":
            memory.update(mid, text=m.private.get("text", ""))
        elif act == "delete":
            memory.delete(mid)
        elif act == "keep_both":     # a flagged pair (M2, D-128): both stay
            memory.update(mid, review="")
        elif act == "keep_new":      # this one is right: the one it may contradict is archived
            row = memory.get(mid) or {}
            other = re.search(r"\((#m\w+)\)\s*$", row.get("review", ""))
            if other and (old := memory.find(other.group(1))):
                memory.update(old["id"], status="archived")
            memory.update(mid, review="")
        elif act == "keep_old":      # the earlier one is right: this one is archived
            memory.update(mid, status="archived", review="")
        elif act == "archive":
            memory.update(mid, status="archived")
        elif act == "restore":
            memory.update(mid, status="active")
        elif act == "visibility":
            row = memory.get(mid) or {}
            if m.data.get("value") == "private" and not row.get("owner") and not row.get("about"):
                return {"error": "A private memory needs a person it belongs to"}
            memory.update(mid, visibility=m.data.get("value"), owner=row.get("owner") or (row.get("about") or [""])[0])
        else:
            return {"error": f"unknown action {act}"}
        return {"ok": True}
    except Refused as e:
        return {"error": "Passwords, card and phone numbers can't be memories" if str(e) == "credential" else "Nothing to remember"}
    except ValueError as e:
        return {"error": str(e)}


async def alert_action(schedule: Schedule, conv: Conversation, m: Message) -> dict:
    """Buttons and forms on the dashboard: shut off, snooze, cancel, new timer (FEAT-039). (A module's card
    on the Overview, like the calendar's, removes its items through its own dashboard requests.)"""
    from ..common import when
    act = m.data.get("action")
    if act == "stop":
        return {"stopped": await schedule.acknowledge("dashboard")}
    if act == "snooze":
        return {"snoozed": await schedule.snooze(float(m.data.get("minutes") or 0) or None, how="dashboard")}
    if act == "cancel":
        schedule.cancel(str(m.data["id"]))
        return {"ok": True}
    if act == "timer":
        secs = float(m.data.get("minutes", 0)) * 60
        if not 1 <= secs <= 24 * 3600:
            return {"error": "Pick between 1 second and 24 hours"}
        row = schedule.add("timer", (m.private.get("label") or "timer").strip()[:40] or "timer", schedule.clock() + secs, length=secs)
        return {"ok": True, "id": row["id"], "said": when.say_duration(secs)}
    return {"error": f"unknown action {act}"}


async def chat(conv: Conversation, people: People, schedule: Schedule, d: dict) -> dict:
    """The Overview's chat box: a message typed to Pip (core/conversation.py `typed`). {"text", "person" (whose
    message it is: a household member picked on the page, or "" for someone), "aloud" (say the reply in the
    room too)} → {"reply"}. Like anything said to Pip, a typed message answers a ringing alarm: "snooze" snoozes,
    "okay" stops it, and anything else goes on to Claude, who's told it's ringing (D-94). Private both ways."""
    text = re.sub(r"\s+", " ", str(d.get("text") or "")).strip()[:500]
    if not text:
        return {"error": "Type something first"}
    p = people.get(str(d.get("person") or "")) if d.get("person") else None
    who = Match(p["id"], p["name"], "sure", 1.0) if p else Match()   # "someone": nobody in particular
    was_ringing = bool(schedule.ringing)
    reply = await conv.typed(text, who, aloud=bool(d.get("aloud")))
    if not reply and was_ringing and not schedule.ringing:
        reply = "Okay, it's off."   # (stopping an alarm by voice says nothing; here the page needs a reply)
    return {"reply": reply}
