"""The plainest path (core/conversation.py): a question to Pip is answered out loud, sentence by sentence; only his
name listens for the rest; a pause mid-sentence waits; answers are cut after two sentences; his name in room talk."""
import asyncio
import json

from helpers import answering_setup as setup
from helpers import asked, asked_how, audio_b64, finish


async def test_question_is_answered_out_loud(tmp_path):
    bus, conv, ears, got, tasks = await setup(tmp_path, "Pippletron, what's the capital of Australia?")
    await ears.emit("heard.utterance", {"reason": "room", "had_speech": True, "seconds": 2.0},
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
    await ears.emit("heard.utterance", {"reason": "room", "had_speech": True, "seconds": 2.0},
                    private={"audio_b64": audio_b64()})
    await asyncio.sleep(0.3)
    assert not [g for g in got if g[0] in ("ears.say", "ears.chime")]
    heard = [r["data"] for r in bus.trace.recent() if r["type"] == "room.heard"]
    assert heard and heard[-1]["how"] == "room"
    await finish(bus, tasks)


async def test_only_the_name_listens_quietly_for_the_rest(tmp_path):
    bus, conv, ears, got, tasks = await setup(tmp_path, "Pippletron.")
    await ears.emit("heard.utterance", {"reason": "room", "had_speech": True, "seconds": 1.0},
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
    await ears.emit("heard.utterance", {"reason": "barge_in", "had_speech": True, "seconds": 1.5, "quiet_s": 0.0},
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


async def test_every_sentence_claude_wrote_is_said(tmp_path):
    long = ("One.", "Two.", "Three.", "Four.")   # never cut short (D-130): keeping it short is Claude's job
    bus, conv, ears, got, tasks = await setup(tmp_path, "Pip, what's up?", sentences=long)
    await ears.emit("heard.utterance", {"reason": "barge_in", "had_speech": True, "seconds": 1.5},
                    private={"audio_b64": audio_b64()})
    await asyncio.sleep(0.3)
    assert [t for k, t, _ in got if k == "ears.say"] == list(long)
    await finish(bus, tasks)


async def test_empty_transcript_says_nothing(tmp_path):
    bus, conv, ears, got, tasks = await setup(tmp_path, " ")
    await ears.emit("heard.utterance", {"reason": "barge_in", "had_speech": True, "seconds": 1.0},
                    private={"audio_b64": audio_b64()})
    await asyncio.sleep(0.3)
    assert [t for k, t, _ in got if k == "ears.say"] == []   # nothing made out: no "Sorry?" (no wake word, D-134)
    await finish(bus, tasks)


async def room_line(ears, conv, text):
    conv.stt.text = text
    await ears.emit("heard.utterance", {"reason": "room", "had_speech": True, "seconds": 1.5, "quiet_s": 0.0},
                    private={"audio_b64": audio_b64()})
    await asyncio.sleep(0.15)


async def test_just_his_name_after_a_question_answers_it(tmp_path):
    """D-134: "what's the capital of Australia… Pip?" The name came on its own, after a pause: what was said just
    before it is the question, so he answers it rather than waiting for more."""
    bus, conv, ears, got, tasks = await setup(tmp_path, "")
    conv.joining.heard = lambda: None
    asked_how.clear()
    await room_line(ears, conv, "what's the capital of Australia")
    await room_line(ears, conv, "Pip?")
    await asyncio.sleep(1.3)                                          # the short turn wait, then Claude
    assert asked_how == [("name_after", 1)]
    assert [t for k, t, _ in got if k == "ears.say"] == ["Canberra is the capital.", "Not Sydney!"]
    assert ("ears.chime", None, None) in got
    await finish(bus, tasks)


async def test_calling_him_again_after_he_missed_it(tmp_path):
    """"Hey Pip, what's the weather" went unheard as his name (misheard): "Hey Pip" again answers it."""
    bus, conv, ears, got, tasks = await setup(tmp_path, "")
    conv.joining.heard = lambda: None
    asked_how.clear()
    await room_line(ears, conv, "Hey Pipe, what's the weather")
    await room_line(ears, conv, "Hey Pip.")
    await asyncio.sleep(1.3)
    assert asked_how == [("name_after", 1)]
    await finish(bus, tasks)


async def test_just_his_name_with_nothing_before_listens(tmp_path):
    """Nothing said in the last 30 s (or only before his own last answer): he listens for the rest, as before."""
    bus, conv, ears, got, tasks = await setup(tmp_path, "")
    asked_how.clear()
    conv.said("Canberra.")                                            # his own last line: what came before is done
    await room_line(ears, conv, "Pip.")
    await asyncio.sleep(1.3)
    assert asked_how == [] and ("ears.listen", None, 5.0) in got
    await finish(bus, tasks)


async def test_his_name_then_the_question_is_one_ordinary_request(tmp_path):
    bus, conv, ears, got, tasks = await setup(tmp_path, "")
    conv.joining.heard = lambda: None
    asked_how.clear()
    await room_line(ears, conv, "so anyway")
    await ears.emit("heard.started", {"reason": "room"})              # they go on straight after his name
    await room_line(ears, conv, "Pip,")
    await room_line(ears, conv, "what time is it?")
    await asyncio.sleep(1.3)
    assert asked_how == [("direct", None)] and asked[-1] == "Pip, what time is it?"
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
