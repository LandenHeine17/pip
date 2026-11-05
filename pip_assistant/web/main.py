"""Start pip-web: the dashboard. Step 1 shows program health and the live decision trace;
step 4 adds timers, alarms, reminders and the calendar (with Shut off / Snooze buttons).

Logins and roles arrive in step 8 (PIP-NEW-054). Until then it listens only on the address in
settings (on the Pi: the Tailscale address), so only the owner's devices can reach it.
"""
import asyncio
import collections
import json
from importlib import resources

import uvicorn
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

from .. import __version__
from ..common.messages import Message
from ..common.program import make_bus, start

TRACE_KEEP = 300


class State:
    def __init__(self):
        self.health: dict = {}
        self.trace = collections.deque(maxlen=TRACE_KEEP)
        self.conversation = collections.deque(maxlen=50)  # live only, in memory; never stored (D-53)
        self.listeners: set[asyncio.Queue] = set()

    def push(self, kind: str, data: dict) -> None:
        for q in list(self.listeners):
            if q.qsize() < 200:
                q.put_nowait((kind, data))


def build_app(bus, state: State) -> Starlette:
    page = resources.files("pip_assistant.web").joinpath("static/index.html").read_text()

    async def index(request):
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    async def health(request):
        return JSONResponse({"version": __version__, "connected": bus.connected.is_set(), "programs": state.health})

    async def trace(request):
        if not state.trace and bus.connected.is_set():
            try:
                r = await bus.request("core", "core.trace", {"limit": TRACE_KEEP}, timeout=3)
                for e in reversed(r.data.get("entries", [])):
                    state.trace.append(e)
            except Exception:
                pass
        return JSONResponse(list(state.trace)[::-1][:200])

    async def alerts(request):
        try:
            r = await bus.request("core", "core.alerts", timeout=3)
            return JSONResponse(r.private or {"error": r.data.get("error", "no answer")})
        except Exception as e:
            return JSONResponse({"error": f"pip-core not reachable ({type(e).__name__})"}, status_code=503)

    async def alert_action(request):
        """Shut off / snooze / cancel / new timer / typed command. Labels and typed text travel privately."""
        body = await request.json()
        private = {k: str(body.pop(k))[:200] for k in ("label", "text") if k in body}
        data = {k: body[k] for k in ("action", "id", "minutes") if k in body}
        try:
            r = await bus.request("core", "core.alert_action", data, private, timeout=5)
        except Exception as e:
            return JSONResponse({"error": f"pip-core not reachable ({type(e).__name__})"}, status_code=503)
        return JSONResponse({**r.data, **r.private})

    async def conversation(request):
        return JSONResponse(list(state.conversation)[::-1])

    async def stream(request):
        q: asyncio.Queue = asyncio.Queue()
        state.listeners.add(q)

        async def events():
            try:
                yield "retry: 3000\n\n"
                while True:
                    try:
                        kind, data = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"event: {kind}\ndata: {json.dumps(data, default=str)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                state.listeners.discard(q)

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    return Starlette(routes=[Route("/", index), Route("/api/health", health), Route("/api/trace", trace), Route("/api/conversation", conversation),
                             Route("/api/alerts", alerts), Route("/api/alerts/action", alert_action, methods=["POST"]),
                             Route("/api/stream", stream)])


async def main() -> None:
    bus, cfg = make_bus("web")
    state = State()

    async def on_health(msg: Message):
        state.health = msg.data.get("programs", {})
        state.push("health", state.health)

    async def on_trace(msg: Message):
        state.trace.append(msg.data)
        state.push("trace", msg.data)

    async def on_line(msg: Message):
        line = {"ts": msg.ts, **msg.data, "text": msg.private.get("text", "")}
        state.conversation.append(line)
        state.push("conversation", line)

    async def on_alert(msg: Message):
        state.push("alerts", {"type": msg.type})

    bus.subscribe("alert.*", on_alert)
    bus.subscribe("conversation.line", on_line)
    bus.subscribe("health.snapshot", on_health)
    bus.subscribe("trace.entry", on_trace)
    app = build_app(bus, state)
    server = uvicorn.Server(uvicorn.Config(app, host=cfg["web"]["host"], port=cfg["web"]["port"],
                                           log_level="warning", proxy_headers=False))
    bus_task = asyncio.create_task(bus.run())
    try:
        await server.serve()  # handles SIGTERM itself
    finally:
        bus_task.cancel()


def run() -> None:
    start("web", main)


if __name__ == "__main__":
    run()
