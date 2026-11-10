"""Changing anything works the same for every kind of item (after the owner's test, 2026-11-05): "change that
alarm to 7:30", "add a minute", "move the dentist", "cancel that" (core/commands.py, core/alerts.py)."""
from datetime import datetime

import pytest
from helpers import clock_at, commands_at

NOW = datetime(2026, 11, 4, 23, 55)  # a Wednesday, just before midnight (when the owner tested)
Clock = clock_at(NOW)



@pytest.fixture
def cmds(tmp_path):
    return commands_at(tmp_path, Clock())   # (commands, schedule, clock), with the calendar module


def when_(row, key="due"):
    return datetime.fromtimestamp(row[key])


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
