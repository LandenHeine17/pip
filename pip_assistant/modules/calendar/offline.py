"""The calendar's part of Pip's own offline commands (D-94; core/commands.py runs them when the internet is down,
and for commands typed on the dashboard).

    "put the dentist on the calendar on Friday at 3"     → add (asks "What day is …?" if no day is said)
    "take the dentist off the calendar"                  → remove
    "what's on the calendar tomorrow?", "what do I have this week?"
    "move the dentist to 4", "push it back an hour", "cancel that"   (the shared rules in core/commands.py;
                                                                      they use items / get / move / cancel below)

Privacy is the calendar's own rule (Calendar.may_hear): whoever is asking only hears about, and only changes,
the events they may (private events nobody by voice; an "only me" account's events only their owner). The
dashboard's typed commands (`call` None) see everything.
"""
import re
from datetime import datetime, timedelta

from pip_assistant.common import when
from pip_assistant.core.modules import OfflineItems


class CalendarOffline(OfflineItems):
    kind = "event"
    words = "calendar|event|appointment"
    empty = "Your calendar's empty."

    def __init__(self, store, may_hear):
        self.store = store          # store.py: the events on the Pi
        self.may_hear = may_hear    # (event, call) → may the person asking hear about it, or change it?
        self.rules = [self.add, self.remove, self.query]

    # ---------------------------------------------------------------- the shared rules' side (core/commands.py)
    def owns(self, t: str) -> bool:
        return bool(re.search(r"\bcalendar\b", t))

    def _sees(self, ev: dict, call) -> bool:
        return call is None or self.may_hear(ev, call)

    def items(self, call) -> list[dict]:
        return [e for e in self.store.all_events() if self._sees(e, call)]

    def get(self, item_id: str, call) -> dict | None:
        ev = self.store.event(item_id)
        return ev if ev and self._sees(ev, call) else None

    def when(self, row: dict) -> float:
        return row["start"]

    def move(self, row: dict, at: datetime | None = None, shift: float | None = None) -> str:
        start = at or datetime.fromtimestamp(row["start"] + (shift or 0))
        all_day = bool(row["all_day"]) and at is not None and at.time() == datetime.min.time()
        ev = self.store.update_event(row["id"], start, all_day=all_day if at else None)
        now = datetime.fromtimestamp(self.store.clock())
        when_txt = when.say_day(start.date(), now) + ("" if ev["all_day"] else f" at {when.say_clock(start)}")
        return f"Moved {ev['title']} to {when_txt}."

    def cancel(self, row: dict) -> str:
        self.store.remove_event(row["id"])
        return f"Took {row['title']} off the calendar."

    # ---------------------------------------------------------------- the calendar's own phrases
    def add(self, t: str, ask):
        m = re.search(r"\b(put|add|schedule|pencil( in)?|book|note)\b\s+(.+?)\s+\b(on|to|in|into)\b\s+(the |my |our )?(calendar|schedule)\b(.*)$", t) or \
            re.search(r"\b(add|put|schedule|create)\b\s+(an? )?(event|appointment)\s+(called |for )?(.+)$", t)
        if not m or ask.CHANGE_WORDS.search(t):
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
        now = ask.now()
        rep = when.repeat(combo)
        d = when.day(combo, now)
        has_time = bool(re.search(r"\b(at|from|by)\s+\d|\d\s*(am|pm)|\bnoon\b|\bmidnight\b|\d:\d\d", when.numbers(combo)))
        if rep.startswith("weekly:") and d is None:
            d = when.day(when.WEEKDAYS[int(rep[7:])], now)
        if has_time:
            at = when.clock(combo, now)
            if at is None:
                return None
            if d and at.date() != d:
                at = datetime.combine(d, at.time())
            all_day = False
        elif d:
            at, all_day = datetime.combine(d, datetime.min.time()), True
        else:
            def answer(t2: str):   # "What day is the dentist?" → "Friday at 3"
                if not (d2 := when.day(t2, ask.now())):
                    return None
                at2 = when.clock(t2, ask.now())
                start = datetime.combine(d2, at2.time()) if at2 else datetime.combine(d2, datetime.min.time())
                return self._add(ask, title, start, all_day=not at2, rep=rep)
            return ask.ask(f"What day is {title}?", answer)
        heads = None
        if asked := list(re.finditer(r"\b(remind|heads up|warn|tell me)\b", combo)):
            # Only what comes after "remind me" / "heads up" / … is the heads-up time ("…at 3, warn me 10
            # minutes before"), never the event's own length.
            heads = when.duration(combo[asked[-1].end():]) or 0.0
        return self._add(ask, title, at, all_day=all_day, rep=rep, heads=heads)

    def _add(self, ask, title: str, at: datetime, all_day: bool = False, rep: str = "", heads: float | None = None) -> str:
        ev = self.store.add_event(title, at, all_day=all_day, repeat=rep, heads_up_before=heads)
        ask.remember(ev["id"])
        when_txt = when.say_day(at.date(), ask.now()) + ("" if all_day else f" at {when.say_clock(at)}")
        if rep:
            when_txt = when.say_repeat(rep) + ("" if all_day else f" at {when.say_clock(at)}")
        return f"Added {title}, {when_txt}."

    def remove(self, t: str, ask):
        m = re.search(r"\b(remove|delete|cancel|take|clear)\b\s+(.+?)\s+\b(from|off|on)\b\s+(the |my |our )?(calendar|schedule)\b", t)
        if not m:
            return None
        wanted = re.sub(r"^(the|my|our|a|an)\s+", "", m.group(2)).strip()
        if ask.REF.fullmatch(wanted) or wanted in ("that event", "that appointment", "it", "that one"):
            if ev := ask.focus():
                return self.cancel(ev)
            return "Which event? I'm not sure which one you mean."
        wanted_day = when.day(wanted, ask.now())
        wanted = re.split(r"\s+\b(on|at|this|next|tomorrow|today)\b", f"{wanted} ")[0].strip()
        scored = sorted(((ask.match(e["title"].lower(), wanted), e) for e in self.items(ask.call)), key=lambda x: -x[0])
        picks = [e for sc, e in scored if sc >= 0.75]
        if wanted_day:
            picks = [e for e in picks if self.store.occurs_on(e, wanted_day)] or picks
        if not picks:
            return f"I can't find {wanted} on the calendar."
        if len(picks) > 1:
            return f"There are {len(picks)} of those. Which day?"
        return self.cancel(picks[0])

    def query(self, t: str, ask):
        if not (re.search(r"\b(calendar|schedule|planned|plans)\b", t) and re.search(r"\b(what|anything|any|is there|do (i|we) have|what's)\b", t)) and \
                not re.match(r"^(what'?s|what is) (on|happening|going on) (today|tonight|tomorrow|this week|on \w+day|\w+day)\W*$", t) and \
                not re.match(r"^(what do|do) (i|we) have (on |going on )?(today|tonight|tomorrow|this week|on \w+day)\W*$", t):
            return None
        now = ask.now()
        if re.search(r"\bthis week|next few days|coming up|upcoming\b", t):
            days = [now.date() + timedelta(days=i) for i in range(7)]
        else:
            days = [when.day(t, now) or now.date()]
        found = [(d, at, ev) for d in days for at, ev in self.store.events_on(d)
                 if not ev["private"] and self._sees(ev, ask.call)]
        if not found:
            label = "this week" if len(days) > 1 else when.say_day(days[0], now)
            return f"Nothing on the calendar {label}."
        items = []
        for d, at, ev in found[:5]:
            day_txt = "" if len(days) == 1 else f" {when.say_day(d, now)}"
            items.append(f"{ev['title']}{day_txt}" + ("" if ev["all_day"] else f" at {when.say_clock(at)}"))
        more = f", and {len(found) - 5} more" if len(found) > 5 else ""
        lead = "This week" if len(days) > 1 else when.say_day(days[0], now).capitalize()
        return f"{lead}: {ask.join(items)}{more}."
