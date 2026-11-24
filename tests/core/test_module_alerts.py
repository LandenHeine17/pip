"""Modules hear Pip's alarms (Module.on_alert, D-143): ringing, stopped, snoozed, unanswered, with the alarms
themselves; what a module wants said is said once he's free (Conversation.say_when_free). And what else a module
may know of them: the alarms set (ModuleAlerts.upcoming) and where Pip lives (ctx.location)."""
import asyncio
from datetime import datetime

import pytest
from helpers import FakeSTT, clock_at, finish, utt
from helpers import alarms_bus as setup_bus

from pip_assistant.common.modules import ROOT, read
from pip_assistant.core.modules import Module, ModuleAlerts, Registry
from pip_assistant.core.schedule import Schedule

NOW = datetime(2026, 11, 4, 7, 0)
Clock = clock_at(NOW)


class Listener(Module):
    def __init__(self, reply="Good morning!"):
        self.heard, self.reply = [], reply

    async def on_alert(self, event, alerts):
        self.heard.append((event, [a["label"] for a in alerts]))
        return self.reply if event == "stopped" else None


async def ring(tmp_path, stt_text):
    clock = Clock()
    bus, sched, conv, ears, got, tasks = await setup_bus(tmp_path, clock, FakeSTT(stt_text))
    listener = Listener()
    conv.modules.modules["listener"] = listener
    sched.add("alarm", "wake up", clock() + 60)
    clock.t += 61
    await asyncio.sleep(0.4)
    return bus, sched, ears, got, tasks, listener


async def test_a_module_hears_the_alarm_ring_and_stop_and_its_words_come_after(tmp_path):
    bus, sched, ears, got, tasks, listener = await ring(tmp_path, "okay, I'm up")
    assert listener.heard == [("ringing", ["wake up"])]
    d, p = utt("barge_in")
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(1.2)
    assert listener.heard[-1] == ("stopped", ["wake up"]) and not sched.ringing
    says = [t for k, t, _ in got if k == "ears.say"]
    assert says[-1] == "Good morning!"    # after the ringing, once he's free
    await finish(bus, tasks)


async def test_snoozed_and_unanswered_are_heard_too_and_nothing_is_said(tmp_path):
    bus, sched, ears, got, tasks, listener = await ring(tmp_path, "snooze")
    d, p = utt("barge_in")
    await ears.emit("heard.utterance", d, private=p)
    await asyncio.sleep(0.5)
    assert listener.heard[-1] == ("snoozed", ["wake up"])
    assert "Good morning!" not in [t for k, t, _ in got if k == "ears.say"]
    sched.clock.t += 5 * 60 + 1          # it rings again; nobody answers this time
    await asyncio.sleep(0.4)
    sched.clock.t += 2                   # (ring_minutes is 0.02 here)
    await asyncio.sleep(0.6)
    assert listener.heard[-2:] == [("ringing", ["wake up"]), ("unanswered", ["wake up"])]
    assert "Good morning!" not in [t for k, t, _ in got if k == "ears.say"]
    await finish(bus, tasks)


def test_a_module_may_read_the_alarms_set(tmp_path):
    clock = Clock()
    s = Schedule(tmp_path / "pip.db", None, {}, clock=clock)
    s.add("timer", "pasta", clock() + 60, length=60)
    s.add("alarm", "wake up", clock() + 3600, repeat="weekdays")
    s.add("alarm", "alarm", clock() + 600)
    alarms = ModuleAlerts(s).upcoming("alarm")
    assert [a["label"] for a in alarms] == ["alarm", "wake up"]           # soonest first, alarms only
    assert set(alarms[0]) == {"id", "kind", "label", "due", "repeat"}
    with pytest.raises(ValueError):
        ModuleAlerts(s).upcoming("event")


def test_a_module_knows_where_pip_lives(tmp_path):
    reg = Registry.load({"thinker": {"location": "Lincoln, Nebraska"}}, infos=[read(ROOT / "_template")],
                        db_path=tmp_path / "pip.db")
    assert reg.modules["_template"].ctx.location == "Lincoln, Nebraska"


async def test_one_failing_module_doesnt_stop_the_others_hearing(tmp_path):
    class Broken(Module):
        async def on_alert(self, event, alerts):
            raise RuntimeError("boom")
    reg = Registry()
    reg.modules = {"broken": Broken(), "ok": Listener("Hi.")}
    assert await reg.on_alert("stopped", [{"label": "x"}]) == "Hi."
