"""People and voices without Claude: used **offline** (D-94), and by Claude's learn_voice action.

Online, "I'm Landen", "learn my voice" and "who am I?" are understood by Claude in any wording, which
replies with ACTION {"do": "learn_voice", "name": …} (or just answers); `introduced` then does the work.

    "Pip, I'm Landen" / "my name is Landen"   someone on the household list: Pip starts learning their voice
                                              (sentences to read pop up on the dashboard); already learned:
                                              one more sample. Someone new: remembered for this visit only.
    "Pip, learn my voice"                      the same; if Pip doesn't know who's talking he asks their name
    (names are matched the way they sound, so "I'm Landon" finds Landen, D-93)
    "Pip, who am I?"           who Pip thinks is talking
    offline "remember that…"   goes to the memory inbox, since Claude can't word and sort it right now

Returns what to say, a StartLearning (Conversation then runs the voice learning), or None when it
isn't one of these.
"""
import re
import time
from dataclasses import dataclass

WHO = re.compile(r"\b(who am i|do you know who i am|do you recogni[sz]e (me|my voice)|whose voice is this|who'?s talking)\b", re.I)
LEARN = re.compile(r"\b((learn|remember|know|recogni[sz]e|save|record|get|train on) (my|our|me by my) voice|"
                   r"(re-?learn|retrain|update) my voice|learn (who i am|to recogni[sz]e me|me)|voice (learning|training))\b", re.I)
# "I'm Sam", "my name is Sam", "hey Pip, this is Sam, learn my voice". Names are capitalised by speech-to-text.
INTRO = re.compile(r"\b(?i:i'?m|i am|my name is|my name'?s|this is|it'?s|call me)\s+([A-Z][a-z]+)\b")
NAME_ONLY = re.compile(r"^\W*(?i:(?:it'?s|i'?m|i am|this is|my name is|my name'?s)\s+)?([A-Z][a-z]+)\W*$")
REMEMBER = re.compile(r"^\W*(?:(?:hey )?(?:pip|pippletron|pippy)\W+)?(?:please |can you |could you )?remember(?: that)? (.{3,})$", re.I)
NOT_NAMES = {"Pip", "Pippletron", "Pippy", "Back", "Home", "Here", "Done", "Fine", "Okay", "Good", "Ready", "Up", "Awake",
             "Me", "Just", "Not", "Sorry", "Sure", "Going", "Still", "So", "Really", "Very", "The", "That", "This", "It",
             "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Today", "Tomorrow",
             "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December", "Christmas", "Halloween", "Thanksgiving", "Yes", "No", "Hi", "Hello", "Hey"}


@dataclass
class StartLearning:
    """Start guided voice learning for this person: Pip says `say`, then beeps for the first sentence."""
    person_id: str
    name: str
    say: str


def _name(text: str) -> str | None:
    words = re.findall(r"[\w']+", text)
    m = INTRO.search(text)
    if not m or m.group(1) in NOT_NAMES:
        return None
    # A short introduction, or one that asks Pip to learn the voice ("…it's Sam, can you learn my voice?")
    return m.group(1) if len(words) <= 8 or LEARN.search(text) else None


READ_THEM = "Read the sentences on my dashboard out loud, one after each beep."


class PeopleCommands:
    def __init__(self, people, memory=None):
        self.people, self.memory = people, memory
        self.awaiting_name_until = 0.0   # Pip just asked "Who am I learning?"

    async def handle(self, text: str, speaker, pcm: bytes, offline: bool = False) -> str | None:
        p = self.people
        if WHO.search(text):
            if speaker and speaker.level == "sure":
                return f"You sound like {speaker.name}."
            if speaker and speaker.level == "probably":
                return f"I think you're {speaker.name}, but I'm not sure."
            if not p.embedder.available:
                return "I can't recognise voices yet: the voice model isn't installed."
            return "I don't recognise your voice yet. Say \"Pip, I'm\" and your name, and I'll learn it."
        learn = bool(LEARN.search(text))
        name = _name(text)
        if not name and time.monotonic() < self.awaiting_name_until and (m := NAME_ONLY.match(text.strip())) \
                and m.group(1) not in NOT_NAMES:
            name, learn = m.group(1), True          # the answer to "Who am I learning?"
        if name:
            self.awaiting_name_until = 0.0
            return await self.introduced(name, learn, speaker, pcm)
        if learn:
            if not p.embedder.available:
                return "I can't recognise voices yet: the voice model isn't installed."
            if speaker and speaker.level == "sure" and speaker.person_id and not speaker.guest:
                return StartLearning(speaker.person_id, speaker.name,
                                     f"Okay {speaker.name}, let's learn your voice better. " + READ_THEM)
            self.awaiting_name_until = time.monotonic() + 30
            return "Sure! Who am I learning? Say your name."
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

    async def introduced(self, name: str, learn: bool, speaker, pcm: bytes):
        p = self.people
        person = p.by_name(name)
        if person is None:
            # Not on the household list: remembered for this visit only (D-89). Adding people is the dashboard's job
            # (voice is never enough for anything administrative, PIP-NEW-027).
            hint = " If you live here, you can be added on my dashboard, then I'll learn your voice properly."
            if await p.add_guest(name, pcm):
                return f"Nice to meet you, {name}! I'll remember your voice for this visit." + (hint if learn else "")
            return f"Nice to meet you, {name}!" + (hint if learn else "")
        name = person["name"]
        if speaker and speaker.level == "sure" and not speaker.guest and speaker.person_id != person["id"]:
            return f"You sound like {speaker.name} to me. If you're {name}, use Learn my voice on the dashboard."
        if not p.embedder.available:
            return f"Hi {name}! I can't learn voices yet: the voice model isn't installed."
        has_voice = any(x["id"] == person["id"] and x["voice_samples"] for x in p.all())
        it_is_them = speaker and speaker.person_id == person["id"] and speaker.level in ("sure", "probably")
        if not has_voice:
            return StartLearning(person["id"], name, f"Hi {name}! Let's learn your voice. " + READ_THEM)
        if learn and speaker and speaker.person_id == person["id"] and speaker.level == "sure":
            return StartLearning(person["id"], name, f"Okay {name}, let's learn your voice better. " + READ_THEM)
        if it_is_them:
            n = await p.add_samples(person["id"], [pcm], "introduction")
            return f"Hi {name}! I'll get better at recognising you." if n else f"Hi {name}!"
        # Their voice is learned but this doesn't sound like them: don't let anyone add to someone else's voice
        return (f"Hi {name}! You don't sound like you to me today. "
                "To re-learn your voice, press Learn my voice next to your name on my dashboard.")
