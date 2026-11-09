"""Start pip-core. `build` creates every part (message hub, decision trace, watchdog, timers and
alarms, people, memory, modules, the conversation) and connects them; the dashboard's requests
(`core.*`, sent by pip-web) are answered by the functions in dashapi.py. `main` then runs until stopped."""
import asyncio
import logging
import signal
from dataclasses import dataclass

from .. import __version__
from ..common import paths, settings
from ..common.program import release_info
from ..common.messages import Message
from .bus import Bus
from .conversation import Conversation
from .dashapi import (alert_action, alerts_snapshot, memory_action, memory_snapshot, mic_action, people_action,
                      tv_snapshot)
from .memory import Memory
from .mic import MicSwitch
from .prefs import Prefs
from .modules import Registry
from .net import Net
from .people import People
from .schedule import Schedule
from .stt import make_local_stt, make_stt
from .health import Health
from .trace import Trace

log = logging.getLogger("pip-core")


@dataclass
class Core:
    """Everything build() made that main() starts and stops."""
    bus: Bus
    trace: Trace
    schedule: Schedule
    net: Net
    modules: Registry


def build(cfg: dict) -> Core:
    trace = Trace(paths.database(), keep_hours=cfg["trace"]["keep_hours"])
    health = Health(cfg["programs"]["expected"], stuck_after=cfg["programs"]["stuck_after_seconds"],
                    restart=cfg["programs"]["restart_stuck"], release=release_info()["release"])
    bus = Bus(paths.core_socket(), trace, health)
    mic = MicSwitch(paths.database())
    prefs = Prefs(paths.database())
    net = Net(bus)
    bus.core_handlers["core.health"] = lambda m: {"programs": health.snapshot(), "version": __version__,
                                                  "mic": mic.snapshot(), "online": net.online,
                                                  **release_info()}
    bus.core_handlers["core.trace"] = lambda m: {"entries": trace.recent(int(m.data.get("limit", 200)))}
    try:
        stt = make_stt(cfg)
    except Exception as e:  # Pip still runs (wake words, health) without speech-to-text
        log.warning("speech-to-text unavailable: %s", e)
        stt = None
    schedule = Schedule(paths.database(), bus, cfg)
    people = People(paths.database(), cfg)
    memory = Memory(paths.database())
    # Features that plug in (pip_assistant/modules/, ARCHITECTURE §7): calendar, to-dos, Google accounts…
    modules = Registry.load(cfg, bus=bus, schedule=schedule, people=people, net=net)
    conv = Conversation(bus, cfg, stt, schedule=schedule, local_stt=make_local_stt(cfg), people=people, memory=memory,
                        mic=mic, modules=modules, prefs=prefs)
    conv.net = net
    bus.core_handlers["core.alerts"] = lambda m: ({"count": len(schedule.active())}, alerts_snapshot(schedule, modules))
    bus.core_handlers["core.tv"] = lambda m: ({}, tv_snapshot(schedule, modules))   # the TV page (D-110)
    bus.core_handlers["core.alert_action"] = lambda m: alert_action(schedule, conv, m)
    bus.core_handlers["module.request"] = modules.request          # a module's dashboard page (/api/m/<name>)
    bus.core_handlers["core.modules"] = lambda m: {"modules": modules.pages()}
    bus.core_handlers["core.people"] = lambda m: ({"count": len(people.all())},
                                                   {**people.snapshot(), "enrolling": conv.enrol_status()})
    bus.core_handlers["core.people_action"] = lambda m: people_action(people, memory, conv, m)
    bus.core_handlers["core.memory"] = lambda m: (memory.counts(), memory_snapshot(memory, people))
    bus.core_handlers["core.memory_action"] = lambda m: memory_action(memory, people, m)
    bus.core_handlers["core.mic"] = lambda m: mic_action(conv, m)
    bus.core_handlers["core.prefs"] = lambda m: conv.set_pref(m.data.get("key"), m.data["value"]) if "key" in m.data \
        else conv.prefs_snapshot()
    # Step 8 (D-117): the Settings page, the personality editor and the import from Ziggletron. Requests and
    # replies travel in `private` (personality text, preview questions), so they're never written to the trace.
    from .migrate import Migration
    from .personalityapi import PersonalityAPI
    from .settingsapi import SettingsAPI
    personality_api, migration = PersonalityAPI(conv), Migration(memory, people)
    settings_api = SettingsAPI(bus, conv)

    async def privately(handler, m):
        return {"ok": True}, await handler(dict(m.private or {}))
    bus.core_handlers["core.personality"] = lambda m: privately(personality_api.handle, m)
    bus.core_handlers["core.migrate"] = lambda m: privately(migration.handle, m)
    bus.core_handlers["core.settings"] = lambda m: privately(settings_api.handle, m)
    return Core(bus, trace, schedule, net, modules)


async def main() -> None:
    core = build(settings.load())
    bus, trace = core.bus, core.trace
    await bus.start()
    await bus.publish(Message("event", "core.started", data={"version": __version__}))
    log.info("pip-core %s listening on %s", __version__, paths.core_socket())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    keeper = asyncio.create_task(bus.housekeeping())
    alarms = asyncio.create_task(core.schedule.run())
    online = asyncio.create_task(core.net.run())
    core.modules.start()
    from .voicecheck import voice_seconds_sync  # load the voice detector now, not on the first thing heard
    loop.run_in_executor(None, voice_seconds_sync, bytes(3200))
    await stop.wait()
    keeper.cancel()
    alarms.cancel()
    online.cancel()
    core.modules.stop()
    await bus.publish(Message("event", "core.stopping"))
    trace.flush()
    await bus.stop()


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    asyncio.run(main())


if __name__ == "__main__":
    run()
