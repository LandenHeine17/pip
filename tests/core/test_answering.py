"""The plainest path (core/conversation.py): a question to Pip is answered out loud, sentence by sentence; only his
name listens for the rest; a pause mid-sentence waits; answers are cut after two sentences; his name in room talk."""
import asyncio
import json

from helpers import answering_setup as setup
from helpers import asked, audio_b64, finish


async def test_question_is_answered_out_loud(tmp_path):
    bus, conv, ears, got, tasks = await setup(tmp_path, "Pippletron, what's the capital of Australia?")
    await ears.emit("heard.wake", {"word": "pippletron"})
    await ears.emit("heard.utterance", {"reason": "wake", "had_speech": True, "seconds": 2.0},
                    private={"audio_b64": audio_b64()})
    await asyncio.sleep(0.3)
    await ears.emit("speech.started", {})
    await ears.emit("speech.finished", {"interrupted": False, "spoken": True})
    await asyncio.sleep(0.2)
    says = [t for k, t, _ in got if k == "ears.say"]
    assert ("ears.chime", None, None) in got
    assert says == ["Canberra is the capital.", "Not Sydney!"]
    assert ("ears.listen", None, 8.0) in got                      # follow-up window opened
    assert [(x["speaker"], x["ours"]) for x in conv.transcript.lines] == [("Someone", False), ("Pip", True)]  # RAM only
    trace = json.dumps(bus.trace.recent())
    assert "Canberra" not in trace and "Australia" not in trace   # words never logged
    await finish(bus, tasks)


async def test_ziggletron_is_not_for_pip(tmp_path):
    bus, conv, ears, got, tasks = await setup(tmp_path, "Ziggletron, what time is it?")
    await ears.emit("heard.utterance", {"reason": "wake", "had_speech": True, "seconds": 2.0},
                    private={"audio_b64": audio_b64()})
    await asyncio.sleep(0.3)
    assert not [g for g in got if g[0] == "ears.say"]
    assert "wake.rejected" in [r["type"] for r in bus.trace.recent()]
    await finish(bus, tasks)


async def test_only_the_name_listens_quietly_for_the_rest(tmp_path):
    bus, conv, ears, got, tasks = await setup(tmp_path, "Pippletron.")
    await ears.emit("heard.utterance", {"reason": "wake", "had_speech": True, "seconds": 1.0},
                    private={"audio_b64": audio_b64()})
    await asyncio.sleep(0.3)
    assert [t for k, t, _ in got if k == "ears.say"] == []          # no "Yes?" talking over them
    assert ("ears.listen", None, 5.0) in got
    conv.stt.text = "what's the capital of Australia"                # the rest, after a pause, no name
    await ears.emit("heard.utterance", {"reason": "room", "had_speech": True, "seconds": 2.0},
                    private={"audio_b64": audio_b64()})
    await asyncio.sleep(0.3)
    assert [t for k, t, _ in got if k == "ears.say"] == ["Canberra is the capital.", "Not Sydney!"]
    await finish(bus, tasks)


async def test_paused_mid_sentence_waits_and_joins_the_rest(tmp_path):
    bus, conv, ears, got, tasks = await setup(tmp_path, "Pippletron, what's the")
    conv.unfinished_wait = 1.2                                       # counted from when they stopped (D-122)
    await ears.emit("heard.utterance", {"reason": "wake", "had_speech": True, "seconds": 1.5, "quiet_s": 0.0},
                    private={"audio_b64": audio_b64()})
    await asyncio.sleep(0.2)
    assert [t for k, t, _ in got if k == "ears.say"] == []
    await ears.emit("heard.started", {"reason": "follow_up"})
    conv.stt.text = "capital of Australia?"
    await asyncio.sleep(1.5)                                          # longer than the wait: still holding
    await ears.emit("heard.utterance", {"reason": "follow_up", "had_speech": True, "seconds": 1.5},
                    private={"audio_b64": audio_b64()})
    await asyncio.sleep(0.3)
    assert asked[-1] == "Pippletron, what's the capital of Australia?"
    assert [t for k, t, _ in got if k == "ears.say"] == ["Canberra is the capital.", "Not Sydney!"]
    await finish(bus, tasks)


async def test_answers_are_cut_after_two_sentences_unless_detail_asked(tmp_path):
    long = ("One.", "Two.", "Three.", "Four.")
    bus, conv, ears, got, tasks = await setup(tmp_path, "Pip, what's up?", sentences=long)
    await ears.emit("heard.utterance", {"reason": "wake", "had_speech": True, "seconds": 1.5},
                    private={"audio_b64": audio_b64()})
    await asyncio.sleep(0.3)
    assert [t for k, t, _ in got if k == "ears.say"] == ["One.", "Two."]
    got.clear()
    conv.stt.text = "Pip, tell me a story"
    await ears.emit("heard.utterance", {"reason": "wake", "had_speech": True, "seconds": 1.5},
                    private={"audio_b64": audio_b64()})
    await asyncio.sleep(0.3)
    assert [t for k, t, _ in got if k == "ears.say"] == list(long)
    await finish(bus, tasks)


async def test_empty_transcript_asks_again(tmp_path):
    bus, conv, ears, got, tasks = await setup(tmp_path, " ")
    await ears.emit("heard.utterance", {"reason": "wake", "had_speech": True, "seconds": 1.0},
                    private={"audio_b64": audio_b64()})
    await asyncio.sleep(0.3)
    assert [t for k, t, _ in got if k == "ears.say"] == ["Sorry, I didn't catch that. Could you say it again?"]
    await finish(bus, tasks)


async def test_name_in_room_transcript_gets_an_answer(tmp_path):
    bus, conv, ears, got, tasks = await setup(tmp_path, "What's the capital of Australia, Pipp?")
    await ears.emit("heard.utterance", {"reason": "room", "had_speech": True, "seconds": 2.0},
                    private={"audio_b64": audio_b64()})
    await asyncio.sleep(0.3)
    assert ("ears.chime", None, None) in got
    assert [t for k, t, _ in got if k == "ears.say"] == ["Canberra is the capital.", "Not Sydney!"]
    await finish(bus, tasks)


async def test_room_talk_without_the_name_is_ignored(tmp_path):
    bus, conv, ears, got, tasks = await setup(tmp_path, "I think we should get pizza tonight.")
    await ears.emit("heard.utterance", {"reason": "room", "had_speech": True, "seconds": 2.0},
                    private={"audio_b64": audio_b64()})
    await asyncio.sleep(0.3)
    assert got == [] and "room.heard" in [r["type"] for r in bus.trace.recent()]
    await finish(bus, tasks)
