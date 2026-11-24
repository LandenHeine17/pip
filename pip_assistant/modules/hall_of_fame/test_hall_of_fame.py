"""The Hall of Fame module (D-147), loaded the way pip-core loads modules. The lines here are made up."""
import json
from types import SimpleNamespace

import pytest

from pip_assistant.common.modules import ROOT, read
from pip_assistant.core import actions
from pip_assistant.core.memory import Memory
from pip_assistant.core.modules import Call, Invalid, Registry

LINES = ["[18:14] Sam: I could beat a goose in a fight", "[18:14] Pip: The goose has never lost, Sam.",
         "Someone: he's right you know"]


class Clock:
    t = 1_800_000_000.0

    def __call__(self):
        return self.t


def person(name):
    return Call(speaker=SimpleNamespace(person_id="p-" + name, name=name, level="sure", guest=False), kind="direct")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    clock = Clock()
    reg = Registry.load({}, infos=[read(ROOT / "screen"), read(ROOT / "hall_of_fame")], db_path=tmp_path / "pip.db", clock=clock)
    return SimpleNamespace(reg=reg, hall=reg.modules["hall_of_fame"], clock=clock, tmp=tmp_path)


def act(env, a: dict, call=None):
    """One of Claude's actions, checked then done, as core/actions.py does."""
    verb = f"{a['do']}:{a['kind']}" if a["do"] in ("add", "look") else a["do"]
    action = env.reg.action(verb)
    call = call or person("Sam")
    return action.do(action.check(a, call), call)


def save(env, title="Sam versus the goose", lines=LINES, **kw):
    return act(env, {"do": "add", "kind": "moment", "title": title, "lines": lines}, **kw)


def test_a_moment_is_saved_word_for_word_when_asked(env):
    assert save(env) == "Saved to the Hall of Fame: Sam versus the goose."
    [m] = env.hall.moments()
    assert m["lines"] == ["Sam: I could beat a goose in a fight", "Pip: The goose has never lost, Sam.",
                          "Someone: he's right you know"]                      # the words, without the times
    assert m["people"] == "Sam" and m["source"] == "Sam, by voice" and m["ref"].startswith("#h") and not m["starred"]
    assert save(env) == "That one's already in the Hall of Fame." and len(env.hall.moments()) == 1
    assert env.hall.find(m["ref"]) == "moment" and env.hall.find("#m123456") is None
    stranger = Call(speaker=None)
    assert "Saved" in save(env, "Another", ["Someone: the fan is haunted"], call=stranger)
    assert env.hall.moments()[0]["source"] == "someone, by voice" or env.hall.moments()[1]["source"] == "someone, by voice"


def test_it_plugs_in_through_claudes_ordinary_actions(env, tmp_path):
    """`add` with kind "moment" reaches the module through core/actions.py, like any module's action."""
    cmds = SimpleNamespace(now=lambda: __import__("datetime").datetime(2026, 11, 24, 18, 0))
    plan = actions.check(cmds, actions.parse('ACTION {"do": "add", "kind": "moment", "title": "Goose", "lines": ["Sam: honk"]}'),
                         {"modules": env.reg, "speaker": None})
    assert plan[0][0] == "module" and plan[0][2] == {"title": "Goose", "lines": ["Sam: honk"]}


def test_what_is_not_fit_to_keep_is_refused(env):
    for bad in ({"lines": []}, {"lines": "   "}, {"lines": [f"Sam: {i}" for i in range(9)]}, {"lines": 7},
                {"lines": ["Sam: the door code is 4471"]}, {"title": "my password is hunter2", "lines": ["Sam: ha"]}):
        with pytest.raises(Invalid):
            act(env, {"do": "add", "kind": "moment", "title": "x", **bad})
    assert env.hall.moments() == []
    save(env, title="", lines=["Sam: " + "ha" * 400])                           # no title: its first line, cut
    [m] = env.hall.moments()
    assert len(m["lines"][0]) == 300 and m["title"] == m["lines"][0][:80]


