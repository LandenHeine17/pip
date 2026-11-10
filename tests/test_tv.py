"""Step 7 (D-110): the TV page, its own narrow stream, what pip-core sends it, the mouth level, clothing."""
import asyncio
import fnmatch
import time

from starlette.testclient import TestClient

from pip_assistant.common.messages import Message
from pip_assistant.common.weather import dress
from pip_assistant.core.trace import Trace
from pip_assistant.web.main import State, build_app, listen


class FakeBus:
    def __init__(self, reply=None):
        self.handlers, self.reply = [], reply or {}

    class connected:
        @staticmethod
        def is_set():
            return True

    def subscribe(self, pattern, handler=None):
        self.handlers.append((pattern, handler))

    async def request(self, target, kind, data=None, private=None, timeout=5):
        return Message("reply", kind, data={}, private=self.reply.get(kind, {}))

    async def deliver(self, msg: Message):
        for pattern, h in self.handlers:
            if h and fnmatch.fnmatchcase(msg.type, pattern):
                await h(msg)


def tv_feed(state: State) -> asyncio.Queue:
    q = asyncio.Queue()
    state.tv_listeners.add(q)
    return q


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


async def test_the_tv_hears_only_what_it_shows():
    """PIP-NEW-056: captions only of lines to and from Pip; never room talk, the trace, or the people list."""
    bus, state = FakeBus(), State()
    listen(bus, state)
    q = tv_feed(state)
    line = lambda data, text: Message("event", "conversation.line", data=data, private={"text": text, "speaker": "Landen"})
    await bus.deliver(line({"who": "room", "how": "room"}, "did you see the game"))           # room talk
    await bus.deliver(line({"who": "room", "how": "mentioned"}, "Pip was glitchy"))
    await bus.deliver(line({"who": "room", "how": "direct"}, "Pip, how long on the pasta?"))   # said to Pip
    await bus.deliver(line({"who": "pip"}, "About seven minutes."))
    await bus.deliver(Message("event", "pip.state", data={"state": "speaking"}))
    await bus.deliver(Message("event", "speech.level", data={"level": 0.4}))
    await bus.deliver(Message("event", "trace.entry", data={"type": "x"}))
    await bus.deliver(Message("event", "voice.enrolled", data={}))
    await bus.deliver(Message("event", "alert.ringing", data={"id": "a", "kind": "timer"}))
    got = drain(q)
    assert [k for k, _ in got] == ["caption", "caption", "state", "level", "refresh"]
    texts = [d["text"] for k, d in got if k == "caption"]
    assert texts == ["Pip, how long on the pasta?", "About seven minutes."]
    assert "game" not in str(got) and "glitchy" not in str(got)


def test_the_tv_page_and_its_snapshot(monkeypatch):
    from pip_assistant.common import weather

    async def fixed(self):
        return {"temp": 49, "wear": "umbrella"}
    monkeypatch.setattr(weather.Weather, "screen", fixed)
    snap = {"now": time.time(), "alerts": [{"kind": "timer", "label": "pasta", "due": time.time() + 60}], "events": []}
    client = TestClient(build_app(FakeBus({"core.tv": snap}), State(), {}))
    page = client.get("/tv")
    assert page.status_code == 200 and "<canvas" in page.text and "/api/tv/stream" in page.text
    r = client.get("/api/tv").json()
    assert r["alerts"][0]["label"] == "pasta" and r["weather"]["wear"] == "umbrella" and r["state"] == "idle"


