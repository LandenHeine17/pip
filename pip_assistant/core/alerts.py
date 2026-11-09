"""Making, changing and cancelling timers, alarms and reminders, and saying what was done.

This is the code that really does it, for both ways of asking: Pip's own offline phrases
(core/commands.py) and Claude's ACTION lines (core/actions.py). It knows nothing about wording what
people say; it gets a length, a time or a row, changes the schedule (core/schedule.py), and returns
the short reply (D-79). Pip only says something is set once it really is (PIP-NEW-039). Calendar
events can be cancelled and moved here too (the calendar module's store does the work).

It also remembers what "that" and "it" mean: whatever was made, changed or rung most recently
(`remember` / `focus`), so "cancel that" right after setting a timer cancels that timer.

    alerts = Alerts(schedule)
    alerts.make_timer(600, "pasta")              # "Pasta timer's on, 10 minutes."
    alerts.apply_change("alert", row, shift=60)  # "Done, 6 minutes left."
"""
import re
import time
from datetime import datetime

from ..common import when
from .schedule import Schedule

PASSED = "That time's already passed, so I left it as it was."


def at_phrase(at: datetime, now: datetime, soon: bool = True) -> str:
    """'at 6 PM', 'at 9 AM tomorrow', 'on Tuesday at 3 PM', 'in 20 minutes' (when it's soon)."""
    secs = (at - now).total_seconds()
    if soon and secs < 3600 and at.second == now.second:
        return f"in {when.say_duration(round(secs / 60) * 60)}"
    txt = when.say_time(at, now)
    return f"on {txt}" if txt.split()[0].title() in [d.title() for d in when.WEEKDAYS] else f"at {txt}"


