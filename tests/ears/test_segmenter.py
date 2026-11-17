"""Cutting what's said out of the sound (ears/segmenter.py): the speech just before a capture started is kept,
a pause ends it, nothing said gives up, and nothing runs past the longest request."""
from helpers import LOUD, QUIET

from pip_assistant.ears.segmenter import FRAME_S, Segmenter


def run_frames(seg, frames):
    out = None
    for f in frames:
        out = seg.feed(f, -20.0 if f.any() else -96.0) or out
    return out


def test_phrase_lookback_keeps_the_speech_before_the_start():
    seg = Segmenter()
    run_frames(seg, [QUIET] * 20 + [LOUD] * 25)          # 2 s of speech already under way (talked over, D-128)
    seg.start("barge_in", lookback="phrase")
    utt = run_frames(seg, [QUIET] * 12)                  # then a pause
    assert utt and utt.reason == "barge_in" and utt.had_speech
    assert 2.0 <= utt.seconds <= 2.6                     # the speech before, plus a little


def test_a_pause_ends_it_and_a_short_gap_does_not():
    seg = Segmenter(end_silence_s=0.7)
    seg.start("room", lookback=0.4)
    assert run_frames(seg, [LOUD] * 10 + [QUIET] * 4 + [LOUD] * 10) is None   # a 0.3 s gap between words
    utt = run_frames(seg, [QUIET] * 9)
    assert utt and utt.reason == "room" and utt.seconds > 20 * FRAME_S
    assert utt.quiet_s == round(9 * FRAME_S, 2)


def test_nothing_said_gives_up():
    seg = Segmenter(wait_for_speech_s=1.0)
    seg.start("follow_up", lookback=0.0)
    utt = run_frames(seg, [QUIET] * 20)
    assert utt and not utt.had_speech


def test_nothing_runs_past_the_longest_request():
    seg = Segmenter(max_s=2.0)
    seg.start("room", lookback=0.4)
    utt = run_frames(seg, [LOUD] * 40)
    assert utt and utt.seconds <= 2.1
