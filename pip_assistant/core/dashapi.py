"""Pip-core's answers to the dashboard's requests (`core.*`, sent by pip-web); main.py connects each to its name.

    alerts_snapshot   the timers card (core.alerts), with the cards modules add (the calendar's next two weeks)
    tv_snapshot       what the face on the monitor shows (core.tv): timers, and each module's boxes
    alert_action      the timers card's buttons and forms, and commands typed on the dashboard (core.alert_action)
    people_action     people and voices (core.people_action)
    memory_snapshot   the Memory Center's list (core.memory)
    memory_action     the Memory Center's buttons (core.memory_action)
    mic_action        the Listening switch (core.mic)

Anything personal (names, memories, labels, what was typed) travels in `private`, so it's never logged.
Modules' parts come from their hooks (core/modules.py: home, tv, room_may_see, people_links); nothing here
names a module.
"""
from ..common.messages import Message
from .conversation import Conversation
from .memory import CATEGORIES, Memory, Refused
from .modules import Registry
from .people import People, m0_owner_clips
from .schedule import Schedule


def alerts_snapshot(schedule: Schedule, modules: Registry) -> dict:
    """The dashboard's timers card, plus the cards modules add to the front page (the calendar's next two weeks).
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


def people_snapshot(people: People, modules: Registry, conv: Conversation) -> dict:
    """The People card: everyone, voice learning in progress, and the links modules add for each person
    ("Connect Google"). Sent privately."""
    snap = people.snapshot()
    links = {p["id"]: ls for p in snap.get("people", []) if (ls := modules.people_links(p["id"]))}
    return {**snap, "enrolling": conv.enrol_status(), "links": links}


def memory_snapshot(memory: Memory, people: People) -> dict:
    """The Memory Center's list, with people's names filled in (sent privately)."""
    names = {p["id"]: p["name"] for p in people.all()}
    rows = [{**r, "about_names": [names.get(i, "?") for i in r["about"]], "owner_name": names.get(r["owner"], "")}
            for r in memory.items()]
    return {"memories": rows, "categories": CATEGORIES}


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
            n = memory.forget_person(pid)
            people.remove_person(pid)
            return {"ok": True, "memories_removed": n}
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


def memory_action(memory: Memory, people: People, m: Message):
    """The Memory Center: inbox approvals, add, edit, private/household, archive, delete (PIP-NEW-029/030)."""
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
        elif act == "approve":
            memory.approve(mid)
        elif act in ("reject", "delete"):
            memory.delete(mid)
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
    on the front page, like the calendar's, removes its items through its own dashboard requests.)"""
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
    if act == "command":  # typed into the dashboard: the same commands as speaking; any typed message answers an alarm
        if schedule.ringing:
            await schedule.acknowledge("typed")
        reply = await conv.commands.handle(m.private.get("text", ""))
        return ({"handled": reply is not None}, {"reply": reply or ""})
    return {"error": f"unknown action {act}"}
