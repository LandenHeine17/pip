"""A program whose main loop dies exits (so systemd restarts it) instead of looking healthy while deaf or mute;
and an event sent while pip-core is restarting is dropped, not raised into that loop."""
import asyncio

import pytest

from pip_assistant.common.client import BusClient
from pip_assistant.common.program import serve


async def test_an_event_while_pip_core_is_away_is_dropped(tmp_path):
    bus = BusClient("ears", socket_path=tmp_path / "none.sock")
    await bus.emit("heard.started", {})                    # not connected: no ConnectionError
    await bus.command("core", "x", {})
    with pytest.raises(ConnectionError):
        await bus.request("core", "x", timeout=0.1)        # a request still says it failed


async def test_a_dead_main_loop_ends_the_program(tmp_path):
    bus = BusClient("ears", socket_path=tmp_path / "none.sock")

    async def listen():
        await asyncio.sleep(0.01)
        raise RuntimeError("microphone gone")

    with pytest.raises(SystemExit) as e:
        await serve(bus, listen)
    assert e.value.code == 1
