"""Cuts what someone says to Pip out of the microphone stream (one "utterance").

A capture starts on a wake word, at the start of speech inside the follow-up window, when
someone talks over Pip, or (with transcript_wake on, D-92) whenever anyone in the room speaks.
It ends after a short pause, or after a maximum length. The wake word can be anywhere
in the sentence: the last few seconds are always kept, so "what time is it, Pippletron?" includes the question that came *before* the name.

The speakerphone's noise suppression outputs near-silence between words, so loudness alone
separates speech from quiet well (measured in M0).
"""
import collections
from dataclasses import dataclass

import numpy as np

FRAME_S = 0.08


@dataclass
class Utterance:
    reason: str            # "wake" | "follow_up" | "barge_in" | "room" (anything else said: room transcript, D-92)
    audio: np.ndarray      # int16 at 16 kHz
    had_speech: bool
    word: str = ""

    @property
    def seconds(self) -> float:
        return round(len(self.audio) / 16000, 2)


class Segmenter:
    def __init__(self, speech_dbfs=-50.0, end_silence_s=0.8, max_s=12.0, wait_for_speech_s=4.0,
                 keep_s=6.0, gap_s=0.6, after_wake_s=2.0):
        self.speech_dbfs = speech_dbfs
        self.end_frames = round(end_silence_s / FRAME_S)
        self.max_frames = round(max_s / FRAME_S)
        self.wait_frames = round(wait_for_speech_s / FRAME_S)
        self.gap_frames = round(gap_s / FRAME_S)
        self.after_wake_frames = round(after_wake_s / FRAME_S)
        self.history = collections.deque(maxlen=round(keep_s / FRAME_S))  # (frame, is_speech)
        self.capture = None

    @property
    def capturing(self) -> bool:
        return self.capture is not None

    def is_speech(self, db: float) -> bool:
        return db > self.speech_dbfs

    def start(self, reason: str, word: str = "", lookback: str | float = "phrase") -> None:
        """Begin capturing. lookback="phrase": include the speech run just before now (wake word
        at the end of a sentence); a number: include that many seconds."""
        past = list(self.history)
        if lookback == "phrase":
            # walk back from now over speech, allowing short gaps between words
            i, quiet, first_speech = len(past), 0, None
            while i > 0:
                if past[i - 1][1]:
                    quiet, first_speech = 0, i - 1
                else:
                    quiet += 1
                    if quiet >= self.gap_frames:
                        break
                i -= 1
            start = first_speech if first_speech is not None else len(past)
            keep = past[max(0, start - 2):]  # the phrase, plus ~0.16 s of lead-in
        else:
            keep = past[-max(1, round(float(lookback) / FRAME_S)):] if past else []
        frames = [f for f, _ in keep]
        had = any(s for _, s in keep)
        self.capture = {"reason": reason, "word": word, "frames": frames, "had_speech": had,
                        "after": False, "quiet": 0, "n": 0}

    def feed(self, frame: np.ndarray, db: float) -> Utterance | None:
        speech = self.is_speech(db)
        self.history.append((frame, speech))
        c = self.capture
        if not c:
            return None
        c["frames"].append(frame)
        c["n"] += 1
        if speech:
            c["had_speech"] = c["after"] = True
            c["quiet"] = 0
        else:
            c["quiet"] += 1
        if c["reason"] == "wake":
            # Wait briefly for a question *after* the name; if none comes, the question came before it.
            done = (c["after"] and c["quiet"] >= self.end_frames) or \
                   (not c["after"] and c["n"] >= self.after_wake_frames)
        else:
            done = (c["had_speech"] and c["quiet"] >= self.end_frames) or \
                   (not c["had_speech"] and c["n"] >= self.wait_frames)
        done = done or c["n"] >= self.max_frames
        if not done:
            return None
        self.capture = None
        frames = c["frames"]
        if c["had_speech"] and c["quiet"]:
            frames = frames[:len(frames) - max(0, c["quiet"] - 3)]  # drop trailing silence, keep ~0.24 s
        return Utterance(c["reason"], np.concatenate(frames) if frames else np.zeros(0, np.int16),
                         c["had_speech"], c["word"])

    def cancel(self) -> None:
        self.capture = None
