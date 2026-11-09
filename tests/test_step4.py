"""M1a step 4 tests: understanding times, Pip's own commands, ringing, restarts, offline, privacy."""
import asyncio
import json
from datetime import datetime, timedelta

import pytest

from modhelp import registry

from pip_assistant.common.client import BusClient
from pip_assistant.core import when
from pip_assistant.core.bus import Bus
from pip_assistant.core.commands import Commands
from pip_assistant.core.conversation import Conversation
from pip_assistant.core.health import Health
from pip_assistant.core.schedule import Schedule
from pip_assistant.core.trace import Trace

NOW = datetime(2026, 11, 4, 19, 30)  # a Wednesday evening


class Clock:
    def __init__(self, dt=NOW):
        self.t = dt.timestamp()

    def __call__(self):
        return self.t


# ------------------------------------------------------------------ understanding times
@pytest.mark.parametrize("text,secs", [
    ("ten minutes", 600), ("an hour and a half", 5400), ("half an hour", 1800), ("twenty five minutes", 1500),
    ("1 hour and 20 minutes", 4800), ("90 seconds", 90), ("a minute", 60), ("two and a half minutes", 150),
    ("a couple of minutes", 120), ("what's the weather", None)])
def test_durations(text, secs):
    assert when.duration(text) == secs


@pytest.mark.parametrize("text,expect", [
    ("at 7:30", datetime(2026, 11, 5, 7, 30)),    # already past today -> tomorrow
    ("at 8", datetime(2026, 11, 4, 20, 0)),       # no AM/PM: whichever comes next
    ("at 6", datetime(2026, 11, 5, 6, 0)),
    ("tomorrow at 3", datetime(2026, 11, 5, 15, 0)),   # another day: daytime hours preferred
    ("tuesday at 8", datetime(2026, 11, 10, 8, 0)),
    ("at noon", datetime(2026, 11, 5, 12, 0)), ("7 pm tomorrow", datetime(2026, 11, 5, 19, 0)),
    ("quarter to 8", datetime(2026, 11, 4, 19, 45)), ("at 10 tonight", datetime(2026, 11, 4, 22, 0)),
    ("for 10 minutes", None), ("set a timer for 10 minutes", None)])
def test_clock_times(text, expect):
    assert when.clock(text, NOW) == expect


def test_repeats_keep_their_clock_time():
    assert when.next_repeat("weekdays", datetime(2026, 11, 6, 8, 0), datetime(2026, 11, 6, 8, 1)) == datetime(2026, 11, 9, 8, 0)
    assert when.next_repeat("weekly:0", datetime(2026, 11, 9, 9, 0), datetime(2026, 11, 9, 9, 1)) == datetime(2026, 11, 16, 9, 0)
    # Across the daylight-saving change (Nov 1 2026 in the US) a daily 7 AM alarm stays at 7 AM
    assert when.next_repeat("daily", datetime(2026, 10, 31, 7, 0), datetime(2026, 10, 31, 7, 1)).hour == 7


def test_saying_durations():
    assert when.say_duration(600) == "10 minutes"
    assert when.say_duration(5400) == "an hour and a half"
    assert when.say_duration(3660) == "1 hour and 1 minute"
    assert when.say_left(4 * 60 + 40) == "5 minutes"


# ------------------------------------------------------------------ commands
@pytest.fixture
def cmds(tmp_path):
    clock = Clock()
    s = Schedule(tmp_path / "pip.db", clock=clock)
    c = Commands(s, clock=clock, calendar=registry(tmp_path, s, clock).get("calendar"))   # the calendar module (D-96)
    return c, s, clock


async def say(c, text):
    return await c.handle(text)


async def test_timers(cmds):
    c, s, clock = cmds
    assert await say(c, "Pippletron, set a timer for ten minutes.") == "Timer's on, 10 minutes."
    assert await say(c, "Hey Pip, set a pasta timer for 5 minutes") == "Pasta timer's on, 5 minutes."
    assert await say(c, "set a 3 minute egg timer") == "Egg timer's on, 3 minutes."
    assert await say(c, "how long is left on the pasta timer?") == "Pasta has 5 minutes left."
    assert await say(c, "add 2 minutes to the egg timer") == "Done, 5 minutes left."
    assert (await say(c, "cancel the timer")).startswith("Which one:")
    assert await say(c, "pasta") == "Cancelled the pasta timer."
    assert await say(c, "cancel the laundry timer") == "There's no laundry timer."
    assert await say(c, "cancel all timers") == "Cancelled both timers."
    assert await say(c, "cancel the timer") == "You don't have any timers."


