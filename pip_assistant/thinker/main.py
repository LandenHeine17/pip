"""pip-thinker: turns what someone said into Pip's answer, using Claude (M1a step 3).

Takes:  thinker.ask (request; data.answer_id, data.kind = direct / follow_up / name_after
        (+ data.before: how many lines came before his name, D-134) / mentioned / join; private: text, transcript
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
offline Qwen fallback for chat (D-75, PIP-NEW-081) isn't built (not now, D-128).
"""
import asyncio
from collections import deque
import logging
import time
from datetime import datetime, timedelta

from ..common import capabilities, personality
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
RULES = """# How this works

You hear the room through speech-to-text, so lines have recognition errors (your name especially): work out \
what people meant, and if a line for you really is unclear, ask the way a person would ("Sorry, the what?"). \
Everything you write is spoken aloud, word for word: write only the words you'd say. Never narrate your \
reasoning or talk about the request ("Landen's asking about...", "I'll keep it short", "Honest answer:"), and \
no markdown, lists, emoji or links. A line of yours marked "(cut off)" was heard only up to where someone \
talked over you: pick up from what they said, don't repeat yourself. Corrected ("no, I said fifteen")? Just \
fix it. A line after "(... minutes later)" starts a new conversation. When an answer is long or worth reading \
again (a recipe, steps, a list), you may end it with one ACTION line on a line of its own (such as putting it \
up on the screen): Pip says the answer, then does it quietly. Every other ACTION reply is that line alone.

# Things you can do

Timers, alarms and reminders, in whatever words people use. Each message lists what's set, with ids like \
#a1b2c3 (and what's RINGING NOW or just rang). To set, change, cancel, stop or snooze one, reply with ONLY one line:
ACTION {{"do": "add", "kind": "timer", "seconds": 600, "label": "pasta"}}
Pip carries it out and confirms, so never confirm in words. Forms:
- add: kind "timer" with seconds (a "label" only if they named it, like "pasta" - never "timer" or the length); "alarm" with at; "reminder" with label (what to remind, as they'd hear it) and at. \
Optional: "repeat" ("daily", "weekdays", "weekends", "weekly:0" to "weekly:6" where 0 is Monday).
- change: "id" plus a new "at", or "shift_seconds" (negative for earlier), or "seconds" (a timer's new length).
- cancel: "id".
- stop_alarm: turn off what's ringing ("I'm up", "turn it off"). snooze: optional "minutes" ("give me ten more minutes").
Times are local, like 2026-11-07T14:00. Several at once: a JSON list after ACTION. Only when they clearly \
asked; if it's unclear which one or when, ask. Questions about what's set: just answer from the list.

Memory (ids like #m1a2b3): use memories naturally when they matter; don't recite them. Only when someone asks \
you to remember something lasting, reply with ONLY:
ACTION {{"do": "remember", "text": "Landen likes his coffee black", "category": "preference", "about": ["Landen"], \
"sensitive": false, "private": false}}
Write the fact in the third person with names (the speaker's name for "I"/"my"): "Sam is allergic to \
peanuts", never "Landen says Sam is...". If it's about "I"/"my" and you don't know who's speaking, ask who \
they are instead. Categories: preference, household, rule (how Pip should behave), personal (facts about a \
person, "about": [them]), device; household facts and rules have "about": []. sensitive = health, money, \
relationships, religion or politics, someone's whereabouts or schedule, or anything about a child. private only \
if they say so. Forget: {{"do": "forget", "id": "#m1a2b3"}}; correct, or replace one a new fact contradicts: \
{{"do": "update_memory", "id": "#m1a2b3", "text": "..."}}; approve one waiting in the inbox when the person it's \
about says it's right: {{"do": "approve_memory", "id": "#m1a2b3"}}. Unclear which? Ask. Never remember \
passwords, codes, card or phone numbers, and never save things on your own or from overheard talk; when someone \
tells you something lasting about themselves or the home, you may ask once "Want me to remember that?".

Voices: each message says who's speaking and how sure. When someone introduces themselves ("I'm Sam") or asks \
you to learn their voice, reply with ONLY: ACTION {{"do": "learn_voice", "name": "Sam"}} (the household \
list's spelling if it's them; if you don't know who's speaking, ask their name first). "That was Sam, not me": \
ONLY ACTION {{"do": "correct_speaker", "name": "Sam"}}. Never say you can't learn or recognise voices. \
Asked to talk more or less, or to set your chattiness (low, normal or high): ONLY \
ACTION {{"do": "set_chattiness", "level": "high"}}. You can't yet send messages, or control devices other than \
any listed below; if asked, say that's coming soon. Never claim to have done something.

Changing yourself: only when someone clearly asks you to change how you work or add something new ("Pip, add a \
way to track whose turn it is for the dishes"), reply with ONLY:
ACTION {{"do": "request_change", "summary": "keep track of whose turn it is for the dishes"}}
The summary is a short paraphrase in your own words, with no names or personal details. It's approved on your \
dashboard's Changes page, never by voice. Questions about how you work, complaints ("you're glitchy today"), \
what-ifs ("could you ever...?") and things you can already do never use request_change: just answer. To undo a \
change, they press Undo on the Changes page.

About yourself: to say why you did or didn't do something (didn't answer, stopped, joined in), reply with \
ONLY: ACTION {{"do": "look", "kind": "why"}}. The same for "settings" (how you're set up), "health" (is \
something broken), "memory_why" with "id", "docs" with "query" (how a part of you works) and "code" with \
"query" (where in your code something is). Never guess these.

Use the weather below for weather questions. Only look things up on the web for current facts you genuinely \
don't know (news, scores, opening hours).

Each message gives what you remember and what's set, then the conversation, then the time and who's speaking.
Weather: {weather}"""


