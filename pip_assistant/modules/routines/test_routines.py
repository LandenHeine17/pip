"""The routines module (D-143), loaded the way pip-core loads modules, with a fake home, calendar and to-do list."""
import asyncio
import json
from datetime import datetime

import httpx
import pytest

from pip_assistant.common.modules import ROOT, read
from pip_assistant.core.modules import Call, Done, Invalid, Module, Registry
from pip_assistant.core.schedule import Schedule
from pip_assistant.modules.routines.module import check_steps, plain
from pip_assistant.modules.routines.readouts import Readouts

NOW = datetime(2026, 11, 24, 22, 30)   # a Tuesday night


class Clock:
    def __init__(self):
        self.t = NOW.timestamp()

    def __call__(self):
        return self.t


class FakeHome(Module):
    """home_control's api: remembers what it was asked to switch; "big lamp" never answers."""
    def __init__(self):
        self.api = self
        self.switched, self.broken = [], set()

    async def switch(self, target, on):
        if target not in ("lights", "fans", "everything", "tv lamp", "big lamp"):
            raise LookupError(target)
        self.switched.append((target, on))
        return ["the big lamp"] if target in self.broken else []

    def devices(self):
        return [{"name": "tv lamp", "kind": "light", "on": True}, {"name": "Fan", "kind": "fan", "on": False}]


class FakeCalendar(Module):
    def __init__(self, clock):
        self.api, self.clock, self.asked = self, clock, []

    def events_on(self, day, call):
        self.asked.append((day, call))
        if day == datetime(2026, 11, 25).date():
            return [{"id": "e1", "title": "Gym", "at": datetime(2026, 11, 25, 16).timestamp(), "all_day": False},
                    {"id": "e2", "title": "Chemistry", "at": datetime(2026, 11, 25, 9).timestamp(), "all_day": False}]
        return []


class FakeTodos(Module):
    def __init__(self):
        self.api = self

    def items(self, call):
        return [{"id": "t1", "title": "laundry"}, {"id": "t2", "title": "call the bank"}]


@pytest.fixture
def setup(tmp_path):
    clock = Clock()
    sched = Schedule(tmp_path / "pip.db", None, {}, clock=clock)
    reg = Registry.load({"thinker": {"location": "Lincoln, Nebraska"}}, infos=[read(ROOT / "routines")],
                        db_path=tmp_path / "pip.db", clock=clock, schedule=sched)
    home, cal = FakeHome(), FakeCalendar(clock)
    reg.modules.update({"home_control": home, "calendar": cal, "todos": FakeTodos()})
    r = reg.modules["routines"]
    r.reads.weather.data = {"temp": 30, "sky": "clear", "days": [{"high": 41, "low": 25, "rain": 40}]}
    r.reads.weather.fetched = 9e12   # (never stale)
    r.reads.jokes = ["Here's a joke."]

    async def offline(fun=False):   # (no real weather or Wikipedia in tests)
        pass
    r.reads.warm = offline
    return reg, r, home, sched, clock, cal


async def settle(r):
    await asyncio.gather(*list(r.jobs))


def test_phrases_are_compared_plainly():
    assert plain("hey pip, good night everyone") == "good night"
    assert plain("Okay Pip, I’m heading out now.") == "im heading out"
    assert plain("Goodnight, Pip") == "goodnight"


async def test_goodnight_said_to_pip_runs_at_once(setup):
    reg, r, home, sched, clock, cal = setup
    sched.add("alarm", "wake up", datetime(2026, 11, 25, 7, 30).timestamp(), repeat="weekdays")
    said = reg.room_heard("Pip, goodnight!", Call(kind="direct"))
    assert isinstance(said, Done)   # Claude isn't asked
    assert said == "Goodnight! Your alarm's set for 7:30 AM. First thing tomorrow: Chemistry at 9 AM."
    await settle(r)
    assert home.switched == [("lights", False), ("fans", True)]


async def test_goodnight_to_someone_else_isnt_for_pip(setup):
    reg, r, home, *_ = setup
    assert reg.room_heard("goodnight", Call(kind="room")) is None
    assert reg.room_heard("Pip, goodnight moon is a great book", Call(kind="direct")) is None
    assert home.switched == []


async def test_in_other_words_claude_runs_it_and_hears_what_didnt_switch(setup):
    reg, r, home, *_ = setup
    home.broken.add("everything")
    act = reg.action("routine")
    plan = act.check({"do": "routine", "name": "heading out"}, Call())
    assert await act.do(plan, Call()) == "See you later! I couldn't reach the big lamp."
    with pytest.raises(Invalid):
        act.check({"do": "routine", "name": "disco party"}, Call())


async def test_the_wake_up_alarm_lamps_then_the_briefing(setup):
    reg, r, home, sched, clock, cal = setup
    clock.t = datetime(2026, 11, 25, 7, 30).timestamp()
    wake = [{"kind": "alarm", "label": "wake up", "repeat": "weekdays", "due": clock.t}]
    assert await reg.on_alert("ringing", wake) == ""            # nothing said over the ringing
    assert home.switched == [("lights", True)]
    assert await reg.on_alert("snoozed", wake) == ""            # snoozed: nothing more
    said = await reg.on_alert("stopped", wake)
    assert said == ("Good morning! It's 30 degrees and clear now, with a high of 41 and a low of 25, and a 40 percent "
                    "chance of rain or snow. Today: Chemistry at 9 AM and Gym at 4 PM. On your to-do list: laundry "
                    "and call the bank. Here's a joke.")
    assert cal.asked[-1][1].person_id is None                   # nobody in particular: only what all may hear


