"""Start pip-web: Pip's web pages. It serves

    /          the dashboard (static/index.html): health, the Listening switch, the conversation feed,
               timers and calendar, people, memory, preferences, the decision trace
    /tv        the face on the monitor and its boxes (static/tv.html, D-110), with its own data and stream
    /m/<name>/ each module's own page

pip-web keeps nothing itself: `/api/...` requests are passed to pip-core over the bus, and `listen`
turns what pip-core publishes into live streams for the pages (`/api/stream`, `/api/tv/stream`).

Modules (pip_assistant/modules/, ARCHITECTURE §7) bring their own pages: pip-web serves a module's
static/ folder at /m/<name>/ and passes its requests (/api/m/<name>) to pip-core, which hands them
to the module. Everything in them (event titles, names) travels privately and is never logged;
secrets never reach pip-web.

Logins and roles arrive in step 8 (PIP-NEW-054). Until then it listens only on the addresses in
settings (on the Pi: the Tailscale address, plus 127.0.0.1 for the monitor's own browser), so only
the owner's devices and the Pi itself can reach it.
"""
import asyncio
import collections
import json
import mimetypes
import socket
from importlib import resources

import uvicorn
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .. import __version__
from ..common.messages import Message
from ..common.modules import enabled as enabled_modules
from ..common.program import make_bus, start
from ..common import vitals
from ..common.weather import Weather
from .security import SameSite, module_get_allowed

TRACE_KEEP = 300


class State:
    def __init__(self):
        self.health: dict = {}
        self.trace = collections.deque(maxlen=TRACE_KEEP)
        self.conversation = collections.deque(maxlen=120)  # live only, in memory; never stored (D-53, D-108)
        self.listeners: set[asyncio.Queue] = set()
        self.online = True       # D-95: the dashboard's small offline indicator
        self.pip_state = "idle"  # idle / listening / thinking / speaking / interrupted / ringing / offline / mic_off
        self.pip_detail: dict = {}   # what exactly he's doing within it: {detail, words, ends_in} (D-112)
        self.pip_quiet = None        # pip-core's "a good moment to restart" (core/states.py quiet())
        self.tv_listeners: set[asyncio.Queue] = set()   # the TV page's own, narrower stream (D-110)
        self.tv_boxes: list[str] = []   # the kinds of box on the monitor now (D-111)
        self.tv_room = False            # a conversation box showing room talk is up: room lines go to the TV too

    def push(self, kind: str, data: dict) -> None:
        for q in list(self.listeners):
            if q.qsize() < 200:
                q.put_nowait((kind, data))

    def screen(self, boxes: list, room: bool) -> None:
        self.tv_boxes, self.tv_room = [str(b) for b in boxes], bool(room)

    def push_tv(self, kind: str, data: dict) -> None:
        """Only what the TV shows: state, the mouth level, captions of lines to and from Pip, "refresh",
        the screen's options changing, and room talk only while a conversation box shows it (D-110, D-111)."""
        for q in list(self.tv_listeners):
            if q.qsize() < 200:
                q.put_nowait((kind, data))


