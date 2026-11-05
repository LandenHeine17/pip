"""Pip's own commands about people and voices, done on the Pi (they work offline too).

    "Pip, leave me out"        your room speech stays on the Pi from now on (D-77); guests: this visit only
    "Pip, include me again"    undo that
    "Pip, I'm Sam"             a household member: one more voice sample; someone new: remembered for this visit
    "Pip, who am I?"           who Pip thinks is talking
    offline "remember that…"   goes to the memory inbox, since Claude can't word and sort it right now

Returns what to say, or None when it isn't one of these.
"""
import re

LEAVE_OUT = re.compile(r"\b(leave me out|opt me out|don'?t (listen to|send) me|stop listening to me|"
                       r"don'?t send my (voice|speech))\b", re.I)
INCLUDE = re.compile(r"\b(include me( again| back)?|opt me (back )?in|you can listen to me( again)?)\b", re.I)
WHO = re.compile(r"\b(who am i|do you know who i am|do you recogni[sz]e (me|my voice)|whose voice is this|who'?s talking)\b", re.I)
INTRO = re.compile(r"^\W*(?i:(?:(?:hey|hi|hello|ok|okay)\W+)?(?:(?:pip|pippletron|pippy)\W+)?(?:(?:hi|hello)\W+)?"
                   r"(?:i'?m|i am|my name is|my name'?s|this is|it'?s)\s+)([A-Z][a-z]+)\W*$")
REMEMBER = re.compile(r"^\W*(?:(?:hey )?(?:pip|pippletron|pippy)\W+)?(?:please |can you |could you )?remember(?: that)? (.{3,})$", re.I)
NOT_NAMES = {"Pip", "Pippletron", "Back", "Home", "Here", "Done", "Fine", "Okay", "Good", "Ready", "Up", "Awake", "Me", "Just"}


class PeopleCommands:
    def __init__(self, people, memory=None):
        self.people, self.memory = people, memory

    async def handle(self, text: str, speaker, pcm: bytes, offline: bool = False) -> str | None:
        p = self.people
        if LEAVE_OUT.search(text):
            if speaker and speaker.level != "unknown" and speaker.person_id:
                if speaker.guest:
                    p.guests[speaker.person_id]["opted_out"] = True
                    return "Okay, I'll leave you out for this visit."
                p.update_person(speaker.person_id, opted_out=True)
                return f"Okay {speaker.name}, I'll leave you out. What you say in the room stays on the Pi. You can still call me by name."
            if not p.embedder.available:
                return "I can't recognise voices yet, so I can't leave just you out."
            if await p.add_guest("Guest", pcm, opted_out=True):
                return "Okay, I'll leave you out for this visit."
            return "Say that again a bit longer, so I can learn your voice."
        if INCLUDE.search(text):
            if speaker and speaker.person_id and speaker.level != "unknown":
                if speaker.guest:
                    p.guests[speaker.person_id]["opted_out"] = False
                else:
                    p.update_person(speaker.person_id, opted_out=False)
                return f"Okay {speaker.name}, you're back in."
            return "I don't recognise your voice, so you weren't left out."
        if WHO.search(text):
            if speaker and speaker.level == "sure":
                return f"You sound like {speaker.name}."
            if speaker and speaker.level == "probably":
                return f"I think you're {speaker.name}, but I'm not sure."
            return "I don't recognise your voice yet." if p.embedder.available else "I can't recognise voices yet."
        m = INTRO.match(text.strip())
        if m and m.group(1) not in NOT_NAMES:
            name = m.group(1)
            person = p.by_name(name)
            if person:
                if speaker and speaker.level == "sure" and speaker.person_id != person["id"]:
                    return f"You sound like {speaker.name} to me. If you're {person['name']}, use Learn my voice on the dashboard."
                n = await p.add_samples(person["id"], [pcm], "introduction")
                return f"Hi {person['name']}! I'll get better at recognising you." if n else f"Hi {person['name']}!"
            if await p.add_guest(name, pcm):
                return f"Nice to meet you, {name}! I'll remember your voice for this visit."
            return f"Nice to meet you, {name}!"
        if offline and self.memory is not None and (m := REMEMBER.match(text.strip())):
            from .memory import Refused
            who = speaker.name if speaker and speaker.level == "sure" else "someone"
            try:
                _, outcome = self.memory.add(m.group(1).rstrip(" .!"), "household", pending_because="said while offline",
                                             source=f"{who}, by voice (offline)")
            except Refused:
                return "I can't keep passwords, card or phone numbers in my memory."
            return "I already knew that." if outcome == "duplicate" else \
                "I'm offline, so I put that in the memory inbox to check later."
        return None
