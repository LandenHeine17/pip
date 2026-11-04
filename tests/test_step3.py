"""M1a step 3 tests: sentence splitting, utterance cutting, the conversation flow, the Claude reader."""
import asyncio
import base64
import json

import numpy as np

from pip_assistant.common.client import BusClient
from pip_assistant.core.bus import Bus
from pip_assistant.core.conversation import Conversation
from pip_assistant.core.health import Health
from pip_assistant.core.trace import Trace
from pip_assistant.ears.segmenter import FRAME_S, Segmenter
from pip_assistant.thinker.claude import ClaudeCLI, SentenceSplitter, speakable

LOUD = (np.ones(1280) * 3000).astype(np.int16)
QUIET = np.zeros(1280, np.int16)


# ------------------------------------------------------------------ sentences
def test_sentence_splitter_streams_and_cleans():
    sp = SentenceSplitter()
    out = []
    for piece in ["It's **cool** today", ", about 53 degrees. Grab a", " jacket! See [AccuWeather](https://x.y)",
                  " for more.\n\nSources:\n- [AccuWeather](https://x)"]:
        out += sp.feed(piece)
    out += sp.flush()
    assert out == ["It's cool today, about 53 degrees.", "Grab a jacket!", "See AccuWeather for more."]


def test_speakable_removes_links_and_markdown():
    assert speakable("# Hi *there* https://example.com now") == "Hi there now"


# ------------------------------------------------------------------ segmenter
def run_frames(seg, frames):
    out = None
    for f in frames:
        out = seg.feed(f, -20.0 if f.any() else -96.0) or out
    return out


def test_wake_at_end_of_sentence_keeps_the_question_before_it():
    seg = Segmenter()
    run_frames(seg, [QUIET] * 20 + [LOUD] * 25)          # "what time is it, Pippletron" (2 s of speech)
    seg.start("wake", word="pippletron", lookback="phrase")
    utt = run_frames(seg, [QUIET] * 30)                  # then a pause (Pip waits ~2 s for more)
    assert utt and utt.reason == "wake" and utt.had_speech
    assert 2.0 <= utt.seconds <= 2.6                     # the speech before the name, plus a little


def test_wake_then_question_after_it():
    seg = Segmenter()
    run_frames(seg, [QUIET] * 10 + [LOUD] * 8)           # "Pippletron"
    seg.start("wake", lookback="phrase")
    utt = run_frames(seg, [QUIET] * 4 + [LOUD] * 20 + [QUIET] * 15)  # short pause, then the question
    assert utt.had_speech and utt.seconds > (8 + 4 + 20) * FRAME_S - 0.1


def test_wake_with_nothing_said_gives_up():
    seg = Segmenter(after_wake_s=1.0)
    run_frames(seg, [QUIET] * 10)
    seg.start("wake", lookback="phrase")
    utt = run_frames(seg, [QUIET] * 20)
    assert utt and not utt.had_speech


def test_name_then_pause_then_question_is_one_request():
    seg = Segmenter(after_wake_s=2.0)
    run_frames(seg, [QUIET] * 10 + [LOUD] * 8)           # "Pippletron"
    seg.start("wake", lookback="phrase")
    utt = run_frames(seg, [QUIET] * 14 + [LOUD] * 20 + [QUIET] * 15)  # 1.1 s pause, then the question
    assert utt.seconds > (8 + 14 + 20) * FRAME_S - 0.1    # name, pause and question together


# ------------------------------------------------------------------ conversation flow
class FakeSTT:
    def __init__(self, text):
        self.text = text

    async def transcribe(self, pcm):
        return self.text


