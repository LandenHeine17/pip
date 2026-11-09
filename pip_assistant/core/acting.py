"""Doing things for people: Pip's own commands, and the ACTIONs Claude asks for.

Part of the conversation (core/conversation.py): `Conversation` takes these methods in as `Acting`, so
they read and set its fields like its own.

    local_command     Pip's own fixed-phrasing commands, on the Pi with no AI (core/commands.py; offline,
                      or if Claude fails, D-83, D-94)
    people_command    "I'm Sam", "learn my voice", "who am I?", "remember…" offline (core/peoplecmds.py)
    claude_action     Claude replied with an ACTION: check all of it, then do all of it or none (D-84).
                      Ringing alarms, voice learning and change requests are done here; the rest by
                      core/actions.py.
    request_change    asking Pip to change himself (M1b, PIP-NEW-069): passed on to pip-builder
    look_up           Claude asked to see something first (ACTION look, D-102): fetch it and ask again
"""
import json
import logging
import time

from ..common.messages import Message

log = logging.getLogger(__name__)


class Acting:
    async def local_command(self, text: str, speaker=None) -> bool:
        """Pip's own commands, done on the Pi with no AI, as `speaker` (who asked). True if it was one."""
        if not self.commands:
            return False
        t0 = time.perf_counter()
        try:
            reply = await self.commands.handle(text, self._action_ctx(speaker)["call"])
        except Exception as e:
            log.exception("command failed")
            await self.bus.publish(Message("event", "command.failed", data={"error": type(e).__name__}))
            await self.say(self.SAYINGS["command_failed"], "error")
            return True
        if reply is None:
            return False
        self.answer_id = None
        await self.command("thinker", "thinker.cancel")
        await self.bus.publish(Message("event", "command.done", data={"seconds": round(time.perf_counter() - t0, 3),
                                                                      "spoken": bool(reply)}))
        if not reply:  # "stop", "never mind", or answering an alarm: just go quiet
            await self.command("ears", "ears.stop")
            return True
        await self.speak_reply(reply, "command", t0, digits=3)
        return True

    async def people_command(self, text: str, offline: bool = False, asker: tuple | None = None) -> bool:
        """Offline (or if Claude fails): 'I'm Sam', 'learn my voice', 'who am I?', 'remember…' (core/peoplecmds.py)."""
        if not self.people_cmds:
            return False
        speaker, pcm = asker or (self.speaker, self.last_pcm)
        try:
            reply = await self.people_cmds.handle(text, speaker, pcm, offline)
        except Exception:
            log.exception("people command failed")
            reply = self.SAYINGS["command_failed"]
        if reply is None:
            return False
        self.answer_id = None
        await self.command("thinker", "thinker.cancel")
        await self.bus.publish(Message("event", "command.done", data={"kind": "people", "spoken": True}))
        from .peoplecmds import StartLearning
        if isinstance(reply, StartLearning):
            await self.start_learning(reply, pcm)
            return True
        await self.speak_reply(reply, "command")
        return True

    async def claude_action(self, raw: str, t0: float, again: tuple | None = None, asker: tuple | None = None) -> None:
        """Claude turned a request into actions; Pip checks them, does them and confirms (D-84, D-94).
        Ringing alarms and voice learning are handled here (they need the alarm and the audio);
        the rest by core/actions.py. Anything invalid means nothing at all is done.
        A look-up ("look", D-102) isn't a change: Pip fetches it and asks Claude again, once (`again`)."""
        from . import actions
        from .peoplecmds import StartLearning
        asker = asker or (self.speaker, self.last_pcm)
        speaker, pcm = asker
        said, done, learning = [], 0, None
        try:
            items = actions.parse(raw)
            looks = [a for a in items if a.get("do") == "look"]
            if looks:
                return await self.look_up(looks, again, t0, asker)

            here = [a for a in items if a.get("do") in actions.HERE]
            rest = [a for a in items if a.get("do") not in actions.HERE]
            plan = actions.check_here(here)
            if rest:
                actions.check(self.commands, rest, self._action_ctx(speaker))   # validate before doing anything
            for step in plan:
                if step[0] == "stop_alarm":
                    n = await self.schedule.acknowledge("claude")
                    said.append("" if n else "Nothing's ringing.")
                elif step[0] == "snooze":
                    n = await self.schedule.snooze(step[1], how="claude")
                    from ..common import when
                    said.append(f"Snoozed for {when.say_duration((step[1] or self.schedule.snooze_min) * 60)}." if n
                                else "Nothing's ringing.")
                elif step[0] == "request_change":
                    text, ok = await self.request_change(step[1], speaker)
                    said.append(text)
                    done += ok
                    continue
                elif step[0] == "learn_voice":
                    r = await self.people_cmds.introduced(step[1], True, speaker, pcm) \
                        if self.people_cmds else "I can't recognise voices yet."
                    if isinstance(r, StartLearning):
                        learning = r
                    else:
                        said.append(r)
                done += 1
            if rest:
                s, n = await actions.apply_async(self.commands, json.dumps(rest), self._action_ctx(speaker))
                said.append(s)
                done += n
        except Exception as e:
            if not isinstance(e, (actions.Invalid, ValueError, TypeError, KeyError)):
                log.exception("action failed")
            said, done, learning = [actions.FAILED], 0, None
        await self.bus.publish(Message("event", "action.done", data={"count": done, "ok": bool(done)}))
        text = " ".join(s for s in said if s)
        if text:
            await self.speak_reply(text, "command", t0)
        if learning:
            await self.start_learning(learning, pcm)

    async def request_change(self, summary: str, speaker) -> tuple[str, bool]:
        """Someone asked Pip to change how he works (ACTION request_change, PIP-NEW-069, AC-SM-01). Only a voice
        he recognises for sure, and not a guest's, may ask; pip-builder then writes a plan for the dashboard's
        Changes page, where the owner approves it (voice never approves anything). Claude's paraphrase travels
        only in the private part, so it's never in the trace. Returns (what to say, whether it was passed on)."""
        if speaker is None or speaker.level != "sure" or speaker.guest or not speaker.person_id:
            await self.bus.publish(Message("event", "change.refused", data={"why": "voice not recognised"}))
            return self.SAYINGS["change_not_you"], False
        try:
            r = await self.bus_request("builder", "builder.ask",
                                       {"source": "voice", "asked_by": speaker.label, "person_id": speaker.person_id},
                                       {"text": summary}, timeout=10)
            ok = not r.data.get("error")
        except Exception:   # pip-builder not running, or too slow
            ok = False
        if not ok:
            await self.bus.publish(Message("event", "change.refused", data={"why": "builder unavailable"}))
            return self.SAYINGS["change_unavailable"], False
        return self.SAYINGS["change_asked"], True

    async def look_up(self, looks: list[dict], again: tuple | None, t0: float, asker: tuple | None = None) -> None:
        """Claude asked to see more first (the calendar for next Friday, …): fetch it and ask again."""
        from . import actions
        found = []
        asker = asker or (self.speaker, self.last_pcm)
        if again is not None:
            ctx = self._action_ctx(asker[0])
            for a in looks[:3]:
                try:
                    _, act, plan, call = actions.check_module_action(f"look:{a.get('kind')}", a, ctx, ctx["call"].now)
                    found.append(act.do(plan, call))
                except Exception as e:
                    if not isinstance(e, (actions.Invalid, ValueError, TypeError, KeyError)):
                        log.exception("look-up failed")
        await self.bus.publish(Message("event", "action.looked", data={"count": len(found), "again": again is not None}))
        if not found:   # a second look-up, or nothing to look at: don't go round in circles
            text = "Sorry, I couldn't find that."
            await self.speak_reply(text, "command", t0)
            return
        text, reason, kind, mood = again
        await self.answer(text, reason, kind=kind, mood=mood, looked_up=" ".join(found), asker=asker)