class Alerts:
    def __init__(self, schedule: Schedule, clock=time.time, calendar=None, may_hear=None):
        self.s = schedule
        self.cal = calendar   # the calendar module's events (modules/calendar/store.py), or None if it's off
        # The calendar module's privacy rule (Calendar.may_hear): may the person asking hear about, or change,
        # this event? "Only me" accounts' events are theirs alone, private ones nobody's (by voice).
        self.may_hear = may_hear
        self.clock = clock
        self._focus: tuple[str, str, float] | None = None  # ("alert"/"event", id, when): what "that" and "it" mean

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.clock())

    # ---------------------------------------------------------------- "that" / "it"
    def remember(self, table: str, item_id: str) -> None:
        self._focus = (table, item_id, self.clock())

    def focus(self, max_age: float = 15 * 60, call=None) -> tuple[str, dict] | None:
        """The item talked about (or rung) most recently, if it still exists. `call`: who's asking
        (core/modules.Call), so a calendar event they mayn't hear about is never "that"; None sees everything."""
        cands = [f for f in (self._focus, ("alert", *self.s.last_rung) if self.s.last_rung else None) if f]
        for table, item_id, at in sorted(cands, key=lambda f: -f[2]):
            if self.clock() - at > max_age:
                continue
            if table == "alert":
                row = self.s.get(item_id)
                if row and row["state"] in ("waiting", "ringing"):
                    return "alert", row
            elif self.cal and (ev := self.cal.event(item_id)) and self.sees(ev, call):
                return "event", ev
        return None

    def sees(self, ev: dict, call) -> bool:
        """May `call` (who's asking; None = the dashboard) hear about (and change) this calendar event?
        The calendar module decides."""
        return call is None or self.may_hear is None or self.may_hear(ev, call)

    # ---------------------------------------------------------------- making
    def make_timer(self, secs: float, label: str) -> str:
        secs = max(1.0, secs)
        row = self.s.add("timer", label or "timer", self.clock() + secs, length=secs)
        self.remember("alert", row["id"])
        name = "Timer's" if not label else f"{label.capitalize()} timer's"
        return f"{name} on, {when.say_duration(secs)}."

    def make_alarm(self, at: datetime, label: str, rep: str) -> str:
        at = when.first_repeat(rep, at) if rep else at
        row = self.s.add("alarm", label or "alarm", at.timestamp(), repeat=rep, planned=at)
        self.remember("alert", row["id"])
        r = f" {when.say_repeat(rep)}" if rep else ""
        return f"Alarm set for {when.say_clock(at)}{r}." if rep else f"Alarm set for {when.say_time(at, self.now())}."

    def make_reminder(self, at: datetime, what: str, rep: str) -> str:
        at = when.first_repeat(rep, at) if rep else at
        what = re.sub(r"\bmy\b", "your", re.sub(r"\bi\b", "you", what))
        row = self.s.add("reminder", what, at.timestamp(), repeat=rep, planned=at)
        self.remember("alert", row["id"])
        r = f"{when.say_repeat(rep)} at {when.say_clock(at)}" if rep else at_phrase(at, self.now())
        return f"Okay, I'll remind you {r}."

    # ---------------------------------------------------------------- cancelling
    def cancel_item(self, table: str, row: dict) -> str:
        if table == "event":
            self.cal.remove_event(row["id"])
            return f"Took {row['title']} off the calendar."
        self.s.cancel(row["id"])
        return self.cancelled_text([row])

    def names(self, rows) -> list[str]:
        """How to tell items apart when asking "which one?": 'pasta', 'the 7 AM one', 'the 10 minute one'."""
        out = []
        for r in rows:
            if r["kind"] == "alarm" or r["label"] in ("", "timer", "alarm"):
                out.append(f"the {when.say_clock(datetime.fromtimestamp(r['due']))} one" if r["kind"] != "timer" else
                           f"the {when.say_duration(r['length'] or 0)} one".replace("minutes one", "minute one")
                           .replace("hours one", "hour one").replace("seconds one", "second one"))
            else:
                out.append(r["label"])
        return out

    def cancelled_text(self, rows) -> str:
        kind = rows[0]["kind"]
        if len(rows) > 1:
            return f"Cancelled all {len(rows)} {kind}s." if len(rows) > 2 else f"Cancelled both {kind}s."
        r = rows[0]
        if r["kind"] == "timer":
            return "Timer's cancelled." if r["label"] in ("", "timer") else f"Cancelled the {r['label']} timer."
        if r["kind"] == "alarm":
            return f"Cancelled your {when.say_clock(datetime.fromtimestamp(r['due']))} alarm."
        return "Reminder's cancelled."

    # ---------------------------------------------------------------- changing (timers, alarms, reminders, events)
    def apply_change(self, table: str, row: dict, at: datetime | None = None, shift: float | None = None,
                     length: float | None = None) -> str:
        """Change one item, read it back, and say what it is now. Used by voice rules and Claude's actions alike."""
        now_ts = self.clock()
        self.remember(table, row["id"])
        if table == "event":
            start = at or datetime.fromtimestamp(row["start"] + (shift or 0))
            all_day = bool(row["all_day"]) and at is not None and at.time() == datetime.min.time()
            ev = self.cal.update_event(row["id"], start, all_day=all_day if at else None)
            when_txt = when.say_day(start.date(), self.now()) + ("" if ev["all_day"] else f" at {when.say_clock(start)}")
            return f"Moved {ev['title']} to {when_txt}."
        if row["kind"] == "timer":
            if length:
                due, length_ = now_ts + max(1.0, length), max(1.0, length)
            elif at:
                due, length_ = at.timestamp(), (row["length"] or 0) + (at.timestamp() - row["due"])
            else:
                due = max(now_ts + 1, row["due"] + (shift or 0))
                length_ = (row["length"] or 0) + (due - row["due"])
            self.s.update(row["id"], due=due, length=length_, state="waiting")
            return f"Changed it to {when.say_duration(length_)}." if length else f"Done, {when.say_left(due - now_ts)} left."
        new = at or datetime.fromtimestamp(row["due"] + (shift or 0))
        if row["repeat"]:
            new = when.first_repeat(row["repeat"], new)
            if new.timestamp() <= now_ts:
                new = when.next_repeat(row["repeat"], new, self.now()) or new
        if new.timestamp() <= now_ts:
            return PASSED
        self.s.update(row["id"], due=new.timestamp(), planned=new.isoformat(timespec="seconds"), state="waiting")
        if row["kind"] == "alarm":
            r = f" {when.say_repeat(row['repeat'])}" if row["repeat"] else ""
            return f"Moved your alarm to {when.say_clock(new)}{r}." if r else f"Moved your alarm to {when.say_time(new, self.now())}."
        return f"Okay, I'll remind you {at_phrase(new, self.now(), soon=False)} instead."