def test_pip_core_sends_the_tv_no_private_events_and_only_soon_heads_ups(tmp_path):
    from datetime import datetime, timedelta

    from pip_assistant.core.dashapi import tv_snapshot
    from pip_assistant.core.schedule import Schedule
    from modhelp import registry
    sched = Schedule(tmp_path / "pip.db")
    reg = registry(tmp_path, schedule=sched, names=("google", "calendar"))
    cal = reg.module("calendar").store
    soon = datetime.now() + timedelta(hours=3)
    cal.add_event("Dentist", soon)
    cal.add_event("Therapy", soon + timedelta(hours=1), private=True)
    sched.add("timer", "pasta", time.time() + 600, length=600)
    sched.add("event", "Far away", time.time() + 3 * 3600)
    sched.add("event", "Very soon", time.time() + 600)
    snap = tv_snapshot(sched, reg)
    assert [e["title"] for e in snap["boxes"]["calendar"]["events"]] == ["Dentist"]   # the calendar module's tv()
    assert {a["label"] for a in snap["alerts"]} == {"pasta", "Very soon"}


def test_the_mouth_level_never_goes_in_the_trace(tmp_path):
    t = Trace(tmp_path / "pip.db")
    assert t.add(Message("event", "speech.level", source="ears", data={"level": 0.5})) is None
    assert t.add(Message("event", "speech.started", source="ears", data={})) is not None


def test_what_the_face_wears():
    assert dress(95, 60) == "storm" and dress(63, 50) == "umbrella" and dress(73, 20) == "snow"
    assert dress(3, 30) == "beanie" and dress(0, 75) == "sunglasses" and dress(0, 75, day=False) == ""
    assert dress(2, 55) == ""


async def test_room_talk_reaches_the_tv_only_with_a_conversation_box_showing_it():
    """D-111: the owner wants room talk in the Conversation box; it's sent only while one shows it."""
    bus, state = FakeBus(), State()
    listen(bus, state)
    q = tv_feed(state)
    room = Message("event", "conversation.line", data={"who": "room", "how": "room"}, private={"text": "did you see the game"})
    await bus.deliver(room)
    assert drain(q) == []
    await bus.deliver(Message("event", "module.screen.changed", data={"layout": True, "boxes": ["clock", "conversation"], "room": True}))
    await bus.deliver(room)
    got = drain(q)
    assert ("refresh", {}) in got and ("line", {"who": "room", "how": "room", "speaker": "", "text": "did you see the game"}) in got
    await bus.deliver(Message("event", "module.screen.changed", data={"layout": True, "boxes": ["conversation"], "room": False}))
    await bus.deliver(room)
    assert [k for k, _ in drain(q)] == ["refresh"]
    await bus.deliver(Message("event", "module.screen.options", data={"layout": True, "boxes": [], "room": False}))
    assert [k for k, _ in drain(q)] == ["options"]


def test_pi_health_only_while_its_box_is_up():
    snap = {"now": time.time(), "alerts": [], "boxes": {},
            "layout": {"view": {"sections": [{"type": "clock"}]}}}
    state = State()
    client = TestClient(build_app(FakeBus({"core.tv": snap}), state, {}))
    assert "vitals" not in client.get("/api/tv").json() and client.get("/api/tv/vitals").json() == {}
    snap["layout"]["view"]["sections"].append({"type": "health"})
    assert "memory" in client.get("/api/tv").json()["vitals"]
    assert "cpu" in client.get("/api/tv/vitals").json()


def test_pip_core_sends_the_screen_and_only_the_lists_on_it(tmp_path):
    from pip_assistant.core.dashapi import tv_snapshot
    from pip_assistant.core.schedule import Schedule
    from modhelp import registry
    sched = Schedule(tmp_path / "pip.db")
    reg = registry(tmp_path, schedule=sched, names=("google", "todos", "screen"))
    reg.module("todos").store.add("milk", "pip")
    snap = tv_snapshot(sched, reg)
    assert snap["layout"]["name"] == "default" and "todos" not in snap["boxes"]
    reg.modules["screen"].screen.show("morning")    # it has the household's to-do list
    snap = tv_snapshot(sched, reg)
    assert snap["boxes"]["todos"]["lists"][""]["items"] == ["milk"]   # the to-do module's tv()


