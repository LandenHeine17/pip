"""Is Pip online? (D-95) A light check every 30 s that Groq can be reached: no audio, no key, nothing
about anyone. Offline after two failed checks in a row (or at once when speech-to-text fails);
online again on the first success. Published as `net.changed {online}` for the dashboard's small
offline indicator; Conversation uses it to fall back to Pip's own offline commands (D-83, D-94).

The check sends no key on purpose, so Groq answers "401 Unauthorized": that still means reachable. httpx
logs every request, so those lines are dropped from the log (`QuietCheck`) instead of showing up as a
fake error every 30 s; speech-to-text's own requests are still logged.
"""
import asyncio
import logging

import httpx

from ..common.messages import Message

log = logging.getLogger(__name__)
URL = "https://api.groq.com/openai/v1/models"   # any answer (even "not signed in") means reachable


class QuietCheck(logging.Filter):
    """Drops httpx's "HTTP Request: GET …/models 401 Unauthorized" line for the online check."""
    def filter(self, record: logging.LogRecord) -> bool:
        return URL not in record.getMessage()


logging.getLogger("httpx").addFilter(QuietCheck())


class Net:
    def __init__(self, bus, every: float = 30.0):
        self.bus, self.every = bus, every
        self.online = True
        self.fails = 0

    async def check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as http:
                await http.get(URL, headers={"User-Agent": "pip-assistant"})
            return True
        except Exception:
            return False

    async def set(self, online: bool) -> None:
        if online:
            self.fails = 0
        if online != self.online:
            self.online = online
            log.info("now %s", "online" if online else "offline")
            await self.bus.publish(Message("event", "net.changed", data={"online": online}))

    async def failed(self) -> None:
        """Speech-to-text couldn't reach Groq: offline right away."""
        self.fails = 2
        await self.set(False)

    async def run(self) -> None:
        while True:
            if await self.check():
                await self.set(True)
            else:
                self.fails += 1
                if self.fails >= 2:
                    await self.set(False)
            await asyncio.sleep(self.every)
