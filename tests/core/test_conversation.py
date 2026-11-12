"""Listening like Ziggletron (D-92) and the Listening switches (D-91, core/conversation.py, micctl.py): everything
said goes in the transcript, mentions let Claude decide, joining in, the mic switches and claps, offline; and the
fixes from the 2026-11-09 review (what was heard before switching off, a held turn, who asked)."""
import asyncio
import json
import time

import pytest
from helpers import FakeSTT, finish, room, says
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


# ---------------------------------------------------------------- nothing waits behind speech-to-text (D-122)
class DelayedSTT(FakeSTT):
    def __init__(self, text, seconds):
        super().__init__(text)
        self.seconds = seconds

    async def transcribe(self, pcm):
        self.calls += 1
        await asyncio.sleep(self.seconds)
        return self.text


async def test_urgent_messages_do_not_wait_behind_speech_to_text(tmp_path):
    """Speech-to-text takes ~0.5 s (2 s here). It used to hold up every message from pip-ears after it: his name, a
    talk-over and "someone started talking" now arrive at once, and what was heard is still handled in order."""
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, social={"join_in": False})
    conv.stt = DelayedSTT("did you see the game", 2.0)
    seen = []
    orig_wake, orig_cut = conv.on_wake, conv.on_interrupted

    async def wake(msg):
        seen.append(("wake", time.monotonic()))
        await orig_wake(msg)

    async def cut(msg):
        seen.append(("cut", time.monotonic()))
        await orig_cut(msg)
    bus.core_listeners = [(p, {conv.on_wake: wake, conv.on_interrupted: cut}.get(h, h)) for p, h in bus.core_listeners]
    t0 = time.monotonic()
    await ears.emit("heard.utterance", *room())
    await ears.emit("heard.utterance", *room())                   # two in a row: both transcribed, in order
    await ears.emit("speech.interrupted", {"level_dbfs": -20})
    await ears.emit("heard.wake", {"word": "pippletron", "score": 0.9})
    await asyncio.sleep(0.3)
    assert [w for w, _ in seen] == ["cut", "wake"] and all(at - t0 < 0.3 for _, at in seen)
    assert conv.stt.calls == 1                                    # the first is still being transcribed
    await asyncio.sleep(4.2)
    await conv.drain()
    assert conv.stt.calls == 2 and len(conv.transcript.lines) == 2
    await finish(bus, tasks)


async def test_too_much_waiting_skips_the_oldest_room_talk(tmp_path):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, social={"join_in": False})
    conv.stt = DelayedSTT("did you see the game", 0.5)
    for _ in range(conv.HEARD_MAX + 2):
        await conv.queue_utterance(Message("event", "heard.utterance", source="ears", data=room()[0], private=room()[1]))
    assert len(conv.heard) == conv.HEARD_MAX
    skipped = [r for r in bus.trace.recent() if r["type"] == "heard.skipped"]
    assert len(skipped) == 2
    await finish(bus, tasks)


@pytest.mark.real_turn_waits
async def test_talking_again_during_speech_to_text_keeps_the_turn_open(tmp_path):
    """'someone started talking' now arrives while the first part is still with speech-to-text: he must still
    wait for the rest instead of answering half a sentence."""
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, social={"join_in": False})
    conv.stt = DelayedSTT("Pip, so tomorrow", 0.5)
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.1)
    await ears.emit("heard.started", {"reason": "room"})             # they carry on
    await asyncio.sleep(2.0)
    assert not asks                                                 # still holding for the rest
    conv.stt = DelayedSTT("what's the weather?", 0.1)
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(1.5)
    assert len(asks) == 1 and asks[0][1]["text"] == "Pip, so tomorrow what's the weather?"
    await finish(bus, tasks)


# ---------------------------------------------------------------- D-124: follow-ups are for the person he answered
def test_who_a_follow_up_is_for(tmp_path):
    from pip_assistant.core.conversation import Conversation
    from pip_assistant.core.people import Match
    c = Conversation(Bus(tmp_path / "s", Trace(tmp_path / "pip.db"), Health([], restart=False)), {"conversation": {}})
    alex, sam = Match("a", "Alex", "sure", 0.8), Match("s", "Sam", "sure", 0.8)
    c.follow = {"asker": alex, "question": False, "until": time.monotonic() + 5}
    assert c.in_follow_up()
    assert c.follow_up_for(Match("a", "Alex", "probably", 0.4)) == "direct"     # the same voice
    assert c.follow_up_for(sam) == "room"                                       # clearly someone else
    assert c.follow_up_for(Match()) == "follow_up"                              # can't tell: Claude decides
    c.follow["asker"] = None
    assert c.follow_up_for(alex) == "follow_up"
    c.follow["question"] = True                                                  # he asked: anyone may answer
    assert c.follow_up_for(sam) == "direct"
    c.follow["question"], c.follow_anyone = False, True                          # the setting: anyone, as before
    assert c.follow_up_for(sam) == "direct"


