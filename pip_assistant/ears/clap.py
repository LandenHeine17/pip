"""Two claps switch Pip's listening off or on (D-91). Runs on the Pi; nothing is recorded or sent.

Claps are heard on a separate, plain USB microphone (the speakerphone's own noise filter erases them).
Tuned on the owner's recordings from that microphone (Session F, 2026-11-08, `results/clap-detect.json`):

    a clap   very loud (its loudest 10 ms at least -12 dBFS), at least 25 dB above the room just before,
             and gone again within a quarter of a second
    claps    exactly two, 0.15–1.5 s apart, then nothing more for 0.7 s (so three or more claps,
             applause, or claps in the middle of other noise don't count)

On those recordings: every double clap is heard (7 of 7); a single clap, three or more claps, talking,
snaps, knocks and a door are not. Tongue clicks can look like claps (the owner said that's fine).
"""
import math
from collections import deque

import numpy as np

SLICE = 160  # 10 ms at 16 kHz


def _db(x: float) -> float:
    return 20 * math.log10(max(x, 1e-9) / 32768)


class ClapDetector:
    def __init__(self, cfg: dict | None = None):
        c = cfg or {}
        self.peak_min = c.get("clap_peak_dbfs", -12.0)             # how loud a clap must be
        self.jump = c.get("clap_jump_db", 25.0)                    # how far above the room just before
        self.max_len = int(c.get("clap_max_ms", 250) / 10)         # gone again within this
        self.gap_min = int(c.get("clap_gap_min_ms", 150) / 10)     # the two claps at least this far apart…
        self.gap_max = int(c.get("clap_gap_max_ms", 1500) / 10)    # …and at most this far
        self.settle = int(c.get("clap_settle_ms", 700) / 10)       # then nothing more for this long
        self.reset()

    def reset(self) -> None:
        self.t = 0                       # 10 ms slices seen
        self.room = deque(maxlen=20)     # recent levels while nothing stood out (the room's background)
        self.sound = None                # a sound going on: {start, bg, top, loud}
        self.claps: list[int] = []       # when recent claps started
        self.spoiled_until = 0           # after a third clap: ignore until things calm down

    def feed(self, frame) -> bool:
        """Feed one frame (int16 samples). True when exactly two claps were just heard."""
        x = np.asarray(frame, dtype=np.float32)
        fired = False
        for i in range(0, len(x) - SLICE + 1, SLICE):
            s = x[i:i + SLICE]
            fired = self._slice(max(_db(float(np.sqrt(np.mean(s * s)))), -90.0)) or fired
        return fired

    def _bg(self) -> float:
        return float(np.median(self.room)) if self.room else -90.0

    def _slice(self, level: float) -> bool:
        self.t += 1
        if self.sound is None:
            if len(self.room) >= 5 and level - self._bg() >= 15:      # something stands out: follow it
                self.sound = {"start": self.t, "bg": self._bg(), "top": level}
            else:
                self.room.append(level)
        else:
            snd = self.sound
            snd["top"] = max(snd["top"], level) if self.t - snd["start"] < 5 else snd["top"]
            length = self.t - snd["start"]
            if level < snd["bg"] + 6 or length > self.max_len:       # it's over (or it's no clap: too long)
                self.sound = None
                is_clap = length <= self.max_len and snd["top"] >= self.peak_min and snd["top"] - snd["bg"] >= self.jump
                if is_clap:
                    self._clap(snd["start"])
                elif length > self.max_len and snd["top"] >= self.peak_min:
                    self.claps = []                                  # a loud bang or long noise: start over
        return self._decide()

    def _clap(self, start: int) -> None:
        if start < self.spoiled_until:
            self.spoiled_until = start + self.settle
            return
        self.claps = [c for c in self.claps if start - c <= self.gap_max]
        if self.claps and start - self.claps[-1] < self.gap_min:
            return                                                   # the same clap's echo
        self.claps.append(start)
        if len(self.claps) > 2:                                      # three or more: not the switch
            self.claps, self.spoiled_until = [], start + self.settle

    def _decide(self) -> bool:
        if len(self.claps) == 2 and self.sound is None and self.t - self.claps[-1] >= self.settle:
            self.claps = []
            return True
        if self.claps and self.t - self.claps[-1] > self.gap_max + self.settle:
            self.claps = []
        return False