async def test_timer_without_length_asks_and_actually_corrects(cmds):
    c, s, clock = cmds
    assert await say(c, "set a timer") == "For how long?"
    assert await say(c, "twelve minutes") == "Timer's on, 12 minutes."
    assert await say(c, "actually 15 minutes") == "Changed it to 15 minutes."
    [t] = s.active(("timer",))
    assert t["due"] == pytest.approx(clock() + 900)


async def test_alarms_and_reminders(cmds):
    c, s, clock = cmds
    assert await say(c, "wake me up at 7") == "Alarm set for 7 AM tomorrow."
    assert await say(c, "set an alarm for 6:30 am every weekday") == "Alarm set for 6:30 AM every weekday."
    assert await say(c, "remind me at 6 to take the bins out") == "Okay, I'll remind you at 6 AM tomorrow."
    assert await say(c, "remind me in 20 minutes to check the oven") == "Okay, I'll remind you in 20 minutes."
    assert await say(c, "remind me every monday at 9 am to take out the recycling") == "Okay, I'll remind you every Monday at 9 AM."
    assert await say(c, "remind me to water the plants") == "When should I remind you?"
    assert await say(c, "tomorrow at 9") == "Okay, I'll remind you at 9 AM tomorrow."
    assert (await say(c, "what reminders do I have")).startswith("Reminders: check the oven")
    assert await say(c, "cancel my 7 am alarm") == "Cancelled your 7 AM alarm."
    labels = {a["label"] for a in s.active(("reminder",))}
    assert {"take the bins out", "check the oven", "water the plants", "take out the recycling"} <= labels


async def test_asking_about_alarms_and_timers_together_answers_both(cmds):
    c, s, clock = cmds
    assert await say(c, "what alarms and timers do I have") == "Nothing set: no alarms or timers."
    await say(c, "wake me up at 7")
    assert await say(c, "what alarms and timers do I have") == "One alarm, for 7 AM tomorrow. No timers."
    await say(c, "set a pasta timer for 5 minutes")
    assert await say(c, "any timers or alarms?") == "Timers: the pasta timer with 5 minutes left. One alarm, for 7 AM tomorrow."
    assert await say(c, "what timers do I have") == "Pasta has 5 minutes left."        # timers alone: as before
    assert await say(c, "what reminders do I have") == "No reminders set."


async def test_calendar(cmds):
    c, s, clock = cmds
    assert await say(c, "put the dentist on the calendar for Tuesday at 3") == "Added dentist, Tuesday at 3 PM."
    assert await say(c, "add soccer practice to the calendar every Thursday at 5 pm") == "Added soccer practice, every Thursday at 5 PM."
    assert await say(c, "what do I have tomorrow") == "Tomorrow: soccer practice at 5 PM."
    assert (await say(c, "what's on the calendar this week?")).startswith("This week: soccer practice tomorrow")
    assert await say(c, "take the dentist off the calendar") == "Took dentist off the calendar."
    assert await say(c, "what's on tuesday") == "Nothing on the calendar Tuesday."


@pytest.mark.parametrize("text", [
    "what's the weather today?", "how long does it take to boil an egg?", "remind me what the capital of France is",
    "how long do I need to bake cookies", "set the oven to 350", "tell me a story", "what's on TV tonight?"])
async def test_ordinary_questions_still_go_to_claude(cmds, text):
    c, s, clock = cmds
    assert await say(c, text) is None


async def test_time_and_stop(cmds):
    c, s, clock = cmds
    assert await say(c, "what time is it?") == "It's 7:30 PM."
    assert await say(c, "Pip, stop") == ""
    assert await say(c, "snooze") == "Nothing's ringing right now."


