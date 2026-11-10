"""Claude's ACTION lines (core/actions.py, D-84, D-94): checked first, all or nothing, then done and confirmed by Pip;
voice actions (learning a voice), and look-ups Claude asks for."""
import asyncio
import json
from datetime import datetime

import pytest
from helpers import FakeSTT, clock_at, commands_at, finish, room, says, utt
from helpers import OneVoice as FakeEmbedder
from helpers import room_setup as setup
from helpers import state_bus as setup_bus

from pip_assistant.core import actions
from pip_assistant.core.people import People
from pip_assistant.core.schedule import Schedule

NOW = datetime(2026, 11, 4, 23, 55)  # a Wednesday, just before midnight (when the owner tested)
Clock = clock_at(NOW)



@pytest.fixture
def cmds(tmp_path):
    return commands_at(tmp_path, Clock())   # (commands, schedule, clock), with the calendar module


async def test_actions_add_change_cancel(cmds):
    c, s, _ = cmds
    said, n = actions.apply(c, 'ACTION {"do": "add", "kind": "event", "title": "dentist", "at": "2026-11-06T14:00"}', {'modules': c.reg})
    assert (said, n) == ("Added dentist, Friday at 2 PM.", 1)
    [e] = c.cal.all_events()
    ref = s.short(e["id"])
    assert ref in actions.apply(c, 'ACTION {"do": "look", "kind": "calendar", "from": "2026-11-06"}', {'modules': c.reg})[0]
    said, _ = actions.apply(c, f'ACTION {{"do": "change", "id": "{ref}", "at": "2026-11-07T10:00"}}', {'modules': c.reg})
    assert said == "Moved dentist to Saturday at 10 AM."
    said, _ = actions.apply(c, f'ACTION [{{"do": "cancel", "id": "{ref}"}}, '
                               '{"do": "add", "kind": "timer", "seconds": 300, "label": "tea"}]', {"modules": c.reg})
    assert said == "Took dentist off the calendar. Tea timer's on, 5 minutes."
    said, _ = actions.apply(c, 'ACTION {"do": "add", "kind": "reminder", "label": "call Mom", '
                               '"at": "2026-11-05T18:00", "repeat": "weekdays"}', {"modules": c.reg})
    assert said == "Okay, I'll remind you every weekday at 6 PM."
    said, _ = actions.apply(c, 'ACTION {"do": "add", "kind": "event", "title": "Exam", "date": "2026-11-09", "all_day": true}', {'modules': c.reg})
    assert said == "Added Exam, Monday."


@pytest.mark.parametrize("raw", [
    'ACTION {"do": "add", "kind": "alarm", "at": "2026-11-01T07:00"}',     # in the past
    'ACTION {"do": "change", "id": "#ffffff", "at": "2026-11-05T07:00"}',  # unknown id
    'ACTION {"do": "add", "kind": "spaceship"}',
    'ACTION {"do": "add", "kind": "timer", "seconds": 999999}',
    'ACTION not json at all',
    'ACTION [{"do": "add", "kind": "timer", "seconds": 60}, {"do": "explode"}]',  # one bad: nothing done
])
async def test_bad_actions_change_nothing(cmds, raw):
    c, s, _ = cmds
    assert actions.apply(c, raw, {"modules": c.reg}) == (actions.FAILED, 0)
    assert s.active() == [] and c.cal.all_events() == []


async def test_claude_action_is_carried_out_and_confirmed_by_pip(tmp_path):
    clock = Clock()
    raw = 'ACTION {"do": "add", "kind": "event", "title": "dentist", "at": "2026-11-06T14:00"}'
    bus, sched, conv, ears, got, tasks = await setup_bus(
        tmp_path, clock, FakeSTT("Pip, set a thing on the calendar in two days to go to the dentist at 2"), answer=raw)
    d, p = utt("wake")
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.4)
    assert [t for k, t, _ in got if k == "ears.say"] == ["Added dentist, Friday at 2 PM."]
    assert [e["title"] for e in conv.modules.module("calendar").store.all_events()] == ["dentist"]
    trace = json.dumps(bus.trace.recent())
    assert "dentist" not in trace                                   # what was asked for is never logged
    await finish(bus, tasks)

# ---------------------------------------------------------------- natural language actions (D-94)


def test_ringing_and_voice_actions_are_checked():
    from pip_assistant.core import actions
    assert actions.check_here([{"do": "snooze", "minutes": 10}, {"do": "stop_alarm"}]) == [("snooze", 10.0), ("stop_alarm",)]
    assert actions.check_here([{"do": "learn_voice", "name": "landen"}]) == [("learn_voice", "Landen")]
    for bad in ({"do": "snooze", "minutes": 500}, {"do": "learn_voice", "name": ""}):
        with pytest.raises(actions.Invalid):
            actions.check_here([bad])


async def test_claude_learn_voice_finds_landen_even_spelled_landon(tmp_path):
    people = People(tmp_path / "pip.db", embedder=FakeEmbedder())
    landen = people.add_person("Landen", "admin")
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "Pip, it's Landon here", social={"join_in": False},
                                                    people=people)
    conv.enrol_speech_wait = 0.02
    conv.last_pcm = b"x" * 64000
    await conv.claude_action('ACTION {"do": "learn_voice", "name": "Landon"}', 0.0)
    assert conv.enrol_status()["person_id"] == landen["id"]
    await asyncio.sleep(0.1)
    assert says(got)[-1].startswith("Hi Landen! Let's learn your voice.")
    conv.cancel_enrolment()
    await finish(bus, tasks)


async def test_claude_looks_the_calendar_up_then_answers(tmp_path):
    """D-102: only today and tomorrow go with each message; for next Friday Claude asks Pip to look, once."""
    from datetime import datetime, timedelta

    from helpers import registry

    sched = Schedule(tmp_path / "pip.db")
    reg = registry(tmp_path, schedule=sched)
    day = (datetime.now() + timedelta(days=9)).replace(hour=15, minute=0, second=0, microsecond=0)
    reg.module("calendar").store.add_event("dentist", day)
    look = 'ACTION {"do": "look", "kind": "calendar", "from": "%s"}' % day.date().isoformat()
    bus, conv, ears, got, asks, tasks = await setup(
        tmp_path, "Pip, what have I got that day?", social={"join_in": False}, schedule=sched, modules=reg,
        replies={"direct": [look, "The dentist at 3."]})
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.6)
    assert len(asks) == 2 and "dentist" not in asks[0][1]["schedule"] and not asks[0][1]["looked_up"]
    assert "dentist" in asks[1][1]["looked_up"] and says(got) == ["The dentist at 3."]
    await finish(bus, tasks)
    # Asking to look again on the second round doesn't go round in circles
    (tmp_path / "again").mkdir()
    bus, conv, ears, got, asks, tasks = await setup(
        tmp_path / "again", "Pip, and the day after?", social={"join_in": False}, schedule=sched, modules=reg,
        replies={"direct": [look, look]})
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.6)
    assert len(asks) == 2 and says(got) == ["Sorry, I couldn't find that."]
    await finish(bus, tasks)
