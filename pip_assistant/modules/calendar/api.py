"""What other modules may use of the calendar: `ctx.module("calendar")`, if their module.toml `needs` it.

A module only ever gets the events the person asking (`call`, core/modules.Call) may hear about: the
calendar's own privacy rule (module.py may_hear: private events never, an "only me" account's only to
its owner). Anything more, add it here on purpose.

    cal = ctx.module("calendar")
    cal.events_on(date.today(), call)    # [{"id", "title", "at", "all_day", "location"}], by time
    cal.add_event("dentist", datetime(2026, 11, 13, 15), call)   # on the speaker's calendar, or the household's
"""
from datetime import date, datetime


class CalendarAPI:
    def __init__(self, calendar):
        self._cal = calendar   # module.py's Calendar

    def events_on(self, day: date, call) -> list[dict]:
        """That day's events the person asking may hear about."""
        return [{"id": ev["id"], "title": ev["title"], "at": at.timestamp(), "all_day": bool(ev["all_day"]),
                 "location": ev.get("location", "")}
                for at, ev in self._cal.store.events_on(day) if self._cal.may_hear(ev, call)]

    def add_event(self, title: str, at: datetime, call, all_day: bool = False) -> dict:
        """Add an event (to the speaker's main calendar if Pip is sure who it is, else the household's)."""
        ev = self._cal.store.add_event(title, at, all_day=all_day, calendar_id=self._cal.target("", call))
        return {"id": ev["id"], "title": ev["title"]}
