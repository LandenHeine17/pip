"""What's been said in the room lately, for Claude (D-92, like Ziggletron).

Every line Pip hears is kept, whether or not it was said to him, along with what he said himself:

    [14:02] Landen: did you see the game last night
    [14:02] Someone: no, who won?
    [14:03] Pip: Nebraska, by three. Close one.

Speakers are a name (voice recognised, "sure"), "Name?" (probably them) or "Someone". Pip's own
lines cut short by someone talking over him end with "(cut off)".

Kept in memory only (never on disk, never logged), the last 40 lines and at most 3 hours back. It
travels to pip-thinker only in messages' `private` part (D-34).
"""
import time
from collections import deque


class Transcript:
    def __init__(self, max_lines: int = 40, max_age: float = 3 * 3600, clock=time.time):
        self.lines: deque[dict] = deque(maxlen=max_lines)
        self.max_age, self.clock = max_age, clock
        self.count = 0   # lines ever added: tells whether the conversation moved on

    def add(self, speaker: str, text: str, ours: bool = False) -> dict:
        line = {"ts": self.clock(), "speaker": speaker, "text": text.strip(), "ours": ours, "cut": False}
        self.lines.append(line)
        self.count += 1
        return line

    def _fresh(self) -> list[dict]:
        cutoff = self.clock() - self.max_age
        return [line for line in self.lines if line["ts"] >= cutoff]

    def last(self) -> dict | None:
        fresh = self._fresh()
        return fresh[-1] if fresh else None

    def last_human(self) -> dict | None:
        return next((line for line in reversed(self._fresh()) if not line["ours"]), None)

    def last_ours(self) -> dict | None:
        return next((line for line in reversed(self._fresh()) if line["ours"]), None)

    def mark_cut(self, cut: bool = True) -> None:
        """Someone talked over Pip's last line (cut=False: it was only a "yeah", and he carried on)."""
        if (line := self.last_ours()) is not None:
            line["cut"] = cut

    def render(self) -> list[str]:
        out = []
        for line in self._fresh():
            when = time.strftime("%H:%M", time.localtime(line["ts"]))
            out.append(f"[{when}] {line['speaker']}: {line['text']}" + (" (cut off)" if line["cut"] else ""))
        return out
