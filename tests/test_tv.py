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

    from pip_assistant.core.main import tv_snapshot
    from pip_assistant.core.schedule import Schedule
    from modhelp import registry
    sched = Schedule(tmp_path / "pip.db")
    reg = registry(tmp_path, schedule=sched, names=("google", "calendar"))
    cal = reg.get("calendar")
    soon = datetime.now() + timedelta(hours=3)
    cal.add_event("Dentist", soon)
    cal.add_event("Therapy", soon + timedelta(hours=1), private=True)
    sched.add("timer", "pasta", time.time() + 600, length=600)
    sched.add("event", "Far away", time.time() + 3 * 3600)
    sched.add("event", "Very soon", time.time() + 600)
    snap = tv_snapshot(sched, reg)
    assert [e["title"] for e in snap["events"]] == ["Dentist"]
    assert {a["label"] for a in snap["alerts"]} == {"pasta", "Very soon"}


def test_the_mouth_level_never_goes_in_the_trace(tmp_path):
    t = Trace(tmp_path / "pip.db")
    assert t.add(Message("event", "speech.level", source="ears", data={"level": 0.5})) is None
    assert t.add(Message("event", "speech.started", source="ears", data={})) is not None


def test_what_the_face_wears():
    assert dress(95, 60) == "storm" and dress(63, 50) == "umbrella" and dress(73, 20) == "snow"
    assert dress(3, 30) == "beanie" and dress(0, 75) == "sunglasses" and dress(0, 75, day=False) == ""
    assert dress(2, 55) == ""


def test_six_seven_is_heard_only_as_words():
    """The kept six-seven joke (D-35): the page's own pattern, run on the same cases in Python."""
    import re
    from pathlib import Path
    page = (Path(__file__).parent.parent / "pip_assistant/web/static/tv.html").read_text()
    words = re.search(r"words: /(.+)/i,", page).group(1)
    heard = lambda text: bool(re.search(words, text, re.I))
    for yes in ("six seven", "Six, seven!", "6 7", "6-7", "it's 67", "SIX SEVEN SIX SEVEN"):
        assert heard(yes), yes
    for no in ("6:07", "167", "6.7", "67.5", "sixty seven", "1.67", "six", "seventy"):
        assert not heard(no), no
    assert 'd.who === "you" && heardSixSeven(d.text)' in page   # never his own replies


async def test_room_talk_reaches_the_tv_only_with_a_conversation_box_showing_it():
    """D-111: the owner wants room talk in the Conversation box; it's sent only while one shows it."""
    bus, state = FakeBus(), State()
    listen(bus, state)
    q = tv_feed(state)
    room = Message("event", "conversation.line", data={"who": "room", "how": "room"}, private={"text": "did you see the game"})
    await bus.deliver(room)
    assert drain(q) == []
    await bus.deliver(Message("event", "module.screen.changed", data={"boxes": ["clock", "conversation"], "room": True}))
    await bus.deliver(room)
    got = drain(q)
    assert ("refresh", {}) in got and ("line", {"who": "room", "how": "room", "speaker": "", "text": "did you see the game"}) in got
    await bus.deliver(Message("event", "module.screen.changed", data={"boxes": ["conversation"], "room": False}))
    await bus.deliver(room)
    assert [k for k, _ in drain(q)] == ["refresh"]
    await bus.deliver(Message("event", "module.screen.options", data={"boxes": [], "room": False}))
    assert [k for k, _ in drain(q)] == ["options"]


def test_pi_health_only_while_its_box_is_up():
    snap = {"now": time.time(), "alerts": [], "events": [],
            "screen": {"view": {"sections": [{"type": "clock"}]}}}
    state = State()
    client = TestClient(build_app(FakeBus({"core.tv": snap}), state, {}))
    assert "vitals" not in client.get("/api/tv").json() and client.get("/api/tv/vitals").json() == {}
    snap["screen"]["view"]["sections"].append({"type": "health"})
    assert "memory" in client.get("/api/tv").json()["vitals"]
    assert "cpu" in client.get("/api/tv/vitals").json()


def test_pip_core_sends_the_screen_and_only_the_lists_on_it(tmp_path):
    from pip_assistant.core.main import tv_snapshot
    from pip_assistant.core.schedule import Schedule
    from modhelp import registry
    sched = Schedule(tmp_path / "pip.db")
    reg = registry(tmp_path, schedule=sched, names=("google", "todos", "screen"))
    reg.get("todos").add("milk", "pip")
    snap = tv_snapshot(sched, reg)
    assert snap["screen"]["name"] == "default" and snap["lists"] == {}
    reg.modules["screen"].screen.show("morning")    # it has the household's to-do list
    snap = tv_snapshot(sched, reg)
    assert snap["lists"][""]["items"] == ["milk"]


def test_every_detail_has_a_face_and_reaches_the_tv():
    """D-112: each situation in pip-core's DETAILS has its own expression on the monitor (plain thinking and
    talking use the state's own face)."""
    import asyncio
    from pathlib import Path
    from pip_assistant.core.conversation import DETAILS
    page = (Path(__file__).parent.parent / "pip_assistant/web/static/tv.html").read_text()
    for detail in DETAILS:
        if detail not in ("answer", "asking"):
            assert f':{detail}"' in page, detail
    bus, state = FakeBus(), State()
    listen(bus, state)
    q = tv_feed(state)
    asyncio.run(bus.deliver(Message("event", "pip.state", data={"state": "listening", "detail": "turn",
                                                                 "words": DETAILS["turn"], "ends_in": 2.5})))
    assert drain(q) == [("state", {"state": "listening", "detail": "turn", "words": DETAILS["turn"], "ends_in": 2.5})]
    client = TestClient(build_app(FakeBus({"core.tv": {}}), state, {}))
    assert client.get("/api/tv").json()["state_detail"]["detail"] == "turn"
