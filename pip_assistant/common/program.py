"""Shared start-up for Pip's programs: settings, logging, connect to pip-core, stop cleanly."""
import asyncio
import json
import logging
import signal
from functools import cache
from pathlib import Path
from typing import Awaitable, Callable

from . import settings
from .client import BusClient
from .messages import Message


@cache
def release_info() -> dict:
    """Which installed version this code is (PIP-NEW-080): the RELEASE file that `release.py install` writes
    into each version's folder (~/pip-app/releases/<id>/RELEASE). Running straight from the repo there is
    none, so it's "dev". Read once at start-up: a running program never changes version."""
    f = Path(__file__).resolve().parents[2] / "RELEASE"
    try:
        info = json.loads(f.read_text())
        return {"release": str(info["short"]), "release_time": info.get("time")}
    except (OSError, ValueError, KeyError, TypeError):
        return {"release": "dev", "release_time": None}


def release_id() -> str:
    return release_info()["release"]


def make_bus(name: str) -> tuple[BusClient, dict]:
    cfg = settings.load()
    bus = BusClient(name, heartbeat_seconds=cfg["programs"]["heartbeat_seconds"])
    bus.status["release"] = release_id()  # every heartbeat says which version is running (health check)

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
