"""pip-core's message hub over real sockets (routing, requests, no impersonation, reconnecting, big replies), the
decision trace (never anything private) and the watchdog (core/bus.py, trace.py, health.py)."""
import asyncio
import json

import pytest

from pip_assistant.common.client import BusClient
from pip_assistant.common.messages import Message
from pip_assistant.core.bus import Bus
from pip_assistant.core.health import MISSING, OK, STUCK, Health
from pip_assistant.core.trace import Trace

# ---------------------------------------------------------------- the decision trace


def test_trace_never_stores_private(tmp_path):
    t = Trace(tmp_path / "pip.db")
    t.add(Message("event", "heard.utterance", source="ears", data={"seconds": 2.1},
                  private={"text": "my secret words", "audio": "AAAA"}))
    t.add(Message("heartbeat", "heartbeat", source="ears", data={"rss_mb": 50}))
    rows = t.recent()
    assert len(rows) == 1 and rows[0]["data"] == {"seconds": 2.1}
    raw = (tmp_path / "pip.db").read_bytes()
    assert b"my secret words" not in raw


def test_trace_prunes_old_entries(tmp_path):
    t = Trace(tmp_path / "pip.db", keep_hours=48)
    t.add(Message("event", "old", ts=1000.0)); t.add(Message("event", "new"))
    t.flush()
    assert t.prune() == 1
    assert [r["type"] for r in t.recent()] == ["new"]

# ---------------------------------------------------------------- the watchdog


def test_health_states_and_restart():
    now = [1000.0]
    restarted = []
    h = Health(["ears"], stuck_after=10, restart=True, restart_fn=lambda n: restarted.append(n) or True,
               clock=lambda: now[0])
    h.restart = True  # pretend we run under systemd
    assert h.state("ears") == MISSING
    h.heartbeat("ears", {"rss_mb": 40})
    assert h.state("ears") == OK
    now[0] += 11
    assert h.state("ears") == STUCK
    assert h.check() == [{"program": "ears", "state": STUCK, "restarted": True}]
    assert h.check() == []  # not again within 60 s
    assert restarted == ["ears"]

# ---------------------------------------------------------------- the hub, over real sockets


@pytest.fixture
async def hub(tmp_path):
    sock = tmp_path / "core.sock"
    bus = Bus(sock, Trace(tmp_path / "pip.db"), Health(["a", "b"], restart=False))
    bus.core_handlers["core.health"] = lambda m: {"programs": bus.health.snapshot()}
    await bus.start()
    clients = []

    async def connect(name):
        c = BusClient(name, socket_path=sock, heartbeat_seconds=0.1)
        clients.append(c)
        return c

    yield bus, sock, connect, clients
    for c in clients:
        if getattr(c, "_task", None):
            c._task.cancel()
    await bus.stop()


async def start(c):
    c._task = asyncio.create_task(c.run())
    await asyncio.wait_for(c.connected.wait(), 3)
    await asyncio.sleep(0.05)


async def test_events_commands_requests_and_trace(hub):
    bus, sock, connect, _ = hub
    got = []
    a, b = await connect("a"), await connect("b")

    async def on_timer(msg):
        got.append(("event", msg.type, msg.private.get("label")))

    async def on_say(msg):
        got.append(("command", msg.type, msg.private.get("text")))

    async def on_ask(msg):
        return {"answer": msg.data["n"] * 2}

    b.subscribe("timer.*", on_timer)
    b.on("b.say", on_say)
    b.on("b.ask", on_ask)
    await start(a); await start(b)

    await a.emit("timer.done", {"id": 1}, private={"label": "pasta"})
    await a.command("b", "b.say", private={"text": "hello"})
    reply = await a.request("b", "b.ask", {"n": 21}, timeout=3)
    missing = await a.request("nobody", "x.ask", timeout=3)
    core = await a.request("core", "core.health", timeout=3)
    await asyncio.sleep(0.1)

    assert ("event", "timer.done", "pasta") in got
    assert ("command", "b.say", "hello") in got
    assert reply.data == {"answer": 42}
    assert "isn't running" in missing.data["error"]
    assert core.data["programs"]["a"]["state"] == OK
    rows = bus.trace.recent()
    types = [r["type"] for r in rows]
    assert "timer.done" in types and "b.say" in types and "b.ask" in types
    assert all("pasta" not in json.dumps(r) and "hello" not in json.dumps(r) for r in rows)


async def test_programs_cannot_impersonate(hub):
    bus, sock, connect, _ = hub
    seen = []
    a, b = await connect("a"), await connect("b")

    async def on_any(msg):
        seen.append(msg.source)

    b.subscribe("x.*", on_any)
    await start(a); await start(b)
    await a.send(Message("event", "x.y", source="core"))  # send() overwrites source anyway
    a._writer.write(Message("event", "x.z", source="core").encode())  # raw lie
    await a._writer.drain()
    await asyncio.sleep(0.1)
    assert seen == ["a", "a"]


async def test_client_reconnects_after_core_restart(hub, tmp_path):
    bus, sock, connect, _ = hub
    a = await connect("a")
    await start(a)
    await bus.stop()
    await asyncio.sleep(0.2)
    assert not a.connected.is_set()
    bus2 = Bus(sock, Trace(tmp_path / "pip2.db"), Health(["a"], restart=False))
    await bus2.start()
    await asyncio.wait_for(a.connected.wait(), 5)
    for _ in range(50):  # the core registers the client a moment after it connects
        if "a" in bus2.writers:
            break
        await asyncio.sleep(0.02)
    assert "a" in bus2.writers
    await bus2.stop()


async def test_big_replies_get_through_and_never_cut_a_program_off(hub):
    """A busy Google calendar made a >64 KB reply that silently cut pip-web off from pip-core for good."""
    from pip_assistant.common.messages import READ_LIMIT
    bus, sock, connect, _ = hub
    a, b = await connect("a"), await connect("b")

    async def big(msg):
        return {"events": ["x" * 300] * 1000}        # ~300 KB, like six weeks of a full calendar

    b.on("b.big", big)
    await start(a); await start(b)
    r = await a.request("b", "b.big", timeout=5)
    assert len(r.data["events"]) == 1000
    # Even a line past every limit is only skipped: the program stays connected and keeps working
    a._writer.write(b"{" + b"x" * (READ_LIMIT + 10) + b"}\n")
    await a._writer.drain()
    await asyncio.sleep(0.2)
    r = await a.request("b", "b.big", timeout=5)
    assert a.connected.is_set() and len(r.data["events"]) == 1000