# ------------------------------------------------------------------ ringing (with a fake pip-ears)
async def setup_bus(tmp_path, clock, stt=None, local_stt=None, answers=None):
    sock = tmp_path / "core.sock"
    bus = Bus(sock, Trace(tmp_path / "pip.db"), Health(["ears", "thinker"], restart=False))
    sched = Schedule(tmp_path / "pip.db", bus, {"alarms": {"announce_every_seconds": 0.2, "ring_minutes": 0.02}}, clock=clock)
    conv = Conversation(bus, {"conversation": {"follow_up_seconds": 8.0}}, stt, schedule=sched, local_stt=local_stt,
                        modules=registry(tmp_path, sched, clock))
    await bus.start()
    ears, thinker = BusClient("ears", socket_path=sock), BusClient("thinker", socket_path=sock)
    got = []

    async def record(msg):
        got.append((msg.type, msg.private.get("text"), msg.data))

    for t in ("ears.chime", "ears.say", "ears.listen", "ears.ring", "ears.ring_stop", "ears.stop"):
        ears.on(t, record)

    async def ask(msg):
        got.append(("thinker.ask", msg.private.get("text"), msg.private.get("schedule")))
        answer = (answers or {}).get(msg.private.get("text"), "Claude here.")
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
    def __init__(self, text="", fail=False):
        self.text, self.fail, self.calls = text, fail, 0

    async def transcribe(self, pcm):
        self.calls += 1
        if self.fail:
            raise ConnectionError("no internet")
        return self.text


def utt(reason="wake"):
    import base64
    return {"reason": reason, "had_speech": True, "seconds": 1.5}, {"audio_b64": base64.b64encode(b"\1\0" * 800).decode()}


async def test_timer_rings_until_answered(tmp_path):
    clock = Clock()
    stt = FakeSTT("okay okay, I'm up")
    bus, sched, conv, ears, got, tasks = await setup_bus(tmp_path, clock, stt)
    sched.add("timer", "pasta", clock() + 60, length=60)
    clock.t += 61
    await asyncio.sleep(0.9)
    says = [t for k, t, _ in got if k == "ears.say"]
    assert ("ears.ring" in [k for k, _, _ in got]) and says[0] == "Your pasta timer is done."
    assert len(says) >= 2                                     # repeated while nobody answers
    d, p = utt("barge_in")                                    # someone talks over the ringing
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.3)
    assert "ears.ring_stop" in [k for k, _, _ in got] and not sched.ringing
    assert sched.active() == []
    trace = json.dumps(bus.trace.recent())
    assert "pasta" not in trace and "I'm up" not in trace     # labels and words never logged
    await finish(bus, tasks)


async def test_chatter_does_not_stop_the_alarm_but_snooze_does(tmp_path):
    clock = Clock()
    stt = FakeSTT("did you see the game last night")
    bus, sched, conv, ears, got, tasks = await setup_bus(tmp_path, clock, stt)
    sched.add("alarm", "alarm", clock() + 5)
    clock.t += 6
    await asyncio.sleep(0.3)
    d, p = utt("room")
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.2)
    assert sched.ringing                                      # TV/chatter: keeps ringing (FEAT-037)
    stt.text = "snooze ten minutes"
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.2)
    assert not sched.ringing
    [a] = sched.active()
    assert a["state"] == "waiting" and a["due"] == pytest.approx(clock() + 600)
    assert "Snoozed for 10 minutes." in [t for k, t, _ in got if k == "ears.say"]
    await finish(bus, tasks)


async def test_unanswered_alarm_stops_by_itself(tmp_path):
    clock = Clock()
    bus, sched, conv, ears, got, tasks = await setup_bus(tmp_path, clock, FakeSTT(""))
    sched.add("reminder", "call mom", clock() + 1)
    clock.t += 2
    await asyncio.sleep(0.3)
    assert sched.ringing
    clock.t += 5 * 60                                         # ring_minutes passes
    await asyncio.sleep(0.4)
    assert not sched.ringing and "alert.unanswered" in [r["type"] for r in bus.trace.recent()]
    await finish(bus, tasks)


async def test_any_wording_goes_to_claude_whose_action_pip_carries_out(tmp_path):
    """Online, there are no fixed phrasings: Claude understands the request, Pip's code does it and confirms (D-94)."""
    clock = Clock()
    said = "Pippletron, could you give me like ten minutes on a timer"
    stt = FakeSTT(said)
    bus, sched, conv, ears, got, tasks = await setup_bus(
        tmp_path, clock, stt, answers={said: 'ACTION {"do": "add", "kind": "timer", "seconds": 600}'})
    d, p = utt("wake")
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.3)
    assert [k for k, _, _ in got if k == "thinker.ask"] == ["thinker.ask"]
    assert [t for k, t, _ in got if k == "ears.say"] == ["Timer's on, 10 minutes."] and len(sched.active()) == 1
    stt.text = "Pip, what's the capital of Australia?"         # Claude sees what's set
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.3)
    ask = [x for x in got if x[0] == "thinker.ask"][-1]
    assert "timer 'timer' with 10 minutes left" in ask[2]
    await finish(bus, tasks)