async def answered_then(tmp_path, monkeypatch, then_voice, asker):
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "Pip, when does the bus come?", social={"join_in": False},
                                                    conversation={"follow_up_seconds": 5.0})

    async def identify(pcm):
        return who["now"]
    who = {"now": asker}
    monkeypatch.setattr(conv, "identify", identify)
    await ears.emit("heard.utterance", {**room()[0], "reason": "wake"}, private=room()[1])
    await asyncio.sleep(0.2)
    await ears.emit("speech.started", {})
    await ears.emit("speech.finished", {"interrupted": False, "spoken": True})
    await asyncio.sleep(0.1)
    assert conv.in_follow_up() and len(asks) == 1
    who["now"], conv.stt.text = then_voice, "and on Sunday"
    await ears.emit("heard.utterance", {**room()[0], "reason": "follow_up"}, private=room()[1])
    await asyncio.sleep(0.3)
    return bus, conv, asks, tasks


async def test_the_same_voice_carries_on_without_his_name(tmp_path, monkeypatch):
    from pip_assistant.core.people import Match
    alex = Match("a", "Alex", "sure", 0.8)
    bus, conv, asks, tasks = await answered_then(tmp_path, monkeypatch, alex, alex)
    assert [k for k, _ in asks] == ["direct", "direct"]
    await finish(bus, tasks)


async def test_someone_elses_voice_after_an_answer_is_room_talk(tmp_path, monkeypatch):
    from pip_assistant.core.people import Match
    bus, conv, asks, tasks = await answered_then(tmp_path, monkeypatch, Match("s", "Sam", "sure", 0.8),
                                                 Match("a", "Alex", "sure", 0.8))
    assert [k for k, _ in asks] == ["direct"] and conv.transcript.lines[-1]["text"] == "and on Sunday"
    assert [r["data"]["decision"] for r in bus.trace.recent() if r["type"] == "turn.decision"][0] == "follow_up_other_voice"
    await finish(bus, tasks)


async def test_an_unknown_voice_after_an_answer_lets_claude_decide(tmp_path, monkeypatch):
    bus, conv, asks, tasks = await answered_then(tmp_path, monkeypatch, None, None)
    assert [k for k, _ in asks] == ["direct", "follow_up"]
    await finish(bus, tasks)


# ---------------------------------------------------------------- joining in more politely (D-124)
async def test_no_join_in_on_a_question_to_someone_else(tmp_path):
    """CB-012: 'Sam, did you finish the essay?' - the next turn is Sam's, even if Pip could answer."""
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "Sam, did you finish the essay?",
                                                    replies={"join": "SAY: It's due Friday."})
    conv.joining.last_check = -1e9
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.4)
    assert asks == []
    await finish(bus, tasks)


async def test_told_off_after_joining_in_he_stays_out(tmp_path):
    """CB-031: 'not now' soon after he joined in, or being talked over while he does: no joining in for 15 minutes."""
    bus, conv, ears, got, asks, tasks = await setup(tmp_path, "not now Pip we're talking")
    conv.joining.joined()
    await ears.emit("heard.utterance", *room())
    await asyncio.sleep(0.3)
    assert conv.joining.suppressed_until > time.time() + 14 * 60
    conv.joining.suppressed_until, conv.joining.last_check, conv.joining.joined_at = 0.0, -1e9, 0.0
    assert not conv.joining.told_off("we're talking about the game")      # long after he joined in: not about him
    conv.voice_kind, conv.say_reason = "join", "answer"
    await ears.emit("speech.interrupted", {"level_dbfs": -20, "said": 0})
    await asyncio.sleep(0.1)
    assert conv.joining.suppressed_until > time.time() + 14 * 60
    whys = [r["data"]["why"] for r in bus.trace.recent() if r["type"] == "turn.decision"
            and r["data"]["decision"] == "join_suppressed"]
    assert sorted(whys) == ["talked_over", "told"]
    await finish(bus, tasks)


async def test_a_join_in_reply_that_comes_too_late_is_dropped(tmp_path):
    """CB-030: by 5 s the moment has passed."""
    bus, conv, ears, got, asks, tasks = await setup(tmp_path)
    conv.transcript.add("Someone", "I wonder how far away the moon is")
    conv.join_count = conv.transcript.count
    conv.answer_id, conv.answer_kind, conv.join_spoke = "j1", "join", False
    conv.join_asked_at = time.monotonic() - 6
    await conv.on_sentence(Message("event", "answer.sentence", data={"answer_id": "j1"}, private={"text": "Far."}))
    await asyncio.sleep(0.05)
    assert says(got) == [] and conv.answer_id is None
    await finish(bus, tasks)
