"""Listening like Ziggletron (D-92) and the Listening switches (D-91, core/conversation.py, micctl.py): everything
said goes in the transcript, mentions let Claude decide, joining in, the mic switches and claps, offline; and the
fixes from the 2026-11-09 review (what was heard before switching off, a held turn, who asked)."""
import asyncio
import json
import time

import pytest
from helpers import finish, room, says
from helpers import room_setup as setup

from pip_assistant.common.messages import Message
from pip_assistant.core.bus import Bus
from pip_assistant.core.health import Health
from pip_assistant.core.mic import MicSwitch
from pip_assistant.core.trace import Trace

# ---------------------------------------------------------------- the Listening switches and claps


def test_mic_switches_are_separate(tmp_path):
    m = MicSwitch(tmp_path / "pip.db")
    assert m.listening and m.mode == "on"
    assert m.clapped() and m.mode == "claps_only" and not m.listening
    assert m.clapped() and m.listening
    m.set_dashboard(False)
    assert m.mode == "off" and not m.clapped() and not m.listening     # claps can't undo the dashboard
    assert MicSwitch(tmp_path / "pip.db").mode == "off"                 # kept across restarts


async def test_ears_is_told_the_mic_mode_when_it_connects(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path)
    assert ("ears.mic", None, {"mode": "on"}) in got
    await finish(bus, tasks)


async def test_two_claps_switch_listening_off_and_on_but_not_past_the_dashboard(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "did you see the game")
    await ears.emit("mic.clapped", {})
    await asyncio.sleep(0.1)
    assert got[-2:] == [("ears.mic", None, {"mode": "claps_only"}), ("ears.tone", None, {"name": "mic_off"})]
    assert conv.state == "mic_off"
    await ears.emit("heard.utterance", *room())               # heard just before: still used (D-114); pip-ears
    await asyncio.sleep(0.1)                                   # captures nothing new once it's off
    assert conv.stt.calls == 1 and conv.state == "mic_off"
    await ears.emit("mic.clapped", {})
    await asyncio.sleep(0.1)
    assert got[-2:] == [("ears.mic", None, {"mode": "on"}), ("ears.tone", None, {"name": "mic_on"})]
    await conv.set_mic(False)                                  # the dashboard switch
    await asyncio.sleep(0.05)
    assert got[-2:] == [("ears.mic", None, {"mode": "off"}), ("ears.tone", None, {"name": "mic_off"})]
    n = len(got)
    await ears.emit("mic.clapped", {})                         # clapping can't turn him back on
    await asyncio.sleep(0.1)
    assert len(got) == n and not conv.mic.listening
    await conv.set_mic(True)
    assert conv.mic.listening
    await finish(bus, tasks)

# ---------------------------------------------------------------- everything heard, mentions, joining in


async def test_everything_said_goes_in_the_transcript_and_claude_sees_it(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "did you see the game last night", social={"join_in": False})
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.2)
    assert says(got) == [] and not asks                        # not for Pip: just heard
    conv.stt.text = "Pip, who won?"
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.3)
    assert ("ears.chime", None, {}) in got and says(got) == ["Sure."]
    kind, private = asks[0]
    assert kind == "direct" and [x.split("] ", 1)[1] for x in private["transcript"]] == \
        ["Someone: did you see the game last night", "Someone: Pip, who won?"]
    assert conv.transcript.last()["ours"] and conv.transcript.last()["text"] == "Sure."
    trace = json.dumps(bus.trace.recent())
    assert "game" not in trace and "who won" not in trace     # never logged (D-34)
    await finish(bus, tasks)


async def test_a_mention_lets_claude_decide_and_pass_means_quiet(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "Pip's been glitchy today", social={"join_in": False})
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.3)
    assert [k for k, _ in asks] == ["mentioned"] and says(got) == [] and ("ears.chime", None, {}) not in got
    assert "answer.passed" in [r["type"] for r in bus.trace.recent()]
    await finish(bus, tasks)


async def test_pip_joins_in_after_a_pause(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "I wonder how far away the moon is",
                                                    replies={"join": "About 239,000 miles."})
    conv.joining.last_check = -1e9
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.5)
    assert [k for k, _ in asks] == ["join"] and says(got) == ["About 239,000 miles."]
    assert "SAY:" not in json.dumps(asks)                      # the instructions are added by pip-thinker
    # Not again straight away: at most every 40 s in the normal mood
    conv.stt.text = "huh, that's further than I thought"
    await ears.emit("speech.finished", {"interrupted": False})
    await asyncio.sleep(0.4)                                   # (the follow-up window closes)
    conv.expect_until = 0
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.4)
    assert [k for k, _ in asks] == ["join"]
    await finish(bus, tasks)


