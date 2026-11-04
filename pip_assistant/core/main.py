"""Start pip-core: the message hub, decision trace and watchdog."""
import asyncio
import logging
import signal

from .. import __version__
from ..common import paths, settings
from ..common.messages import Message
from .bus import Bus
from .conversation import Conversation
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
    Conversation(bus)
    return bus, trace, health


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
    await stop.wait()
    keeper.cancel()
    await bus.publish(Message("event", "core.stopping"))
    trace.flush()
    await bus.stop()


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    asyncio.run(main())


if __name__ == "__main__":
    run()
