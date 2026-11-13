"""After the owner's test (2026-11-07): being told to be quiet (D-105), the chime switch, knowing when
someone's done talking (D-106), timer names, approving your own memory by voice (D-107), the clap microphone."""
import asyncio
import time

import numpy as np
import pytest
from helpers import OneVoice as FakeEmbedder
from helpers import finish, room, says
from helpers import room_setup as setup

from pip_assistant.core import actions
from pip_assistant.core.commands import timer_name
from pip_assistant.core.memory import Memory
from pip_assistant.core.people import Match, People
from pip_assistant.core.prefs import Prefs


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
    await ears.emit("heard.utterance", {"reason": "follow_up", "had_speech": True, "seconds": 3.0, "quiet_s": 0.0},
                    private=room()[1])
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
    assert c.turn_wait("what time is it?") == 1.0
    assert c.turn_wait("set a timer for ten minutes") == 1.0                       # short request
    assert c.turn_wait("I think we should probably get the pizza from the other place tonight") == 1.6
    assert c.turn_wait("We went to the lake. It was freezing.") == 2.0
    assert c.turn_wait("what's the weather like in the") == 3.0                    # sounds unfinished
    assert c.turn_wait("Pip, remind me to") == 3.0
    assert c.turn_wait("set a timer for") == 3.0
    assert c.turn_wait("what's that for?") == 1.0                                  # a question mark wins
    c.last_cut = time.monotonic()
    assert c.turn_wait("what time is it?") == 1.5                                  # just talked over: takes the hint


@pytest.mark.real_turn_waits
async def test_the_wait_counts_from_when_they_stopped_talking(tmp_path):
    """D-122: the pause ears already waited (quiet_s) and speech-to-text count towards the wait, so a short
    question 0.9 s after they stopped is answered 0.1 s later, not 1.0 s later."""
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "Pip, what time is it?", social={"join_in": False})
    data, private = room()
    await ears.emit("heard.utterance", {**data, "quiet_s": 0.9}, private=private)
    await asyncio.sleep(0.45)
    assert len(asks) == 1                                                       # well inside the 1.0 s budget
    [d] = [r["data"] for r in conv.bus.trace.recent() if r["type"] == "turn.decision"]
    assert d["decision"] == "answered" and d["why"] == "short" and 0.9 <= d["since_speech_s"] < 1.5
    await finish(bus, tasks)


@pytest.mark.real_turn_waits
async def test_the_after_interrupt_wait_is_for_the_next_turn_only(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "Pip, what time is it?", social={"join_in": False})
    await ears.emit("speech.interrupted", {"level_dbfs": -20})
    await asyncio.sleep(0.05)
    assert conv.turn_wait("what time is it?") == 1.5
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(1.7)
    assert len(asks) == 1 and conv.turn_wait("what time is it?") == 1.0         # used up by that turn
    await finish(bus, tasks)


def test_backchannels_are_only_listening():
    from pip_assistant.core.turns import is_backchannel
    assert all(is_backchannel(t) for t in ("Yeah.", "mhm", "Uh-huh.", "okay", "haha", "Oh, okay."))
    assert not any(is_backchannel(t) for t in ("Yeah, but what about tomorrow?", "No, fifteen.", "stop", "wait"))


def test_laughing_over_pip_is_only_listening():
    from pip_assistant.core.turns import listening_only
    for t in ("Hahaha!", "Ha ha ha.", "Ha, ha.", "hehe", "lol", "(laughs)", "[Laughter]", "*chuckles*", "Thank you.", "Ha-ha"):
        assert listening_only(t) == "laugh", t
    assert listening_only("Haha, yeah.") == "backchannel" and listening_only("(laughs) okay") == "backchannel"
    for t in ("Haha, wait, what time?", "He said no.", "Hi Pip", "Thank you, but stop.", "ha ha no stop"):
        assert listening_only(t) == "", t


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
    """Memories still waiting in the inbox (from before D-119, or the Ziggletron import) can be approved by voice."""
    people = People(tmp_path / "pip.db", embedder=FakeEmbedder())
    landen, sam = people.add_person("Landen", "admin"), people.add_person("Sam")
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
    raw = ('ACTION {"do": "remember", "text": "Sam is allergic to peanuts", "category": "personal", '
           '"about": ["Sam"], "sensitive": true}')
    mem.add("Sam is allergic to peanuts", "personal", [sam["id"]], sensitive=True, pending_because="from Ziggletron")
    [row] = mem.items("pending")
    # Someone else saying it again: it's waiting, not "already known"
    assert actions.apply(Cmds, raw, ctx(sure(landen)))[0] == "That's waiting in the memory inbox."
    # Claude sees it only when it's Sam speaking, and only Sam may approve it
    assert "allergic" in mem.for_claude(sam["id"], "sure") and "allergic" not in mem.for_claude(landen["id"], "sure")
    ref = mem.short(row["id"])
    approve = f'ACTION {{"do": "approve_memory", "id": "{ref}"}}'
    assert actions.apply(Cmds, approve, ctx(sure(landen))) == (actions.FAILED, 0)
    assert actions.apply(Cmds, approve, ctx(Match(sam["id"], "Sam", "probably", 0.6))) == (actions.FAILED, 0)
    assert actions.apply(Cmds, approve, ctx(sure(sam)))[0] == "Saved: Sam is allergic to peanuts."
    assert mem.items("pending") == [] and mem.items("active")[0]["text"] == "Sam is allergic to peanuts"