def build_app(bus, state: State, cfg: dict | None = None) -> Starlette:
    page = resources.files("pip_assistant.web").joinpath("static/index.html").read_text()
    tv_page = resources.files("pip_assistant.web").joinpath("static/tv.html").read_text()
    weather = Weather((cfg or {}).get("thinker", {}).get("location", "Lincoln, Nebraska"))
    css = resources.files("pip_assistant.web").joinpath("static/pip.css").read_text()
    js = resources.files("pip_assistant.web").joinpath("static/pip.js").read_text()
    mods = {m.name: m for m in enabled_modules(cfg or {})}

    async def stylesheet(request):
        return Response(css, media_type="text/css", headers={"Cache-Control": "no-store"})

    async def script(request):
        return Response(js, media_type="text/javascript", headers={"Cache-Control": "no-store"})

    async def module_list(request):
        return JSONResponse([{"name": m.name, "label": m.label or m.name.title(), "page": f"/m/{m.name}/"}
                             for m in sorted(mods.values(), key=lambda m: m.label or m.name) if m.page])

    async def module_file(request):
        """A file from a module's static/ folder (its dashboard page). Nothing outside that folder."""
        m = mods.get(request.path_params["name"])
        rel = request.path_params.get("path") or (m.page if m else "")
        if m is None or not rel:
            return JSONResponse({"error": "no such module page"}, status_code=404)
        root = (m.path / "static").resolve()
        f = (root / rel).resolve()
        if root not in f.parents or not f.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        kind = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        return Response(f.read_bytes(), media_type=kind, headers={"Cache-Control": "no-store"})

    async def module_api(request):
        """GET ?action=…&… or POST {action, …} → the module in pip-core. All of it travels privately."""
        name = request.path_params["name"]
        if name not in mods:
            return JSONResponse({"error": "no such module"}, status_code=404)
        if request.method == "GET":
            args = dict(request.query_params)
        else:
            try:
                args = await request.json()
            except ValueError:   # not JSON (or not UTF-8)
                args = None
            if not isinstance(args, dict):
                return JSONResponse({"error": "send a JSON object"}, status_code=400)
        action = str(args.pop("action", "status"))
        if request.method == "GET" and not module_get_allowed(action):
            return JSONResponse({"error": "That needs a POST (it changes something)"}, status_code=405)
        try:
            r = await bus.request("core", "module.request", {"module": name, "action": action}, args, timeout=60)
        except Exception as e:
            return JSONResponse({"error": f"pip-core not reachable ({type(e).__name__})"}, status_code=503)
        return JSONResponse(r.private if r.private else r.data)

    async def index(request):
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    async def health(request):
        return JSONResponse({"version": __version__, "connected": bus.connected.is_set(), "programs": state.health,
                             "state": state.pip_state, "state_detail": {**state.pip_detail, "quiet": state.pip_quiet},
                             "online": state.online})

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

    async def core_get(kind: str):
        try:
            r = await bus.request("core", kind, timeout=3)
            return JSONResponse(r.private or {"error": r.data.get("error", "no answer")})
        except Exception as e:
            return JSONResponse({"error": f"pip-core not reachable ({type(e).__name__})"}, status_code=503)

    async def core_action(request, kind: str, private_keys: tuple, data_keys: tuple):
        """Names and memory texts are personal: they travel in the private part, never logged."""
        body = await request.json()
        private = {k: str(body.pop(k))[:300] for k in private_keys if k in body}
        data = {k: body[k] for k in data_keys if k in body}
        try:
            r = await bus.request("core", kind, data, private, timeout=30)
        except Exception as e:
            return JSONResponse({"error": f"pip-core not reachable ({type(e).__name__})"}, status_code=503)
        return JSONResponse({**r.data, **r.private})

    async def people(request):
        return await core_get("core.people")

    async def people_action(request):
        return await core_action(request, "core.people_action", ("name",), ("action", "id", "role", "value"))

    async def memory(request):
        return await core_get("core.memory")

    async def memory_action(request):
        return await core_action(request, "core.memory_action", ("text", "about"), ("action", "id", "category", "private", "value"))

    async def mic(request):
        """GET: is Pip listening? POST {"value": true/false}: the dashboard's Listening switch (D-91)."""
        data = {"value": bool((await request.json()).get("value"))} if request.method == "POST" else {}
        try:
            r = await bus.request("core", "core.mic", data, timeout=5)
        except Exception as e:
            return JSONResponse({"error": f"pip-core not reachable ({type(e).__name__})"}, status_code=503)
        return JSONResponse(r.data)

    async def prefs(request):
        """GET: the dashboard preferences (core/prefs.py) and the microphones there are.
        POST {"key", "value"}: change one."""
        data = {}
        if request.method == "POST":
            body = await request.json()
            data = {"key": str(body.get("key", "")), "value": body.get("value")}
        try:
            r = await bus.request("core", "core.prefs", data, timeout=10)
        except Exception as e:
            return JSONResponse({"error": f"pip-core not reachable ({type(e).__name__})"}, status_code=503)
        return JSONResponse(r.data)

    async def conversation(request):
        return JSONResponse(list(state.conversation)[::-1])

    def sse(listeners: set):
        q: asyncio.Queue = asyncio.Queue()
        listeners.add(q)

        async def events():
            try:
                yield "retry: 3000\n\n"
                while True:
                    try:
                        kind, data = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"event: {kind}\ndata: {json.dumps(data, default=str)}\n\n"
                    except asyncio.TimeoutError:
                        yield "event: ping\ndata: {}\n\n"   # a real event, so pages can tell a hung stream
            finally:
                listeners.discard(q)

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    async def stream(request):
        return sse(state.listeners)

    # ---------------------------------------------------------------- the TV page (D-110): the face and its boxes
    async def tv(request):
        return HTMLResponse(tv_page, headers={"Cache-Control": "no-store"})

    async def settings_page(request):
        """The Settings page (step 8, D-117). Read fresh each time, so it never needs a restart to update."""
        html = resources.files("pip_assistant.web").joinpath("static/settings.html").read_text()
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    async def changes_page(request):
        """The Changes page (M1b): Pip's self-changes, waiting for the owner's approvals."""
        html = resources.files("pip_assistant.web").joinpath("static/changes.html").read_text()
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    def passed_to_core(msg_type: str, timeout: float = 15, target: str = "core", get: str = "get"):
        """A route that hands its request to pip-core (or another program): GET → {"action": get}, POST → the
        JSON body. It travels privately and comes back privately."""
        async def handler(request):
            if request.method == "POST":
                try:
                    body = await request.json()
                except ValueError:
                    return JSONResponse({"error": "That request isn't JSON"}, status_code=400)
                if not isinstance(body, dict):
                    return JSONResponse({"error": "That request isn't a JSON object"}, status_code=400)
            else:
                body = {"action": get}
            try:
                r = await bus.request(target, msg_type, {}, body, timeout=timeout)
            except Exception as e:
                return JSONResponse({"error": f"pip-{target} not reachable ({type(e).__name__})"}, status_code=503)
            return JSONResponse(r.private if r.private else r.data)
        return handler

    async def tv_data(request):
        """Everything the TV page needs when it loads or refreshes: Pip's state, what pip-core sends
        (timers, events, the screen's layout and its to-do lists: core/main.py tv_snapshot), the weather,
        and the Pi's health numbers if that box is showing."""
        out = {"state": state.pip_state, "state_detail": state.pip_detail, "online": state.online, "weather": {}}
        try:
            r = await bus.request("core", "core.tv", timeout=3)
            out.update(r.private or {})
            if screen := out.get("screen"):
                sections = screen["view"]["sections"]
                state.screen([x["type"] for x in sections],
                             any(x["type"] == "conversation" and x.get("room", True) for x in sections))
        except Exception:
            out["error"] = "pip-core not reachable"
        out["weather"] = await weather.screen_within(1.0)   # never hold the page up for a slow weather service
        if "health" in state.tv_boxes:
            out["vitals"] = vitals_now()
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    async def tv_vitals(request):
        """The Pi health box's numbers, every few seconds while it's on the screen (nothing personal)."""
        return JSONResponse(vitals_now() if "health" in state.tv_boxes else {}, headers={"Cache-Control": "no-store"})

    def vitals_now() -> dict:
        ok = sum(1 for p in state.health.values() if isinstance(p, dict) and p.get("state") == "ok")
        return {**vitals.read(), "programs_ok": ok, "programs": len(state.health)}

    async def tv_stream(request):
        return sse(state.tv_listeners)

    return Starlette(routes=[Route("/", index), Route("/pip.css", stylesheet), Route("/pip.js", script), Route("/api/modules", module_list),
                             Route("/tv", tv), Route("/api/tv", tv_data), Route("/api/tv/stream", tv_stream), Route("/api/tv/vitals", tv_vitals),
                             Route("/m/{name}/", module_file), Route("/m/{name}/{path:path}", module_file),
                             Route("/api/m/{name}", module_api, methods=["GET", "POST"]),
                             Route("/api/health", health), Route("/api/trace", trace), Route("/api/conversation", conversation),
                             Route("/api/alerts", alerts), Route("/api/alerts/action", alert_action, methods=["POST"]),
                             Route("/api/people", people), Route("/api/people/action", people_action, methods=["POST"]),
                             Route("/api/memory", memory), Route("/api/memory/action", memory_action, methods=["POST"]),
                             Route("/api/mic", mic, methods=["GET", "POST"]),
                             Route("/api/prefs", prefs, methods=["GET", "POST"]), Route("/api/stream", stream),
                             Route("/settings", settings_page),
                             Route("/api/settings", passed_to_core("core.settings", 300), methods=["GET", "POST"]),
                             Route("/api/personality", passed_to_core("core.personality", 70), methods=["GET", "POST"]),
                             Route("/api/migrate", passed_to_core("core.migrate", 60), methods=["GET", "POST"]),
                             Route("/changes", changes_page),
                             Route("/api/changes", passed_to_core("builder.page", 30, target="builder", get="list"),
                                   methods=["GET", "POST"])])


