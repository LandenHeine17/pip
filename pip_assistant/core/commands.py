"""Pip's own commands: understood on the Pi, without Claude. **For speech, used only offline** (D-94).

Online, everything people say goes to Claude in natural language, and Claude acts through ACTION
lines (core/actions.py). These rules are the fallback when the internet is down (PIP-NEW-044,
D-83): timers, alarms, reminders, "what time is it", "stop", and what modules add (the calendar). What they still do
online: `while_ringing` (the obvious "stop", "okay", "snooze" or his name silence a ringing alarm
instantly; anything else said to Pip while it rings goes to Claude) and the dashboard's box for typing
a command. Replies are short (D-79). If a cancel matches nothing he says so; if it could mean
several, he asks which (FEAT-042).

This file only *understands* the words (the phrase rules below, and the questions Pip asks back,
"For how long?"). Making, changing and cancelling timers, alarms and reminders, wording the reply,
and what "that" and "it" mean are core/alerts.py's job (`self.alerts`), which Claude's actions use
too. A module adds its own phrases, and its items with a time join the shared "move it" and "cancel
that" rules, through Module.offline() (core/modules.OfflineItems; the calendar's are in
modules/calendar/offline.py). Each module decides what the person asking may hear about.

    commands = Commands(schedule)
    reply = await commands.handle("set a pasta timer for ten minutes")   # "Pasta timer's on, 10 minutes."
    reply is None  ->  not a command: ask Claude
    reply == ""    ->  handled, nothing to say (e.g. "stop")
"""
import difflib
import re
import time
from datetime import datetime, timedelta

from ..common import when
from .alerts import Alerts, at_phrase
from .names import NAME_WITH_GREETING as NAME
from .schedule import Schedule

POLITE = re.compile(r"\b(please|can you|could you|would you|will you|can u|i want you to|i need you to|go ahead and|just)\b", re.I)

STOP = re.compile(r"^(stop|stop it|stop talking|never ?mind|quiet|be quiet|shut up|hush|cancel that|forget it|that's enough|enough)$")
ACK = re.compile(r"\b(stop|ok(ay)?|o\.k\.|got it|thanks|thank you|turn (it )?off|shut (it )?off|shut up|i'?m up|i'?m awake|"
                 r"we'?re up|coming|alright|all right|enough|dismiss|quiet|yes|yeah|yep|done|heard you|i hear you)\b")
# Only clear asks to snooze: a bare "later" ("okay I'm up, I'll make coffee later") is not one.
SNOOZE = re.compile(r"\b(snooze|(\d+) more minutes?|five more minutes)\b")
HALLUCINATION = re.compile(r"^(thank you|thanks for watching|you|bye)\W*$")  # what speech-to-text "hears" in noise
REF = re.compile(r"\b(that|it|this|that one|this one)\b")
# Changing something that already exists. Rules that *create* things step aside when they see these,
# so "change that alarm to 7:30" can never make a second alarm.
CHANGE_WORDS = re.compile(r"\b(change|move|push|pull|shift|reschedule|update|bump|switch|postpone|delay|instead|bring)\b|"
                          r"\b(make|set) (it|that|this)\b|^(actually|i mean|i meant|no|wait|sorry)\b")
SHIFT = re.compile(r"\b(add|take|knock)( off)? (another |an? |\d+(\.\d+)? |a couple (of )?|a few )?(more |extra )?"
                   r"(minutes?|seconds?|hours?|mins?|secs?|hrs?)\b|\b(give it|another) (\d+|an?) (more )?(minutes?|seconds?|hours?)\b|"
                   r"\b(later|earlier|sooner|back|forward|ahead)\b")

KIND_WORDS = {"timer": "timer", "timers": "timer", "alarm": "alarm", "alarms": "alarm",
              "reminder": "reminder", "reminders": "reminder"}