async def test_if_claude_fails_pips_own_commands_still_try(tmp_path):
    clock = Clock()
    said = "Pip, set a timer for ten minutes"
    bus, sched, conv, ears, got, tasks = await setup_bus(tmp_path, clock, FakeSTT(said))

    async def broken(*a, **k):
        from pip_assistant.common.messages import Message
        return Message("reply", "thinker.ask", data={"error": "TimeoutError"})
    conv.bus_request = broken
    d, p = utt("wake")
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.3)
    assert [t for k, t, _ in got if k == "ears.say"] == ["Timer's on, 10 minutes."]
    await finish(bus, tasks)


async def test_offline_uses_local_hearing_and_only_does_commands(tmp_path):
    clock = Clock()
    local = FakeSTT("Pip, set a timer for five minutes")
    bus, sched, conv, ears, got, tasks = await setup_bus(tmp_path, clock, FakeSTT(fail=True), local_stt=local)
    d, p = utt("wake")
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.3)
    assert [t for k, t, _ in got if k == "ears.say"] == ["Timer's on, 5 minutes."] and len(sched.active()) == 1
    local.text = "Pip, what's the capital of Australia?"
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.3)
    assert [t for k, t, _ in got if k == "ears.say"][-1].startswith("I'm offline")
    assert "thinker.ask" not in [k for k, _, _ in got]
    d, p = utt("room")                                        # room speech is never transcribed on the Pi (D-50)
    calls = local.calls
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.2)
    assert local.calls == calls
    await finish(bus, tasks)


# ------------------------------------------------------------------ restarts
def test_alarms_survive_a_restart_and_missed_ones_are_noted(tmp_path):
    clock = Clock()
    s = Schedule(tmp_path / "pip.db", clock=clock)
    soon = s.add("timer", "eggs", clock() + 60, length=60)
    long_ago = s.add("reminder", "call mom", clock() + 120)
    daily = s.add("alarm", "alarm", clock() + 180, repeat="daily")
    s.db.close()
    clock.t += 45 * 60                                        # Pip was off for 45 minutes
    s2 = Schedule(tmp_path / "pip.db", clock=clock)
    s2._startup_catch_up()
    states = {a["id"]: a for a in map(dict, s2.db.execute("SELECT * FROM alerts"))}
    assert states[soon["id"]]["state"] == "missed"            # 44 min late: past the 30 min grace
    assert states[long_ago["id"]]["state"] == "missed"
    assert states[daily["id"]]["state"] == "waiting"          # a daily alarm moves on to tomorrow
    assert states[daily["id"]]["due"] == pytest.approx(daily["due"] + 86400)
    assert {a["label"] for a in s2.recent_missed()} == {"eggs", "call mom", "alarm"}


def test_recently_due_rings_after_restart(tmp_path):
    clock = Clock()
    s = Schedule(tmp_path / "pip.db", clock=clock)
    a = s.add("timer", "eggs", clock() + 60, length=60)
    clock.t += 5 * 60
    s2 = Schedule(tmp_path / "pip.db", clock=clock)
    s2._startup_catch_up()
    assert s2._row(a["id"])["state"] == "waiting" and s2._row(a["id"])["due"] < clock()  # rings on the first check


def test_event_heads_up_and_removal(tmp_path):
    clock = Clock()
    s = Schedule(tmp_path / "pip.db", clock=clock)
    cal = registry(tmp_path, s, clock).get("calendar")
    ev = cal.add_event("dentist", datetime(2026, 11, 10, 15, 0), heads_up_before=1800)
    [h] = s.active(("event",))
    assert h["due"] == datetime(2026, 11, 10, 14, 30).timestamp()
    assert Schedule.announcement(h) == "Heads up: dentist in half an hour."
    cal.remove_event(ev["id"])
    assert s.active(("event",)) == []


