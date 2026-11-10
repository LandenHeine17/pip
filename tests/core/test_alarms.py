"""Ringing (core/schedule.py): timers and alarms ring until answered, snooze, stop by themselves, survive restarts,
calendar heads-ups, the dashboard's buttons, and answering a ringing alarm in plain words (D-94)."""
import asyncio
import json
from datetime import datetime

import pytest
from helpers import FakeSTT, clock_at, finish, registry, say, utt
from helpers import alarms_bus as setup_bus

from pip_assistant.core.commands import Commands
from pip_assistant.core.schedule import Schedule

NOW = datetime(2026, 11, 4, 19, 30)  # a Wednesday evening
Clock = clock_at(NOW)


# ---------------------------------------------------------------- ringing (with a fake pip-ears)


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
    assert "did you see the game last night" in "\n".join(conv.transcript.render())   # but it's still heard (2026-11-10)
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

# ---------------------------------------------------------------- restarts and the clock


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
    assert s2.get(a["id"])["state"] == "waiting" and s2.get(a["id"])["due"] < clock()  # rings on the first check


async def test_in_n_hours_counts_real_time_across_a_clock_change(tmp_path):
    clock = Clock(datetime(2026, 10, 31, 22, 0))    # the clocks go back an hour at 2 AM on Nov 1
    s = Schedule(tmp_path / "pip.db", clock=clock)
    c = Commands(s, clock=clock)
    assert await say(c, "wake me up in 8 hours") == "Alarm set for 5 AM tomorrow."
    assert s.active()[0]["due"] - clock() == 8 * 3600
    clock.t = datetime(2026, 11, 1, 0, 30).timestamp()
    assert await say(c, "remind me in 2 hours to check the oven") == "Okay, I'll remind you at 1:30 AM."   # the second 1:30
    assert s.active(("reminder",))[0]["due"] - clock() == 2 * 3600


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

# ---------------------------------------------------------------- heads-ups and the dashboard


def test_event_heads_up_and_removal(tmp_path):
    clock = Clock()
    s = Schedule(tmp_path / "pip.db", clock=clock)
    cal = registry(tmp_path, s, clock).module("calendar").store
    ev = cal.add_event("dentist", datetime(2026, 11, 10, 15, 0), heads_up_before=1800)
    [h] = s.active(("event",))
    assert h["due"] == datetime(2026, 11, 10, 14, 30).timestamp()
    assert Schedule.announcement(h) == "Heads up: dentist in half an hour."
    cal.remove_event(ev["id"])
    assert s.active(("event",)) == []


async def test_dashboard_can_shut_off_and_make_timers(tmp_path):
    from pip_assistant.common.messages import Message
    from pip_assistant.core.dashapi import alert_action
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


async def test_a_snoozed_timer_counts_down_the_snooze(tmp_path):
    """Owner's feedback (2026-11-10): a 5-minute timer snoozed for 15 showed its bar out of 5 minutes."""
    clock = Clock()
    s = Schedule(tmp_path / "pip.db", clock=clock)
    t = s.add("timer", "pasta", clock() + 300, length=300)
    s.ringing = [t["id"]]
    assert await s.snooze(15) == 1
    row = s.get(t["id"])
    assert row["length"] == 900 and row["due"] == clock() + 900 and row["state"] == "waiting"
    alarm = s.add("event", "dentist", clock() + 60, length=1800)   # a heads-up keeps "in half an hour"
    s.ringing = [alarm["id"]]
    await s.snooze(5)
    assert s.get(alarm["id"])["length"] == 1800
