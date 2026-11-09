"""What exactly Pip is doing (D-112): the detail within each state, its words and countdown, and the
Conversation's state bars (only when switched on)."""
import asyncio
import time

from pip_assistant.core.states import DETAILS
from pip_assistant.core.prefs import Prefs
from test_step6 import finish, room, setup


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
                                                           "words": DETAILS[conv.detail], "ends_in": None}]
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
