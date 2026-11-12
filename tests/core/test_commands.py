"""Pip's own commands (core/commands.py, core/alerts.py): timers, alarms, reminders and the calendar's offline phrases,
understood on the Pi (offline, and typed on the dashboard), and what still reaches Claude."""
import asyncio
from datetime import datetime

import pytest
from helpers import FakeSTT, clock_at, commands_at, finish, say, utt
from helpers import alarms_bus as setup_bus

from pip_assistant.core.commands import sure

NOW = datetime(2026, 11, 4, 19, 30)  # a Wednesday evening
Clock = clock_at(NOW)


# ---------------------------------------------------------------- the phrases


@pytest.fixture
def cmds(tmp_path):
    return commands_at(tmp_path, Clock())   # (commands, schedule, clock), with the calendar module


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
    assert await say(c, "remind me tonight at 1 to take meds") == "Okay, I'll remind you at 1 AM."   # same night (D-114)


async def test_cancel_an_alarm_by_a_time_it_does_not_have(cmds):
    c, s, clock = cmds
    clock.t = datetime(2026, 11, 6, 20, 0).timestamp()   # Friday evening
    await say(c, "set an alarm for 7 tomorrow")
    await say(c, "set an alarm for 9 tomorrow")
    c.alerts._focus = None
    assert await say(c, "cancel the 8 am alarm") == "You don't have an alarm at 8 AM."
    assert len(s.active()) == 2
    await say(c, "cancel all alarms")
    await say(c, "set an alarm for 7 every weekday")    # next rings Monday
    c.alerts._focus = None
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

# ---------------------------------------------------------------- with fake programs: Claude first, Pip's own commands as the fallback (D-94)


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
    conv.own_commands = "offline"           # (with "sure" this one is done without Claude anyway, D-123)

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
    assert [t for k, t, _ in got if k == "ears.say"] == ["Timer's on, 5 minutes."]   # stays quiet (D-115)
    assert "thinker.ask" not in [k for k, _, _ in got]
    d, p = utt("room")                                        # room speech is never transcribed on the Pi (D-50)
    calls = local.calls
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.2)
    assert local.calls == calls
    await finish(bus, tasks)


# ---------------------------------------------------------------- D-123: clearly worded commands, without Claude
@pytest.mark.parametrize("said", ["Pip, stop.", "Shut up.", "never mind", "Snooze.", "What time is it?",
                                  "Set a timer for ten minutes.", "Can you set a pasta timer for 5 minutes please?",
                                  "10 minute timer", "start a timer for an hour and a half", "cancel the pasta timer",
                                  "how long is left on the timer"])
def test_clearly_worded_commands_are_sure(said):
    assert sure(said)


@pytest.mark.parametrize("said", ["set a timer for after dinner", "can you stop the music in 10 minutes",
                                  "what time is it in Tokyo", "set a timer for ten minutes and remind me to call mom",
                                  "set an alarm for 7", "remind me to call mom in 10 minutes", "how long until christmas",
                                  "set a timer", "what time does the store close", "stop it pip I'm talking to Sam"])
def test_anything_more_goes_to_claude(said):
    assert not sure(said)


async def test_a_sure_command_is_done_without_claude_and_stop_is_instant(tmp_path):
    clock = Clock()
    stt = FakeSTT("Pip, set a timer for ten minutes.")
    bus, sched, conv, ears, got, tasks = await setup_bus(tmp_path, clock, stt)
    d, p = utt("wake")
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.3)
    assert [t for k, t, _ in got if k == "ears.say"] == ["Timer's on, 10 minutes."] and len(sched.active()) == 1
    stt.text = "Pip, stop."
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.3)
    assert ("ears.stop", None, {}) in got and ("ears.listen", None, {"seconds": 0}) in got
    stt.text = "Pip, set a timer for after dinner."              # not clear: Claude works it out (D-94)
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.3)
    assert [x[1] for x in got if x[0] == "thinker.ask"] == ["Pip, set a timer for after dinner."]
    decisions = [r["data"]["why"] for r in bus.trace.recent() if r["type"] == "turn.decision"]
    assert sorted(decisions) == ["short", "stop", "sure"]     # the timer and stop: fast; "after dinner": answered
    await finish(bus, tasks)
