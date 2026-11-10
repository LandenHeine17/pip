"""What exactly Pip is doing (D-112): the detail within each state, its words and countdown, and the
Conversation's state bars (only when switched on)."""
import asyncio
import time
from datetime import datetime

import numpy as np
from helpers import FakeSTT, clock_at, finish, room, states, utt
from helpers import room_setup as setup
from helpers import state_bus as setup_bus

from pip_assistant.core.prefs import Prefs
from pip_assistant.core.states import DETAILS


def watch(conv):
    """Every pip.state and conversation.event pip-core publishes."""
    seen = []
    publish = conv.bus.publish

    async def recording(msg):
        if msg.type in ("pip.state", "conversation.event"):
            seen.append((msg.type, dict(msg.data), dict(msg.private or {})))
        return await publish(msg)
    conv.bus.publish = recording
    return seen


async def test_each_situation_has_its_detail(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path)
    now = time.monotonic()
    assert conv.situation() == ("idle", "quiet", 0.0)
    conv.room_until = now + 3
    assert conv.situation()[:2] == ("idle", "following")
    conv.answer_id, conv.answer_kind = "a1", "mentioned"
    assert conv.situation()[:2] == ("idle", "mentioned")
    conv.answer_kind = "join"
    assert conv.situation()[:2] == ("idle", "joining")
    conv.answer_kind, conv.filler_said = "direct", None
    assert conv.situation()[:2] == ("thinking", "asking")
    conv.looking_up = True
    assert conv.situation()[:2] == ("thinking", "looking_up")
    conv.filler_said = "searching"
    assert conv.situation()[:2] == ("thinking", "searching")
    conv.answer_id = None
    conv.say_pending_until = now + 3
    assert conv.situation()[:2] == ("thinking", "voice")
    conv.say_pending_until = 0
    conv.transcribing = True
    assert conv.situation()[:2] == ("listening", "hearing")
    conv.transcribing, conv.pending, conv.turn_until = False, "so I was", now + 2
    assert conv.situation() == ("listening", "turn", conv.turn_until)
    conv.pending = ""
    conv.listening_for(5, "follow_up")
    state, detail, ends = conv.situation()
    assert (state, detail) == ("listening", "follow_up") and ends > now + 4
    conv.speaking, conv.say_reason, conv.voice_kind = True, "answer", "join"
    assert conv.situation()[:2] == ("speaking", "joining_in")
    conv.say_reason = "welcome"
    assert conv.situation()[:2] == ("speaking", "welcome")
    conv.say_reason = "not caught"
    assert conv.situation()[:2] == ("speaking", "trouble")
    conv.speaking = False
    conv.cut_until = time.monotonic() + 1
    assert conv.situation()[:2] == ("interrupted", "cut_in")
    await finish(bus, tasks)


async def test_a_request_goes_through_its_situations_and_only_words_about_pip_travel(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "Pip, what time is it?", social={"join_in": False})
    seen = watch(conv)
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.5)
    states = [(d["state"], d["detail"]) for t, d, _ in seen if t == "pip.state"]
    assert ("listening", "turn") in states and ("thinking", "asking") in states
    assert states.index(("listening", "turn")) < states.index(("thinking", "asking"))
    for t, d, private in seen:
        assert "time is it" not in str(d) and not private          # never what anyone said
        if t == "pip.state":
            assert d["words"] and d["words"] == DETAILS[d["detail"]]   # never blank
    assert not [d for t, d, _ in seen if t == "conversation.event" and d["kind"] == "state"]   # bars are off
    await finish(bus, tasks)


async def test_room_talk_is_followed_and_the_bars_show_when_switched_on(tmp_path):
    prefs = Prefs(tmp_path / "pip.db")
    assert prefs.get("state_bars") is False
    prefs.set("state_bars", True)
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "did you see the game last night", social={"join_in": False},
                                                    prefs=prefs)
    seen = watch(conv)
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.3)
    assert conv.detail == "following"
    bars = [d for t, d, _ in seen if t == "conversation.event" and d["kind"] == "state"]
    assert bars and bars[-1]["detail"] == "following" and bars[-1]["words"] == "Following the conversation"
    await finish(bus, tasks)


async def test_listening_switched_off_says_how_to_turn_it_back_on_and_a_new_dashboard_is_told(tmp_path):
    from types import SimpleNamespace
    bus, conv, ears, got, asks, tasks = await setup(tmp_path)
    conv.mic = SimpleNamespace(listening=False, dashboard=True)       # switched off by two claps
    assert conv.situation()[:2] == ("mic_off", "claps_off")
    conv.mic.dashboard = False                                        # switched off on the dashboard
    assert conv.situation()[:2] == ("mic_off", "switched_off")
    conv.mic = None
    seen = watch(conv)
    await conv.on_program_subscribed("web")                          # the dashboard restarted: it's told
    assert [d for t, d, _ in seen if t == "pip.state"] == [{"state": conv.state, "detail": conv.detail,
                                                           "words": DETAILS[conv.detail], "ends_in": None, "quiet": True}]
    await finish(bus, tasks)


async def test_a_dashboard_that_connects_is_sent_the_state_once_it_is_listening(tmp_path):
    """Over a real connection: the state is sent after the dashboard has subscribed, not before (it'd be missed)."""
    from pip_assistant.common.client import BusClient
    bus, conv, ears, got, asks, tasks = await setup(tmp_path)
    web, seen = BusClient("web", socket_path=tmp_path / "core.sock"), []

    async def on_state(msg):
        seen.append(msg.data)
    web.subscribe("pip.state", on_state)
    task = asyncio.create_task(web.run())
    await asyncio.wait_for(web.connected.wait(), 3)
    await asyncio.sleep(0.3)
    assert seen and seen[-1]["words"] == DETAILS[conv.detail]
    await finish(bus, tasks + [task])


def test_a_quiet_moment_means_nothing_would_be_cut_off():
    """One rule for settings restarts and installs (core/states.py quiet())."""
    from types import SimpleNamespace

    from pip_assistant.core.states import quiet
    calm = dict(state="idle", answer_id=None, pending="", transcribing=False, ringing=False, speaking=False)
    assert quiet(SimpleNamespace(**calm))
    assert quiet(SimpleNamespace(**{**calm, "state": "offline"}))
    assert not quiet(SimpleNamespace(**{**calm, "state": "interrupted"}))
    assert not quiet(SimpleNamespace(**{**calm, "state": "mic_off", "answer_id": "a1"}))   # still answering (D-114)
    assert not quiet(SimpleNamespace(**{**calm, "state": "listening", "pending": "what time"}))


NOW = datetime(2026, 11, 4, 23, 55)  # the thinking-tone tests' moment: a Wednesday, just before midnight
Clock = clock_at(NOW)


# ---------------------------------------------------------------- the thinking tone and Pip's state (D-86), with the fake programs of 2026-11-05


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
