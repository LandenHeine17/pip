"""Asking Pip to change himself by voice (M1b, PIP-NEW-069, AC-SM-01): only voices he's sure of, never guests;
the request goes to pip-builder in the private part; voice never approves anything."""
import asyncio

import pytest

from pip_assistant.common.client import BusClient
from pip_assistant.core import actions
from pip_assistant.core.conversation import SAYINGS
from pip_assistant.core.people import Match
from pip_assistant.thinker.main import RULES
from test_step6 import finish, says, setup

ASK = 'ACTION {"do": "request_change", "summary": "keep track of whose turn it is for the dishes"}'
LANDEN = Match("p1", "Landen", "sure", 0.9)


async def with_builder(tmp_path, reply=None, running=True):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, social={"join_in": False})
    seen = []
    if running:
        builder = BusClient("builder", socket_path=tmp_path / "core.sock")

        async def on_ask(msg):
            seen.append((msg.data, msg.private))
            return msg.reply(reply or {"id": "c1"}, source="builder")
        builder.on("builder.ask", on_ask)
        tasks.append(asyncio.create_task(builder.run()))
        await asyncio.wait_for(builder.connected.wait(), 3)
        await asyncio.sleep(0.05)
    return bus, conv, got, seen, tasks


async def test_a_recognised_member_can_ask(tmp_path):
    bus, conv, got, seen, tasks = await with_builder(tmp_path)
    await conv.claude_action(ASK, 0.0, asker=(LANDEN, b""))
    await asyncio.sleep(0.05)
    assert seen == [({"source": "voice", "asked_by": "Landen", "person_id": "p1"},
                     {"text": "keep track of whose turn it is for the dishes"})]
    assert says(got)[-1] == SAYINGS["change_asked"]
    # what was asked travels only in the private part: never in the trace
    assert not any("dishes" in str(r) for r in bus.trace.recent())
    await finish(bus, tasks)


@pytest.mark.parametrize("who", [None, Match("p1", "Landen", "probably", 0.6), Match(None, "", "unknown", 0.1),
                                 Match("g1", "Sam", "sure", 0.9, guest=True)])
async def test_unsure_voices_and_guests_are_told_to_use_the_dashboard(tmp_path, who):
    bus, conv, got, seen, tasks = await with_builder(tmp_path)
    await conv.claude_action(ASK, 0.0, asker=(who, b""))
    await asyncio.sleep(0.05)
    assert not seen and says(got)[-1] == SAYINGS["change_not_you"]
    assert "Changes page" in SAYINGS["change_not_you"] and "Landen" not in SAYINGS["change_not_you"]
    await finish(bus, tasks)


@pytest.mark.parametrize("running,reply", [(False, None), (True, {"error": "paused: usage limit"})])
async def test_builder_down_or_paused_is_a_plain_refusal(tmp_path, running, reply):
    bus, conv, got, seen, tasks = await with_builder(tmp_path, reply, running)
    await conv.claude_action(ASK, 0.0, asker=(LANDEN, b""))
    await asyncio.sleep(0.05)
    assert says(got)[-1] == SAYINGS["change_unavailable"]
    await finish(bus, tasks)


def test_request_change_is_checked():
    assert actions.check_here([{"do": "request_change", "summary": "  add a  dishes rota "}]) == \
        [("request_change", "add a dishes rota")]
    for bad in ({"do": "request_change"}, {"do": "request_change", "summary": ""}, {"do": "request_change", "summary": "hm"},
                {"do": "request_change", "summary": "x" * 400},
                {"do": "request_change", "summary": "remember the wifi password is hunter2"}):
        with pytest.raises(actions.Invalid):
            actions.check_here([bad])


async def test_an_invalid_request_changes_nothing(tmp_path):
    bus, conv, got, seen, tasks = await with_builder(tmp_path)
    await conv.claude_action('ACTION {"do": "request_change", "summary": ""}', 0.0, asker=(LANDEN, b""))
    await asyncio.sleep(0.05)
    assert not seen and says(got)[-1] == actions.FAILED
    await finish(bus, tasks)


async def test_joining_in_never_asks_for_a_change(tmp_path):
    """Nothing overheard is acted on: an ACTION while joining in is dropped (so a change is never asked for)."""
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, replies={"join": ASK})
    conv.commands = object()
    called = []

    async def record(*a, **kw):
        called.append(a)
    conv.claude_action = record
    await conv.answer("", "join", kind="join")
    assert not called
    await finish(bus, tasks)


def test_rules_explain_request_change():
    rules = RULES.format(weather="Sunny")
    assert '"do": "request_change"' in rules
    for words in ("clearly asks", "Questions about how you", "complaints", "what-ifs", "no names or personal details",
                  "Undo on the Changes page", "never by voice"):
        assert words in rules, words
