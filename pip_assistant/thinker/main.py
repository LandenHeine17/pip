"""pip-thinker: turns what someone said into Pip's answer, using Claude (M1a step 3).

Takes:  thinker.ask (request; data.answer_id, data.kind = direct / mentioned / join; private: text, transcript
        (the room conversation), schedule, people, memories, mood, looked_up: all filled in by pip-core)
        → streams answer.sentence events, then replies with the whole answer (and any ACTION line, privately)
        thinker.ask with data.kind = preview (private: personality, question): the Settings page's "Try it".
        Answers with that personality, nothing from the room, nothing spoken or done; replies private.text.
        thinker.cancel {answer_id} (command)
Sends:  answer.sentence {answer_id} (+ private.text) · answer.searching {answer_id}
        usage.limited {until}: Claude said the plan's usage limit is reached; joining in and self-changes pause
        until then (D-69; core/joining.py). Replies to people keep trying.
Usage:  every request is counted by kind in the usage table (common/usage.py); today's and the week's totals
        and paused_until go out with each heartbeat (the dashboard's Programs section shows them).
System prompt = the personality (common/personality.py: ~/pip-data/personality.md, re-read when it changes)
+ RULES (fixed here) + each module's [claude] instructions (module.toml).
Offline, Pip's own commands (timers, alarms, reminders, calendar) still work in pip-core; the
offline Qwen fallback for chat (D-75, PIP-NEW-081) is planned for M2.
"""
import asyncio
from collections import deque
import logging
import time
from datetime import datetime, timedelta

from ..common import personality
from ..common.messages import Message
from ..common.modules import enabled
from ..common.program import make_bus, serve, start
from ..common.usage import Usage, is_limit_error, paused_until
from ..common.weather import Weather
from ..common import when
from .claude import ClaudeCLI

log = logging.getLogger("pip-thinker")

