"""Modules really plug in (ARCHITECTURE §7, phase 4 of the structure review).

A module that pip-core has never heard of (the template, which rolls dice) gets its offline phrase, its
box on the monitor and its card on the dashboard through the hooks in core/modules.py, with no change to
pip-core. And what modules get of pip-core, and of each other, is small and keeps everyone's privacy.
"""
import importlib.util
from datetime import date, datetime
from pathlib import Path

import pytest

from fakes.google import FakeGoogle, connect
from pip_assistant.common.modules import ROOT, read
from pip_assistant.core.commands import Commands
from pip_assistant.core.dashapi import alerts_snapshot, tv_snapshot
from pip_assistant.core.modules import Call, ModulePeople, Registry
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
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("pip_check_map", root / "tools" / "check_map.py")
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    assert check.modules_named(root) == []