def test_anyone_can_have_one_removed_by_voice(env):
    save(env)
    ref = env.hall.moments()[0]["ref"]
    with pytest.raises(Invalid):
        act(env, {"do": "remove_moment", "id": "#hzzzzzz"})
    assert act(env, {"do": "remove_moment", "id": ref}, call=Call(speaker=None)) == "Taken out of the Hall of Fame: Sam versus the goose."
    assert env.hall.moments() == []


def test_looking_them_up(env):
    assert "empty" in act(env, {"do": "look", "kind": "moments"})
    save(env)
    env.clock.t += 60
    save(env, "The haunted fan", ["Alex: the fan turned itself on", "Pip: I'd never."])
    latest = act(env, {"do": "look", "kind": "moments"})
    assert latest.startswith("The Hall of Fame has 2 moments. The latest: ") and latest.index("haunted fan") < latest.index("goose")
    assert "Alex: the fan turned itself on / Pip: I'd never." in latest          # the words, for telling it
    found = act(env, {"do": "look", "kind": "moments", "query": "the goose fight"})
    assert "Matching: " in found and "goose" in found and "haunted" not in found
    assert act(env, {"do": "look", "kind": "moments", "query": "submarine"}) == 'Nothing in the Hall of Fame for "submarine" (2 moments in it).'
    for i in range(12):
        save(env, f"Long one {i}", [f"Sam: {i} " + "word " * 55] * 4)
    assert len(act(env, {"do": "look", "kind": "moments"})) <= 1200                # never more than a look-up's worth


def test_a_few_titles_go_with_each_request_and_never_the_words(env):
    assert env.hall.summary(person("Sam")) == ""
    save(env)
    for i in range(12):
        env.clock.t += 1
        save(env, f"A rather long title for moment number {i}", [f"Sam: line {i} " + "abcdefghijkl"[i] * 30])
    star = env.hall.moments()[-1]                                               # the goose, the oldest
    env.hall.db.execute("UPDATE hall_of_fame_moments SET starred = 1 WHERE id = ?", (star["id"],))
    env.hall.db.commit()
    s = env.hall.summary(person("Sam"))
    assert len(s) <= 200 and s.startswith("Hall of Fame moments you can bring up") and s.endswith(".")
    assert s.index("Sam versus the goose") < s.index("A rather long title")       # starred first
    assert "never lost" not in s and "line " not in s                             # titles only
    days = set()
    for _ in range(6):
        env.clock.t += 86400
        days.add(env.hall.summary(person("Sam")))
    assert len(days) > 1 and all("Sam versus the goose" in d for d in days)       # the others change with the day
    env.hall.ctx.settings["bring_up"] = False
    assert env.hall.summary(person("Sam")) == ""


def test_showing_one_on_the_monitor_and_taking_it_down(env):
    screen = env.reg.modules["screen"].api
    assert "empty" in act(env, {"do": "show_moment"})
    save(env)
    m = env.hall.moments()[0]
    assert act(env, {"do": "show_moment", "id": m["ref"]}) == "Here's one: Sam versus the goose."
    assert screen.covered() == "moment" and env.hall.showing == m["id"]
    box = env.hall.tv([{"type": "moment"}])["moment"]
    assert box["title"] == "Sam versus the goose" and box["lines"][1].startswith("Pip:") and env.hall.tv([]) == {}
    env.clock.t += 59
    env.hall.tidy()
    assert screen.covered() == "moment"
    env.clock.t += 2
    env.hall.tidy()
    assert screen.covered() is None and env.hall.showing is None                  # down again after a minute
    act(env, {"do": "show_moment"})
    assert act(env, {"do": "show_moment", "off": True}) == "Taken down." and screen.covered() is None
    with pytest.raises(Invalid):
        act(env, {"do": "show_moment", "id": "#hzzzzzz"})
    act(env, {"do": "show_moment"})
    env.hall.remove(m["id"])                                                      # removed while it's up: taken down
    assert screen.covered() is None and env.hall.tv([{"type": "moment"}]) == {"moment": {}}