# ---------------------------------------------------------------- D-91: claps on their own microphone
async def test_the_clap_microphone_is_chosen_on_the_dashboard(tmp_path):
    prefs = Prefs(tmp_path / "pip.db")
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, prefs=prefs)
    assert ("ears.clap_mic", None, {"device": "", "on": False}) in got     # none yet: told when ears connects
    p = await conv.prefs_snapshot()
    assert [m["label"] for m in p["mics"]] == ["USB Speaker Phone", "Cheap USB mic"] and p["clap_mic"] == ""
    assert "error" in await conv.set_pref("clap_mic", "alsa_input.usb-Speaker_Phone")   # can't hear claps
    await conv.set_pref("clap_mic", "alsa_input.usb-cheap-mic")
    await asyncio.sleep(0.05)
    assert ("ears.clap_mic", None, {"device": "alsa_input.usb-cheap-mic", "on": False}) in got   # chosen, claps still off
    await conv.set_pref("claps", True)
    await asyncio.sleep(0.05)
    assert ("ears.clap_mic", None, {"device": "alsa_input.usb-cheap-mic", "on": True}) in got
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
        last = {}

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
    e.mic_mode, e.speaking, e.ring_task, e.claps_live = "claps_only", False, None, False
    await e.on_clap_mic(type("M", (), {"data": {"device": "alsa_input.usb-cheap-mic", "on": False}})())
    await asyncio.sleep(0.05)
    assert opened == ["alsa_input.usb-cheap-mic"] and emitted == ["mic.clapped_trial"]   # off: only a trial
    e.clap_task.cancel()
    e.clap_task, e.clap_mic, Claps.fed, opened[:] = None, "", 0, []
    await e.on_clap_mic(type("M", (), {"data": {"device": "alsa_input.usb-cheap-mic", "on": True}})())
    await asyncio.sleep(0.05)
    assert opened == ["alsa_input.usb-cheap-mic"] and emitted[-1] == "mic.clapped"
    e.speaking = True                                  # Pip talking: his own sound reaches that mic too
    Claps.fed = 0
    await e.on_clap_mic(type("M", (), {"data": {"device": "other", "on": True}})())
    await asyncio.sleep(0.05)
    assert Claps.fed == 0
    e.clap_task.cancel()


def test_what_the_owner_saw_one_memory_not_two(tmp_path):
    """Test 8: Landen's wording "Landen says Sam is…" (no 'about'), then Sam saying it himself."""
    people = People(tmp_path / "pip.db", embedder=FakeEmbedder())
    landen, sam = people.add_person("Landen", "admin"), people.add_person("Sam")
    mem = Memory(tmp_path / "pip.db")

    class Cmds:
        class s:
            find = staticmethod(lambda ref: None)
        now = staticmethod(__import__("datetime").datetime.now)
    sure = lambda p: Match(p["id"], p["name"], "sure", 0.9)
    ctx = lambda sp: {"memory": mem, "people": people, "speaker": sp}
    mem.add("Landen says Sam is allergic to peanuts", "household", sensitive=True, pending_because="from Ziggletron")
    assert len(mem.items("pending")) == 1   # (waiting from before D-119)
    assert "allergic" in mem.for_claude(sam["id"], "sure", speaker_name="Sam")      # Sam is asked about it
    said, _ = actions.apply(Cmds, 'ACTION {"do": "remember", "text": "Sam is allergic to peanuts", "category": "personal", '
                                  '"about": ["Sam"], "sensitive": true}', ctx(sure(sam)))
    assert said == "Saved: Sam is allergic to peanuts."
    assert mem.items("pending") == [] and [r["text"] for r in mem.items("active")] == ["Sam is allergic to peanuts"]


