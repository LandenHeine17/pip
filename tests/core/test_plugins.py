"""Modules really plug in (ARCHITECTURE §7, phase 4 of the structure review).

A module that pip-core has never heard of (the template, which rolls dice) gets its offline phrase, its
box on the monitor and its card on the dashboard through the hooks in core/modules.py, with no change to
pip-core. And what modules get of pip-core, and of each other, is small and keeps everyone's privacy.
"""
import importlib.util
from datetime import date, datetime

import pytest
from fakes.google import FakeGoogle, connect

from pip_assistant.common.modules import ROOT, read
from pip_assistant.core.commands import Commands
from pip_assistant.core.dashapi import alerts_snapshot, tv_snapshot
from pip_assistant.core.modules import Call, Done, ModulePeople, Registry
from pip_assistant.core.people import Match, People
from pip_assistant.core.schedule import Schedule


class NoVoices:
    available = False


@pytest.fixture
def dice(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    sched = Schedule(tmp_path / "pip.db")
    reg = Registry.load({}, infos=[read(ROOT / "screen"), read(ROOT / "_template")], schedule=sched,
                        db_path=tmp_path / "pip.db")
    return reg, sched


async def test_a_new_module_plugs_in_without_changing_pip_core(dice):
    reg, sched = dice
    # Offline, Pip's own commands understand its phrase.
    cmds = Commands(sched, items=reg.offline_items())
    assert (await cmds.handle("Pip, roll a twenty-sided die")).startswith("It's a ")
    # The screen learned its kind of box: Claude can put it up by name or by another word for it.
    act = reg.action("add_box")
    act.do(act.check({"do": "add_box", "type": "last roll"}, Call()), Call())
    tv = tv_snapshot(sched, reg)
    assert "dice" in [s["type"] for s in tv["layout"]["view"]["sections"]]
    assert tv["boxes"]["dice"]["sides"] == 20                       # what its box shows
    # Its card is on the dashboard's front page.
    [card] = alerts_snapshot(sched, reg)["cards"]
    assert card["title"] == "Dice · last rolls" and card["link"] == "/m/_template/"


def test_modules_see_names_and_roles_only(tmp_path):
    people = People(tmp_path / "pip.db", embedder=NoVoices())
    p = people.add_person("Landen", "admin")
    small = ModulePeople(people)
    assert small.all() == [{"id": p["id"], "name": "Landen", "role": "admin"}]       # no voices, nothing else
    assert small.by_name("landon") == small.get(p["id"]) == {"id": p["id"], "name": "Landen", "role": "admin"}


def test_a_heads_up_stays_off_the_room_monitor_when_its_module_is_off(dice):
    reg, sched = dice
    sched.add("event", "Sam's therapy", sched.clock() + 600, event_id="e123")   # a calendar heads-up, calendar off
    sched.add("timer", "pasta", sched.clock() + 300, length=300)
    assert [a["label"] for a in tv_snapshot(sched, reg)["alerts"]] == ["pasta"]


async def test_another_module_gets_only_events_the_asker_may_hear_about(tmp_path, monkeypatch):
    """The calendar's api (what other modules use) keeps its privacy rule: an "only me" account's events
    only for their owner, private events never."""
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    fake = FakeGoogle()
    people = People(tmp_path / "pip.db", embedder=NoVoices())
    landen, sam = people.add_person("Landen", "admin"), people.add_person("Sam")
    reg = Registry.load({}, infos=[read(ROOT / "google"), read(ROOT / "calendar")], schedule=Schedule(tmp_path / "pip.db"),
                        people=people, db_path=tmp_path / "pip.db", transport=fake.transport())
    fake.calendars["sam@example.com"] = {"summary": "Sam", "primary": True, "accessRole": "owner", "owner": "sam@example.com"}
    day = date.today()
    fake.add_event("sam@example.com", summary="interview", start={"dateTime": f"{day}T15:00:00"},
                   end={"dateTime": f"{day}T16:00:00"})
    acc = await connect(reg.modules["google"], fake, sam["id"], "sam@example.com")
    await reg.modules["calendar"].sync.run_once()
    await reg.modules["google"].dashboard("sharing", {"id": acc["id"], "value": "me"})
    api = reg.get("calendar")

    def asker(p):
        return Call(speaker=Match(p["id"], p["name"], "sure", 0.9), now=datetime.now())
    assert [e["title"] for e in api.events_on(day, asker(sam))] == ["interview"]
    assert api.events_on(day, asker(landen)) == [] and api.events_on(day, Call()) == []
    api.add_event("secret", datetime.combine(day, datetime.min.time()).replace(hour=18), asker(landen))
    reg.module("calendar").store.update_event(next(e["id"] for e in reg.module("calendar").store.all_events()
                                                   if e["title"] == "secret"), private=True)
    assert "secret" not in [e["title"] for e in api.events_on(day, asker(landen))]


def test_pips_programs_never_name_a_module():
    """tools/check_map.py's check, here too so the builder's sandbox runs it: core, ears, thinker, web, builder and
    common ask every module through core/modules.py's hooks, never by name."""
    from helpers import REPO as root
    spec = importlib.util.spec_from_file_location("pip_check_map", root / "tools" / "check_map.py")
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    assert check.modules_named(root) == []


# ---------------------------------------------------------------- the room_heard hook (everything heard, offered to modules)
class Echo:
    """A made-up module that answers anything with "echo" in it, to prove the hook needs no module's name in pip-core."""
    def __init__(self):
        self.seen = []

    def room_heard(self, text, call):
        self.seen.append((text, call.kind))
        if "done" in text:
            return Done("done it")   # already done (like "Paused."): Claude must never be asked as well
        return "echo" if "echo" in text else None


async def _room(tmp_path, quiet_hours, heard, busy=False, reason="room"):
    import asyncio

    from helpers import finish, room, says
    from helpers import room_setup as setup
    reg = Registry()
    reg.modules["echo"] = echo = Echo()
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, heard, modules=reg, social={"quiet_hours": quiet_hours})
    if busy:
        conv.answer_id = "an answer under way"   # not free to talk
    data, private = room()
    await ears.emit("heard.utterance", {**data, "reason": reason}, private)
    await asyncio.sleep(0.2)
    out = says(got)
    await finish(bus, tasks)
    return out, echo, asks


