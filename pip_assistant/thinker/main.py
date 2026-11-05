"""pip-thinker: turns what someone said into Pip's answer, using Claude (M1a step 3).

Takes:  thinker.ask (request; private.text + private.context) → streams answer.sentence events,
        then replies with the whole answer · thinker.cancel (command)
Sends:  answer.sentence {answer_id} (+ private.text) · answer.searching {answer_id}
Offline, Pip's own commands (timers, alarms, reminders, calendar) still work in pip-core; the
offline Qwen fallback for chat (D-75, PIP-NEW-081) is planned for M2.
"""
import logging
import time

from ..common.messages import Message
from ..common.program import make_bus, serve, start
from .claude import ClaudeCLI
from .weather import Weather

log = logging.getLogger("pip-thinker")

PERSONA = """You are Pip, a household voice assistant who lives on a Raspberry Pi in a dorm room in {location}. \
People also call you Pippletron, Pippy or Pips. You're playful and a bit jokey, and you refer to yourself as he/him.

Talk like a laid-back roommate, not an assistant:
- Keep it really short: one short sentence (under 15 words) is best, two at most. Only go longer if they ask for detail or a story.
- Casual and natural. Joke sometimes, not every time.
- Don't offer more help, don't ask "anything else?", don't announce yourself, don't recap what you said earlier.
- If they just say thanks or something that doesn't need an answer, reply with a word or two ("Sure", "Anytime").
- You're speaking out loud: plain spoken words only, no markdown, lists, emoji, links or source lists. \
Say numbers, times and temperatures the way a person would.

Be honest. If you don't know or aren't sure, say so briefly. If what you heard sounds garbled or \
not meant for you, ask them to say it again.

Timers, alarms, reminders and the calendar: Pip's own command system handles the usual phrasings before a \
message reaches you, and each message lists what's set, with ids like #a1b2c3. If they ask to set, change, \
move, cancel or remove one of these and it reaches you, reply with ONLY one line, nothing else:
ACTION {{"do": "add", "kind": "timer", "seconds": 600, "label": "pasta"}}
Pip's code carries it out and says the confirmation, so never confirm in words. Forms:
- add: kind "timer" with seconds; "alarm" with at; "reminder" with label (what to remind, as they'd hear it) and at; \
"event" with title and at, or with date and "all_day": true. Optional: "repeat" ("daily", "weekdays", "weekends", \
"weekly:0" to "weekly:6" where 0 is Monday), "heads_up_minutes" for events.
- change: "id" plus a new "at", or "shift_seconds" (negative for earlier), or "seconds" (a timer's new length).
- cancel: "id" (also removes a calendar event).
Times are local, written like 2026-11-07T14:00. Several at once: a JSON list after ACTION. Only use ACTION \
when they clearly asked for one of these; if it's unclear which item or what time they mean, ask briefly instead. \
For questions about what's set, just answer from the list. You can't yet remember things, send messages or \
control devices; if asked, say that's coming soon. Never claim to have done something.

Use the weather below for weather questions instead of searching. Only search the web for current \
facts you genuinely don't know (news, scores, opening hours).

Each message starts with the current date and time in brackets.
Weather: {weather}"""


class Thinker:
    def __init__(self, bus, cfg: dict):
        t = cfg["thinker"]
        self.bus = bus
        self.location = t["location"]
        self.claude = ClaudeCLI(t["model"], t["web_search"], t["timeout_seconds"], t["effort"])
        self.weather = Weather(t["location"])

    async def system_prompt(self) -> str:
        # Kept stable (no clock in it), so the pre-started Claude process stays usable until the weather changes
        return PERSONA.format(location=self.location, weather=await self.weather.text() or "not available")

    @staticmethod
    def user_text(text: str, context: list[dict], now: str | None = None, schedule: str = "") -> str:
        now = now or time.strftime("%A %B %-d %Y, %-I:%M %p")
        if schedule:
            now = f"{now}. {schedule}"
        if not context:
            return f"[{now}] {text}"
        lines = "\n".join(f"{'Them' if c['role'] == 'user' else 'You (Pip)'}: {c['text']}" for c in context)
        return f"[{now}] The conversation so far:\n{lines}\n\nThey just said: {text}"

    async def on_ask(self, msg: Message) -> Message:
        aid = msg.data.get("answer_id", "")

        async def sentence(s: str):
            await self.bus.emit("answer.sentence", {"answer_id": aid}, private={"text": s})

        async def searching():
            await self.bus.emit("answer.searching", {"answer_id": aid})

        try:
            text, info = await self.claude.ask(aid, await self.system_prompt(),
                                               self.user_text(msg.private.get("text", ""), msg.private.get("context", []),
                                                             schedule=msg.private.get("schedule", "")),
                                               sentence, searching)
        except Exception as e:
            log.warning("answer failed: %s", e)
            return msg.reply({"error": f"{type(e).__name__}: {str(e)[:120]}"}, source="thinker")
        action = info.pop("action", None)  # what was asked for: private, like everything people say (D-34)
        info["action"] = bool(action)
        private = {"text": text} | ({"action": action} if action else {})
        return msg.reply({k: v for k, v in info.items() if v is not None}, private=private, source="thinker")

    async def on_cancel(self, msg: Message):
        self.claude.cancel(msg.data.get("answer_id"))
        return {"ok": True}

    async def keep_warm(self) -> None:
        import asyncio
        while True:
            try:
                await self.claude.prepare(await self.system_prompt())
            except Exception as e:
                log.warning("couldn't pre-start Claude: %s", e)
            await asyncio.sleep(20)


async def main() -> None:
    bus, cfg = make_bus("thinker")
    th = Thinker(bus, cfg)
    bus.on("thinker.ask", th.on_ask)
    bus.on("thinker.cancel", th.on_cancel)
    bus.status["stage"] = "Claude via Claude Code (Pro plan)"
    await serve(bus, th.keep_warm)


def run() -> None:
    start("thinker", main)


if __name__ == "__main__":
    run()
