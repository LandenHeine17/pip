"""Step 4 fixes after the owner's test (2026-11-05): changing anything, Claude's actions (D-84),
the voice check (D-85), the thinking tone and Pip's state (D-86)."""
import asyncio
import base64
import json
from datetime import datetime

import numpy as np
import pytest

from modhelp import registry

from pip_assistant.common.client import BusClient
from pip_assistant.core import actions
from pip_assistant.core.bus import Bus
from pip_assistant.core.commands import Commands
from pip_assistant.core.conversation import Conversation
from pip_assistant.core.health import Health
from pip_assistant.core.schedule import Schedule
from pip_assistant.core.trace import Trace
from pip_assistant.thinker.claude import ClaudeCLI, is_action

NOW = datetime(2026, 11, 4, 23, 55)  # a Wednesday, just before midnight (when the owner tested)


class Clock:
    def __init__(self, dt=NOW):
        self.t = dt.timestamp()

    def __call__(self):
        return self.t


@pytest.fixture
def cmds(tmp_path):
    clock = Clock()
    s = Schedule(tmp_path / "pip.db", clock=clock)
    reg = registry(tmp_path, s, clock)                  # the calendar is a module now (D-96)
    c = Commands(s, clock=clock, items=reg.offline_items())
    c.reg = reg
    c.cal = reg.module("calendar").store   # the calendar's events, for checking
    return c, s, clock


def when_(row, key="due"):
    return datetime.fromtimestamp(row[key])


# ------------------------------------------------------------------ 1. changing works the same for every kind
async def test_change_that_alarm_moves_it_instead_of_adding_one(cmds):
    c, s, _ = cmds
    assert await c.handle("wake me up at 8 tomorrow") == "Alarm set for 8 AM tomorrow."
    assert await c.handle("Pippletron, can you change that alarm to 7.30?") == "Moved your alarm to 7:30 AM tomorrow."
    [a] = s.active()
    assert when_(a) == datetime(2026, 11, 5, 7, 30)


async def test_change_names_the_alarm_by_its_time_and_shifts(cmds):
    c, s, _ = cmds
    await c.handle("wake me up at 8 tomorrow")
    await c.handle("set an alarm for 6 am")
    assert await c.handle("change the 6 am alarm to 6:15") == "Moved your alarm to 6:15 AM tomorrow."
    assert await c.handle("move my alarm 10 minutes later") == "Moved your alarm to 6:25 AM tomorrow."
    assert sorted(when_(a).strftime("%H:%M") for a in s.active()) == ["06:25", "08:00"]


async def test_change_a_reminder(cmds):
    c, s, _ = cmds
    await c.handle("remind me at 9 tomorrow to call the bank")
    assert await c.handle("push the reminder back an hour") == "Okay, I'll remind you at 10 AM tomorrow instead."
    assert await c.handle("change the bank reminder to 11") == "Okay, I'll remind you at 11 AM tomorrow instead."
    [r] = s.active()
    assert when_(r) == datetime(2026, 11, 5, 11, 0) and r["label"] == "call the bank"


async def test_change_a_timer(cmds):
    c, s, clock = cmds
    await c.handle("set a pasta timer for 2 minutes")
    assert await c.handle("can you add a minute to that timer?") == "Done, 3 minutes left."
    assert await c.handle("make it 5 minutes") == "Changed it to 5 minutes."
    assert await c.handle("take a minute off the pasta timer") == "Done, 4 minutes left."
    [t] = s.active()
    assert t["due"] - clock() == pytest.approx(240)


async def test_change_and_remove_a_calendar_event_with_that(cmds):
    c, s, _ = cmds
    assert await c.handle("add dentist to the calendar on Friday at 2 p.m.") == "Added dentist, Friday at 2 PM."
    assert await c.handle("move the dentist to Saturday") == "Moved dentist to Saturday at 2 PM."
    assert await c.handle("move it to 3") == "Moved dentist to Saturday at 3 PM."
    [e] = c.cal.all_events()
    assert when_(e, "start") == datetime(2026, 11, 7, 15, 0)
    assert await c.handle("can you remove that from the calendar?") == "Took dentist off the calendar."
    assert c.cal.all_events() == []


async def test_event_heads_up_moves_with_the_event(cmds):
    c, s, _ = cmds
    ev = c.cal.add_event("dentist", datetime(2026, 11, 6, 14, 0), heads_up_before=1800)
    c.cal.update_event(ev["id"], datetime(2026, 11, 6, 16, 0))
    [h] = s.active(("event",))
    assert when_(h) == datetime(2026, 11, 6, 15, 30)