async def test_room_speech_is_offered_to_modules_and_their_answer_is_said(tmp_path):
    out, echo, _ = await _room(tmp_path, "", "did you hear the echo")
    assert echo.seen == [("did you hear the echo", "room")] and out == ["echo"]


async def test_speech_to_pip_is_offered_too_and_a_modules_answer_replaces_claude(tmp_path):
    out, echo, asks = await _room(tmp_path, "", "pip, say echo")
    assert echo.seen == [("pip, say echo", "direct")] and out == ["echo"] and not asks
    out, echo, asks = await _room(tmp_path, "", "pip, what time is it")   # no module answers: Claude does
    assert echo.seen == [("pip, what time is it", "direct")] and "echo" not in out


async def test_a_done_answer_replaces_claude_even_while_he_is_busy(tmp_path):
    """D-141: "Pip, pause the video" while he's busy: the module has already paused it (its answer is a Done), so
    Claude isn't asked to do it again; it just isn't said. Any other answer he can't say goes to Claude as before
    ("Pip, set a timer for 67 minutes" mustn't vanish into the six-seven chant)."""
    out, echo, asks = await _room(tmp_path, "", "pip, get it done", busy=True)
    assert echo.seen == [("pip, get it done", "direct")] and out == [] and not asks
    out, echo, asks = await _room(tmp_path, "", "pip, say echo", busy=True)
    assert "echo" not in out and asks          # Claude was asked, and answered


async def test_a_reply_in_the_follow_up_window_is_offered_to_modules_too(tmp_path):
    """D-141: "Pip, louder" within a few seconds of "Here's the video" comes through pip-ears' follow-up path: the
    module still answers it at once, without Claude."""
    out, echo, asks = await _room(tmp_path, "", "pip, say echo", reason="follow_up")
    assert echo.seen == [("pip, say echo", "direct")] and out == ["echo"] and not asks


async def test_modules_are_asked_at_any_hour(tmp_path):
    """The owner, 2026-11-10: the six-seven chant may go off in quiet hours too."""
    out, echo, _ = await _room(tmp_path, "00:00-23:59", "did you hear the echo")
    assert out == ["echo"]


async def _room_while(tmp_path, heard, before):
    """Like _room, with `before(conv)` setting what Pip is doing when it's heard."""
    import asyncio

    from helpers import finish, room, says
    from helpers import room_setup as setup
    reg = Registry()
    reg.modules["echo"] = echo = Echo()
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, heard, modules=reg)
    before(conv)
    data, private = room()
    await ears.emit("heard.utterance", {**data, "reason": "room"}, private)
    await asyncio.sleep(0.2)
    out = says(got)
    await finish(bus, tasks)
    return out, echo, asks, conv


async def test_right_after_he_spoke_room_speech_is_offered_to_modules_too(tmp_path):
    """D-142: in the follow-up window (he's listening for a reply), "six seven" still gets the chant, not Claude."""
    import time

    def window(conv):
        conv.expect_until = conv.follow_until = time.monotonic() + 8
    out, echo, asks, _ = await _room_while(tmp_path, "say echo", window)
    assert echo.seen == [("say echo", "direct")] and out == ["echo"] and not asks


async def test_not_the_rest_of_a_turn_he_is_holding(tmp_path):
    def holding(conv):
        conv.pending = "pip, what's"
    out, echo, asks, _ = await _room_while(tmp_path, "the echo", holding)
    assert echo.seen == []


async def test_a_join_in_he_is_only_thinking_about_gives_way(tmp_path):
    """D-142: Pip was thinking about joining in (nothing said yet) when "six seven" came: the chant wins."""
    def joining(conv):
        conv.answer_id, conv.answer_kind, conv.join_spoke = "a join-in", "join", False
    out, echo, asks, conv = await _room_while(tmp_path, "did you hear the echo", joining)
    assert out == ["echo"] and conv.answer_id is None


async def test_a_typed_message_is_offered_to_modules_too(tmp_path):
    """D-142: "67" typed in the dashboard's chat box goes off like said to him: the module answers, no Claude."""
    from helpers import finish, says
    from helpers import room_setup as setup
    reg = Registry()
    reg.modules["echo"] = echo = Echo()
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, modules=reg)
    assert await conv.typed("say echo") == "echo" and not asks and says(got) == []   # quiet unless asked aloud
    assert echo.seen == [("say echo", "direct")]
    await conv.typed("what time is it")                                                # nobody answers: Claude
    assert asks
    await finish(bus, tasks)