# The fixed part of what Claude is told: actions, timers, memory, voices, look-ups. Always added after the
# personality (common/personality.py, editable on the Settings page), so the personality can't change it (FEAT-022).
RULES = """Timers, alarms and reminders: you handle all of these, in whatever words people use. Each \
message lists what's set, with ids like #a1b2c3 (and what's RINGING NOW or just rang). If they ask to set, \
change, move, cancel or remove one, or to stop or snooze what's ringing, reply with ONLY one line, nothing else:
ACTION {{"do": "add", "kind": "timer", "seconds": 600, "label": "pasta"}}
Pip's code carries it out and says the confirmation, so never confirm in words. Forms:
- add: kind "timer" with seconds (a "label" only if they named it, like "pasta" - never "timer" or the length); "alarm" with at; "reminder" with label (what to remind, as they'd hear it) and at. \
Optional: "repeat" ("daily", "weekdays", "weekends", "weekly:0" to "weekly:6" where 0 is Monday).
- change: "id" plus a new "at", or "shift_seconds" (negative for earlier), or "seconds" (a timer's new length).
- cancel: "id".
- stop_alarm: turn off what's ringing ("I'm up", "turn it off"). snooze: optional "minutes" ("give me ten more minutes").
Times are local, written like 2026-11-07T14:00. Several at once: a JSON list after ACTION. Only use ACTION \
when they clearly asked for one of these; if it's unclear which item or what time they mean, ask briefly instead. \
For questions about what's set, just answer from the list.

Memory: each message says who's speaking (if Pip recognises the voice), who lives here, and what you remember \
(ids like #m1a2b3). Use memories naturally when they matter; don't recite them. Only when someone asks you to \
remember something lasting, reply with ONLY:
ACTION {{"do": "remember", "text": "Landen likes his coffee black", "category": "preference", "about": ["Landen"], \
"sensitive": false, "private": false}}
Write the fact itself in the third person with names (the speaker's name for "I"/"my"): "Sam is allergic \
to peanuts", never "Landen says Sam is allergic to peanuts". A fact about a person is category personal with \
"about": [that person]. If it's about "I"/"my" and \
you don't know who's speaking, don't remember it: ask who they are instead. "about" lists only the people a \
preference or personal fact is about; household facts and rules have "about": []. Categories: preference, household, rule (how Pip should behave), \
personal (facts about a person), device. sensitive = health, money, relationships, religion or politics, \
someone's whereabouts or schedule, or anything about a child. private only if they say so. To forget: \
{{"do": "forget", "id": "#m1a2b3"}}; to correct: {{"do": "update_memory", "id": "#m1a2b3", "text": "..."}}. \
Some memories wait in the inbox for approval (listed as waiting). If the person one is about says it's right \
or to go ahead and save it, approve it: {{"do": "approve_memory", "id": "#m1a2b3"}}. \
If it's unclear which memory they mean, ask. Never remember passwords, codes, card or phone numbers. \
Never save things on your own or from overheard talk. \
Voices: you recognise voices on the Pi (each message says who's speaking and how sure). When someone \
introduces themselves ("I'm Sam", "it's Landen here") or asks you to learn their voice, reply with ONLY: \
ACTION {{"do": "learn_voice", "name": "Sam"}} (use the spelling from the household list if it's them). Pip then \
greets them and walks them through it (sentences on the dashboard), or remembers a visitor for the visit. \
If they ask to learn their voice and you don't know who's speaking, ask their name first. "Who am I?" is \
answered from who's speaking. People who live here are added on your dashboard. Never say you can't learn \
or recognise voices. You can't yet send messages or control devices; \
if asked, say that's coming soon. Never claim to have done something.

Changing yourself: people can ask you to change how you work or add something new to you ("Pip, add a \
way to track whose turn it is for the dishes", "stop saying good morning every time"). Only when someone \
clearly asks you to change or add to yourself, reply with ONLY:
ACTION {{"do": "request_change", "summary": "keep track of whose turn it is for the dishes"}}
The summary is a short paraphrase of the change in your own words, with no names or personal details. Pip \
writes up a plan, and it's approved on your dashboard's Changes page, never by voice. Questions about how you \
work, complaints that don't ask for a change ("you're glitchy today"), what-ifs ("could you ever...?") and \
things you can already do (timers, memories, voices) never use request_change: just answer. If they ask \
to undo or take back a change, tell them to press Undo on the Changes page; you can't approve or undo changes.

Use the weather below for weather questions instead of searching. Only search the web for current \
facts you genuinely don't know (news, scores, opening hours).

Each message starts, in brackets, with the date and time, who's speaking and who lives here, what's set, and what you remember.
Weather: {weather}"""


class OneOffCLI(ClaudeCLI):
    """For previews: no process is started in advance, so a preview never replaces the warm one for Pip's
    real personality."""

    async def prepare(self, system_prompt: str) -> None:
        return


