"""Fixes from the 2026-11-09 code review of the conversation: switched off means nothing more is answered,
a held turn isn't stuck for 16 s, actions are done as the person who asked, only real speech opens the
follow-up window, and voice learning stops when listening is switched off."""
import asyncio
import time

import pytest

from pip_assistant.common.messages import Message
from test_step6 import finish, setup


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


async def test_switched_off_while_transcribing_is_not_answered(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path)
    conv.stt = SlowSTT("Pip, what's the weather tomorrow?")
    await ears.emit("heard.utterance", *utt())
    await asyncio.sleep(0.1)
    assert conv.transcribing
    await conv.set_mic(False)                        # switched off on the dashboard mid-way (D-91)
    conv.stt.release.set()
    await asyncio.sleep(2.5)
    assert asks == [] and not conv.pending
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
