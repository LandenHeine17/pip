"""The message hub inside pip-core. Every program connects here; every message passes through
and is logged to the decision trace (docs/ARCHITECTURE.md §3)."""
import asyncio
import contextlib
import fnmatch
import logging
import os
import time

from ..common.messages import BadMessage, Message
from .health import Health
from .trace import Trace

log = logging.getLogger(__name__)


class Bus:
    def __init__(self, socket_path, trace: Trace, health: Health):
        self.socket_path = socket_path
        self.trace = trace
        self.health = health
        self.writers: dict[str, asyncio.StreamWriter] = {}
        self.patterns: dict[str, list[str]] = {}
        self.waiting: dict[str, str] = {}      # request id -> who asked
        self.core_handlers: dict = {}           # requests addressed to "core"
        self.core_listeners: list = []          # (pattern, coroutine) for events core itself reacts to
        self.core_waiting: dict = {}            # request id -> future, for requests core itself sent
        self.server = None

    async def start(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.socket_path)
        self.server = await asyncio.start_unix_server(self._client, path=str(self.socket_path), limit=8 * 1024 * 1024)
        os.chmod(self.socket_path, 0o600)  # only this user's programs may connect

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for w in list(self.writers.values()):
            w.close()

    # ---------------------------------------------------------------- connections
    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        name = None
        try:
            while line := await reader.readline():
                try:
                    msg = Message.decode(line)
                except BadMessage as e:
                    log.warning("bad message from %s: %s", name or "?", e)
                    continue
                if msg.kind == "hello":
                    name = msg.source
                    old = self.writers.get(name)
                    if old and old is not writer:
                        old.close()
                    self.writers[name] = writer
                    self.health.heartbeat(name, {"pid": msg.data.get("pid")})
                    await self.publish(Message("event", "program.connected", source="core", data={"program": name}))
                    continue
                if not name:
                    continue  # must say hello first
                msg.source = name  # programs can't pretend to be someone else
                await self.route(msg)
        except (ConnectionError, OSError):
            pass
        finally:
            if name and self.writers.get(name) is writer:
                del self.writers[name]
                self.patterns.pop(name, None)
                self.health.disconnected(name)
                await self.publish(Message("event", "program.disconnected", source="core", data={"program": name}))
            with contextlib.suppress(Exception):
                writer.close()

    async def _send(self, name: str, msg: Message) -> bool:
        w = self.writers.get(name)
        if not w:
            return False
        try:
            w.write(msg.encode())
            await w.drain()
            return True
        except (ConnectionError, OSError):
            return False

    # ---------------------------------------------------------------- routing
    async def route(self, msg: Message) -> None:
        if msg.kind == "heartbeat":
            self.health.heartbeat(msg.source, msg.data)
            return
        if msg.kind == "subscribe":
            self.patterns[msg.source] = list(msg.data.get("patterns", []))
            return
        self._log(msg)
        if msg.kind == "event":
            await self._deliver_event(msg)
        elif msg.kind == "command":
            if not await self._send(msg.target, msg):
                await self.publish(Message("event", "message.undelivered", source="core",
                                           data={"type": msg.type, "target": msg.target, "from": msg.source}))
        elif msg.kind == "request":
            if msg.target == "core":
                await self._answer_core(msg)
            elif msg.target in self.writers:
                self.waiting[msg.id] = msg.source
                await self._send(msg.target, msg)
            else:
                await self._reply_to(msg.source, msg.reply({"error": f"{msg.target} isn't running"}, source="core"))
        elif msg.kind == "reply":
            asker = self.waiting.pop(msg.id, msg.target)
            await self._reply_to(asker, msg)

    async def _reply_to(self, asker: str, msg: Message) -> None:
        if asker == "core":
            fut = self.core_waiting.get(msg.id)
            if fut and not fut.done():
                fut.set_result(msg)
        else:
            await self._send(asker, msg)

    async def publish(self, msg: Message) -> None:
        """An event raised by core itself."""
        msg.source = msg.source or "core"
        self._log(msg)
        await self._deliver_event(msg)

    def core_listen(self, pattern: str, handler) -> None:
        """Let a part of pip-core react to events (e.g. the conversation reacting to heard.wake)."""
        self.core_listeners.append((pattern, handler))

    async def _deliver_event(self, msg: Message) -> None:
        for pattern, handler in self.core_listeners:
            if msg.source != "core" and fnmatch.fnmatchcase(msg.type, pattern):
                try:
                    await handler(msg)
                except Exception:
                    log.exception("core handler for %s failed", msg.type)
        for name, pats in list(self.patterns.items()):
            if name != msg.source and any(fnmatch.fnmatchcase(msg.type, p) for p in pats):
                await self._send(name, msg)

    def _log(self, msg: Message) -> None:
        row = self.trace.add(msg)
        if row is None:
            return
        # Live copy for watchers (e.g. the dashboard). Not traced itself, so no loops.
        live = Message("event", "trace.entry", source="core", data=row)
        for name, pats in list(self.patterns.items()):
            if any(fnmatch.fnmatchcase("trace.entry", p) for p in pats) and name != msg.source:
                asyncio.ensure_future(self._send(name, live))

    async def _answer_core(self, msg: Message) -> None:
        """Handlers return data, or (data, private) to send something that must not be logged."""
        handler = self.core_handlers.get(msg.type)
        try:
            result = handler(msg) if handler else {"error": f"core doesn't handle {msg.type}"}
            if asyncio.iscoroutine(result):
                result = await result
        except Exception as e:
            log.exception("core request %s failed", msg.type)
            result = {"error": f"{type(e).__name__}: {e}"}
        data, private = result if isinstance(result, tuple) else (result, {})
        await self._send(msg.source, msg.reply(data, private, source="core"))

    # ---------------------------------------------------------------- housekeeping
    async def housekeeping(self, every: float = 2.0) -> None:
        last_prune = 0.0
        while True:
            await asyncio.sleep(every)
            self.trace.flush()
            for action in self.health.check():
                await self.publish(Message("event", "health.restarted", source="core", data=action))
            await self.publish_health()
            if time.time() - last_prune > 3600:
                self.trace.prune()
                if getattr(self, "schedule", None):
                    self.schedule.prune_events()
                last_prune = time.time()

    async def publish_health(self) -> None:
        snap = self.health.snapshot()
        msg = Message("event", "health.snapshot", source="core", data={"programs": snap})
        await self._deliver_event(msg)  # delivered live, not traced every 2 s
