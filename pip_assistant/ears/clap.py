"""Two claps switch Pip's listening off or on (D-91). Runs on the Pi; nothing is recorded or sent.

A clap is a sudden, loud, very short sound: it jumps well above the room's background, is at its
loudest straight away (speech builds up over a few hundredths of a second), stays near that peak for
under ~80 ms and dies away within about 150 ms. Speech, music, a door or a dropped pan either last longer or don't come as a
pair. What counts:

    quiet  →  clap  →  0.1–1.5 s  →  clap  →  0.6 s with no third clap and no talking

so applause, a run of knocks or someone mid-sentence never counts. The sound is looked at in 10 ms
slices of each 80 ms frame. Settings: [mic] in defaults.toml.
"""
import math

import numpy as np

SLICE = 160  # 10 ms at 16 kHz


def _db(x: float) -> float:
    return 20 * math.log10(max(x, 1e-9) / 32768)


class ClapDetector:
    def __init__(self, cfg: dict | None = None):
        c = cfg or {}
        self.peak_min = c.get("clap_peak_dbfs", -35.0)      # how loud a clap must be at its peak
        self.second_peak = c.get("clap_second_peak_dbfs", -45.0)   # the second one may be quieter
        self.second_rise = c.get("clap_second_rise_db", 12.0)
        self.rise = c.get("clap_rise_db", 20.0)             # how far above the background it jumps
        self.drop = c.get("clap_drop_db", 15.0)             # how far it must fall again…
        self.decay = int(c.get("clap_decay_ms", 150) / 10)  # …and within how long
        self.gap_min = int(c.get("clap_gap_min_ms", 100) / 10)
        self.gap_max = int(c.get("clap_gap_max_ms", 1500) / 10)
        self.after = int(c.get("clap_quiet_after_ms", 600) / 10)
        self.before = int(c.get("clap_quiet_before_ms", 300) / 10)
        self.talk_db = c.get("clap_talk_dbfs", -45.0)       # at least this loud counts as talking (not quiet)
        self.attack = int(c.get("clap_attack_ms", 30) / 10)  # loudest point at most this long after it starts
        self.width = int(c.get("clap_width_ms", 80) / 10)    # near its loudest (within 10 dB) for at most this long
        self.reset()

    def reset(self) -> None:
        self.t = 0                  # slices seen
        self.bg = -70.0             # background level (dBFS), slowly tracked
        self.recent: list[float] = []   # the last `before` slice levels
        self.onset = None           # a possible clap still being checked: {start, top, top_t, near}
        self.claps: list[int] = []  # slices of confirmed claps in the current pattern
        self.loud_after = 0         # slices of talking-level sound since the second clap
        self.cooldown = 0
        self.tops: list[float] = []  # how loud each clap was
        self.prev = -90.0           # the last slice's level

    def _quiet_before(self) -> bool:
        if len(self.recent) < self.before:
            return True
        loud = sum(1 for v in self.recent if self._loud(v))
        return loud <= 0.2 * len(self.recent)

    def feed(self, frame) -> bool:
        """Feed one frame (int16 samples). True when two claps were just completed."""
        x = np.asarray(frame, dtype=np.float32)
        fired = False
        for i in range(0, len(x) - SLICE + 1, SLICE):
            s = x[i:i + SLICE]
            fired = self._slice(_db(float(np.sqrt(np.mean(s * s)))), _db(float(np.max(np.abs(s))))) or fired
        return fired

    def _slice(self, level: float, peak: float) -> bool:
        self.t += 1
        if self.cooldown:
            self.cooldown -= 1
            self._track(level)
            return False
        # A possible clap: did it die away fast enough?
        if self.onset is not None:
            o = self.onset
            if level > o["top"]:
                o["top"], o["top_t"] = level, self.t
            if level > o["top"] - 10:
                o["near"] += 1
            if o["top_t"] - o["start"] > self.attack or o["near"] > self.width or self.t - o["start"] > self.decay:
                self.onset = None          # builds up or lasts too long for a clap (speech, music, a slam that rings on)
                if len(self.claps) < 2:
                    self.claps = []
                # after two claps, a stray sound only counts towards "talking" in the quiet check below
            elif level <= o["top"] - self.drop:
                self.onset = None
                self._clap(o["start"], o["top"])
        elif self._starts(level, peak):
            if self.claps or self._quiet_before():
                self.onset = {"start": self.t, "top": level, "top_t": self.t, "near": 1}
            else:
                self.claps = []
        # After the second clap: wait for quiet, with no third clap and no talking
        if len(self.claps) == 2 and self.onset is None:
            since = self.t - self.claps[1]
            if self._loud(level):
                self.loud_after += 1
            if since >= self.after:
                ok = self.loud_after <= 0.25 * self.after
                self.claps, self.loud_after = [], 0
                if ok:
                    self.cooldown = 150   # 1.5 s before another pair can count
                    self._track(level)
                    return True
        elif len(self.claps) == 1 and self.t - self.claps[0] > self.gap_max and self.onset is None:
            self.claps = []                # only one clap
        self._track(level)
        return False

    def _starts(self, level: float, peak: float) -> bool:
        """A sudden loud sound. The second clap may be quieter: the speakerphone's noise suppression
        often turns the second of two quick claps down (measured 2026-11-07)."""
        jump = level - self.prev >= 10        # a real new sound, not the tail of the last one
        if len(self.claps) == 1:
            return jump and peak >= self.second_peak and level >= self.bg + self.second_rise
        return jump and peak >= self.peak_min and level >= self.bg + self.rise

    def _loud(self, level: float) -> bool:
        return level > max(self.bg + 12, self.talk_db)

    def _clap(self, start: int, top: float) -> None:
        if not self.claps:
            self.claps, self.tops = [start], [top]
        elif len(self.claps) == 1:
            gap = start - self.claps[0]
            if self.gap_min <= gap <= self.gap_max:
                self.claps, self.tops = [self.claps[0], start], self.tops + [top]
            else:
                self.claps, self.tops = [start], [top]
            self.loud_after = 0
        elif top >= min(self.tops) - 12:
            self.claps = []                # a real third clap: applause or knocking, not the signal
        # (a much quieter blip after the second clap is just its echo: ignored)

    def _track(self, level: float) -> None:
        self.prev = max(level, -90.0)
        self.recent.append(level)
        if len(self.recent) > self.before:
            self.recent.pop(0)
        if self.onset is None and not self.claps:  # background follows the room slowly (not during a clap pattern)
            rate = 0.02 if level > self.bg else 0.1
            self.bg += rate * (max(level, -90.0) - self.bg)