def test_every_memory_is_saved_straight_away(tmp_path):
    """D-119 (owner, 2026-11-10): about yourself, someone else, or from a voice Pip isn't sure of: all saved."""
    people = People(tmp_path / "pip.db", embedder=FakeEmbedder())
    landen, sam = people.add_person("Landen", "admin"), people.add_person("Sam")
    mem = Memory(tmp_path / "pip.db")

    class Cmds:
        class s:
            find = staticmethod(lambda ref: None)
        now = staticmethod(__import__("datetime").datetime.now)
    ctx = lambda sp: {"memory": mem, "people": people, "speaker": sp}
    own = 'ACTION {"do": "remember", "text": "Landen has asthma", "category": "personal", "about": ["Landen"], "sensitive": true}'
    assert actions.apply(Cmds, own, ctx(Match(landen["id"], "Landen", "sure", 0.9)))[0] == "Saved: Landen has asthma."
    other = 'ACTION {"do": "remember", "text": "Sam has asthma", "category": "personal", "about": ["Sam"], "sensitive": true}'
    assert actions.apply(Cmds, other, ctx(Match(landen["id"], "Landen", "sure", 0.9)))[0] == "Saved: Sam has asthma."
    unsure = 'ACTION {"do": "remember", "text": "Landen hates hospitals", "category": "personal", "about": ["Landen"], "sensitive": true}'
    assert actions.apply(Cmds, unsure, ctx(Match(landen["id"], "Landen", "probably", 0.6)))[0] == "Saved: Landen hates hospitals."


# ---------------------------------------------------------------- D-108: the conversation feed and the state colours
async def test_the_feed_shows_everything_heard_and_what_happened(tmp_path):
    import json

    from pip_assistant.common.client import BusClient
    prefs = Prefs(tmp_path / "pip.db")
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "did you see the game", social={"join_in": False},
                                                    prefs=prefs, replies={"direct": ""})
    web = BusClient("web", socket_path=tmp_path / "core.sock")
    feed = []

    async def on_feed(msg):
        feed.append((msg.type, msg.data, msg.private))
    web.subscribe("conversation.*", on_feed)
    tasks.append(asyncio.create_task(web.run()))
    await asyncio.wait_for(web.connected.wait(), 3)
    await asyncio.sleep(0.1)
    await ears.emit("heard.utterance", *room())                 # not for Pip: grey
    await asyncio.sleep(0.2)
    conv.stt.text = "Pip, shut up."
    await ears.emit("heard.utterance", *room())                 # for Pip: green, chime, then he stays quiet
    await asyncio.sleep(0.4)
    lines = [(d["how"], p["text"]) for t, d, p in feed if t == "conversation.line"]
    assert lines == [("room", "did you see the game"), ("direct", "Pip, shut up.")]
    events = [d["kind"] for t, d, p in feed if t == "conversation.event"]
    assert events == ["chime", "passed"]
    await conv.set_mic(False)
    conv.mic_off_since -= 12                                    # as if 12 s went by
    await conv.set_mic(True)
    await asyncio.sleep(0.1)
    ev = [d for t, d, p in feed if t == "conversation.event"][-2:]
    assert ev[0] == {"kind": "mic_off", "how": "dashboard"} and ev[1]["kind"] == "mic_on" and ev[1]["off_for"] == 12
    trace = json.dumps(bus.trace.recent())
    assert "the game" not in trace and "shut up" not in trace   # the words are shown, never logged
    await finish(bus, tasks)


async def test_being_talked_over_shows_as_interrupted(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path)
    await ears.emit("speech.interrupted", {"level_dbfs": -20})
    await asyncio.sleep(0.1)
    assert conv.state == "interrupted"
    await asyncio.sleep(1.6)
    assert conv.state == "idle"
    await finish(bus, tasks)


async def test_a_clap_switch_off_is_cleared_when_claps_cant_switch_him_back(tmp_path):
    """Clapped off, then the clap switch is unticked: he'd be stuck deaf, so listening comes back on."""
    prefs = Prefs(tmp_path / "pip.db")
    prefs.set("clap_mic", "alsa_input.usb-cheap-mic")
    prefs.set("claps", True)
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, prefs=prefs)
    await ears.emit("mic.clapped", {})
    await asyncio.sleep(0.1)
    assert not conv.mic.listening
    await conv.set_pref("claps", False)
    await asyncio.sleep(0.05)
    assert conv.mic.listening and ("ears.mic", None, {"mode": "on"}) in got
    await finish(bus, tasks)


