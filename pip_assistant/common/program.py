"""Shared start-up for Pip's programs: settings, logging, connect to pip-core, stop cleanly."""
import asyncio
import logging
import signal
from typing import Awaitable, Callable

from . import settings
from .client import BusClient
from .messages import Message


def make_bus(name: str) -> tuple[BusClient, dict]:
    cfg = settings.load()
    bus = BusClient(name, heartbeat_seconds=cfg["programs"]["heartbeat_seconds"])

    async def ping(msg: Message) -> dict:
        return {"pong": True, "program": name}

    bus.on(f"{name}.ping", ping)
    return bus, cfg


async def serve(bus: BusClient, *extra: Callable[[], Awaitable[None]]) -> None:
    """Run the bus connection (and any extra tasks) until SIGTERM/SIGINT."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    tasks = [asyncio.create_task(bus.run())] + [asyncio.create_task(fn()) for fn in extra]
    await stop.wait()
    for t in tasks:
        t.cancel()


def start(name: str, main: Callable[[], Awaitable[None]]) -> None:
    logging.basicConfig(level=logging.INFO, format=f"pip-{name}: %(message)s")
    asyncio.run(main())
