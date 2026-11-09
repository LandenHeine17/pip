"""Pip joining in when nobody spoke to him, like Ziggletron (D-92).

After someone says something that wasn't for Pip, and the room pauses (1.2 s), Pip may ask Claude
whether he'd say something right now. Claude answers PASS (stay quiet) or "SAY: …" (only those
words are spoken). He only asks when all of these hold:

    - he isn't busy (answering, speaking, ringing, learning a voice), he's listening and online
    - it's a live conversation: the last thing said was 1.2–60 s ago, by a person, and it's new
      since he last considered joining in
    - enough time has passed since he last considered it (mood: quiet 2 min, normal 40 s, chatty 15 s)
    - it isn't quiet hours (23:00–07:00, D-66)
    - Claude's usage limit isn't reached: after pip-thinker's "usage.limited" he doesn't join in until it
      resets (D-69, common/usage.py). Replies to people carry on as normal.

If anyone starts talking while he's deciding, or the conversation moves on before he speaks, he
drops it. Joining in can never set timers or save memories (nothing overheard is ever acted on).
"""
import asyncio
import time

INTERVALS = {"quiet": 120.0, "normal": 40.0, "chatty": 15.0}
MOODS = {
    "quiet": "Speak up rarely - only when you'd really want to.",
    "normal": "Join in like a friendly, engaged person would, without dominating.",
    "chatty": "You're in a sociable mood: join in often, while still letting people talk.",
}


def in_quiet_hours(spec: str, now: float | None = None) -> bool:
    """'01:00-06:00' → is it that time now? Empty means never."""
    if not spec:
        return False
    start, end = (int(p.split(":")[0]) * 60 + int(p.split(":")[1]) for p in spec.split("-"))
    t = time.localtime(now if now is not None else time.time())
    m = t.tm_hour * 60 + t.tm_min
    return start <= m < end if start <= end else (m >= start or m < end)


class Joining:
    def __init__(self, conv, cfg: dict | None = None, clock=time.time):
        s = (cfg or {}).get("social", {})
        self.conv, self.clock = conv, clock
        self.enabled = s.get("join_in", True)
        self.mood = s.get("mood", "normal") if s.get("mood", "normal") in MOODS else "normal"
        self.pause = s.get("pause_seconds", 1.2)
        self.live = s.get("live_conversation_seconds", 60.0)
        self.quiet_hours = s.get("quiet_hours", "23:00-07:00")
        self.last_check = 0.0
        self.checked_count = -1      # transcript.count when he last considered joining in
        self.task: asyncio.Task | None = None
        self.paused_until = 0.0      # Claude's usage limit: no joining in until then (unix time)
        bus = getattr(conv, "bus", None)
        if bus is not None and hasattr(bus, "core_listen"):
            bus.core_listen("usage.limited", self.on_limited)

    async def on_limited(self, msg) -> None:
        """pip-thinker: Claude's usage limit is reached until data.until."""
        try:
            self.paused_until = max(self.paused_until, float(msg.data.get("until") or 0))
        except (TypeError, ValueError):
            self.paused_until = max(self.paused_until, self.clock() + 3600)
        self.cancel_wait()

    @property
    def interval(self) -> float:
        return INTERVALS[self.mood]

    def heard(self) -> None:
        """A line nobody said to Pip: consider joining in once the room pauses."""
        self.cancel_wait()
        if self.enabled:
            self.task = asyncio.create_task(self._after_pause())

    def someone_started(self) -> None:
        """Someone started talking: no pause yet, and drop a join-in he was still deciding on."""
        self.cancel_wait()
        if self.conv.answer_id and self.conv.answer_kind == "join" and not self.conv.join_spoke:
            self.conv.cancel_answer("someone started talking")

    def cancel_wait(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = None

    async def _after_pause(self) -> None:
        await asyncio.sleep(self.pause)
        self.task = None
        if self.ready():
            self.last_check = self.clock()
            self.checked_count = self.conv.transcript.count
            await self.conv.join_in(self.mood_text())

    def mood_text(self) -> str:
        return MOODS[self.mood]

    def ready(self) -> bool:
        c, tr, now = self.conv, self.conv.transcript, self.clock()
        last, human = tr.last(), tr.last_human()
        return (self.enabled and c.can_join() and last is not None and not last["ours"] and human is not None
                and tr.count != self.checked_count
                and self.pause - 0.05 <= now - human["ts"] <= self.live
                and now - self.last_check >= self.interval
                and now >= self.paused_until
                and not in_quiet_hours(self.quiet_hours, now))
