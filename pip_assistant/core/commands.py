"""Pip's own commands: understood on the Pi, without Claude. **For speech, used only offline** (D-94).

Online, everything people say goes to Claude in natural language, and Claude acts through ACTION
lines (core/actions.py). These rules are the fallback when the internet is down (PIP-NEW-044,
D-83): timers, alarms, reminders, the calendar, "what time is it", "stop". What they still do
online: `while_ringing` (the obvious "stop", "okay", "snooze" or his name silence a ringing alarm
instantly; anything else said to Pip while it rings goes to Claude) and the dashboard's box for typing
a command. Replies are short (D-79). If a cancel matches nothing he says so; if it could mean
several, he asks which (FEAT-042).

This file only *understands* the words (the phrase rules below, and the questions Pip asks back,
"For how long?"). Making, changing and cancelling timers, alarms and reminders, wording the reply,
and what "that" and "it" mean are core/alerts.py's job (`self.alerts`), which Claude's actions use
too. The calendar phrasings (`_event_add`, `_event_remove`, `_event_query`) are still here, and
only see the events the person asking may hear about (`_sees`).

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


class Commands:
    def __init__(self, schedule: Schedule, clock=time.time, calendar=None, may_hear=None):
        # Making, changing and cancelling things, and what "that" means, is core/alerts.py's job.
        self.alerts = Alerts(schedule, clock=clock, calendar=calendar, may_hear=may_hear)
        self.s = schedule
        self.cal = calendar   # the calendar module's events (modules/calendar/store.py), or None if it's off
        self.call = None   # who's asking (core/modules.Call) during handle(); None = the dashboard, sees everything
        self.clock = clock
        self.awaiting: dict | None = None   # Pip asked a question ("For how long?") and waits for the answer

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.clock())

    # ---------------------------------------------------------------- "that" / "it"
    def focus(self, max_age: float = 15 * 60) -> tuple[str, dict] | None:
        """The item talked about (or rung) most recently, if it still exists and whoever is asking may hear about it."""
        return self.alerts.focus(max_age, self.call)

    def _sees(self, ev: dict) -> bool:
        """May whoever is asking hear about (and change) this calendar event? The calendar module decides."""
        return self.alerts.sees(ev, self.call)

    def _events(self) -> list[dict]:
        return [e for e in self.cal.all_events() if self._sees(e)] if self.cal else []

    @staticmethod
    def _kind_of(table: str, row: dict) -> str:
        return "event" if table == "event" else row["kind"]

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
        `call`: who's asking (core/modules.Call), so calendar events they mayn't hear about are left out;
        None (the dashboard's typed commands) sees every event."""
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
        for rule in (self._snooze, self._time, self._cancel, self._change, self._timer_query,
                     self._timer, self._alarm, self._list_alerts, self._event_add, self._event_remove,
                     self._event_query, self._reminder):
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
        if a["intent"] == "event_day" and (d := when.day(t, self.now())):
            at = when.clock(t, self.now())
            start = datetime.combine(d, at.time()) if at else datetime.combine(d, datetime.min.time())
            return self._add_event(a["slots"]["title"], start, all_day=not at, rep=a["slots"].get("repeat", ""))
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
        if re.search(r"\bcalendar\b", t):
            return None
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

    @staticmethod
    def _join(items, word="and") -> str:
        items = list(items)
        return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" {word} " + items[-1]

    # ---------------------------------------------------------------- changing anything (timers, alarms, reminders, events)
    def _change(self, t):
        """'Change that alarm to 7:30', 'add a minute to the pasta timer', 'move the dentist to Saturday',
        'push my reminder back an hour', 'actually 15 minutes'. Works the same for every kind of item."""
        n = when.numbers(t)
        if not (CHANGE_WORDS.search(t) or SHIFT.search(n)) or re.search(r"\b(cancel|delete|remove)\b", t):
            return None
        if re.match(r"^(actually|i mean|i meant|no|wait|sorry)\b", t) and not self.focus(120):
            return None  # a correction only makes sense right after setting something
        kind = self._kind_in(t) or ("event" if re.search(r"\b(calendar|event|appointment)\b", t) else None)
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
        if table == "event":
            return row["title"]
        return self.alerts.names([row])[0]

    def _target(self, t: str, kind: str | None):
        """What they want to change: (table, row), a list to choose from, a reply ("You don't have..."), or None."""
        alerts = [("alert", a) for a in self.s.active(("timer", "alarm", "reminder")) if not kind or a["kind"] == kind]
        events = [("event", e) for e in self._events()] if kind in (None, "event") else []
        everything = alerts + events
        if kind != "timer" and (at := when.clock(t, self.now())):  # "the 6 AM alarm", "the dentist at 2"
            by_time = [(tb, x) for tb, x in everything if
                       abs(datetime.fromtimestamp(x["start"] if tb == "event" else x["due"]).time().hour * 60 +
                           datetime.fromtimestamp(x["start"] if tb == "event" else x["due"]).minute -
                           (at.hour * 60 + at.minute)) < 1]
            if len(by_time) == 1:
                return by_time[0]
        wanted = " ".join(w for w in re.sub(r"[^\w' ]", " ", label_from(t)).split()
                          if w not in ("alarm", "reminder", "event", "calendar", "appointment", "move", "change", "push",
                                       "back", "later", "earlier", "make", "actually", "reschedule", "shift", "bump",
                                       "minute", "hour", "please", "can", "you", "our", "today", "tomorrow")
                          and not re.match(rf"^({'|'.join(when.WEEKDAYS)})$", w))
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
            return f"You don't have any {kind}s." if kind and kind != "event" else (None if not kind else "Your calendar's empty.")
        if kind and len(everything) > 1:
            return everything
        return None

    def _change_item(self, table: str, row: dict, t: str) -> str | None:
        """Work out the new time from what they said, then change it."""
        n = when.numbers(t)
        k = self._kind_of(table, row)
        old = datetime.fromtimestamp(row["start"] if table == "event" else row["due"])
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

    # ---------------------------------------------------------------- calendar
    def _event_add(self, t):
        if not self.cal:
            return None
        m = re.search(r"\b(put|add|schedule|pencil( in)?|book|note)\b\s+(.+?)\s+\b(on|to|in|into)\b\s+(the |my |our )?(calendar|schedule)\b(.*)$", t) or \
            re.search(r"\b(add|put|schedule|create)\b\s+(an? )?(event|appointment)\s+(called |for )?(.+)$", t)
        if not m or CHANGE_WORDS.search(t):
            return None
        if m.re.pattern.startswith(r"\b(put"):
            title, rest = m.group(3), m.group(7)
        else:
            title, rest = m.group(5), ""
        combo = f"{title} {rest}"
        # Pull the time and day out of the title ("dentist on tuesday at 3").
        title = re.split(r"\s+\b(on|at|for|this|next|tomorrow|today|tonight|every)\b\s+", f" {title} ")[0].strip()
        title = re.sub(r"^(a|an|the|my|our)\s+", "", title).strip()
        if not title:
            return None
        rep = when.repeat(combo)
        d = when.day(combo, self.now())
        has_time = bool(re.search(r"\b(at|from|by)\s+\d|\d\s*(am|pm)|\bnoon\b|\bmidnight\b|\d:\d\d", when.numbers(combo)))
        if rep.startswith("weekly:") and d is None:
            d = when.day(when.WEEKDAYS[int(rep[7:])], self.now())
        if has_time:
            at = when.clock(combo, self.now())
            if at is None:
                return None
            if d and at.date() != d:
                at = datetime.combine(d, at.time())
            all_day = False
        elif d:
            at, all_day = datetime.combine(d, datetime.min.time()), True
        else:
            return self._ask(f"What day is {title}?", "event_day", title=title, repeat=rep)
        heads = None
        if asked := list(re.finditer(r"\b(remind|heads up|warn|tell me)\b", combo)):
            # Only what comes after "remind me" / "heads up" / … is the heads-up time ("…at 3, warn me 10
            # minutes before"), never the event's own length.
            heads = when.duration(combo[asked[-1].end():]) or 0.0
        return self._add_event(title, at, all_day=all_day, rep=rep, heads=heads)

    def _add_event(self, title: str, at: datetime, all_day: bool = False, rep: str = "", heads: float | None = None) -> str:
        ev = self.cal.add_event(title, at, all_day=all_day, repeat=rep, heads_up_before=heads)
        self.alerts.remember("event", ev["id"])
        when_txt = when.say_day(at.date(), self.now()) + ("" if all_day else f" at {when.say_clock(at)}")
        if rep:
            when_txt = when.say_repeat(rep) + ("" if all_day else f" at {when.say_clock(at)}")
        return f"Added {title}, {when_txt}."

    def _event_remove(self, t):
        if not self.cal:
            return None
        m = re.search(r"\b(remove|delete|cancel|take|clear)\b\s+(.+?)\s+\b(from|off|on)\b\s+(the |my |our )?(calendar|schedule)\b", t)
        if not m:
            return None
        wanted = re.sub(r"^(the|my|our|a|an)\s+", "", m.group(2)).strip()
        if REF.fullmatch(wanted) or wanted in ("that event", "that appointment", "it", "that one"):
            f = self.focus()
            if f and f[0] == "event":
                return self.alerts.cancel_item(*f)
            return "Which event? I'm not sure which one you mean."
        wanted_day = when.day(wanted, self.now())
        wanted = re.split(r"\s+\b(on|at|this|next|tomorrow|today)\b", f"{wanted} ")[0].strip()
        evs = self._events()
        scored = sorted(((match_label(e["title"].lower(), wanted), e) for e in evs), key=lambda x: -x[0])
        picks = [e for sc, e in scored if sc >= 0.75]
        if wanted_day:
            picks = [e for e in picks if self.cal.occurs_on(e, wanted_day)] or picks
        if not picks:
            return f"I can't find {wanted} on the calendar."
        if len(picks) > 1:
            return f"There are {len(picks)} of those. Which day?"
        return self.alerts.cancel_item("event", picks[0])

    def _event_query(self, t):
        if not self.cal:
            return None
        if not (re.search(r"\b(calendar|schedule|planned|plans)\b", t) and re.search(r"\b(what|anything|any|is there|do (i|we) have|what's)\b", t)) and \
                not re.match(r"^(what'?s|what is) (on|happening|going on) (today|tonight|tomorrow|this week|on \w+day|\w+day)\W*$", t) and \
                not re.match(r"^(what do|do) (i|we) have (on |going on )?(today|tonight|tomorrow|this week|on \w+day)\W*$", t):
            return None
        now = self.now()
        if re.search(r"\bthis week|next few days|coming up|upcoming\b", t):
            days = [now.date() + timedelta(days=i) for i in range(7)]
        else:
            days = [when.day(t, now) or now.date()]
        found = [(d, at, ev) for d in days for at, ev in self.cal.events_on(d)
                 if not ev["private"] and self._sees(ev)]
        if not found:
            label = "this week" if len(days) > 1 else when.say_day(days[0], now)
            return f"Nothing on the calendar {label}."
        items = []
        for d, at, ev in found[:5]:
            day_txt = "" if len(days) == 1 else f" {when.say_day(d, now)}"
            items.append(f"{ev['title']}{day_txt}" + ("" if ev["all_day"] else f" at {when.say_clock(at)}"))
        more = f", and {len(found) - 5} more" if len(found) > 5 else ""
        lead = "This week" if len(days) > 1 else when.say_day(days[0], now).capitalize()
        return f"{lead}: {self._join(items)}{more}."
