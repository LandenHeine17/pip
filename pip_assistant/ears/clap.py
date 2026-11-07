"""Two claps switch Pip's listening off or on (D-91). Runs on the Pi; nothing is recorded or sent.

NOT FINISHED (owner, 2026-11-07): kept deliberately simple and **switched off by default**
([mic] claps = false) until it's tuned on the owner's recordings of real claps and look-alikes
(recording page, Session E). In this simple form, ordinary talking sets it off far too often
(141 times in the 9 minutes of M0 recordings), so don't switch it on yet.

What it does now: a "clap" is a sound that jumps suddenly and loudly above what came just before;
two of them 0.1–1.5 s apart count as two claps.
"""
import math

import numpy as np

SLICE = 160  # 10 ms at 16 kHz


def _db(x: float) -> float:
    return 20 * math.log10(max(x, 1e-9) / 32768)


class ClapDetector:
    def __init__(self, cfg: dict | None = None):
        c = cfg or {}
        self.peak_min = c.get("clap_peak_dbfs", -35.0)     # how loud a clap must be at its peak
        self.jump = c.get("clap_jump_db", 15.0)            # how suddenly it must get louder
        self.gap_min = int(c.get("clap_gap_min_ms", 100) / 10)
        self.gap_max = int(c.get("clap_gap_max_ms", 1500) / 10)
        self.reset()

    def reset(self) -> None:
        self.t = 0                    # 10 ms slices seen
        self.prev = -90.0             # the last slice's level
        self.sound = None             # (start, loudest level) of a sound still going on
        self.claps: list[int] = []    # when recent claps started
        self.cooldown = 0

    def feed(self, frame) -> bool:
        """Feed one frame (int16 samples). True when two claps were just heard."""
        x = np.asarray(frame, dtype=np.float32)
        fired = False
        for i in range(0, len(x) - SLICE + 1, SLICE):
            s = x[i:i + SLICE]
            fired = self._slice(max(_db(float(np.sqrt(np.mean(s * s)))), -90.0), _db(float(np.max(np.abs(s))))) or fired
        return fired

    def _slice(self, level: float, peak: float) -> bool:
        self.t += 1
        fired = False
        if self.cooldown:
            self.cooldown -= 1
        elif self.sound is not None:
            start, top = self.sound
            if level < top - 12:                     # the sound has died down: it was one clap
                self.sound = None
                self.claps = [c for c in self.claps if self.t - c <= self.gap_max] + [start]
                if len(self.claps) >= 2 and self.claps[-1] - self.claps[-2] >= self.gap_min:
                    fired, self.claps, self.cooldown = True, [], 150   # 1.5 s before it can count again
            else:
                self.sound = (start, max(top, level))
        elif peak >= self.peak_min and level - self.prev >= self.jump:
            self.sound = (self.t, level)
        self.prev = level
        return fired