LABEL_JUNK = {"set", "start", "make", "put", "create", "give", "me", "us", "a", "an", "the", "my", "new", "for", "on",
              "timer", "called", "named", "labelled", "labeled", "quick", "another", "one", "up", "to", "and", "of",
              "minute", "minutes", "second", "seconds", "hour", "hours", "min", "mins", "sec", "secs", "half", "quarter",
              "in", "at", "it", "that", "this", "is", "i", "want", "need", "with", "about", "go", "off",
              "how", "long", "much", "left", "time", "what", "what's", "whats", "there", "still", "remaining", "more",
              "extra", "add", "take", "knock", "cancel", "delete", "remove", "change", "update", "reset", "check",
              "many", "do", "have", "running", "going", "timers", "any", "has", "got", "be", "will", "until", "till"}


def clean(text: str) -> str:
    t = NAME.sub(" ", text)
    t = POLITE.sub(" ", t)
    t = re.sub(r"[^\w':.\- ]+", " ", t.lower())
    t = re.sub(r"\b(um+|uh+|hmm+|like|so|hey|yo)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .,-")
    return t


def label_from(t: str) -> str:
    """'set a pasta timer for 10 minutes' -> 'pasta' · 'timer for 5 minutes for the eggs' -> 'eggs'."""
    t = when.numbers(t)
    t = re.sub(rf"\b\d+(?:\.\d+)?\s*(?:and a half |and a quarter )?{when.UNIT}( and a half| and a quarter)?\b", " ", t)
    t = re.sub(r"\b(half an hour|half hour|an hour and a half)\b", " ", t)
    m = re.search(r"\b(called|named|labell?ed)\s+(.+)$", t)
    if m:
        words = m.group(2).split()
    else:
        before, _, after = t.partition("timer")
        after_m = re.search(r"\bfor (?:the |my )?(.+)$", after)
        words = before.split() if any(w not in LABEL_JUNK for w in before.split()) else (after_m.group(1).split() if after_m else [])
    words = [w.strip(".,'") for w in words if w.strip(".,'") and w.strip(".,'") not in LABEL_JUNK and not w.isdigit()]
    return " ".join(words[-3:])


def timer_name(label: str) -> str:
    """A timer's name as Claude gave it, without words that aren't a name: '10 minute timer' → '' (so Pip
    doesn't say "10 minute timer timer's on"), 'Pasta timer' → 'pasta'."""
    words = [w.strip(".,'") for w in when.numbers(label.lower()).split()]
    return " ".join(w for w in words if w and w not in LABEL_JUNK and not w.replace(".", "").isdigit())[:60]


def match_label(label: str, wanted: str) -> float:
    if not wanted:
        return 0.0
    if wanted in label or label in wanted:
        return 1.0
    return difflib.SequenceMatcher(None, label, wanted).ratio()


def join(items, word="and") -> str:
    """'a', 'a and b', 'a, b and c'."""
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" {word} " + items[-1]


class Asking:
    """What a module's offline phrase rules get (core/modules.OfflineItems.rules): who's asking, the time, and
    Pip's shared phrase helpers. Nothing else of pip-core."""
    REF, CHANGE_WORDS = REF, CHANGE_WORDS          # "that / it", and words that mean changing something
    match = staticmethod(match_label)              # how well a name matches what was said (0 to 1; 0.75 is a match)
    join = staticmethod(join)

    def __init__(self, cmds: "Commands", kind: str):
        self._cmds, self._kind = cmds, kind

    @property
    def call(self):
        """Who's asking (core/modules.Call), or None for the dashboard's typed commands (they see everything)."""
        return self._cmds.call

    def now(self) -> datetime:
        return self._cmds.now()

    def ask(self, question: str, answer) -> str:
        """Ask a question back ("What day is the dentist?"); the next thing said within 20 s goes to
        `answer(t)`, which returns the reply (or None: it wasn't an answer)."""
        self._cmds.awaiting = {"intent": "module", "answer": answer, "until": self._cmds.clock() + 20}
        return question

    def remember(self, item_id: str) -> None:
        """This item is now what "that" and "it" mean."""
        self._cmds.alerts.remember(self._kind, item_id)

    def focus(self) -> dict | None:
        """The item "that" means, if it's one of this module's."""
        f = self._cmds.focus()
        return f[1] if f and f[0] == self._kind else None


