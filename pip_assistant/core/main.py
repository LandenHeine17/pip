"""Start pip-core: the message hub, decision trace and watchdog."""
import asyncio
import logging
import signal

from .. import __version__
from ..common import paths, settings
from ..common.messages import Message
from .bus import Bus
from .conversation import Conversation
from .memory import Memory, Refused
from .people import People, m0_owner_clips
from .schedule import Schedule
from .stt import make_local_stt, make_stt
from .health import Health
from .trace import Trace

log = logging.getLogger("pip-core")


def build(cfg: dict) -> tuple[Bus, Trace, Health]:
    trace = Trace(paths.database(), keep_hours=cfg["trace"]["keep_hours"])
    health = Health(cfg["programs"]["expected"], stuck_after=cfg["programs"]["stuck_after_seconds"],
                    restart=cfg["programs"]["restart_stuck"])
    bus = Bus(paths.core_socket(), trace, health)
    bus.core_handlers["core.health"] = lambda m: {"programs": health.snapshot(), "version": __version__}
    bus.core_handlers["core.trace"] = lambda m: {"entries": trace.recent(int(m.data.get("limit", 200)))}
    try:
        stt = make_stt(cfg)
    except Exception as e:  # Pip still runs (wake words, health) without speech-to-text
        log.warning("speech-to-text unavailable: %s", e)
        stt = None
    schedule = Schedule(paths.database(), bus, cfg)
    people = People(paths.database(), cfg)
    memory = Memory(paths.database())
    conv = Conversation(bus, cfg, stt, schedule=schedule, local_stt=make_local_stt(cfg), people=people, memory=memory)
    bus.core_handlers["core.alerts"] = lambda m: ({"count": len(schedule.active())}, schedule.snapshot())
    bus.core_handlers["core.alert_action"] = lambda m: alert_action(schedule, conv, m)
    bus.core_handlers["core.people"] = lambda m: ({"count": len(people.all())},
                                                   {**people.snapshot(), "enrolling": conv.enrol_status()})
    bus.core_handlers["core.people_action"] = lambda m: people_action(people, memory, conv, m)
    bus.core_handlers["core.memory"] = lambda m: (memory.counts(), memory_snapshot(memory, people))
    bus.core_handlers["core.memory_action"] = lambda m: memory_action(memory, people, m)
    bus.schedule = schedule
    bus.memory = memory
    return bus, trace, health


def memory_snapshot(memory: Memory, people: People) -> dict:
    names = {p["id"]: p["name"] for p in people.all()}
    rows = [{**r, "about_names": [names.get(i, "?") for i in r["about"]], "owner_name": names.get(r["owner"], "")}
            for r in memory.items()]
    from .memory import CATEGORIES
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
        if act == "opt_out":
            people.update_person(pid, opted_out=bool(m.data.get("value")))
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
            row = memory._row(mid) or {}
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
    """Buttons and forms on the dashboard: shut off, snooze, cancel, new timer, remove an event (FEAT-039)."""
    from . import when
    act = m.data.get("action")
    if act == "stop":
        return {"stopped": await schedule.acknowledge("dashboard")}
    if act == "snooze":
        return {"snoozed": await schedule.snooze(float(m.data.get("minutes") or 0) or None, how="dashboard")}
    if act == "cancel":
        schedule.cancel(str(m.data["id"]))
        return {"ok": True}
    if act == "remove_event":
        schedule.remove_event(str(m.data["id"]))
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


async def main() -> None:
    bus, trace, _ = build(settings.load())
    await bus.start()
    await bus.publish(Message("event", "core.started", data={"version": __version__}))
    log.info("pip-core %s listening on %s", __version__, paths.core_socket())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    keeper = asyncio.create_task(bus.housekeeping())
    alarms = asyncio.create_task(bus.schedule.run())
    from .voicecheck import voice_seconds_sync  # load the voice detector now, not on the first thing heard
    loop.run_in_executor(None, voice_seconds_sync, bytes(3200))
    await stop.wait()
    keeper.cancel()
    alarms.cancel()
    await bus.publish(Message("event", "core.stopping"))
    trace.flush()
    await bus.stop()


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    asyncio.run(main())


if __name__ == "__main__":
    run()