async def test_an_ordinary_alarm_runs_nothing(setup):
    reg, r, home, *_ = setup
    plain_alarm = [{"kind": "alarm", "label": "alarm"}, {"kind": "timer", "label": "wake up"}]
    assert await reg.on_alert("ringing", plain_alarm) == "" and await reg.on_alert("stopped", plain_alarm) == ""
    assert home.switched == []


async def test_what_rang_while_out_is_told_once_when_back(setup):
    reg, r, home, *_ = setup
    await reg.on_alert("unanswered", [{"kind": "timer", "label": "pasta"}])   # not out yet: not kept
    reg.room_heard("Pip, I'm heading out", Call(kind="direct"))
    await settle(r)
    assert home.switched == [("everything", False)]
    await reg.on_alert("unanswered", [{"kind": "timer", "label": "pasta"}, {"kind": "reminder", "label": "take the bins out"}])
    said = reg.room_heard("Pip, I'm back", Call(kind="direct"))
    assert said == ("Welcome back! While you were out, nobody answered your pasta timer and a reminder to take the "
                    "bins out.")
    assert reg.room_heard("Pip, I'm back", Call(kind="direct")) == "Welcome back!"   # told once


async def test_your_own_routine_from_the_page(setup):
    reg, r, home, *_ = setup
    out = await r.dashboard("save", {"name": "Movie time", "phrases": ["movie time", "movie night"], "steps": [
        {"do": "switch", "target": "lights", "on": False}, {"do": "switch", "target": "tv lamp", "on": True},
        {"do": "say", "text": "Enjoy the movie!"}]})
    said = reg.room_heard("Pip, movie night", Call(kind="direct"))
    assert said == "Enjoy the movie!"
    await settle(r)
    assert home.switched == [("lights", False), ("tv lamp", True)]
    with pytest.raises(Invalid):   # a phrase another routine has
        await r.dashboard("save", {"name": "Bed", "phrases": ["good night"], "steps": [{"do": "say", "text": "Hi"}]})
    listed = await r.dashboard("list", {})
    assert [x["name"] for x in listed["routines"]][-1] == "Movie time" and listed["devices"][0]["name"] == "tv lamp"
    goodnight = next(x for x in listed["routines"] if x["builtin"] == "goodnight")
    with pytest.raises(Invalid):
        await r.dashboard("delete", {"id": goodnight["id"]})   # built in: put back, not deleted
    await r.dashboard("save", {"id": goodnight["id"], "name": "Goodnight", "phrases": ["goodnight"],
                               "steps": [{"do": "say", "text": "Sleep well."}]})
    assert reg.room_heard("Pip, goodnight", Call(kind="direct")) == "Sleep well."
    await r.dashboard("reset", {"id": goodnight["id"]})
    assert reg.room_heard("Pip, good night", Call(kind="direct")).startswith("Goodnight!")
    await r.dashboard("delete", {"id": out["id"]})
    assert reg.room_heard("Pip, movie time", Call(kind="direct")) is None


def test_steps_are_checked():
    for bad in ([], [{"do": "switch", "target": "lights"}], [{"do": "say", "text": " "}], [{"do": "read", "what": "x"}],
                [{"do": "dance"}], [{"do": "say", "text": "hi"}] * 13):
        with pytest.raises(Invalid):
            check_steps(bad)


async def test_home_control_off_skips_the_switching(setup):
    reg, r, home, *_ = setup
    del reg.modules["home_control"]
    assert await reg.action("routine").do(r.find("heading out"), Call()) == "See you later!"


async def test_the_fun_line_is_only_cheerful(tmp_path):
    selected = {"selected": [
        {"year": 1996, "text": "A plane crashed into the ocean, killing 125 people."},
        {"year": 1963, "text": "The first episode of Doctor Who (pictured) was broadcast."},
        {"year": 1980, "text": "An earthquake struck Italy."},
        {"year": 1981, "text": "Members of Parliament wrote a letter opposing the economic policy."}]}
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=selected))
    clock = Clock()
    reg = Registry.load({}, infos=[read(ROOT / "routines")], db_path=tmp_path / "pip.db", clock=clock,
                        transport=transport)
    reads: Readouts = reg.modules["routines"].reads
    await reads.fetch_fun()
    assert reads.fun_lines == ["On this day in 1963, the first episode of Doctor Who was broadcast."]
    assert reads.read_fun(Call(), []) == reads.fun_lines[0]


def test_the_front_page_card(setup):
    reg, r, home, sched, clock, cal = setup
    assert reg.home_cards() == []
    sched.add("alarm", "Wake-up", datetime(2026, 11, 25, 7).timestamp())
    [card] = reg.home_cards()
    assert card["items"][0]["title"] == "Wake-up alarm" and json.dumps(card)