def listen(bus, state: State) -> None:
    """What pip-web hears from pip-core, and where it goes: the dashboard's stream, and the TV page's
    narrower one (D-110: state, mouth level, captions of lines to and from Pip, "refresh"; D-111: room
    talk only while a conversation box on the monitor shows it)."""

    async def on_health(msg: Message):
        state.health = msg.data.get("programs", {})
        state.push("health", state.health)

    async def on_trace(msg: Message):
        state.trace.append(msg.data)
        state.push("trace", msg.data)

    async def on_line(msg: Message):
        line = {"ts": msg.ts, **msg.data, "text": msg.private.get("text", "")}
        if msg.private.get("speaker"):
            line["speaker"] = msg.private["speaker"]
        state.conversation.append(line)
        state.push("conversation", line)
        if line.get("who") == "pip" or line.get("how") == "direct":   # captions: only lines to and from Pip
            state.push_tv("caption", {"who": "pip" if line.get("who") == "pip" else "you",
                                      "speaker": line.get("speaker", ""), "text": line["text"]})
        elif state.tv_room:   # room talk: only while a conversation box shows it (D-111)
            state.push_tv("line", {"who": "room", "how": line.get("how", "room"),
                                   "speaker": line.get("speaker", ""), "text": line["text"]})

    def feed_event(ts: float, kind: str, **data) -> None:
        """An event bar on the conversation feed (D-108): what happened, no words."""
        line = {"ts": ts, "who": "event", "kind": kind, **data}
        state.conversation.append(line)
        state.push("conversation", line)

    async def on_event(msg: Message):
        feed_event(msg.ts, **msg.data)

    async def on_alert(msg: Message):
        state.push("alerts", {"type": msg.type})
        state.push_tv("refresh", {})
        what = msg.type.split(".", 1)[1]
        if what in ("ringing", "stopped", "snoozed"):
            feed_event(msg.ts, "alert", what=what, alert=msg.data.get("kind", ""), minutes=msg.data.get("minutes"))

    async def on_state(msg: Message):
        state.pip_state = msg.data.get("state", "idle")
        state.pip_detail = {k: msg.data.get(k) for k in ("detail", "words", "ends_in")}
        state.pip_quiet = msg.data.get("quiet")   # for installs' quiet moment (release.py), in /api/health only
        state.push("state", {"state": state.pip_state, **state.pip_detail})
        state.push_tv("state", {"state": state.pip_state, **state.pip_detail})

    async def on_level(msg: Message):
        state.push_tv("level", {"level": msg.data.get("level", 0)})

    async def on_voice(msg: Message):
        state.push("people", {"type": msg.type, **msg.data})

    async def on_mic(msg: Message):
        state.push("mic", msg.data)

    async def on_net(msg: Message):
        was, state.online = state.online, bool(msg.data.get("online", True))
        state.push("net", {"online": state.online})
        if was != state.online:
            feed_event(msg.ts, "online" if state.online else "offline")

    async def on_module(msg: Message):
        _, name, kind = (msg.type.split(".", 2) + ["", ""])[:3]
        state.push("module", {"module": name, "type": kind})   # module pages reload what changed
        if name == "calendar" or (name == "todos" and "todos" in state.tv_boxes):
            state.push_tv("refresh", {})
        if name == "screen":   # the screen changed (D-111): the monitor fetches its new layout
            state.screen(msg.data.get("boxes", []), msg.data.get("room", False))
            state.push_tv("options" if kind == "options" else "refresh", {})

    bus.subscribe("health.snapshot", on_health)
    bus.subscribe("trace.entry", on_trace)
    bus.subscribe("conversation.line", on_line)
    bus.subscribe("conversation.event", on_event)
    bus.subscribe("alert.*", on_alert)
    bus.subscribe("pip.state", on_state)
    bus.subscribe("speech.level", on_level)
    bus.subscribe("voice.*", on_voice)
    bus.subscribe("mic.changed", on_mic)
    bus.subscribe("net.changed", on_net)
    bus.subscribe("module.*", on_module)

    async def on_builder(msg: Message):   # a self-change moved on (M1b): the Changes page and the badge reload
        state.push("builder", {"id": msg.data.get("id"), "status": msg.data.get("status")})
    bus.subscribe("builder.changed", on_builder)


async def main() -> None:
    bus, cfg = make_bus("web")
    state = State()
    listen(bus, state)
    # Only pages from Pip himself may change things: no logins (D-116), so a same-site check stops any other
    # website open on your phone from doing it (FEAT-072, web/security.py). The tests use build_app directly.
    app = SameSite(build_app(bus, state, cfg))
    w = cfg["web"]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", proxy_headers=False,
                                           timeout_graceful_shutdown=3))  # live pages would otherwise hold a stop for 90 s
    hosts = list(dict.fromkeys([w["host"], w.get("tv_host") or w["host"]]))   # Tailscale, and localhost for the monitor
    sockets = []
    for host in hosts:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, w["port"]))
        sockets.append(s)
    bus_task = asyncio.create_task(bus.run())
    try:
        await server.serve(sockets=sockets)  # handles SIGTERM itself
    finally:
        bus_task.cancel()


def run() -> None:
    start("web", main)


if __name__ == "__main__":
    run()
