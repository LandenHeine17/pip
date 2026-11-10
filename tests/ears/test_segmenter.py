"""Cutting what's said out of the sound (ears/segmenter.py): a wake word at the end or start of a sentence, a pause
after the name, and a wake word during room capture."""
from helpers import LOUD, QUIET

from pip_assistant.ears.segmenter import FRAME_S, Segmenter


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


def test_wake_during_room_capture_becomes_a_wake_request():
    seg = Segmenter()
    run_frames(seg, [QUIET] * 5)
    seg.start("room", lookback=0.4)
    run_frames(seg, [LOUD] * 10)
    c = seg.capture
    c["reason"], c["word"], c["after"] = "wake", "pippletron", c["had_speech"]
    utt = run_frames(seg, [QUIET] * 12)
    assert utt and utt.reason == "wake" and utt.word == "pippletron"
