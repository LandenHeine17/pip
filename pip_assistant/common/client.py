"""How a program connects to pip-core: introduce itself, send heartbeats, handle messages.

    bus = BusClient("ears")
    bus.on("ears.say", handle_say)            # commands/requests addressed to this program
    bus.subscribe("timer.*")                  # events from others
    await bus.run()                            # connects (and reconnects) forever
    await bus.emit("heard.wake", {"word": "pippletron"})
    reply = await bus.request("thinker", "thinker.ask", {...}, timeout=20)
"""
import asyncio
import contextlib
import fnmatch
import logging
import os
import time
from typing import Awaitable, Callable

from . import paths
from .messages import BadMessage, Message, new_id

log = logging.getLogger(__name__)
Handler = Callable[[Message], Awaitable[Message | dict | None]]


def rss_mb() -> float:
    """This process's memory use (resident set), in MB."""
    try:
        with open("/proc/self/statm") as fh:
            return round(int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576, 1)
    except (OSError, ValueError):
        return 0.0


class BusClient:
    def __init__(self, name: str, socket_path=None, heartbeat_seconds: float = 2.0):
        self.name = name
        self.socket_path = socket_path or paths.core_socket()
        self.heartbeat_seconds = heartbeat_seconds
        self.handlers: dict[str, Handler] = {}
        self.listeners: list[tuple[str, Handler]] = []
        self.patterns: list[str] = []
        self.started = time.time()
        self.connected = asyncio.Event()
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self.status: dict = {}  # extra fields reported with each heartbeat

    def on(self, msg_type: str, handler: Handler) -> None:
        """Handle commands and requests of this type addressed to this program."""
        self.handlers[msg_type] = handler

    def subscribe(self, pattern: str, handler: Handler | None = None) -> None:
        """Receive events whose type matches `pattern` (e.g. "timer.*")."""
        self.patterns.append(pattern)
        if handler:
            self.listeners.append((pattern, handler))

    async def send(self, msg: Message) -> None:
        if not self._writer:
            raise ConnectionError("not connected to pip-core")
        msg.source = self.name
        self._writer.write(msg.encode())
        await self._writer.drain()

    async def emit(self, msg_type: str, data: dict | None = None, private: dict | None = None) -> None:
        await self.send(Message("event", msg_type, data=data or {}, private=private or {}))

    async def command(self, target: str, msg_type: str, data: dict | None = None, private: dict | None = None) -> None:
        await self.send(Message("command", msg_type, target=target, data=data or {}, private=private or {}))

    async def request(self, target: str, msg_type: str, data: dict | None = None,
                      private: dict | None = None, timeout: float = 10.0) -> Message:
        msg = Message("request", msg_type, target=target, id=new_id(), data=data or {}, private=private or {})
        fut = asyncio.get_running_loop().create_future()
        self._pending[msg.id] = fut
        try:
            await self.send(msg)
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(msg.id, None)

    async def run(self) -> None:
        """Connect to pip-core and keep the connection up, reconnecting with back-off."""
        delay = 0.5
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(str(self.socket_path))
            except OSError:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 5.0)
                continue
            delay = 0.5
            self._writer = writer
            try:
                await self.send(Message("hello", "hello", data={"pid": os.getpid()}))
                if self.patterns:
                    await self.send(Message("subscribe", "subscribe", data={"patterns": self.patterns}))
                self.connected.set()
                beat = asyncio.create_task(self._heartbeat())
                try:
                    await self._read(reader)
                finally:
                    beat.cancel()
            except (ConnectionError, OSError) as e:
                log.warning("%s: lost connection to pip-core (%s)", self.name, e)
            finally:
                self.connected.clear()
                self._writer = None
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(ConnectionError("pip-core connection lost"))
                with contextlib.suppress(Exception):
                    writer.close()
            await asyncio.sleep(delay)

    async def _heartbeat(self) -> None:
        while True:
            await self.send(Message("heartbeat", "heartbeat", data={
                "rss_mb": rss_mb(), "uptime_s": round(time.time() - self.started), **self.status}))
            await asyncio.sleep(self.heartbeat_seconds)

    async def _read(self, reader: asyncio.StreamReader) -> None:
        while line := await reader.readline():
            try:
                msg = Message.decode(line)
            except BadMessage as e:
                log.warning("%s: ignoring bad message: %s", self.name, e)
                continue
            asyncio.create_task(self._dispatch(msg))

    async def _dispatch(self, msg: Message) -> None:
        if msg.kind == "reply":
            fut = self._pending.get(msg.id)
            if fut and not fut.done():
                fut.set_result(msg)
            return
        if msg.kind == "event":
            for pattern, handler in self.listeners:
                if fnmatch.fnmatchcase(msg.type, pattern):
                    await self._safely(handler, msg)
            return
        handler = self.handlers.get(msg.type)
        if msg.kind == "request":
            if not handler:
                await self.send(msg.reply({"error": f"{self.name} doesn't handle {msg.type}"}))
                return
            result = await self._safely(handler, msg)
            if isinstance(result, Message):
                await self.send(result)
            else:
                await self.send(msg.reply(result if isinstance(result, dict) else {}))
        elif msg.kind == "command" and handler:
            await self._safely(handler, msg)

    async def _safely(self, handler: Handler, msg: Message):
        try:
            return await handler(msg)
        except Exception as e:  # one bad handler must never take the program down
            log.exception("%s: handler for %s failed", self.name, msg.type)
            return {"error": f"{type(e).__name__}: {e}"}
