"""pip-ears tests: chime, loudness, wake words (on synthetic speech), and that the speaker plays everything and stops on cue."""
import asyncio

import numpy as np
import pytest

from pip_assistant.common import paths
from pip_assistant.ears.audio import chime, level_dbfs

VOICE = paths.data_dir() / "models" / "voices" / "en_US-lessac-medium.onnx"
needs_voice = pytest.mark.skipif(not VOICE.exists(), reason="voice model not in ~/pip-data/models/voices")


def test_chime_and_levels():
    c = chime(22050)
    assert c.dtype == np.int16 and 0.3 < len(c) / 22050 < 0.4
    assert level_dbfs(np.zeros(1280, np.int16)) == -96.0
    assert -12 < level_dbfs((np.ones(1280) * 10000).astype(np.int16)) < -10


def speech_16k(voice, text):
    from scipy.signal import resample_poly
    a = voice.speak(text)
    a = resample_poly(a.astype(np.float32), 16000, voice.rate).astype(np.int16)
    return np.concatenate([np.zeros(8000, np.int16), a, np.zeros(16000, np.int16)])


@pytest.mark.pi_data
@needs_voice
@pytest.mark.parametrize("text,expected", [("Pippletron, what time is it?", "pippletron"),
                                           ("Hey Pip, what time is it?", "hey_pip"),
                                           ("The pizza is in the oven.", None),
                                           ("Ziggletron, what time is it?", None)])
def test_wake_words_on_synthetic_speech(text, expected):
    from pip_assistant.ears.voice import make_voice
    from pip_assistant.ears.wake import WakeDetector
    voice = make_voice("piper", "en_US-lessac-medium", 1.0, repeatable=True)
    det = WakeDetector(["pippletron", "hey_pip"], thresholds={"pippletron": 0.8, "hey_pip": 0.5})
    audio, hit = speech_16k(voice, text), None
    for i in range(0, len(audio) - 1280, 1280):
        hit = hit or det.feed(audio[i:i + 1280])
    assert (hit[0] if hit else None) == expected


async def test_speaker_really_sends_the_audio(tmp_path):
    """The player must receive every sample (pw-play needs --raw for this; M1a step 2 bug)."""
    from pip_assistant.ears.audio import Speaker
    out = tmp_path / "played.raw"
    player = tmp_path / "fake-player"
    player.write_text(f'#!/bin/sh\necho "$@" > {tmp_path}/args\ncat > {out}\n')
    player.chmod(0o755)
    audio = (np.sin(np.arange(22050) / 10) * 8000).astype(np.int16)
    finished = await Speaker(player=str(player)).play(audio, 22050)
    assert finished
    assert out.read_bytes() == audio.tobytes()
    assert "--raw" in (tmp_path / "args").read_text()


async def test_speaker_stop_cuts_off(tmp_path):
    from pip_assistant.ears.audio import Speaker
    player = tmp_path / "slow-player"
    player.write_text("#!/bin/sh\nsleep 5\n")
    player.chmod(0o755)
    sp = Speaker(player=str(player))
    audio = np.zeros(22050 * 5, np.int16)
    task = asyncio.create_task(sp.play(audio, 22050))
    await asyncio.sleep(0.3)
    await sp.stop()
    assert await asyncio.wait_for(task, 2) is False


async def test_talking_over_pip_keeps_a_sentence_already_under_way():
    """Owner's feedback (2026-11-10): a timer went off mid-sentence, he talked over its announcement, and only the
    words after it were heard. Now the recording that was already going carries on whole."""
    import numpy as np
    from pip_assistant.ears import main as ears_main
    from pip_assistant.ears.segmenter import Segmenter

    class Bus:
        emitted = []

        async def emit(self, kind, data):
            Bus.emitted.append(kind)
    e = ears_main.Ears.__new__(ears_main.Ears)
    e.bus, e.seg, e.said_n = Bus(), Segmenter(), 0

    async def stop_speaking():
        pass
    e.stop_speaking = stop_speaking
    loud = (np.ones(1280) * 3000).astype(np.int16)
    e.seg.start("follow_up", lookback=0.4)
    for _ in range(40):                                  # 3 s of the sentence before the alarm
        e.seg.feed(loud, -20.0)
    before = len(e.seg.capture["frames"])
    await e.talked_over(-20.0)
    assert e.seg.capture["reason"] == "follow_up" and len(e.seg.capture["frames"]) == before
    assert Bus.emitted == ["speech.interrupted"]
    e.seg.capture["reason"] = "room"                     # room talk carrying on over him becomes for him
    await e.talked_over(-20.0)
    assert e.seg.capture["reason"] == "barge_in" and len(e.seg.capture["frames"]) == before
    e.seg.capture = None                                 # nothing under way: a new recording, as before
    await e.talked_over(-20.0)
    assert e.seg.capture["reason"] == "barge_in"


async def test_only_a_voice_cuts_him_off(monkeypatch):
    """D-122: ~240 ms of loud sound over Pip stops him only if it has a human voice in it (the owner's spec,
    2026-11-12). A knock or a clap is traced as speech.overlap_ignored, once, and he carries on."""
    from pip_assistant.ears import main as ears_main
    from pip_assistant.ears.segmenter import Segmenter

    class Bus:
        emitted = []

        async def emit(self, kind, data):
            Bus.emitted.append(kind)
    e = ears_main.Ears.__new__(ears_main.Ears)
    e.bus, e.seg, e.cfg, e.loud_frames, e.overlap_noted = Bus(), Segmenter(), {"barge_in_dbfs": -45.0}, 0, False
    cut = []

    async def talked_over(db):
        cut.append(db)
    e.talked_over = talked_over
    loud = (np.ones(1280) * 3000).astype(np.int16)
    voice = {"seconds": 0.0}
    monkeypatch.setattr(ears_main, "voice_seconds_sync", lambda pcm: voice["seconds"])
    for _ in range(9):                                   # a knock that goes on: three checks, no voice
        e.seg.history.append((loud, True))
        await e.heard_over(loud, -20.0)
    assert cut == [] and Bus.emitted == ["speech.overlap_ignored"]
    voice["seconds"] = 0.3                               # someone talking
    for _ in range(3):
        await e.heard_over(loud, -20.0)
    assert cut == [-20.0]
    e.cfg["barge_in_voice_check"], voice["seconds"] = False, 0.0   # switched off: loudness alone, as before D-122
    for _ in range(3):
        await e.heard_over(loud, -20.0)
    assert cut == [-20.0, -20.0]
    for _ in range(5):                                   # quiet: never
        await e.heard_over(loud, -60.0)
    assert len(cut) == 2
