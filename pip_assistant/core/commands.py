"""Pip's own commands: understood on the Pi, without Claude, so they work offline (PIP-NEW-044).

Timers, alarms, reminders, the calendar, "what time is it", "stop" and answering a ringing alarm.
Anything that isn't clearly one of these goes to Claude as before. Replies are short (D-79), and
Pip only says something is set once it really is (PIP-NEW-039). If a cancel matches nothing he
says so; if it could mean several, he asks which (FEAT-042).

    commands = Commands(schedule)
    reply = await commands.handle("set a pasta timer for ten minutes")   # "Pasta timer's on, 10 minutes."
    reply is None  ->  not a command: ask Claude
    reply == ""    ->  handled, nothing to say (e.g. "stop")
"""
import difflib
import re
import time
from datetime import datetime, timedelta

from . import when
from .schedule import Schedule

NAME = re.compile(r"\b(hey |hi |ok |okay )?(pip|pipp|pips|pippy|pippie|pippletron|pipple ?tron|piple ?tron|pipletron)\b[,.!]?", re.I)
POLITE = re.compile(r"\b(please|can you|could you|would you|will you|can u|i want you to|i need you to|go ahead and|just)\b", re.I)

STOP = re.compile(r"^(stop|stop it|stop talking|never ?mind|quiet|be quiet|shut up|hush|cancel that|forget it|that's enough|enough)$")
ACK = re.compile(r"\b(stop|ok(ay)?|o\.k\.|got it|thanks|thank you|turn (it )?off|shut (it )?off|shut up|i'?m up|i'?m awake|"
                 r"we'?re up|coming|alright|all right|enough|dismiss|quiet|yes|yeah|yep|done|heard you|i hear you)\b")
SNOOZE = re.compile(r"\b(snooze|(\d+) more minutes?|five more minutes|later)\b")
HALLUCINATION = re.compile(r"^(thank you|thanks for watching|you|bye)\W*$")  # what speech-to-text "hears" in noise

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


def at_phrase(at: datetime, now: datetime, soon: bool = True) -> str:
    """'at 6 PM', 'at 9 AM tomorrow', 'on Tuesday at 3 PM', 'in 20 minutes' (when it's soon)."""
    secs = (at - now).total_seconds()
    if soon and secs < 3600 and at.second == now.second:
        return f"in {when.say_duration(round(secs / 60) * 60)}"
    txt = when.say_time(at, now)
    return f"on {txt}" if txt.split()[0].title() in [d.title() for d in when.WEEKDAYS] else f"at {txt}"


def match_label(label: str, wanted: str) -> float:
    if not wanted:
        return 0.0
    if wanted in label or label in wanted:
        return 1.0
    return difflib.SequenceMatcher(None, label, wanted).ratio()