class Commands:
    def __init__(self, schedule: Schedule, clock=time.time, items=()):
        # Making, changing and cancelling things, and what "that" means, is core/alerts.py's job.
        # `items`: what modules add (core/modules.OfflineItems), e.g. the calendar's phrases and events.
        self.items = {p.kind: p for p in items}
        self.alerts = Alerts(schedule, clock=clock, items=self.items)
        self.s = schedule
        self.call = None   # who's asking (core/modules.Call) during handle(); None = the dashboard, sees everything
        self.clock = clock
        self.awaiting: dict | None = None   # Pip asked a question ("For how long?") and waits for the answer

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.clock())

    # ---------------------------------------------------------------- "that" / "it"
    def focus(self, max_age: float = 15 * 60) -> tuple[str, dict] | None:
        """The item talked about (or rung) most recently, if it still exists and whoever is asking may hear about it."""
        return self.alerts.focus(max_age, self.call)

    @staticmethod
    def _kind_of(table: str, row: dict) -> str:
        return row["kind"] if table == "alert" else table

    def _when(self, table: str, row: dict) -> float:
        return row["due"] if table == "alert" else self.items[table].when(row)

    # ---------------------------------------------------------------- entry points
    async def while_ringing(self, text: str, reason: str) -> str | None:
        """An alarm is ringing and someone spoke. Snooze / acknowledge, else keep ringing.
        Returns a reply (handled), or None to treat it as an ordinary request (they said Pip's name)."""
        t = clean(text)
        if reason == "room" and HALLUCINATION.match(t):
            return ""
        if SNOOZE.search(when.numbers(t)) and not re.search(r"\bdon'?t snooze\b", t):
            mins = (when.duration(t) or 0) / 60 or None
            if m := re.search(r"\b(\d+) more minutes?\b", when.numbers(t)):
                mins = float(m.group(1))
            n = await self.s.snooze(mins)
            return f"Snoozed for {when.say_duration((mins or self.s.snooze_min) * 60)}." if n else ""
        if ACK.search(t):
            await self.s.acknowledge("voice")
            return ""
        if NAME.search(text):
            await self.s.acknowledge("name")
            return None if t else ""
        if reason != "room":
            return None  # said to Pip, but not an obvious stop/snooze: Claude works out what they want (D-94)
        return ""  # TV or chatter: recorded, but the alarm keeps ringing (FEAT-037)

    async def handle(self, text: str, call=None) -> str | None:
        """A request for Pip. Returns what to say, "" for nothing, or None if it's not a command.
        `call`: who's asking (core/modules.Call), so a module's items they mayn't hear about (calendar events) are
        left out; None (the dashboard's typed commands) sees everything."""
        self.call = call
        try:
            return await self._handle(text)
        finally:
            self.call = None

    async def _handle(self, text: str) -> str | None:
        t = clean(text)
        if not t:
            return None
        if self.awaiting and self.clock() < self.awaiting["until"]:
            reply = await self._answer_question(t)
            if reply is not None:
                return reply
        self.awaiting = None
        if re.match(r"^(cancel|delete|remove|scrap) (that|it|this)( one)?$", t) and (f := self.focus(120)):
            return self.alerts.cancel_item(*f)  # just set something, then "cancel that"
        if STOP.match(t):
            return ""
        modules = [lambda t, r=r, k=p.kind: r(t, Asking(self, k)) for p in self.items.values() for r in p.rules]
        for rule in (self._snooze, self._time, self._cancel, self._change, self._timer_query,
                     self._timer, self._alarm, self._list_alerts, *modules, self._reminder):
            reply = rule(t)
            if hasattr(reply, "__await__"):
                reply = await reply
            if reply is not None:
                return reply
        return None

    def _ask(self, question: str, intent: str, **slots) -> str:
        self.awaiting = {"intent": intent, "slots": slots, "until": self.clock() + 20}
        return question

    async def _answer_question(self, t: str) -> str | None:
        a = self.awaiting
        self.awaiting = None
        if a["intent"] == "timer_length" and (secs := when.duration(t)):
            return self.alerts.make_timer(secs, a["slots"].get("label") or label_from(t))
        if a["intent"] == "alarm_time" and (at := when.clock(f"at {t}" if re.match(r"^\d", when.numbers(t)) else t, self.now(),
                                                              alarm=True)):
            return self.alerts.make_alarm(at, a["slots"].get("label", ""), a["slots"].get("repeat", ""))
        if a["intent"] == "reminder_time":
            at = self._reminder_time(t)
            if at:
                return self.alerts.make_reminder(at, a["slots"]["what"], a["slots"].get("repeat", ""))
        if a["intent"] == "module":   # a module asked (Asking.ask): its answer function decides
            return a["answer"](t)
        if a["intent"] == "which_change":
            picks = [(tb, x) for tb, x in a["slots"]["options"] if match_label(self._title(tb, x), t) >= 0.75]
            if len(picks) == 1:
                return self._change_item(*picks[0], a["slots"]["text"])
            return None
        if a["intent"] == "which":
            picks = [x for x in a["slots"]["options"] if match_label(x["label"], t) >= 0.75]
            if re.search(r"\b(all|both|every|everything)\b", t):
                picks = a["slots"]["options"]
            if picks:
                for x in picks:
                    self.s.cancel(x["id"])
                return self.alerts.cancelled_text(picks)
        return None

    # ---------------------------------------------------------------- rules (each returns a reply or None)
    async def _snooze(self, t):
        if re.match(r"^snooze\b", t):
            if not self.s.ringing:
                return "Nothing's ringing right now."
            return await self.while_ringing(t, "voice")
        return None

    def _time(self, t):
        now = self.now()
        if re.match(r"^(what('?s| is) the time|what time is it|what time's it|got the time|time check)( now| right now)?\W*$", t):
            return f"It's {when.say_clock(now)}."
        if re.match(r"^(what('?s| is) (the date|today'?s date|the day)|what day is (it|today)|what('?s| is) today)( today)?\W*$", t):
            return f"It's {now.strftime('%A, %B')} {now.day}."
        return None

    def _kind_in(self, t) -> str | None:
        for w, k in KIND_WORDS.items():
            if re.search(rf"\b{w}\b", t):
                return k
        return None

    def _kinds_in(self, t) -> list[str]:
        """Every kind mentioned, in the order said: "alarms and timers" → ["alarm", "timer"]."""
        found = sorted((m.start(), k) for w, k in KIND_WORDS.items() for m in re.finditer(rf"\b{w}\b", t))
        return list(dict.fromkeys(k for _, k in found))

    def _cancel(self, t):
        if not re.search(r"\b(cancel|delete|remove|clear|kill|turn off|get rid of|stop|end|scrap|drop)\b", t):
            return None
        kind = self._kind_in(t)
        if any(p.owns(t) for p in self.items.values()):
            return None   # "cancel the dentist on the calendar": the module's own rule
        if REF.search(t) and (f := self.focus()) and (not kind or self._kind_of(*f) == kind):
            return self.alerts.cancel_item(*f)  # "cancel that" / "cancel that timer": the one we just talked about
        if not kind:
            return None
        opts = self.s.active((kind,))
        everything = re.search(r"\b(all|every|both|everything)\b", t)
        if not opts:
            return f"You don't have any {kind}s."
        wanted = re.sub(rf"\b(cancel|delete|remove|clear|kill|turn off|get rid of|stop|end|scrap|drop|all|every|both|the|my|"
                        rf"that|this|{kind}s?|for|at|in|on|a|an|please|one)\b", " ", t)
        wanted = re.sub(r"\s+", " ", wanted).strip()
        if kind == "alarm" and (at := when.clock(t, self.now())):
            # By the time of day, like _target: "my 7 AM alarm" is the weekday one even if it next rings Monday.
            picks = [o for o in opts if datetime.fromtimestamp(o["due"]).strftime("%H:%M") == at.strftime("%H:%M")]
            if not picks:
                return f"You don't have an alarm at {when.say_clock(at)}."
        elif wanted and not everything:
            scored = sorted(((match_label(o["label"], wanted), o) for o in opts), key=lambda x: -x[0])
            picks = [o for sc, o in scored if sc >= 0.75]
            if not picks:
                return f"There's no {wanted} {kind}."
        else:
            picks = opts
        if len(picks) > 1 and not everything:
            names = self.alerts.names(picks)
            if len(set(names)) == len(names):
                return self._ask(f"Which one: {self._join(names, 'or')}?", "which", options=picks)
        for p in picks:
            self.s.cancel(p["id"])
        return self.alerts.cancelled_text(picks)

    _join = staticmethod(join)

    # ---------------------------------------------------------------- changing anything (timers, alarms, reminders, modules' items)
    def _change(self, t):
        """'Change that alarm to 7:30', 'add a minute to the pasta timer', 'move the dentist to Saturday',
        'push my reminder back an hour', 'actually 15 minutes'. Works the same for every kind of item."""
        n = when.numbers(t)
        if not (CHANGE_WORDS.search(t) or SHIFT.search(n)) or re.search(r"\b(cancel|delete|remove)\b", t):
            return None
        if re.match(r"^(actually|i mean|i meant|no|wait|sorry)\b", t) and not self.focus(120):
            return None  # a correction only makes sense right after setting something
        kind = self._kind_in(t) or next((p.kind for p in self.items.values() if re.search(rf"\b({p.words})\b", t)), None)
        target_text = re.split(r"\s\b(to|until|till)\b\s", t)[0] if re.search(r"\s\b(to|until|till)\b\s", t) else t
        found = self._target(target_text, kind)
        if found is None:
            return None
        if isinstance(found, str):
            return found
        if isinstance(found, list):
            names = [self._title(tb, x) for tb, x in found]
            self.awaiting = {"intent": "which_change", "slots": {"options": found, "text": t}, "until": self.clock() + 20}
            return f"Which one: {self._join(names, 'or')}?"
        return self._change_item(*found, t)

    def _title(self, table: str, row: dict) -> str:
        if table != "alert":
            return row["title"]
        return self.alerts.names([row])[0]

    def _target(self, t: str, kind: str | None):
        """What they want to change: (table, row), a list to choose from, a reply ("You don't have..."), or None."""
        alerts = [("alert", a) for a in self.s.active(("timer", "alarm", "reminder")) if not kind or a["kind"] == kind]
        mods = [(p.kind, x) for p in self.items.values() if kind in (None, p.kind) for x in p.items(self.call)]
        everything = alerts + mods
        if kind != "timer" and (at := when.clock(t, self.now())):  # "the 6 AM alarm", "the dentist at 2"
            by_time = [(tb, x) for tb, x in everything if
                       abs(datetime.fromtimestamp(self._when(tb, x)).time().hour * 60 +
                           datetime.fromtimestamp(self._when(tb, x)).minute - (at.hour * 60 + at.minute)) < 1]
            if len(by_time) == 1:
                return by_time[0]
        skip = {"alarm", "reminder", "move", "change", "push", "back", "later", "earlier", "make", "actually", "reschedule",
                "shift", "bump", "minute", "hour", "please", "can", "you", "our", "today", "tomorrow"}
        skip |= {w for p in self.items.values() for w in p.words.split("|")}   # "event", "calendar", …
        wanted = " ".join(w for w in re.sub(r"[^\w' ]", " ", label_from(t)).split()
                          if w not in skip and not re.match(rf"^({'|'.join(when.WEEKDAYS)})$", w))
        if wanted:
            scored = [(match_label(self._title(tb, x).lower(), wanted), (tb, x)) for tb, x in everything]
            picks = [p for sc, p in scored if sc >= 0.75]
            if len(picks) == 1:
                return picks[0]
            if len(picks) > 1:
                return picks
        f = self.focus()
        if f and (not kind or self._kind_of(*f) == kind):
            return f
        if len(everything) == 1 and (kind or REF.search(t)):
            return everything[0]
        if not everything:
            return None if not kind else self.items[kind].empty if kind in self.items else f"You don't have any {kind}s."
        if kind and len(everything) > 1:
            return everything
        return None

    def _change_item(self, table: str, row: dict, t: str) -> str | None:
        """Work out the new time from what they said, then change it."""
        n = when.numbers(t)
        k = self._kind_of(table, row)
        old = datetime.fromtimestamp(self._when(table, row))
        value = re.split(r"\s\b(to|until|till)\b\s", t)[-1] if re.search(r"\s\b(to|until|till)\b\s", t) else t
        secs = when.duration(n)
        if secs and SHIFT.search(n):
            sign = -1 if re.search(r"\b(take|knock|earlier|sooner|forward|ahead)\b", n) else 1
            return self.alerts.apply_change(table, row, shift=sign * secs)
        if k == "timer":
            if secs or (m := re.search(r"\b(\d+(?:\.\d+)?)\s*$", n)):
                return self.alerts.apply_change(table, row, length=secs or float(m.group(1)) * 60)  # "actually 15" = minutes
            return None
        at = self._retime(old, value)
        if at is None:
            return None
        return self.alerts.apply_change(table, row, at=at)

    def _retime(self, old: datetime, v: str) -> datetime | None:
        """A new time for an item at `old`. Keeps the day unless one is said, and keeps the clock time if only a day
        is said. Without AM/PM, picks whichever is nearer the old time ('8 AM alarm to 7:30' = 7:30 AM)."""
        now = self.now()
        nv = when.numbers(v)
        d = when.day(v, now)
        at = when.clock(f"at {nv}" if re.match(r"^\s*\d", nv) else nv, now)
        if at is None:
            return datetime.combine(d, old.time()) if d else None
        explicit = re.search(r"\d\s*(am|pm)\b|a\.m|p\.m|\b(morning|afternoon|evening|tonight|night|noon|midnight)\b", nv) or at.hour >= 13
        hours = [at.hour] if explicit else sorted({at.hour % 12, at.hour % 12 + 12})
        base = d or old.date()
        opts = [datetime.combine(base, datetime.min.time()).replace(hour=h, minute=at.minute) for h in hours]
        pick = min(opts, key=lambda o: abs((o - old).total_seconds()))
        if pick <= now and not d:
            future = [o for o in opts if o > now]
            pick = min(future) if future else pick + timedelta(days=1)
        return pick

    def _timer_query(self, t):
        if not re.search(r"\b(how (long|much time|much longer)|time left|time is left|time's left|what timers|any timers|"
                         r"timers? (do i have|are (there|running|on|going|set))|how'?s (the|my) .*timer|check (the|my) timers?)\b", t):
            return None
        if "timer" not in t and not re.search(r"\bleft\b", t):
            return None
        if len(self._kinds_in(t)) > 1:
            return None   # "what alarms and timers do I have": _list_alerts answers for each
        timers = self.s.active(("timer",))
        if not timers:
            return "No timers running." if "timer" in t or re.search(r"\bleft\b", t) else None
        wanted = label_from(t)
        if wanted:
            best = max(timers, key=lambda o: match_label(o["label"], wanted))
            if match_label(best["label"], wanted) >= 0.75:
                timers = [best]
        parts = []
        for r in timers:
            left = when.say_left(r["due"] - self.clock())
            name = "Your timer" if r["label"] in ("", "timer") else r["label"].capitalize()
            parts.append(f"{name} has {left} left" if len(timers) > 1 or name != "Your timer" else f"{left} left")
        return (self._join(parts) + ".").capitalize() if len(parts) > 1 else parts[0][0].upper() + parts[0][1:] + "."

    def _timer(self, t):
        if not re.search(r"\btimer\b|\bcount ?down\b", t) or re.search(r"\b(cancel|delete|remove|what|how|any)\b", t) or \
                CHANGE_WORDS.search(t):
            return None
        secs = when.duration(t)
        label = label_from(t)
        if not secs:
            return self._ask("For how long?", "timer_length", label=label)
        return self.alerts.make_timer(secs, label)

    def _alarm(self, t):
        if not re.search(r"\b(alarm|wake (me|us|everyone|everybody)( up)?|get (me|us) up)\b", t) or \
                re.search(r"\b(what|any|which|cancel|delete|remove|turn off)\b", t) or CHANGE_WORDS.search(t):
            return None
        rep = when.repeat(t)
        at = when.clock(t if re.search(r"\b(at|for|by)\b", t) or re.search(r"\d(:\d\d)?\s*(am|pm)", t) else f"at {t}", self.now(),
                        alarm=True)
        if not at:
            if secs := when.duration(t):  # "wake me up in 20 minutes" (real time, even across a clock change)
                return self.alerts.make_alarm(datetime.fromtimestamp(self.clock() + secs), "", rep)
            return self._ask("For what time?", "alarm_time", label="", repeat=rep)
        return self.alerts.make_alarm(at, "", rep)

    def _list_alerts(self, t):
        """"What alarms do I have?", "any reminders?", "what alarms and timers are set?" (each kind asked about)."""
        m = re.search(r"\b(what|which|any|list|tell me( my)?)\b.*\b(alarms?|reminders?|timers?)\b", t)
        if not m or re.search(r"\bremind me\b", t):
            return None
        kinds = self._kinds_in(t)
        if kinds == ["timer"]:
            return None   # timers alone: _timer_query answers ("Pasta has 5 minutes left.")
        said = [x for x in (self._say_kind(k) for k in kinds) if x]
        if not said:
            if len(kinds) == 1:
                return f"No {kinds[0]}s set."
            return "Nothing set: no " + self._join([f"{k}s" for k in kinds]).replace(" and ", " or ") + "."
        missing = [f"{k}s" for k in kinds if not self.s.active((k,))]
        return " ".join(said) + (f" No {self._join(missing).replace(' and ', ' or ')}." if missing else "")

    def _say_kind(self, kind: str) -> str:
        """One sentence about what's set of one kind, or "" if there's none."""
        rows = self.s.active((kind,))
        if not rows:
            return ""
        now = self.now()
        if kind == "alarm":
            items = [f"{when.say_clock(datetime.fromtimestamp(r['due']))} {when.say_repeat(r['repeat'])}" if r["repeat"]
                     else when.say_time(datetime.fromtimestamp(r["due"]), now) for r in rows[:5]]
            return f"You've got alarms for {self._join(items)}." if len(items) > 1 else f"One alarm, for {items[0]}."
        if kind == "timer":
            items = [f"{'a timer' if r['label'] in ('', 'timer') else 'the ' + r['label'] + ' timer'} with "
                     f"{when.say_left(r['due'] - self.clock())} left" for r in rows[:4]]
            more = f", plus {len(rows) - 4} more" if len(rows) > 4 else ""
            return f"Timers: {self._join(items)}{more}."
        items = [f"{r['label']} {at_phrase(datetime.fromtimestamp(r['due']), now, soon=False)}" for r in rows[:4]]
        more = f", plus {len(rows) - 4} more" if len(rows) > 4 else ""
        return f"Reminders: {self._join(items)}{more}."

    def _reminder_time(self, t: str) -> datetime | None:
        if m := re.search(r"\bin (.+)$", t):
            if secs := when.duration(m.group(1).split(" to ")[0]):
                return datetime.fromtimestamp(self.clock() + secs)   # real time, even across a clock change
        at = when.clock(t, self.now())
        if not at and when.has_day(t):  # "tomorrow" alone: 9 AM (like an all-day heads-up)
            d = when.day(t, self.now())
            at = datetime.combine(d, datetime.min.time()).replace(hour=9)
            if at <= self.now():
                return None
        return at

    def _reminder(self, t):
        m = re.search(r"\bremind (me|us|everyone|everybody|him|her|them|\w+)\b(.*)$", t)
        if not m:
            return None
        rest = m.group(2)
        if re.match(r"^\s*(what|who|how|why|when|where|which|if|whether)\b", rest):
            return None  # "remind me what the capital of France is": a question, not a reminder
        what_m = re.search(r"\b(to|that|about)\b (.+?)(?=\s+\b(at|in|on|tomorrow|tonight|today|every|each)\b\s+[\w:]+|$)", rest)
        what = what_m.group(2).strip() if what_m else ""
        # The time can be anywhere: "remind me at 6 to...", "every day at 8 remind me to...".
        when_text = t.replace(what, " ") if what else t
        rep = when.repeat(t)
        at = self._reminder_time(when_text) or self._reminder_time(rest)
        if not what and not at:
            return None  # neither what to remind them of nor when: not a reminder
        what = re.sub(r"\b(every ?day|daily|every (week|weekday|monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?)\b", "", what).strip()
        if m.group(1) not in ("me", "us", "everyone", "everybody", "him", "her", "them"):
            what = f"{m.group(1).capitalize()}, {what}" if what else m.group(1).capitalize()
        if not what:
            return None
        if not at:
            return self._ask("When should I remind you?", "reminder_time", what=what, repeat=rep)
        return self.alerts.make_reminder(at, what, rep)
