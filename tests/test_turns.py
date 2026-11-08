"""After the owner's test (2026-11-07): being told to be quiet (D-105), the chime switch, knowing when
someone's done talking (D-106), timer names, approving your own memory by voice (D-107), the clap microphone."""
import asyncio
import time

import numpy as np
import pytest

from pip_assistant.core import actions
from pip_assistant.core.commands import timer_name
from pip_assistant.core.memory import Memory
from pip_assistant.core.people import Match, People
from pip_assistant.core.prefs import Prefs
from test_step6 import FakeEmbedder, finish, room, says, setup


# ---------------------------------------------------------------- D-105: quiet means quiet
async def test_told_to_be_quiet_he_stops_and_goes_back_to_idle(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "Pip, shut up.", social={"join_in": False},
                                                    replies={"direct": ""})            # Claude: PASS
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.4)
    assert says(got) == [] and ("ears.stop", None, {}) in got and ("ears.listen", None, {"seconds": 0}) in got
    assert conv.state == "idle" and conv.expect_until == 0
    await finish(bus, tasks)


async def test_the_chime_can_be_switched_off(tmp_path):
    prefs = Prefs(tmp_path / "pip.db")
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "Pip, what time is it?", social={"join_in": False}, prefs=prefs)
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.3)
    assert ("ears.chime", None, {}) in got
    r = await conv.set_pref("chime", False)
    assert r["chime"] is False and Prefs(tmp_path / "pip.db").get("chime") is False      # kept across restarts
    got.clear()
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.3)
    assert ("ears.chime", None, {}) not in got and says(got) == ["Sure."]
    await finish(bus, tasks)


# ---------------------------------------------------------------- D-106: knowing when they're done
@pytest.mark.real_turn_waits
async def test_he_waits_for_the_end_of_the_turn_and_joins_the_pieces(tmp_path):
    waits = {"turn_wait_short_seconds": 0.3, "turn_wait_statement_seconds": 0.6, "turn_wait_long_seconds": 0.8,
             "turn_wait_after_interrupt_seconds": 0.5}
    bus, conv, ears, got, asks, tasks = await setup(
        tmp_path, "Pip, so I was at the store today and I saw the weirdest thing in the parking lot.",
        social={"join_in": False}, conversation=waits)
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.3)
    assert not asks and ("ears.chime", None, {}) not in got and conv.state == "listening"   # a statement: still waiting
    await ears.emit("heard.started", {"reason": "follow_up"})          # …they carry on
    await asyncio.sleep(0.6)
    assert not asks                                                     # still holding while they talk
    conv.stt.text = "A guy was walking a goose on a leash. What do you think of that?"
    await ears.emit("heard.utterance", {"reason": "follow_up", "had_speech": True, "seconds": 3.0}, private=room()[1])
    await asyncio.sleep(0.15)
    assert not asks
    await asyncio.sleep(0.9)                                            # two sentences: the long wait
    assert len(asks) == 1 and [k for k, _, _ in got].count("ears.chime") == 1 and says(got) == ["Sure."]
    await finish(bus, tasks)


@pytest.mark.real_turn_waits
def test_how_long_he_waits(tmp_path):
    from pip_assistant.core.bus import Bus
    from pip_assistant.core.conversation import Conversation
    from pip_assistant.core.health import Health
    from pip_assistant.core.trace import Trace
    c = Conversation(Bus(tmp_path / "s", Trace(tmp_path / "pip.db"), Health([], restart=False)), {"conversation": {}})
    assert c.turn_wait("what time is it?") == 0.6
    assert c.turn_wait("set a timer for ten minutes") == 0.6                       # short request
    assert c.turn_wait("I think we should probably get the pizza from the other place tonight") == 1.2
    assert c.turn_wait("We went to the lake. It was freezing.") == 1.5
    assert c.turn_wait("what's the weather like in the") == 3.0                    # sounds unfinished
    c.last_cut = time.monotonic()
    assert c.turn_wait("what time is it?") == 1.6                                  # just talked over: takes the hint


# ---------------------------------------------------------------- timer names
def test_timer_names_are_names():
    assert timer_name("10 minute timer") == "" and timer_name("timer") == "" and timer_name("Pasta timer") == "pasta"


def test_a_ten_minute_timer_is_not_a_timer_timer(tmp_path):
    from pip_assistant.core.commands import Commands
    from pip_assistant.core.schedule import Schedule
    c = Commands(Schedule(tmp_path / "pip.db"))
    said, _ = actions.apply(c, 'ACTION {"do": "add", "kind": "timer", "seconds": 600, "label": "10 minute timer"}')
    assert said == "Timer's on, 10 minutes."