async def test_its_page(env):
    d = env.hall.dashboard
    r = await d("add", {"title": "Typed in", "lines": "Sam: one\n\nPip: two\n"})
    [m] = r["moments"]
    assert m["lines"] == ["Sam: one", "Pip: two"] and m["source"] == "added on the dashboard" and r["has_screen"]
    assert "can't be kept" in (await d("add", {"title": "x", "lines": ["Sam: my pin is 1234 and the gate code is 99"]}))["error"]
    assert (await d("star", {"id": m["id"], "on": True}))["moments"][0]["starred"]
    r = await d("edit", {"id": m["id"], "title": "Better", "lines": ["Alex: three"]})
    assert r["moments"][0]["title"] == "Better" and r["moments"][0]["people"] == "Alex" and r["moments"][0]["starred"]
    assert (await d("show", {"id": m["id"]}))["showing"] == m["id"] and (await d("hide", {"id": m["id"]}))["showing"] is None
    assert "error" in await d("edit", {"id": "nope", "title": "x", "lines": ["y"]}) and "error" in await d("dance", {"id": m["id"]})
    assert (await d("delete", {"id": m["id"]}))["moments"] == [] and (await d("list", {}))["moments"] == []


async def test_your_data_and_removing_a_person(env):
    save(env)
    save(env, "The haunted fan", ["Alex: the fan turned itself on", "Pip: I'd never."])
    out = env.hall.export_data()["moments"]
    assert {m["title"] for m in out} == {"Sam versus the goose", "The haunted fan"} and out[0]["lines"]
    assert await env.hall.forget_person("p-Sam", "Sam") == 1                      # the moments Sam is in
    assert [m["title"] for m in env.hall.moments()] == ["The haunted fan"]
    assert await env.hall.forget_person("p-x", "") == 0
    assert await env.hall.wipe_data() == 1 and env.hall.moments() == []


async def test_a_memory_that_is_really_a_moment_is_moved_from_the_memory_card(env):
    """D-147: the Memory card's "Move to Hall of Fame". pip-core doesn't name the module: it asks which modules
    take memories, hands the memory over, and deletes it only once the module has kept it."""
    from pip_assistant.common.messages import Message
    from pip_assistant.core.dashapi import memory_move, memory_snapshot
    from pip_assistant.core.people import People
    mem, people = Memory(env.tmp / "pip.db"), People(env.tmp / "pip.db")
    sam = people.add_person("Sam", "member")
    row, _ = mem.add("Sam once argued with the toaster for ten minutes", "personal", about=[sam["id"]])
    assert memory_snapshot(mem, people, env.reg)["takers"] == [{"module": "hall_of_fame", "label": "Hall of Fame"}]
    move = lambda to: memory_move(mem, people, env.reg, Message("request", "core.memory_action",
                                                               data={"action": "move", "id": row["id"], "to": to}))
    assert "error" in await move("screen") and "error" in await move("nothing") and mem.get(row["id"])   # still a memory
    assert await move("hall_of_fame") == {"ok": True} and mem.get(row["id"]) is None
    [m] = env.hall.moments()
    assert m["lines"] == ["Sam once argued with the toaster for ten minutes"] and m["source"] == "from a memory"
    assert m["people"] == "Sam" and abs(m["at"] - row["created"]) < 1               # its own date; Sam's removal removes it
    assert "error" in await move("hall_of_fame")                                  # it's gone from memories


def test_nothing_said_reaches_the_trace(env, monkeypatch):
    """The words are private: what the module publishes carries numbers only."""
    sent = []

    async def publish(kind, data=None, private=None):
        sent.append((kind, data, private))
    monkeypatch.setattr(env.hall.ctx, "publish", publish)
    import asyncio

    async def go():
        save(env)
        await asyncio.sleep(0)
    asyncio.run(go())
    assert sent and "goose" not in json.dumps(sent) and ("saved", {"lines": 3}, None) in sent