# ---------------------------------------------------------------- CB-020: a "yeah" over Pip isn't "stop"
def barge_in():
    return {"reason": "barge_in", "had_speech": True, "seconds": 0.6, "quiet_s": 0.0}, room()[1]


async def talked_over_mid_answer(tmp_path, heard, monkeypatch=None):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, heard, social={"join_in": False})
    conv.spoken, conv.say_reason, conv.voice_kind = ["The bus comes at ten.", "Then another at half past."], "answer", "direct"
    conv.transcript.add("Pip", "The bus comes at ten. Then another at half past.", ours=True)
    await ears.emit("speech.interrupted", {"level_dbfs": -25, "said": 1})      # cut off in the second sentence
    await asyncio.sleep(0.05)
    got.clear()
    return bus, conv, ears, got, asks, tasks


async def test_a_yeah_over_pip_and_he_carries_on(tmp_path):
    bus, conv, ears, got, asks, tasks = await talked_over_mid_answer(tmp_path, "Yeah.")
    assert conv.paused and conv.paused["left"] == ["Then another at half past."]
    await ears.emit("heard.utterance", *barge_in())
    await asyncio.sleep(0.2)
    assert says(got) == ["Then another at half past."] and not asks and conv.paused is None
    assert not conv.transcript.last_ours()["cut"]
    [d] = [r["data"] for r in bus.trace.recent() if r["type"] == "turn.decision"]
    assert d == {"decision": "resumed_after_backchannel", "why": "backchannel"}
    await finish(bus, tasks)


async def test_a_laugh_over_pip_and_he_carries_on(tmp_path):
    # Whisper writes a laugh as "Ha ha ha." or its phantom "Thank you." (owner, 2026-11-12: cut off by laughter)
    for i, heard in enumerate(("Ha ha ha.", "Thank you.", "(laughing)")):
        (tmp_path / str(i)).mkdir()
        bus, conv, ears, got, asks, tasks = await talked_over_mid_answer(tmp_path / str(i), heard)
        await ears.emit("heard.utterance", *barge_in())
        await asyncio.sleep(0.2)
        assert says(got) == ["Then another at half past."] and not asks and conv.paused is None, heard
        [d] = [r["data"] for r in bus.trace.recent() if r["type"] == "turn.decision"]
        assert d == {"decision": "resumed_after_backchannel", "why": "laugh"}
        await finish(bus, tasks)


async def test_a_long_laugh_still_carries_on(tmp_path, monkeypatch):
    from pip_assistant.core import conversation
    monkeypatch.setattr(conversation, "RESUME_WITHIN_S", 0.1)
    bus, conv, ears, got, asks, tasks = await talked_over_mid_answer(tmp_path, "Hahahaha.")
    conv.max_utterance = 0.4
    await asyncio.sleep(0.3)                     # laughing longer than RESUME_WITHIN_S: still paused, not dropped
    assert conv.paused
    await ears.emit("heard.utterance", *barge_in())
    await asyncio.sleep(0.15)
    assert says(got) == ["Then another at half past."] and not asks
    await finish(bus, tasks)


async def test_real_words_over_pip_take_the_turn(tmp_path):
    bus, conv, ears, got, asks, tasks = await talked_over_mid_answer(tmp_path, "No, I meant the one tomorrow.")
    await ears.emit("heard.utterance", *barge_in())
    await asyncio.sleep(0.3)
    assert "Then another at half past." not in says(got) and len(asks) == 1 and conv.paused is None
    assert asks[0][1]["text"] == "No, I meant the one tomorrow."
    await finish(bus, tasks)


async def test_talked_over_by_nothing_he_carries_on_and_a_long_talk_cancels(tmp_path, monkeypatch):
    bus, conv, ears, got, asks, tasks = await talked_over_mid_answer(tmp_path, "")
    await ears.emit("heard.utterance", {"reason": "barge_in", "had_speech": False, "seconds": 0.4}, private={})
    await asyncio.sleep(0.1)
    assert says(got) == ["Then another at half past."]
    from pip_assistant.core import conversation
    monkeypatch.setattr(conversation, "RESUME_WITHIN_S", 0.2)
    conv.max_utterance = 0.0
    got.clear()
    conv.say_reason = "answer"
    await ears.emit("speech.interrupted", {"level_dbfs": -25, "said": 0})
    await asyncio.sleep(0.4)                                    # nothing ever came back from speech-to-text: given up
    assert conv.paused is None and says(got) == [] and conv.transcript.last_ours()["cut"]
    await finish(bus, tasks)