async def test_dashboard_can_shut_off_and_make_timers(tmp_path):
    from pip_assistant.core.main import alert_action
    from pip_assistant.common.messages import Message
    clock = Clock()
    bus, sched, conv, ears, got, tasks = await setup_bus(tmp_path, clock, FakeSTT(""))
    r = await alert_action(sched, conv, Message("request", "core.alert_action", data={"action": "timer", "minutes": 2},
                                                private={"label": "tea"}))
    assert r["ok"] and sched.active()[0]["label"] == "tea"
    clock.t += 121
    await asyncio.sleep(0.3)
    assert sched.ringing
    r = await alert_action(sched, conv, Message("request", "core.alert_action", data={"action": "stop"}))
    assert r["stopped"] == 1 and not sched.ringing
    await finish(bus, tasks)


# ------------------------------------------------------------------ natural language while ringing (D-94)
async def test_ringing_obvious_words_are_instant_and_anything_else_goes_to_claude(tmp_path):
    clock = Clock()
    said = "ugh, give me a little extra time, would you"
    stt = FakeSTT(said)
    bus, sched, conv, ears, got, tasks = await setup_bus(
        tmp_path, clock, stt, answers={said: 'ACTION {"do": "snooze", "minutes": 10}'})
    sched.add("alarm", "alarm", clock() + 60)
    clock.t += 61
    await asyncio.sleep(0.4)
    assert sched.ringing
    d, p = utt("barge_in")                                    # said to Pip, but not an obvious stop/snooze word
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.4)
    asks = [x for x in got if x[0] == "thinker.ask"]
    assert len(asks) == 1 and asks[0][2].startswith("RINGING NOW:")
    assert not sched.ringing and sched.active()[0]["due"] == clock() + 600
    assert "Snoozed for 10 minutes." in [t for k, t, _ in got if k == "ears.say"]
    # The obvious words never wait for Claude
    clock.t += 601
    await asyncio.sleep(0.4)
    assert sched.ringing
    stt.text = "okay, I'm up"
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.3)
    assert not sched.ringing and len([x for x in got if x[0] == "thinker.ask"]) == 1
    await finish(bus, tasks)


# ------------------------------------------------------------------ fixes from the review (2026-11-09)
@pytest.mark.parametrize("text,expect", [
    ("wake me up tomorrow at 6", "Alarm set for 6 AM tomorrow."),        # an alarm with a day: the morning
    ("set an alarm for tomorrow at 5:30", "Alarm set for 5:30 AM tomorrow."),
    ("set an alarm for half past 6 tomorrow", "Alarm set for 6:30 AM tomorrow."),
    ("set an alarm for seven oh five", "Alarm set for 7:05 AM tomorrow."),
    ("set an alarm for 730", "Alarm set for 7:30 AM tomorrow."),
])
async def test_alarm_wordings(cmds, text, expect):
    c, s, clock = cmds
    assert await say(c, text) == expect


async def test_reminders_keep_the_daytime_preference_and_tonight_after_midnight(cmds):
    c, s, clock = cmds
    assert await say(c, "remind me tomorrow at 3 to call mom") == "Okay, I'll remind you at 3 PM tomorrow."
    clock.t = datetime(2026, 11, 4, 22, 0).timestamp()
    assert await say(c, "remind me tonight at 1 to take meds") == "Okay, I'll remind you at 1 AM tomorrow."


async def test_in_n_hours_counts_real_time_across_a_clock_change(tmp_path):
    clock = Clock(datetime(2026, 10, 31, 22, 0))    # the clocks go back an hour at 2 AM on Nov 1
    s = Schedule(tmp_path / "pip.db", clock=clock)
    c = Commands(s, clock=clock)
    assert await say(c, "wake me up in 8 hours") == "Alarm set for 5 AM tomorrow."
    assert s.active()[0]["due"] - clock() == 8 * 3600
    clock.t = datetime(2026, 11, 1, 0, 30).timestamp()
    assert await say(c, "remind me in 2 hours to check the oven") == "Okay, I'll remind you at 1:30 AM."   # the second 1:30
    assert s.active(("reminder",))[0]["due"] - clock() == 2 * 3600


async def test_cancel_an_alarm_by_a_time_it_does_not_have(cmds):
    c, s, clock = cmds
    clock.t = datetime(2026, 11, 6, 20, 0).timestamp()   # Friday evening
    await say(c, "set an alarm for 7 tomorrow")
    await say(c, "set an alarm for 9 tomorrow")
    c._focus = None
    assert await say(c, "cancel the 8 am alarm") == "You don't have an alarm at 8 AM."
    assert len(s.active()) == 2
    await say(c, "cancel all alarms")
    await say(c, "set an alarm for 7 every weekday")    # next rings Monday
    c._focus = None
    assert await say(c, "cancel my 7 am alarm") == "Cancelled your 7 AM alarm."
    assert s.active() == []