# ---------------------------------------------------------------- D-107: approving your own memory by voice
def test_the_person_a_memory_is_about_can_approve_it(tmp_path):
    people = People(tmp_path / "pip.db", embedder=FakeEmbedder())
    landen, caleb = people.add_person("Landen", "admin"), people.add_person("Caleb")
    mem = Memory(tmp_path / "pip.db")

    class Cmds:
        class s:
            @staticmethod
            def find(ref):
                return None

        @staticmethod
        def now():
            from datetime import datetime
            return datetime.now()
    sure = lambda p: Match(p["id"], p["name"], "sure", 0.9)
    ctx = lambda sp: {"memory": mem, "people": people, "speaker": sp}
    raw = ('ACTION {"do": "remember", "text": "Caleb is allergic to peanuts", "category": "personal", '
           '"about": ["Caleb"], "sensitive": true}')
    said, _ = actions.apply(Cmds, raw, ctx(sure(landen)))
    assert "memory inbox" in said
    [row] = mem.items("pending")
    # Saying it again: it's waiting, not "already known"
    assert actions.apply(Cmds, raw, ctx(sure(caleb)))[0] == "That's waiting in the memory inbox."
    # Claude sees it only when it's Caleb speaking, and only Caleb may approve it
    assert "allergic" in mem.for_claude(caleb["id"], "sure") and "allergic" not in mem.for_claude(landen["id"], "sure")
    ref = mem.short(row["id"])
    approve = f'ACTION {{"do": "approve_memory", "id": "{ref}"}}'
    assert actions.apply(Cmds, approve, ctx(sure(landen))) == (actions.FAILED, 0)
    assert actions.apply(Cmds, approve, ctx(Match(caleb["id"], "Caleb", "probably", 0.6))) == (actions.FAILED, 0)
    assert actions.apply(Cmds, approve, ctx(sure(caleb)))[0] == "Saved: Caleb is allergic to peanuts."
    assert mem.items("pending") == [] and mem.items("active")[0]["text"] == "Caleb is allergic to peanuts"


# ---------------------------------------------------------------- D-91: claps on their own microphone
async def test_the_clap_microphone_is_chosen_on_the_dashboard(tmp_path):
    prefs = Prefs(tmp_path / "pip.db")
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, prefs=prefs)
    assert ("ears.clap_mic", None, {"device": ""}) in got                  # none yet: told when ears connects
    p = await conv.prefs_snapshot()
    assert [m["label"] for m in p["mics"]] == ["USB Speaker Phone", "Cheap USB mic"] and p["clap_mic"] == ""
    assert "error" in await conv.set_pref("clap_mic", "alsa_input.usb-Speaker_Phone")   # can't hear claps
    await conv.set_pref("clap_mic", "alsa_input.usb-cheap-mic")
    await asyncio.sleep(0.05)
    assert ("ears.clap_mic", None, {"device": "alsa_input.usb-cheap-mic"}) in got
    assert Prefs(tmp_path / "pip.db").get("clap_mic") == "alsa_input.usb-cheap-mic"
    await finish(bus, tasks)


async def test_ears_hears_claps_only_on_the_clap_microphone(monkeypatch):
    """The speakerphone isn't fed to the clap detector; the clap mic is, but not while Pip makes sound."""
    from pip_assistant.ears import main as ears_main
    opened = []

    class FakeMic:
        def __init__(self, target=""):
            opened.append(target)

        async def frames(self):
            for _ in range(5):
                yield np.zeros(1280, np.int16)
            await asyncio.sleep(3600)

    class Claps:
        fed = 0

        def reset(self):
            pass

        def feed(self, frame):
            Claps.fed += 1
            return Claps.fed == 3

    emitted = []

    class Bus:
        status = {}

        async def emit(self, kind, data):
            emitted.append(kind)
    monkeypatch.setattr(ears_main, "Microphone", FakeMic)
    e = ears_main.Ears.__new__(ears_main.Ears)
    e.bus, e.claps, e.clap_mic, e.clap_task, e.quiet_after = Bus(), Claps(), "", None, 0.0
    e.mic_mode, e.speaking, e.ring_task = "claps_only", False, None
    await e.on_clap_mic(type("M", (), {"data": {"device": "alsa_input.usb-cheap-mic"}})())
    await asyncio.sleep(0.05)
    assert opened == ["alsa_input.usb-cheap-mic"] and emitted == ["mic.clapped"]
    e.speaking = True                                  # Pip talking: his own sound reaches that mic too
    Claps.fed = 0
    await e.on_clap_mic(type("M", (), {"data": {"device": "other"}})())
    await asyncio.sleep(0.05)
    assert Claps.fed == 0
    e.clap_task.cancel()
