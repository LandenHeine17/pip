"""The to-dos module: household list on the Pi, and two-way sync with a pretend Google Tasks."""
from datetime import datetime

import pytest

from pip_assistant.common.modules import ROOT, read
from pip_assistant.core.modules import Call, Invalid, Registry
from pip_assistant.core.people import Match, People
from pip_assistant.modules.google.testing import FakeGoogle, connect

NOW = datetime(2026, 11, 7, 9, 0)


class NoVoices:
    available = False


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    fake = FakeGoogle()
    people = People(tmp_path / "pip.db", embedder=NoVoices())
    reg = Registry.load({}, infos=[read(ROOT / "google"), read(ROOT / "todos")], people=people,
                        db_path=tmp_path / "pip.db", transport=fake.transport())
    return reg, reg.modules["todos"], fake, people


def say(reg, verb, a, call=None):
    act, call = reg.action(verb), call or Call(now=NOW)
    return act.do(act.check(a, call), call)


def me(p):
    return Call(speaker=Match(p["id"], p["name"], "sure", 0.9), now=NOW)


async def test_household_list_without_google(env):
    reg, todos, fake, people = env
    assert say(reg, "add_task", {"title": "milk"}) == "Added milk to the to-do list."
    assert "milk" in reg.summary(Call(now=NOW))
    [t] = todos.store.items()
    assert say(reg, "complete_task", {"id": "#t" + t["id"][:5]}) == "Ticked off milk."
    assert reg.summary(Call(now=NOW)) == "No open to-dos."
    with pytest.raises(Invalid):
        say(reg, "complete_task", {"id": "#tzzzzz"})


async def test_two_way_with_google_tasks(env):
    reg, todos, fake, people = env
    landen, sam = people.add_person("Landen", "admin"), people.add_person("Sam")
    fake.lists["L1"] = {"title": "My Tasks", "owner": "landen@example.com"}
    fake.lists["S1"] = {"title": "My Tasks", "owner": "sam@example.com"}
    fake.tasks["L1"] = {"t1": {"id": "t1", "title": "return library book", "status": "needsAction", "etag": '"a"',
                               "updated_ts": 0, "due": "2026-11-10T00:00:00.000Z"}}
    await connect(reg.modules["google"], fake, landen["id"], "landen@example.com")
    await connect(reg.modules["google"], fake, sam["id"], "sam@example.com")
    await todos.sync.run_once()
    s = reg.summary(me(landen))
    assert "return library book, due 2026-11-10 (Landen's list)" in s
    # By voice: onto the speaker's own Google list; "Sam's list" when named
    assert say(reg, "add_task", {"title": "buy stamps"}, me(landen)) == "Added buy stamps to the to-do list."
    assert say(reg, "add_task", {"title": "pay rent", "whose": "Sam", "due": "2026-11-15"}, me(landen)).startswith(
        "Added pay rent to Sam's to-dos, for ")
    await todos.sync.run_once()
    assert {t["title"] for t in fake.tasks["L1"].values()} >= {"buy stamps"}
    assert {t["title"] for t in fake.tasks["S1"].values()} == {"pay rent"}
    # Done in Google → done in Pip; ticked in Pip → done in Google
    fake.tasks["L1"]["t1"].update(status="completed", completed="2026-11-07T15:00:00.000Z", etag='"b"',
                                  updated_ts=10 ** 12)
    await todos.sync.run_once()
    assert "library" not in reg.summary(me(landen))
    stamps = next(t for t in todos.store.items() if t["title"] == "buy stamps")
    say(reg, "complete_task", {"id": "#t" + stamps["id"][:5]}, me(landen))
    await todos.sync.run_once()
    assert next(t for t in fake.tasks["L1"].values() if t["title"] == "buy stamps")["status"] == "completed"