def test_every_situation_has_words_and_its_faces_match_and_it_reaches_the_tv():
    """D-112: every situation in core/states.py has words to show under the face (nothing is ever blank),
    each expression in tv.html keyed "state:detail" is a real situation, and the busy ones have their own."""
    import asyncio
    import re
    from pathlib import Path
    from pip_assistant.core.states import DETAILS, SITUATIONS
    page = (Path(__file__).parent.parent / "pip_assistant/web/static/tv.html").read_text()
    assert all(words for words in DETAILS.values())
    assert sum(len(d) for d in SITUATIONS.values()) == len(DETAILS)          # no detail name used twice
    for key in re.findall(r'"(\w+):(\w+)":', page):
        assert key[1] in SITUATIONS.get(key[0], {}), key
    for state, detail in [("listening", d) for d in SITUATIONS["listening"]] + [
            ("thinking", "searching"), ("thinking", "looking_up"), ("thinking", "voice"), ("idle", "following"),
            ("idle", "mentioned"), ("idle", "joining"), ("speaking", "joining_in"), ("speaking", "welcome")]:
        assert f'"{state}:{detail}"' in page, detail
    bus, state = FakeBus(), State()
    listen(bus, state)
    q = tv_feed(state)
    asyncio.run(bus.deliver(Message("event", "pip.state", data={"state": "listening", "detail": "turn",
                                                                 "words": DETAILS["turn"], "ends_in": 2.5})))
    assert drain(q) == [("state", {"state": "listening", "detail": "turn", "words": DETAILS["turn"], "ends_in": 2.5})]
    client = TestClient(build_app(FakeBus({"core.tv": {}}), state, {}))
    assert client.get("/api/tv").json()["state_detail"]["detail"] == "turn"


def test_a_module_request_that_isnt_a_json_object_is_refused_plainly():
    client = TestClient(build_app(FakeBus(), State(), {}))
    name = client.get("/api/modules").json()[0]["page"].split("/")[2]
    for body in (b"{not json", b"[1, 2]", b"\xff\xfe"):
        r = client.post(f"/api/m/{name}", content=body, headers={"content-type": "application/json"})
        assert r.status_code == 400
    assert client.post(f"/api/m/{name}", json={"action": "status"}).status_code == 200


async def test_the_weather_backs_off_after_a_failure_and_never_passes_old_news_as_now(monkeypatch):
    from pip_assistant.common import weather
    now, tries = [1_000_000.0], []
    monkeypatch.setattr(weather.time, "time", lambda: now[0])
    w = weather.Weather("Lincoln, Nebraska")

    async def failing(self):
        tries.append(now[0])
        raise OSError("offline")
    monkeypatch.setattr(weather.Weather, "_fetch", failing)
    assert await w.text() == "" and await w.screen() == {}
    assert len(tries) == 1                                    # not again straight away
    now[0] += 150
    await w.text()
    assert len(tries) == 2                                    # a couple of minutes later: once more

    async def works(self):
        self.summary, self.data, self.fetched = "Now: 40°F", {"temp": 40}, now[0]
    monkeypatch.setattr(weather.Weather, "_fetch", works)
    now[0] += 150
    assert await w.text() == "Now: 40°F"
    monkeypatch.setattr(weather.Weather, "_fetch", failing)
    now[0] += 2 * 3600
    assert await w.text() == "Now: 40°F"                      # an hour or two old: still fine
    now[0] += 2 * 3600
    assert await w.text() == "" and w.cached() == {}           # four hours old: not "now" any more


async def test_the_tv_doesnt_wait_for_slow_weather(monkeypatch):
    from pip_assistant.common import weather

    async def slow(self):
        await asyncio.sleep(5)
        self.summary, self.data, self.fetched = "x", {"temp": 1}, time.time()
    monkeypatch.setattr(weather.Weather, "_fetch", slow)
    w = weather.Weather("Lincoln, Nebraska")
    t = time.monotonic()
    assert await w.screen_within(0.2) == {}
    assert time.monotonic() - t < 1
    w._task.cancel()