async def test_cancel_that_right_after_setting(cmds):
    c, s, _ = cmds
    await c.handle("set a timer for 10 minutes")
    assert await c.handle("actually 15") == "Changed it to 15 minutes."
    assert await c.handle("cancel that") == "Timer's cancelled."
    assert s.active() == []
    assert await c.handle("cancel that") == ""  # nothing just set: it means "stop talking"


async def test_change_of_something_that_does_not_exist(cmds):
    c, s, _ = cmds
    assert await c.handle("change my alarm to 7") == "You don't have any alarms."
    assert s.active() == []


# ------------------------------------------------------------------ 2. Claude's actions (D-84)
async def test_actions_add_change_cancel(cmds):
    c, s, _ = cmds
    said, n = actions.apply(c, 'ACTION {"do": "add", "kind": "event", "title": "dentist", "at": "2026-11-06T14:00"}', {'modules': c.reg})
    assert (said, n) == ("Added dentist, Friday at 2 PM.", 1)
    [e] = c.cal.all_events()
    ref = s.short(e["id"])
    from pip_assistant.core.modules import Call
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


async def test_action_replies_are_never_spoken(tmp_path, monkeypatch):
    """pip-thinker holds back an ACTION reply instead of speaking it."""
    cli = ClaudeCLI()
    lines = [{"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": t}}}
             for t in ("AC", "TION {\"do\": \"add\", ", "\"kind\": \"timer\", \"seconds\": 60}")]
    lines.append({"type": "result", "is_error": False})

    class FakeProc:
        returncode = 0
        stdin = type("S", (), {"write": lambda self, b: None, "close": lambda self: None,
                               "drain": lambda self: asyncio.sleep(0)})()

        def __init__(self):
            async def gen():
                for line in lines:
                    yield (json.dumps(line) + "\n").encode()
            self.stdout = gen()

        async def wait(self):
            return 0

    async def spawn(prompt):
        return FakeProc()

    async def prepare(prompt):
        return None

    monkeypatch.setattr(cli, "_spawn", spawn)
    monkeypatch.setattr(cli, "prepare", prepare)
    spoken = []

    async def on_sentence(s):
        spoken.append(s)

    async def on_searching():
        pass

    text, info = await cli.ask("a1", "sys", "hi", on_sentence, on_searching)
    assert spoken == [] and text == "" and info["action"].startswith("ACTION")
    assert is_action("  ACTION {}") and not is_action("Sure, done.")


# ------------------------------------------------------------------ 3. voice check (D-85)
def test_voice_check_ignores_noise():
    from pip_assistant.core.voicecheck import voice_seconds_sync
    rng = np.random.default_rng(0)
    hiss = (rng.standard_normal(16000) * 32768 * 10 ** (-45 / 20)).astype(np.int16)
    click = np.zeros(16000, np.int16)
    click[8000:8040] = 20000
    assert voice_seconds_sync(hiss.tobytes()) == 0 and voice_seconds_sync(click.tobytes()) == 0


# ------------------------------------------------------------------ 4. thinking tone and Pip's state (D-86)
async def setup_bus(tmp_path, clock, stt, answer="Claude here.", cfg=None):
    sock = tmp_path / "core.sock"
    bus = Bus(sock, Trace(tmp_path / "pip.db"), Health(["ears", "thinker"], restart=False))
    sched = Schedule(tmp_path / "pip.db", bus, {"alarms": {"announce_every_seconds": 0.2, "ring_minutes": 0.02}}, clock=clock)
    conv = Conversation(bus, cfg or {"conversation": {"follow_up_seconds": 0.3}}, stt, schedule=sched,
                        modules=registry(tmp_path, sched, clock))
    await bus.start()
    ears, thinker = BusClient("ears", socket_path=sock), BusClient("thinker", socket_path=sock)
    got = []

    async def record(msg):
        got.append((msg.type, msg.private.get("text"), msg.data))

    for t in ("ears.chime", "ears.say", "ears.listen", "ears.ring", "ears.ring_stop", "ears.stop", "ears.tone"):
        ears.on(t, record)

    async def ask(msg):
        await asyncio.sleep(0.05)
        if answer.startswith("ACTION"):
            return msg.reply({"action": True}, private={"text": "", "action": answer}, source="thinker")
        await thinker.emit("answer.sentence", {"answer_id": msg.data["answer_id"]}, private={"text": answer})
        return msg.reply({}, private={"text": answer}, source="thinker")

    thinker.on("thinker.ask", ask)
    tasks = [asyncio.create_task(x.run()) for x in (ears, thinker)] + [asyncio.create_task(sched.run())]
    for x in (ears, thinker):
        await asyncio.wait_for(x.connected.wait(), 3)
    await asyncio.sleep(0.05)
    return bus, sched, conv, ears, got, tasks