class Thinker:
    def __init__(self, bus, cfg: dict):
        t = cfg["thinker"]
        self.bus = bus
        self.location = t["location"]
        # What each plugged-in module adds (modules/<name>/module.toml [claude]): calendar events, to-dos, …
        self.module_texts = {m.name: m.claude for m in enabled(cfg) if m.claude}
        self.module_text = "\n\n".join(self.module_texts.values())
        self.recent: deque = deque(maxlen=20)   # the last requests' sizes and tokens (RAM only; never the words)
        self.claude = ClaudeCLI(t["model"], t["web_search"], t["timeout_seconds"], t["effort"])
        self.preview_claude = OneOffCLI(t["model"], t["web_search"], t["timeout_seconds"], t["effort"])
        self.weather = Weather(t["location"])
        self.name = cfg.get("pip", {}).get("name", "Pip")   # fills {name} in the personality (Settings)
        try:
            self.usage = Usage()
        except Exception as e:   # the meter must never stop Pip answering
            log.warning("usage meter unavailable: %s", e)
            self.usage = None
        self.paused_until = 0.0
        self.cancelled: set[str] = set()
        self.show_usage()

    name = "Pip"   # fills {name} in the personality; settings ([pip] name) override it in __init__
    usage = None
    paused_until = 0.0
    KIND = {"direct": "answer", "mentioned": "mention", "join": "join", "preview": "preview"}

    # ---------------------------------------------------------------- the usage meter (D-69, PIP-NEW-089)
    def show_usage(self) -> None:
        """Put today's and this week's totals, and any pause, in the heartbeat (→ /api/health)."""
        status = getattr(getattr(self, "bus", None), "status", None)
        if status is None:
            return
        if self.usage is not None:
            try:
                status["usage_today"], status["usage_week"] = self.usage.today(), self.usage.week()
            except Exception as e:
                log.warning("usage meter: %s", e)
        status["paused_until"] = self.paused_until if self.paused_until > time.time() else None

    async def count(self, kind: str, report: dict, failed: Exception | None, cancelled: bool = False) -> None:
        """One Claude request done: count it, and pause joining in if Claude says the limit is reached."""
        if report:
            error, text = report["error"], report["text"]
        else:   # no result from the CLI: it failed (or was stopped) before finishing
            error, text = failed is not None and not cancelled, str(failed or "")
        if self.usage is not None:
            try:
                self.usage.record(self.KIND.get(kind, "other"), report.get("cost"), error)
            except Exception as e:
                log.warning("usage meter: %s", e)
        if error and is_limit_error(text):
            await self.limited(paused_until(text))
        self.show_usage()

    async def limited(self, until: float) -> None:
        self.paused_until = max(self.paused_until, until)
        log.warning("Claude's usage limit reached: joining in paused until %s", time.strftime("%H:%M", time.localtime(until)))
        await self.bus.emit("usage.limited", {"until": round(self.paused_until)})

    def paused(self) -> bool:
        return self.paused_until > time.time()

    async def _ask(self, cli, kind: str, aid: str, *args, **kw):
        """cli.ask(…), counted in the usage meter."""
        report: dict = {}

        def on_result(cost, is_error, text):
            report.update(cost=cost, error=bool(is_error), text=text or "")
        failed = None
        try:
            return await cli.ask(aid, *args, on_result=on_result, **kw)
        except Exception as e:
            failed = e
            raise
        finally:
            stopped = self.__dict__.setdefault("cancelled", set())
            cancelled = aid in stopped
            stopped.discard(aid)
            await self.count(kind, report, failed, cancelled)

    async def prompt_parts(self) -> dict:
        """The fixed part of what Claude is told, piece by piece (the "What Pip sends" page): the personality, the
        rules (with today's weather) and each module's instructions. None of it is anything people said."""
        return {"personality": personality.render(personality.read()[0], self.name, self.location),
                "rules": RULES.format(weather=await self.weather.text() or "not available"),
                "modules": dict(self.__dict__.get("module_texts", {}))}

    def remember_request(self, kind: str, system: str, msg: Message, user: str, info: dict) -> None:
        """One request's sizes and Claude's token counts, for the "What Pip sends" page. Only lengths and numbers:
        what people said is never kept (CLAUDE.md rule 1)."""
        p = msg.private
        lines = p.get("transcript") or []
        self.__dict__.setdefault("recent", deque(maxlen=20)).appendleft({
            "at": round(time.time()), "kind": kind, "system_chars": len(system), "user_chars": len(user),
            "parts": {"room conversation": sum(len(x) + 1 for x in lines), "lines heard": len(lines),
                      "who's here": len(p.get("people", "")), "what's set (timers, calendar, to-dos…)": len(p.get("schedule", "")),
                      "memories": len(p.get("memories", "")), "looked up": len(p.get("looked_up", ""))},
            "tokens": info.get("usage") or {}, "cost_usd": info.get("cost_usd"), "searched": bool(info.get("searched"))})

    async def on_prompt(self, msg: Message) -> Message:
        """The dashboard's "What Pip sends" page: the fixed text in full, and the last requests' sizes and tokens."""
        parts = await self.prompt_parts()
        return msg.reply({"ok": True}, private={"fixed": parts, "recent": list(self.__dict__.get("recent", ()))}, source="thinker")

    async def system_prompt(self, persona: str | None = None) -> str:
        """The personality (the saved one, or `persona` for a preview), then the fixed rules, then the modules'.
        Kept stable (no clock in it), so the pre-started Claude process stays usable until the weather or the
        personality changes."""
        persona = personality.read()[0] if persona is None else persona
        base = personality.render(persona, self.name, self.location) + "\n\n" + \
            RULES.format(weather=await self.weather.text() or "not available")
        return base + ("\n\n" + self.module_text if self.module_text else "")

    ASK = {
        "direct": ("The last line was said to you. Reply with exactly what you'll say. If it doesn't need a reply, "
                   "reply with just PASS: they told you to stop, shut up, be quiet, that's enough, or never mind "
                   "(PASS is how you go quiet - never say \"okay\" or \"stopping\"), or they're done, or they've "
                   "turned to someone else."),
        "mentioned": ("Your name came up in the last line, but that doesn't mean it was said to you - people talk "
                      "about you, too: telling someone what you said earlier, complaining you've been glitchy, saying "
                      "they'll ask you later, telling a story you're in. Respond only if the line is clearly a "
                      "question or request for you, or clearly invites you in. If they're talking about you, or "
                      "to each other, or you're not sure, reply with just PASS and nothing else. Otherwise reply "
                      "with exactly what you'll say."),
        "join": ("Nobody spoke to you directly, and there's a pause in the conversation. Would you say something "
                 "right now? Go by your own sense of when to join in. {mood}\n\nIf you'll stay quiet, reply with "
                 "just PASS. If you'll speak, reply with SAY: followed by exactly the words you'd say out loud - "
                 "nothing before or after them. (Don't use ACTION here.)"),
    }

    @staticmethod
    def time_now(at: datetime | None = None) -> str:
        """The date and time for Claude. Between midnight and 4 AM nobody has slept yet, so "tomorrow" means
        the coming day, not the one after (owner, D-114; common/when.py waking_day): Claude is told so."""
        at = at or datetime.now()
        out = at.strftime("%A %B %-d %Y, %-I:%M %p")
        if at.hour < when.NIGHT_ENDS:
            out += (f" (after midnight: people haven't slept yet, so \"tomorrow\" means {at.strftime('%A')}, "
                    f"the coming day, and \"today\" or \"tonight\" can mean {(at - timedelta(days=1)).strftime('%A')})")
        return out

    @classmethod
    def user_text(cls, text: str, transcript: list[str] | None = None, now: str | None = None, schedule: str = "",
                  people: str = "", memories: str = "", kind: str = "direct", mood: str = "", looked_up: str = "") -> str:
        """What Claude reads: the time, who's here, what's set and remembered, the room conversation, and what's asked."""
        now = now or cls.time_now()
        head = "; ".join(x for x in (now, people, schedule, memories) if x)
        lines = list(transcript or [])
        if not lines and text:
            lines = [f"Someone: {text}"]
        ask = cls.ASK.get(kind, cls.ASK["direct"]).format(mood=mood)
        convo = "\n".join(lines) if lines else "(nothing yet)"
        look = f"\n\nYou asked to look this up first (now answer with it; don't look again): {looked_up}" if looked_up else ""
        return f"[{head}]\n\nThe conversation so far, oldest first:\n{convo}{look}\n\n{ask}"

    async def on_preview(self, msg: Message) -> Message:
        """"Try it" on the Settings page: one answer with an edited personality, as text. Nothing from the room
        (no transcript, memories or schedule), nothing spoken (no answer.sentence events) and no ACTION carried
        out: an ACTION line or PASS comes back as text."""
        aid = msg.data.get("answer_id", "")
        persona = (msg.private.get("personality") or "").strip() or personality.read()[0]
        question = (msg.private.get("question") or "").strip()[:500]
        if not question:
            return msg.reply({"error": "No question"}, source="thinker")

        async def nothing(*_):
            pass
        try:
            text, info = await self._ask(self.preview_claude, "preview", aid, await self.system_prompt(persona),
                                         self.user_text(question, [f"Someone: {question}"]), nothing, nothing)
        except Exception as e:
            log.warning("preview failed: %s", e)
            return msg.reply({"error": f"{type(e).__name__}: {str(e)[:120]}"}, source="thinker")
        if info.get("action"):
            text = info["action"].strip()
        elif info.get("passed"):
            text = "PASS"
        return msg.reply({"preview": True}, private={"text": text}, source="thinker")

    async def on_ask(self, msg: Message) -> Message:
        if msg.data.get("kind") == "preview":
            return await self.on_preview(msg)
        aid = msg.data.get("answer_id", "")

        async def sentence(s: str):
            await self.bus.emit("answer.sentence", {"answer_id": aid}, private={"text": s})

        async def searching():
            await self.bus.emit("answer.searching", {"answer_id": aid})

        kind = msg.data.get("kind", "direct")
        if kind == "join" and self.paused():   # usage limit reached: no joining in until it resets (D-69)
            return msg.reply({"passed": True, "paused": True}, private={"text": ""}, source="thinker")
        system = await self.system_prompt()
        user = self.user_text(msg.private.get("text", ""), msg.private.get("transcript"),
                              schedule=msg.private.get("schedule", ""), people=msg.private.get("people", ""),
                              memories=msg.private.get("memories", ""), kind=kind, mood=msg.private.get("mood", ""),
                              looked_up=msg.private.get("looked_up", ""))
        try:
            text, info = await self._ask(self.claude, kind, aid, system, user, sentence, searching, held=kind == "join")
        except Exception as e:
            log.warning("answer failed: %s", e)
            return msg.reply({"error": f"{type(e).__name__}: {str(e)[:120]}"}, source="thinker")
        if info.get("is_error") and not text and not info.get("action"):
            # The CLI answered with an error (a usage limit, …) rather than words: say so, don't go quiet
            return msg.reply({"error": "Claude reported an error"}, source="thinker")
        self.remember_request(kind, system, msg, user, info)
        info.pop("usage", None)            # the token counts stay here, for the page (not in the reply or the trace)
        action = info.pop("action", None)  # what was asked for: private, like everything people say (D-34)
        info["action"] = bool(action)
        if info.get("passed"):
            text = ""
        private = {"text": text} | ({"action": action} if action else {})
        return msg.reply({k: v for k, v in info.items() if v is not None}, private=private, source="thinker")

    async def on_cancel(self, msg: Message):
        if msg.data.get("answer_id"):
            self.cancelled.add(msg.data["answer_id"])
        self.claude.cancel(msg.data.get("answer_id"))
        self.preview_claude.cancel(msg.data.get("answer_id"))
        return {"ok": True}

    async def keep_warm(self) -> None:
        """Keep a Claude process started and waiting, so the next answer starts faster."""
        while True:
            try:
                await self.claude.prepare(await self.system_prompt())
            except Exception as e:
                log.warning("couldn't pre-start Claude: %s", e)
            self.show_usage()   # a new day, or a pause that has ended
            await asyncio.sleep(20)


async def main() -> None:
    bus, cfg = make_bus("thinker")
    th = Thinker(bus, cfg)
    bus.on("thinker.ask", th.on_ask)
    bus.on("thinker.cancel", th.on_cancel)
    bus.on("thinker.prompt", th.on_prompt)   # the dashboard's "What Pip sends" page
    bus.status["stage"] = "Claude via Claude Code (Pro plan)"
    await serve(bus, th.keep_warm)


def run() -> None:
    start("thinker", main)


if __name__ == "__main__":
    run()
