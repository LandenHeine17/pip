"""Start pip-core: the message hub, decision trace and watchdog."""
import asyncio
import logging
import signal

from .. import __version__
from ..common import paths, settings
from ..common.messages import Message
from .bus import Bus
from .conversation import Conversation
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
    conv = Conversation(bus, cfg, stt, schedule=schedule, local_stt=make_local_stt(cfg))
    bus.core_handlers["core.alerts"] = lambda m: ({"count": len(schedule.active())}, schedule.snapshot())
    bus.core_handlers["core.alert_action"] = lambda m: alert_action(schedule, conv, m)
    bus.schedule = schedule
    return bus, trace, health


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
