"""What Pip tells Claude about the household with each question, and what core/actions.py needs to act.

Part of the conversation (core/conversation.py): `Conversation` takes these methods in as `Context`, so they
read its fields like its own. Everything here is shaped by who's asking: a module only adds what that speaker
may hear, and memories are only those this person may be told (D-34, core/memory.py).

    for_claude    the schedule and each module's part (calendar, to-dos, …), who lives here and who's
                  talking, their memories, and for a join-in his mood. Sent with `thinker.ask`
                  (Conversation.answer); the room transcript and the question itself are added there.
                  Memories come in two parts (D-146): `memories_fixed`, the ones that go whoever is talking
                  (pip-thinker puts them in the fixed part of what Claude is told, which Claude caches), and
                  `memories`, the ones about the people in this conversation.
    _action_ctx   what core/actions.py and core/commands.py need to check and do actions, as who asked
"""
import re

from .joining import MOODS


class Context:
    def for_claude(self, sp, kind: str) -> dict:
        """{schedule, people, memories} for Claude, as speaker `sp` (None when joining in: nobody asked), and
        the mood for a join-in (how chatty he is, D-136)."""
        schedule = self.schedule.summary() if self.schedule else ""
        if self.modules is not None:   # each module's part: the calendar, to-dos, … (only what this speaker may hear)
            from .modules import Call
            schedule = " ".join(x for x in (schedule, self.modules.summary(Call(speaker=sp, kind=kind))) if x)
        people = self.people.summary(sp) if self.people is not None else ""
        memories = self.memory.for_claude(sp.person_id if sp else None, sp.level if sp else "unknown",
                                          split=True, **self.who_is_present(sp)) \
            if self.memory is not None else ""
        # The memories that go whoever is talking (rules, household facts): pip-thinker keeps them in the fixed,
        # cached part of what Claude is told (D-146). Sent with every request so it always has the current ones.
        fixed = self.memory.fixed_for_claude() if self.memory is not None else ""
        if kind == "join":   # nobody spoke to him: just who lives here, and how chatty he is
            return {"schedule": schedule, "people": people.split(". ", 1)[-1], "memories": memories,
                    "memories_fixed": fixed, "mood": MOODS[self.joining.mood]}
        return {"schedule": schedule, "people": people, "memories": memories, "memories_fixed": fixed}

    def who_is_present(self, sp) -> dict:
        """Who's part of the conversation (D-137): the speaker, and anyone whose name is in the room transcript
        (as a speaker or talked about). Memories about anyone else aren't sent, like Ziggletron."""
        if self.people is None:
            return {}
        talk = "\n".join(self.transcript.render())
        everyone = self.people.all()
        present = {p["id"]: p["name"] for p in everyone
                   if p.get("name") and re.search(rf"\b{re.escape(p['name'])}\b", talk, re.I)}
        if sp is not None and getattr(sp, "person_id", None):
            present[sp.person_id] = sp.name
        return {"present": present, "household": [p["name"] for p in everyone if p.get("name")]}

    def _action_ctx(self, speaker=None) -> dict:
        """What core/actions.py needs to check and do actions, as `speaker` (who asked; default: who's talking)."""
        from .modules import Call
        sp = speaker if speaker is not None else self.speaker
        return {"memory": self.memory, "people": self.people, "speaker": sp, "modules": self.modules,
                "call": Call(speaker=sp, now=self.commands.now()) if self.commands else Call(speaker=sp)}