async def test_moving_a_ringing_alarm_stops_it_and_rings_at_the_new_time(cmds):
    c, s, clock = cmds
    clock.t = datetime(2026, 11, 10, 6, 0).timestamp()
    changed = []
    s.watchers.append(lambda: changed.append(list(s.ringing)))
    await say(c, "set an alarm for 7")
    clock.t = datetime(2026, 11, 10, 7, 0, 1).timestamp()
    await s._tick()
    assert s.ringing
    assert await say(c, "change that alarm to 7:30") == "Moved your alarm to 7:30 AM."
    assert s.ringing == [] and changed[-1] == []                     # Pip's face leaves "ringing"
    clock.t = datetime(2026, 11, 10, 7, 30, 1).timestamp()
    await s._tick()
    assert s.ringing                                                  # and it rings again at 7:30
    await s.acknowledge("voice")
    # A ringing timer given 5 more minutes stops ringing too
    await say(c, "set a pasta timer for 1 minute")
    clock.t += 61
    await s._tick()
    assert s.ringing
    await say(c, "add 5 minutes to the pasta timer")
    assert s.ringing == [] and s.active()[0]["state"] == "waiting"


async def test_snoozing_a_repeating_alarm_keeps_its_time(cmds):
    c, s, clock = cmds
    clock.t = datetime(2026, 11, 9, 6, 0).timestamp()
    await say(c, "set an alarm for 7 every weekday")
    clock.t = datetime(2026, 11, 10, 7, 0, 0).timestamp()
    await s._tick()
    clock.t += 13
    await s.snooze(9)
    clock.t += 9 * 60 + 1
    await s._tick()
    await s.acknowledge("voice")
    assert datetime.fromtimestamp(s.active()[0]["due"]) == datetime(2026, 11, 11, 7, 0)   # not 7:09


async def test_a_repeating_alarm_in_the_skipped_hour_comes_back_to_its_time(tmp_path):
    clock = Clock(datetime(2027, 3, 13, 22, 0))     # the clocks skip 2–3 AM on Mar 14 2027
    s = Schedule(tmp_path / "pip.db", clock=clock)
    c = Commands(s, clock=clock)
    await say(c, "set an alarm for 2:30 am every day")
    rang = []
    for _ in range(2):
        clock.t = s.active()[0]["due"] + 1
        await s._tick()
        rang.append(datetime.fromtimestamp(clock.t - 1).strftime("%d %H:%M"))
        await s.acknowledge("voice")
    assert rang == ["14 03:30", "15 02:30"]           # 2:30 didn't exist on the 14th; the next day it's 2:30 again


def test_older_databases_get_the_planned_column(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "pip.db")
    db.execute("CREATE TABLE alerts (id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL, due REAL NOT NULL, "
               "length REAL, repeat TEXT NOT NULL DEFAULT '', state TEXT NOT NULL, event_id TEXT, created REAL NOT NULL, "
               "updated REAL NOT NULL)")
    db.execute("INSERT INTO alerts VALUES ('a1', 'alarm', 'alarm', ?, NULL, 'daily', 'waiting', NULL, 0, 0)",
               (datetime(2026, 11, 5, 7, 0).timestamp(),))
    db.commit()
    db.close()
    s = Schedule(tmp_path / "pip.db", clock=Clock())
    a = s.active()[0]
    s._finish(a, "done")
    assert datetime.fromtimestamp(s.active()[0]["due"]) == datetime(2026, 11, 6, 7, 0)


async def test_later_while_ringing_is_not_a_snooze(cmds):
    c, s, clock = cmds
    await say(c, "set a timer for 1 minute")
    clock.t += 61
    await s._tick()
    assert await c.while_ringing("okay okay I'm up, I'll make coffee later", "room") == ""
    assert s.ringing == [] and s.active() == []                       # stopped, not snoozed
    await say(c, "set a timer for 1 minute")
    clock.t += 61
    await s._tick()
    assert await c.while_ringing("okay, five more minutes", "room") == "Snoozed for 5 minutes."