async def test_no_join_in_if_someone_starts_talking_or_the_conversation_moves_on(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "I wonder how far away the moon is",
                                                    replies={"join": "About 239,000 miles."})
    conv.joining.last_check = -1e9
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.02)
    await ears.emit("heard.started", {"reason": "room"})       # someone carries on before the pause
    await asyncio.sleep(0.3)
    assert asks == [] and says(got) == []
    # Claude decided to say something, but meanwhile someone else spoke: dropped
    conv.transcript.add("Someone", "never mind, I'll look it up")
    conv.join_count = conv.transcript.count - 1
    conv.answer_id, conv.answer_kind, conv.join_spoke = "j1", "join", False
    from pip_assistant.common.messages import Message
    await conv.on_sentence(Message("event", "answer.sentence", data={"answer_id": "j1"}, private={"text": "Hi."}))
    await asyncio.sleep(0.05)
    assert says(got) == [] and conv.answer_id is None
    await finish(bus, tasks)


async def test_offline_indicator(tmp_path):
    from pip_assistant.core.net import Net
    bus = Bus(tmp_path / "core.sock", Trace(tmp_path / "pip.db"), Health([], restart=False))
    net = Net(bus)
    await net.failed()
    assert not net.online and "net.changed" in [r["type"] for r in bus.trace.recent()]
    await net.set(True)
    assert net.online and sorted(r["data"]["online"] for r in bus.trace.recent() if r["type"] == "net.changed") == [False, True]
    bus.trace.flush()

# ---------------------------------------------------------------- fixes from the 2026-11-09 review


def utt(reason="wake"):
    import base64
    return {"reason": reason, "had_speech": True, "seconds": 1.0}, {"audio_b64": base64.b64encode(b"x" * 32000).decode()}


class SlowSTT:
    """Speech-to-text that waits until released, and then gives each text in turn."""
    def __init__(self, *texts):
        self.texts, self.names, self.release = list(texts), [], asyncio.Event()

    async def transcribe(self, pcm):
        await self.release.wait()
        return self.texts.pop(0) if self.texts else ""


async def test_what_was_heard_before_switching_off_is_still_answered(tmp_path):
    """Owner, D-114: switching listening off only stops him hearing anything new."""
    bus, conv, ears, got, asks, tasks = await setup(tmp_path)
    conv.stt = SlowSTT("Pip, what's the weather tomorrow?")
    await ears.emit("heard.utterance", *utt())
    await asyncio.sleep(0.1)
    assert conv.transcribing
    await conv.set_mic(False)                        # switched off on the dashboard mid-way
    conv.stt.release.set()
    await asyncio.sleep(0.5)
    assert len(asks) == 1 and conv.state == "mic_off"
    await finish(bus, tasks)


@pytest.mark.real_turn_waits
async def test_a_held_turn_is_answered_promptly_when_more_sound_was_nothing(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path)
    conv.stt = SlowSTT("Pip, what time is it?", "")
    conv.stt.release.set()
    await ears.emit("heard.utterance", *utt())
    await asyncio.sleep(0.1)
    assert conv.pending
    await ears.emit("heard.started", {"reason": "follow_up"})      # they seemed to go on (a breath, an "um")…
    await ears.emit("heard.utterance", *utt("follow_up"))           # …which transcribed to nothing
    t0 = time.monotonic()
    while not asks and time.monotonic() - t0 < 3:
        await asyncio.sleep(0.05)
    assert asks and time.monotonic() - t0 < 2.5                     # not after 16 s
    await finish(bus, tasks)


async def test_actions_are_done_as_the_person_who_asked(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path)
    landen, sam = object(), object()
    done_as = []

    async def slow_claude(target, msg_type, data, private, timeout):
        await asyncio.sleep(0.2)
        return Message("reply", "thinker.ask", data={}, private={"text": "", "action": 'ACTION {"do": "x"}'})

    async def record(raw, t0, again=None, asker=None):
        done_as.append(asker[0])

    conv.bus_request, conv.claude_action, conv.commands = slow_claude, record, object()
    conv.speaker = landen
    task = asyncio.create_task(conv.answer("what's on my calendar Friday?", "wake"))
    await asyncio.sleep(0.05)
    conv.speaker = sam                     # someone else talks while Claude works
    await task
    assert done_as == [landen]
    await finish(bus, tasks)


async def test_only_real_speech_opens_the_follow_up_window(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path)
    conv.said("Want me to set a timer?")                             # Pip's last line was a question
    await ears.emit("speech.finished", {"interrupted": False, "spoken": False})   # just a chime or tone
    await asyncio.sleep(0.1)
    assert not [d for k, _, d in got if k == "ears.listen"]
    await ears.emit("speech.started", {})
    await ears.emit("speech.finished", {"interrupted": False, "spoken": True})
    await asyncio.sleep(0.1)
    assert [d for k, _, d in got if k == "ears.listen"]
    await finish(bus, tasks)


async def test_switching_listening_off_stops_voice_learning(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path)
    conv.enrolling = {"pid": "p1", "name": "Sam", "got": [], "extra": [], "misses": 0, "until": time.monotonic() + 60}
    await conv.set_mic(False)
    assert conv.enrolling is None
    await finish(bus, tasks)