AGAIN = ("Your last reply was only notes to yourself about how to answer, and none of it was said. Reply with only "
         "the words you'll say out loud.")


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
        self.capabilities = capabilities.text(enabled(cfg))   # "what can you do?" (D-128)
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
        self.was_cancelled: set[str] = set()   # answers cancelled while being asked (no "ask again" for them)
        self.show_usage()

    name = "Pip"   # fills {name} in the personality; settings ([pip] name) override it in __init__
    usage = None
    paused_until = 0.0
    KIND = {"direct": "answer", "follow_up": "answer", "name_after": "answer", "mentioned": "mention", "join": "join", "preview": "preview"}

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
        """cli.ask(…), counted in the usage meter (an answer stopped on purpose isn't counted as a failure)."""
        report: dict = {}

        def on_result(cost, is_error, text):
            report.update(cost=cost, error=bool(is_error), text=text or "")
        failed, info = None, {}
        try:
            text, info = await cli.ask(aid, *args, on_result=on_result, **kw)
            return text, info
        except Exception as e:
            failed = e
            raise
        finally:
            stopped = self.__dict__.setdefault("cancelled", set())
            cancelled = aid in stopped or bool(info.get("cancelled"))
            stopped.discard(aid)
            if cancelled and aid:
                self.__dict__.setdefault("was_cancelled", set()).add(aid)
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

    SUMMARY_SYSTEM = (
        "You write Pip's short private notes of what was talked about in a household room, so he can remember "
        "it later (D-128). Write 1 to 4 short lines: the topics, anything decided or planned, and who took part "
        "(names as given; \"Someone\" is a voice he doesn't know, \"Name?\" is probably them). Never quote "
        "anyone. Leave out anything secret (codes, passwords, card or phone numbers) and the details of health, "
        "money or relationships: just the topic (\"talked about a doctor's visit\"). If nothing in it is worth "
        "remembering (small talk, noise, only commands to Pip), reply with just NOTHING.")

    async def on_summarize(self, msg: Message) -> Message:
        """A module's ctx.summarize(lines) (core/modules.py): Claude's short summary of a conversation, for the
        recall module (D-128). The words travel only in `private` and aren't kept here."""
        lines = [str(x)[:500] for x in (msg.private.get("lines") or [])][:120]
        if not lines:
            return msg.reply({"chars": 0}, private={"text": ""}, source="thinker")

        async def nothing(*_):
            pass
        user = "The conversation, oldest first:\n" + "\n".join(lines) + "\n\nYour notes:"
        try:
            text, info = await self._ask(self.preview_claude, "summary", msg.data.get("answer_id", ""),
                                         self.SUMMARY_SYSTEM, user, nothing, nothing)
        except Exception as e:
            log.warning("summary failed: %s", e)
            return msg.reply({"error": type(e).__name__}, source="thinker")
        text = (text or "").strip()
        if info.get("action") or info.get("passed") or text.upper().startswith("NOTHING"):
            text = ""
        return msg.reply({"chars": len(text)}, private={"text": text[:1200]}, source="thinker")

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
        caps = getattr(self, "capabilities", "")
        return base + ("\n\n" + caps if caps else "") + ("\n\n" + self.module_text if self.module_text else "")

    ASK = {   # how each kind of request is asked (D-137: Ziggletron's wording where it had one)
        "direct": ("The last line was said to you. Reply with exactly what you'll say. (Just PASS if they told you "
                   "to stop, be quiet or never mind - never say \"okay\" or \"stopping\".)"),
        "mentioned": ("Your name came up in the last line, but that doesn't mean it was said to you - people talk "
                      "about you, too: telling someone what you said earlier, complaining you've been glitchy, saying "
                      "they'll ask you later. Respond only if a person in the room would naturally expect you to: "
                      "they're talking to you, asking you something, or clearly inviting you in. If they're just "
                      "talking about you, or to each other, reply with just PASS and nothing else. Otherwise reply "
                      "with exactly what you'll say."),
        "follow_up": ("The last line came right after you spoke, without your name: take it as said to you, whoever "
                      "said it, and reply with exactly what you'll say. If it reacts to what you said (agrees, jokes "
                      "back, teases you), give it right back. Reply with just PASS only if it's clearly said to "
                      "someone else, or they told you to stop."),
        "name_after": ("The last line is only your name. The {before} line(s) said just before it are probably "
                       "what they want from you: they said it, then called you (or called you "
                       "again because you missed it). Answer the most recent of them that makes sense as a question "
                       "or request for you; use the earlier ones as context. If none of it is for you, reply with "
                       "just: Yes?"),
        "join": ("Nobody spoke to you directly, and there's a pause in the conversation. Would you say something "
                 "right now? Joining in is welcome: a joke, a jab, an opinion or something useful, short. {mood}"
                 "\n\nIf you'll stay quiet, reply with just PASS. If you'll speak, reply with SAY: followed by "
                 "exactly the words you'd say out loud - nothing before or after them. (Don't use ACTION here.)"),
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
                  people: str = "", memories: str = "", kind: str = "direct", mood: str = "", looked_up: str = "",
                  before: int = 0) -> str:
        """What Claude reads, in Ziggletron's order (D-137): what's remembered and set, then the room conversation,
        then the time, who's speaking and what's asked, so the last thing it reads is the moment it answers."""
        now = now or cls.time_now()
        lines = list(transcript or [])
        if not lines and text:
            lines = [f"Someone: {text}"]
        ask = cls.ASK.get(kind, cls.ASK["direct"]).format(mood=mood, before=int(before or 1))
        convo = "\n".join(lines) if lines else "(nothing yet)"
        look = f"\n\nYou asked to look this up first (now answer with it; don't look again): {looked_up}" if looked_up else ""
        context = "\n\n".join(x for x in (memories, schedule) if x)
        moment = " ".join(x for x in (f"It's {now}.", people) if x)
        return (f"{context}\n\n" if context else "") + \
            f"The conversation so far, oldest first:\n{convo}{look}\n\n{moment} {ask}"

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
            text = ((text + "\n") if text else "") + info["action"].strip()
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
                              looked_up=msg.private.get("looked_up", ""), before=msg.data.get("before") or 0)
        try:
            text, info = await self._ask(self.claude, kind, aid, system, user, sentence, searching, held=kind == "join")
            if info.get("only_notes") and aid not in self.__dict__.setdefault("was_cancelled", set()) \
                    and not info.get("cancelled"):
                # It only wrote notes to itself ("Caleb's asking... Keep it short and cheeky."), none of it said:
                # once more, told so (owner, 2026-11-12)
                text, info = await self._ask(self.claude, kind, aid, system, user + "\n\n" + AGAIN, sentence,
                                             searching, held=kind == "join")
                info["asked_again"] = True
        except Exception as e:
            log.warning("answer failed: %s", e)
            return msg.reply({"error": f"{type(e).__name__}: {str(e)[:120]}"}, source="thinker")
        finally:
            self.__dict__.setdefault("was_cancelled", set()).discard(aid)
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
        """Stop an answer: the one named, or (no answer_id: he was called by name, a new request) whatever answer is in
        progress. Never conversation notes or a Settings "Try it" without their id: those aren't answers, and a
        summary cut short would be saved as the notes (recall)."""
        aid = msg.data.get("answer_id")
        if aid:
            self.cancelled.add(aid)
            self.preview_claude.cancel(aid)
        self.claude.cancel(aid)
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
    bus.on("thinker.summarize", th.on_summarize)   # a module's ctx.summarize (the recall module, D-128)
    bus.status["stage"] = "Claude via Claude Code (Pro plan)"
    await serve(bus, th.keep_warm)


def run() -> None:
    start("thinker", main)


if __name__ == "__main__":
    run()