async def finish(bus, tasks):
    for t in tasks:
        t.cancel()
    await bus.stop()


class FakeSTT:
    def __init__(self, text=""):
        self.text, self.calls = text, 0

    async def transcribe(self, pcm):
        self.calls += 1
        return self.text


def utt(reason="wake", pcm=b"\1\0" * 800):
    return {"reason": reason, "had_speech": True, "seconds": 1.5}, {"audio_b64": base64.b64encode(pcm).decode()}


def states(bus, details=False):
    """The states published, each change once (within a state, what he's doing can change: D-112)."""
    seen = [(r["data"]["state"], r["data"].get("detail", "")) for r in reversed(bus.trace.recent()) if r["type"] == "pip.state"]
    if details:
        return seen
    return [s for i, (s, _) in enumerate(seen) if i == 0 or seen[i - 1][0] != s]


async def test_thinking_tone_plays_instantly_and_state_follows(tmp_path):
    clock = Clock()
    bus, sched, conv, ears, got, tasks = await setup_bus(tmp_path, clock, FakeSTT("Pip, what's the capital of Australia?"))
    await ears.emit("heard.wake", {"word": "hey_pip"})
    await asyncio.sleep(0.05)
    d, p = utt("wake")
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.02)
    assert "ears.tone" in [k for k, _, _ in got]                     # instantly, before the answer
    await ears.emit("speech.started", {})
    await asyncio.sleep(0.15)
    await ears.emit("speech.finished", {"interrupted": False})
    await asyncio.sleep(0.6)
    s = states(bus)
    assert s == ["listening", "thinking", "speaking", "listening", "idle"]  # no flicker in between (for the face)
    assert states(bus, details=True)[:3] == [("listening", "called"), ("listening", "hearing"), ("listening", "turn")]
    assert ("listening", "follow_up") in states(bus, details=True)
    assert "One sec." not in [t for k, t, _ in got]
    await finish(bus, tasks)


async def test_no_tone_for_pips_own_commands_or_when_off(tmp_path):
    clock = Clock()
    bus, sched, conv, ears, got, tasks = await setup_bus(
        tmp_path, clock, FakeSTT("Pip, set a timer for ten minutes"),
        cfg={"conversation": {"follow_up_seconds": 0.3}, "thinker": {"thinking_tone": "off"}})
    d, p = utt("wake")
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.2)
    conv.stt.text = "Pip, what's the capital of Australia?"
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.3)
    assert "ears.tone" not in [k for k, _, _ in got]
    await finish(bus, tasks)


async def test_ringing_state(tmp_path):
    clock = Clock()
    bus, sched, conv, ears, got, tasks = await setup_bus(tmp_path, clock, FakeSTT("okay"))
    sched.add("timer", "pasta", clock() + 1, length=1)
    clock.t += 2
    await asyncio.sleep(0.3)
    assert conv.state == "ringing"
    await sched.acknowledge("test")
    await asyncio.sleep(0.05)
    assert conv.state != "ringing" and "ringing" in states(bus)
    await finish(bus, tasks)


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


async def test_noise_never_reaches_speech_to_text(tmp_path):
    clock = Clock()
    stt = FakeSTT("Thank you.")
    bus, sched, conv, ears, got, tasks = await setup_bus(
        tmp_path, clock, stt, cfg={"conversation": {"follow_up_seconds": 0.3}, "stt": {"voice_check": True}})
    click = np.zeros(16000, np.int16)
    click[8000:8040] = 20000
    for reason in ("follow_up", "barge_in", "room"):
        d, p = utt(reason, click.tobytes())
        await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.5)
    assert stt.calls == 0 and "thinker.ask" not in [k for k, _, _ in got]
    assert len([r for r in bus.trace.recent() if r["type"] == "heard.noise"]) == 3
    await finish(bus, tasks)