async def setup(tmp_path, stt_text, sentences=("Canberra is the capital.", "Not Sydney!")):
    sock = tmp_path / "core.sock"
    bus = Bus(sock, Trace(tmp_path / "pip.db"), Health(["ears", "thinker"], restart=False))
    conv = Conversation(bus, {"conversation": {"follow_up_seconds": 8.0, "keep_minutes": 10.0, "max_turns": 10}},
                        FakeSTT(stt_text))
    await bus.start()
    ears, thinker = BusClient("ears", socket_path=sock), BusClient("thinker", socket_path=sock)
    got = []

    async def record(msg):
        got.append((msg.type, msg.private.get("text"), msg.data.get("seconds")))

    for t in ("ears.chime", "ears.say", "ears.listen"):
        ears.on(t, record)

    async def ask(msg):
        for s in sentences:
            await thinker.emit("answer.sentence", {"answer_id": msg.data["answer_id"]}, private={"text": s})
        return msg.reply({"first_sentence_s": 0.5}, private={"text": " ".join(sentences)}, source="thinker")

    thinker.on("thinker.ask", ask)
    thinker.on("thinker.cancel", record)
    tasks = [asyncio.create_task(c.run()) for c in (ears, thinker)]
    for c in (ears, thinker):
        await asyncio.wait_for(c.connected.wait(), 3)
    await asyncio.sleep(0.05)
    return bus, conv, ears, got, tasks


async def finish(bus, tasks):
    for t in tasks:
        t.cancel()
    await bus.stop()


def audio_b64():
    return base64.b64encode(LOUD.tobytes() * 10).decode()


async def test_question_is_answered_out_loud(tmp_path):
    bus, conv, ears, got, tasks = await setup(tmp_path, "Pippletron, what's the capital of Australia?")
    await ears.emit("heard.wake", {"word": "pippletron"})
    await ears.emit("heard.utterance", {"reason": "wake", "had_speech": True, "seconds": 2.0},
                    private={"audio_b64": audio_b64()})
    await asyncio.sleep(0.3)
    await ears.emit("speech.finished", {"interrupted": False})
    await asyncio.sleep(0.2)
    says = [t for k, t, _ in got if k == "ears.say"]
    assert ("ears.chime", None, None) in got
    assert says == ["Canberra is the capital.", "Not Sydney!"]
    assert ("ears.listen", None, 8.0) in got                      # follow-up window opened
    assert [h["role"] for h in conv.history] == ["user", "pip"]   # remembered for follow-ups (RAM only)
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


async def test_only_the_name_gets_yes(tmp_path):
    bus, conv, ears, got, tasks = await setup(tmp_path, "Hey Pip.")
    await ears.emit("heard.utterance", {"reason": "wake", "had_speech": True, "seconds": 1.0},
                    private={"audio_b64": audio_b64()})
    await asyncio.sleep(0.3)
    assert [t for k, t, _ in got if k == "ears.say"] == ["Yes?"]
    assert ("ears.listen", None, 8.0) in got
    await finish(bus, tasks)


async def test_empty_transcript_asks_again(tmp_path):
    bus, conv, ears, got, tasks = await setup(tmp_path, " ")
    await ears.emit("heard.utterance", {"reason": "wake", "had_speech": True, "seconds": 1.0},
                    private={"audio_b64": audio_b64()})
    await asyncio.sleep(0.3)
    assert [t for k, t, _ in got if k == "ears.say"] == ["Sorry, I didn't catch that. Could you say it again?"]
    await finish(bus, tasks)


# ------------------------------------------------------------------ Claude reader (fake CLI)
async def test_claude_reader_streams_sentences_and_spots_search(tmp_path):
    events = [{"type": "system"},
              {"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "server_tool_use"}}},
              {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "It's 53 degrees. "}}},
              {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Bring a jacket"}}},
              {"type": "result", "is_error": False, "total_cost_usd": 0.01}]
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\ncat > /dev/null\ncat <<'EOF'\n" + "\n".join(json.dumps(e) for e in events) + "\nEOF\n")
    fake.chmod(0o755)
    cli = ClaudeCLI()
    cli.exe = str(fake)
    said, searched = [], []

    async def on_sentence(s):
        said.append(s)

    async def on_search():
        searched.append(True)

    text, info = await cli.ask("a1", "system", "hi", on_sentence, on_search)
    assert said == ["It's 53 degrees.", "Bring a jacket"] and searched == [True]
    assert info["searched"] and info["first_sentence_s"] is not None
    cli.cancel()