class Commands:
    def __init__(self, schedule: Schedule, clock=time.time):
        self.s = schedule
        self.clock = clock
        self.awaiting: dict | None = None   # Pip asked a question ("For how long?") and waits for the answer
        self.last_timer: tuple[str, float] | None = None  # for "actually, 15 minutes"

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.clock())

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
        return ""  # TV or chatter: recorded, but the alarm keeps ringing (FEAT-037)

    async def handle(self, text: str) -> str | None:
        """A request for Pip. Returns what to say, "" for nothing, or None if it's not a command."""
        t = clean(text)
        if not t:
            return None
        if self.awaiting and self.clock() < self.awaiting["until"]:
            reply = await self._answer_question(t)
            if reply is not None:
                return reply
        self.awaiting = None
        if STOP.match(t):
            return ""
        for rule in (self._snooze, self._correction, self._time, self._cancel, self._change, self._timer_query,
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
            return self._make_timer(secs, a["slots"].get("label") or label_from(t))
        if a["intent"] == "alarm_time" and (at := when.clock(f"at {t}" if re.match(r"^\d", when.numbers(t)) else t, self.now())):
            return self._make_alarm(at, a["slots"].get("label", ""), a["slots"].get("repeat", ""))
        if a["intent"] == "reminder_time":
            at = self._reminder_time(t)
            if at:
                return self._make_reminder(at, a["slots"]["what"], a["slots"].get("repeat", ""))
        if a["intent"] == "event_day" and (d := when.day(t, self.now())):
            at = when.clock(t, self.now())
            title = a["slots"]["title"]
            start = datetime.combine(d, at.time()) if at else datetime.combine(d, datetime.min.time())
            self.s.add_event(title, start, all_day=not at, repeat=a["slots"].get("repeat", ""))
            return f"Added {title}, {when.say_day(d, self.now().date())}" + (f" at {when.say_clock(start)}." if at else ".")
        if a["intent"] == "which":
            picks = [x for x in a["slots"]["options"] if match_label(x["label"], t) >= 0.75]
            if re.search(r"\b(all|both|every|everything)\b", t):
                picks = a["slots"]["options"]
            if picks:
                for x in picks:
                    self.s.cancel(x["id"])
                return self._cancelled_text(picks)
        return None

    # ---------------------------------------------------------------- rules (each returns a reply or None)
    async def _snooze(self, t):
        if re.match(r"^snooze\b", t):
            if not self.s.ringing:
                return "Nothing's ringing right now."
            return await self.while_ringing(t, "voice")
        return None

    def _correction(self, t):
        """'Set a timer for 10 minutes... actually 15': the same timer becomes 15 minutes."""
        if not re.match(r"^(actually|no|wait|sorry|i mean|i meant)\b", t) or not self.last_timer:
            return None
        aid, made = self.last_timer
        secs = when.duration(t)
        row = self.s._row(aid)
        if secs and row and row["state"] == "waiting" and self.clock() - made < 60:
            self.s.update(aid, due=self.clock() + secs, length=secs)
            return f"Changed it to {when.say_duration(secs)}."
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

    def _cancel(self, t):
        if not re.search(r"\b(cancel|delete|remove|clear|kill|turn off|get rid of|stop|end|scrap|drop)\b", t):
            return None
        kind = self._kind_in(t)
        if not kind or re.search(r"\bcalendar\b", t):
            return None
        opts = self.s.active((kind,))
        everything = re.search(r"\b(all|every|both|everything)\b", t)
        if not opts:
            return f"You don't have any {kind}s."
        wanted = re.sub(rf"\b(cancel|delete|remove|clear|kill|turn off|get rid of|stop|end|scrap|drop|all|every|both|the|my|"
                        rf"that|this|{kind}s?|for|at|in|on|a|an|please|one)\b", " ", t)
        wanted = re.sub(r"\s+", " ", wanted).strip()
        if kind == "alarm" and (at := when.clock(t, self.now())):
            picks = [o for o in opts if abs(o["due"] - at.timestamp()) < 60]
        elif wanted and not everything:
            scored = sorted(((match_label(o["label"], wanted), o) for o in opts), key=lambda x: -x[0])
            picks = [o for sc, o in scored if sc >= 0.75]
            if not picks:
                return f"There's no {wanted} {kind}."
        else:
            picks = opts
        if len(picks) > 1 and not everything:
            names = self._names(picks)
            if len(set(names)) == len(names):
                return self._ask(f"Which one: {self._join(names, 'or')}?", "which", options=picks)
        for p in picks:
            self.s.cancel(p["id"])
        return self._cancelled_text(picks)

    def _names(self, rows) -> list[str]:
        out = []
        for r in rows:
            if r["kind"] == "alarm" or r["label"] in ("", "timer", "alarm"):
                out.append(f"the {when.say_clock(datetime.fromtimestamp(r['due']))} one" if r["kind"] != "timer" else
                           f"the {when.say_duration(r['length'] or 0)} one".replace("minutes one", "minute one")
                           .replace("hours one", "hour one").replace("seconds one", "second one"))
            else:
                out.append(r["label"])
        return out

    @staticmethod
    def _join(items, word="and") -> str:
        items = list(items)
        return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" {word} " + items[-1]

    def _cancelled_text(self, rows) -> str:
        kind = rows[0]["kind"]
        if len(rows) > 1:
            return f"Cancelled all {len(rows)} {kind}s." if len(rows) > 2 else f"Cancelled both {kind}s."
        r = rows[0]
        if r["kind"] == "timer":
            return "Timer's cancelled." if r["label"] in ("", "timer") else f"Cancelled the {r['label']} timer."
        if r["kind"] == "alarm":
            return f"Cancelled your {when.say_clock(datetime.fromtimestamp(r['due']))} alarm."
        return "Reminder's cancelled."

    def _change(self, t):
        m = re.search(r"\b(add|put|give it|another)\b.*\b(to|on)\b.*\btimer\b|\b(make|change|update|reset)\b (the|my|that|it|this)\b.*?(timer|it)\b|"
                      r"\b(take|knock)\b.*\boff\b.*\btimer\b|\b(add|another)\b.*\b(more )?(minutes?|seconds?|hours?)\b", t)
        if not m or not (secs := when.duration(t)):
            return None
        timers = self.s.active(("timer",))
        if not timers:
            return None if re.search(r"\b(make|set)\b", t) else "You don't have a timer going."
        wanted = label_from(t)
        pick = max(timers, key=lambda o: match_label(o["label"], wanted)) if wanted else (timers[0] if len(timers) == 1 else None)
        if pick is None:
            return self._ask(f"Which one: {self._join(self._names(timers), 'or')}?", "which_change")
        if re.search(r"\b(add|another|more|extra)\b", t):
            due = pick["due"] + secs
        elif re.search(r"\b(take|knock)\b", t):
            due = max(self.clock() + 1, pick["due"] - secs)
        else:
            due = self.clock() + secs
        self.s.update(pick["id"], due=due, length=(pick["length"] or 0) + (due - pick["due"]))
        return f"Done, {when.say_left(due - self.clock())} left."

    def _timer_query(self, t):
        if not re.search(r"\b(how (long|much time|much longer)|time left|time is left|time's left|what timers|any timers|"
                         r"timers? (do i have|are (there|running|on|going|set))|how'?s (the|my) .*timer|check (the|my) timers?)\b", t):
            return None
        if "timer" not in t and not re.search(r"\bleft\b", t):
            return None
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
        if not re.search(r"\btimer\b|\bcount ?down\b", t) or re.search(r"\b(cancel|delete|remove|what|how|any)\b", t):
            return None
        secs = when.duration(t)
        label = label_from(t)
        if not secs:
            return self._ask("For how long?", "timer_length", label=label)
        return self._make_timer(secs, label)

    def _make_timer(self, secs: float, label: str) -> str:
        secs = max(1.0, secs)
        row = self.s.add("timer", label or "timer", self.clock() + secs, length=secs)
        self.last_timer = (row["id"], self.clock())
        name = "Timer's" if not label else f"{label.capitalize()} timer's"
        return f"{name} on, {when.say_duration(secs)}."

    def _alarm(self, t):
        if not re.search(r"\b(alarm|wake (me|us|everyone|everybody)( up)?|get (me|us) up)\b", t) or \
                re.search(r"\b(what|any|which|cancel|delete|remove|turn off)\b", t):
            return None
        rep = when.repeat(t)
        at = when.clock(t if re.search(r"\b(at|for|by)\b", t) or re.search(r"\d(:\d\d)?\s*(am|pm)", t) else f"at {t}", self.now())
        if not at:
            if secs := when.duration(t):  # "wake me up in 20 minutes"
                return self._make_alarm(self.now() + timedelta(seconds=secs), "", rep)
            return self._ask("For what time?", "alarm_time", label="", repeat=rep)
        return self._make_alarm(at, "", rep)

    def _make_alarm(self, at: datetime, label: str, rep: str) -> str:
        at = when.first_repeat(rep, at) if rep else at
        self.s.add("alarm", label or "alarm", at.timestamp(), repeat=rep)
        r = f" {when.say_repeat(rep)}" if rep else ""
        return f"Alarm set for {when.say_clock(at)}{r}." if rep else f"Alarm set for {when.say_time(at, self.now())}."

    def _list_alerts(self, t):
        m = re.search(r"\b(what|which|any|list|tell me( my)?)\b.*\b(alarms|reminders|alarm|reminder)\b", t)
        if not m or re.search(r"\bremind me\b", t):
            return None
        kind = self._kind_in(t)
        rows = self.s.active((kind,))
        if not rows:
            return f"No {kind}s set."
        now = self.now()
        if kind == "alarm":
            items = [f"{when.say_clock(datetime.fromtimestamp(r['due']))} {when.say_repeat(r['repeat'])}" if r["repeat"]
                     else when.say_time(datetime.fromtimestamp(r["due"]), now) for r in rows[:5]]
            return f"You've got alarms for {self._join(items)}." if len(items) > 1 else f"One alarm, for {items[0]}."
        items = [f"{r['label']} {at_phrase(datetime.fromtimestamp(r['due']), now, soon=False)}" for r in rows[:4]]
        more = f", plus {len(rows) - 4} more" if len(rows) > 4 else ""
        return f"Reminders: {self._join(items)}{more}."

    def _reminder_time(self, t: str) -> datetime | None:
        if m := re.search(r"\bin (.+)$", t):
            if secs := when.duration(m.group(1).split(" to ")[0]):
                return self.now() + timedelta(seconds=secs)
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
            return None  # "remind me what the capital of France is": a question, not a reminder
        what = re.sub(r"\b(every ?day|daily|every (week|weekday|monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?)\b", "", what).strip()
        if m.group(1) not in ("me", "us", "everyone", "everybody", "him", "her", "them"):
            what = f"{m.group(1).capitalize()}, {what}" if what else m.group(1).capitalize()
        if not what:
            return None
        if not at:
            return self._ask("When should I remind you?", "reminder_time", what=what, repeat=rep)
        return self._make_reminder(at, what, rep)

    def _make_reminder(self, at: datetime, what: str, rep: str) -> str:
        at = when.first_repeat(rep, at) if rep else at
        what = re.sub(r"\bmy\b", "your", re.sub(r"\bi\b", "you", what))
        self.s.add("reminder", what, at.timestamp(), repeat=rep)
        r = f"{when.say_repeat(rep)} at {when.say_clock(at)}" if rep else at_phrase(at, self.now())
        return f"Okay, I'll remind you {r}."

    # ---------------------------------------------------------------- calendar
    def _event_add(self, t):
        m = re.search(r"\b(put|add|schedule|pencil( in)?|book|note)\b\s+(.+?)\s+\b(on|to|in|into)\b\s+(the |my |our )?(calendar|schedule)\b(.*)$", t) or \
            re.search(r"\b(add|put|schedule|create)\b\s+(an? )?(event|appointment)\s+(called |for )?(.+)$", t)
        if not m:
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
        if re.search(r"\b(remind|heads up|warn|tell me)\b", combo):
            heads = when.duration(combo.split("remind")[-1]) or 0.0
        self.s.add_event(title, at, all_day=all_day, repeat=rep, heads_up_before=heads)
        when_txt = when.say_day(at.date(), self.now().date()) + ("" if all_day else f" at {when.say_clock(at)}")
        if rep:
            when_txt = when.say_repeat(rep) + ("" if all_day else f" at {when.say_clock(at)}")
        return f"Added {title}, {when_txt}."

    def _event_remove(self, t):
        m = re.search(r"\b(remove|delete|cancel|take|clear)\b\s+(.+?)\s+\b(from|off|on)\b\s+(the |my |our )?(calendar|schedule)\b", t)
        if not m:
            return None
        wanted = re.sub(r"^(the|my|our|a|an)\s+", "", m.group(2)).strip()
        wanted_day = when.day(wanted, self.now())
        wanted = re.split(r"\s+\b(on|at|this|next|tomorrow|today)\b", f"{wanted} ")[0].strip()
        evs = self.s.all_events()
        scored = sorted(((match_label(e["title"].lower(), wanted), e) for e in evs), key=lambda x: -x[0])
        picks = [e for sc, e in scored if sc >= 0.75]
        if wanted_day:
            picks = [e for e in picks if self.s.occurs_on(e, wanted_day)] or picks
        if not picks:
            return f"I can't find {wanted} on the calendar."
        if len(picks) > 1:
            return f"There are {len(picks)} of those. Which day?"
        self.s.remove_event(picks[0]["id"])
        return f"Took {picks[0]['title']} off the calendar."

    def _event_query(self, t):
        if not (re.search(r"\b(calendar|schedule|planned|plans)\b", t) and re.search(r"\b(what|anything|any|is there|do (i|we) have|what's)\b", t)) and \
                not re.match(r"^(what'?s|what is) (on|happening|going on) (today|tonight|tomorrow|this week|on \w+day|\w+day)\W*$", t) and \
                not re.match(r"^(what do|do) (i|we) have (on |going on )?(today|tonight|tomorrow|this week|on \w+day)\W*$", t):
            return None
        now = self.now()
        if re.search(r"\bthis week|next few days|coming up|upcoming\b", t):
            days = [now.date() + timedelta(days=i) for i in range(7)]
        else:
            days = [when.day(t, now) or now.date()]
        found = [(d, at, ev) for d in days for at, ev in self.s.events_on(d)]
        if not found:
            label = "this week" if len(days) > 1 else when.say_day(days[0], now.date())
            return f"Nothing on the calendar {label}."
        items = []
        for d, at, ev in found[:5]:
            day_txt = "" if len(days) == 1 else f" {when.say_day(d, now.date())}"
            items.append(f"{ev['title']}{day_txt}" + ("" if ev["all_day"] else f" at {when.say_clock(at)}"))
        more = f", and {len(found) - 5} more" if len(found) > 5 else ""
        lead = "This week" if len(days) > 1 else when.say_day(days[0], now.date()).capitalize()
        return f"{lead}: {self._join(items)}{more}."
