"""The calendar module (D-98, D-99, D-101): Pip's calendar, synced both ways with Google.

    store.py   the events on the Pi (also used by Pip's offline commands) and their heads-ups
    sync.py    two-way sync with each connected person's Google calendars
    module.py  this file: Claude's event actions, what Claude is told, the dashboard page's requests,
               and the sync loop

Whose calendar? An event Pip adds goes on the calendar of the person named ("put it on Sam's
calendar"), else the speaker's if Pip is sure who it is and they've connected Google, else the
household calendar (setting household_calendar).

What Pip may say: private events never (D-99). Each connected person also chooses (on the Google
page) whether Pip may talk about their calendar to everyone, or only to them (recognised "sure").
"""
import asyncio
import logging
from datetime import date, datetime, timedelta

from pip_assistant.core import when
from pip_assistant.core.modules import Action, Call, Invalid, Module

from .store import CalendarStore
from .sync import CalendarSync

log = logging.getLogger(__name__)
REPEATS = ("", "daily", "weekdays", "weekends", "weekly") + tuple(f"weekly:{i}" for i in range(7))


def short(eid: str) -> str:
    return "#" + eid[:6]


class Calendar(Module):
    def __init__(self, ctx):
        self.ctx = ctx
        self.google = ctx.module("google")
        self.store = CalendarStore(ctx.db(), ctx.schedule, ctx.clock)
        self.api = self.store
        self.sync = CalendarSync(self.store, self.google, ctx.settings, ctx.clock)
        self._wake = asyncio.Event()
        self.store.on_change.append(lambda _eid: self._wake.set())
        if self.google is not None:
            self.google_hooks()
        self.actions = {"add:event": Action(self.check_add, self.do_add),
                        "change:event": Action(self.check_change, self.do_change),
                        "cancel:event": Action(self.check_cancel, self.do_cancel)}

    def google_hooks(self) -> None:
        hooks = getattr(self.google, "on_connect", None)
        if hooks is not None:
            hooks.append(lambda _aid: self._wake.set())
        self.google.on_disconnect.append(self.forget_account)

    def forget_account(self, aid: str) -> None:
        """Someone disconnected Google: drop Pip's copy of their calendars (nothing is deleted in Google)."""
        for c in self.store.calendars():
            if c["account_id"] == aid:
                self.store.remove_calendar(c["id"])

    # ---------------------------------------------------------------- whose calendar, and who may hear about it
    def _people(self) -> dict:
        p = self.ctx.people
        return {x["id"]: x["name"] for x in p.all()} if p else {}

    def _accounts(self) -> dict:
        return {a["id"]: a for a in (self.google.accounts() if self.google else [])}

    def whose(self, cal: dict, accounts: dict, names: dict) -> str:
        acc = accounts.get(cal["account_id"])
        return names.get(acc["person_id"], "") if acc else ""

    def target(self, name: str, call: Call) -> str:
        """The calendar an event goes into."""
        if self.google is None:
            return "pip"
        person = None
        if name:
            p = self.ctx.people.by_name(name) if self.ctx.people else None
            if not p:
                raise Invalid(f"no person called {name}")
            person = p["id"]
        else:
            person = call.person_id
        if person and (acc := self.google.for_person(person)) and (cid := self.sync.primary(acc["id"])):
            return cid
        if name:
            raise Invalid(f"{name} hasn't connected a calendar")
        return self.sync.household()

    def visible(self, ev: dict, call: Call, cals: dict, accounts: dict) -> bool:
        """May Pip mention this event to whoever is asking?"""
        if ev["private"]:
            return False
        acc = accounts.get((cals.get(ev["calendar_id"]) or {}).get("account_id", ""))
        return not acc or acc["sharing"] == "everyone" or acc["person_id"] == call.person_id

    # ---------------------------------------------------------------- what Claude reads
    def summary(self, call: Call) -> str:
        cals = {c["id"]: c for c in self.store.calendars()}
        accounts, names = self._accounts(), self._people()
        today = datetime.fromtimestamp(self.ctx.clock()).date()
        evs = [e for e in self.store.all_events() if self.visible(e, call, cals, accounts)]
        items, seen = [], set()
        for d, at, ev in self.store.between(today, today + timedelta(days=13), evs):
            if ev["id"] in seen:
                continue
            seen.add(ev["id"])
            end = datetime.fromtimestamp(ev["end"])
            t = f"{at:%a %Y-%m-%d}" + (" all day" if ev["all_day"] else f" {at:%H:%M}–{end:%H:%M}")
            rep = f" ({when.say_repeat(ev['repeat'])})" if ev["repeat"] else ""
            who = self.whose(cals.get(ev["calendar_id"], {"account_id": ""}), accounts, names)
            items.append(f"{short(ev['id'])} {ev['title']} {t}{rep}" + (f" at {ev['location']}" if ev["location"] else "")
                         + (f" ({who}'s calendar)" if who else ""))
        out = ("Calendar, next two weeks: " + "; ".join(items) + ".") if items else "Nothing on the calendar in the next two weeks."
        connected = sorted({names.get(a["person_id"], "?") for a in accounts.values() if a["status"] == "ok"})
        if connected:
            out += f" Connected Google calendars: {', '.join(connected)}."
        return out

    def find(self, ref: str) -> str | None:
        ref = ref.strip().lstrip("#").lower()
        if len(ref) < 4:
            return None
        hits = [e for e in self.store.all_events() if e["id"].startswith(ref)]
        return "event" if len(hits) == 1 else None

    def _by_ref(self, ref: str) -> dict:
        ref = str(ref or "").strip().lstrip("#").lower()
        hits = [e for e in self.store.all_events() if len(ref) >= 4 and e["id"].startswith(ref)]
        if len(hits) != 1:
            raise Invalid("unknown event")
        return hits[0]

    # ---------------------------------------------------------------- Claude's actions
    def _time(self, value, call: Call) -> datetime:
        try:
            at = datetime.fromisoformat(str(value)).replace(tzinfo=None, second=0, microsecond=0)
        except ValueError:
            raise Invalid("bad time")
        if not call.now - timedelta(days=1) < at < call.now + timedelta(days=366):
            raise Invalid("time not in the coming year")
        return at

    def check_add(self, a: dict, call: Call) -> dict:
        title = str(a.get("title") or a.get("label") or "").strip()[:200]
        if not title:
            raise Invalid("event without title")
        rep = str(a.get("repeat") or "")
        if rep not in REPEATS:
            raise Invalid("bad repeat")
        if a.get("all_day") or (a.get("date") and not a.get("at")):
            try:
                d = date.fromisoformat(str(a.get("date") or a.get("at"))[:10])
            except ValueError:
                raise Invalid("bad date")
            if not call.now.date() <= d <= call.now.date() + timedelta(days=366):
                raise Invalid("date not in the coming year")
            start, all_day = datetime.combine(d, datetime.min.time()), True
            end = start + timedelta(days=1)
        else:
            start, all_day = self._time(a.get("at"), call), False
            if start < call.now:
                raise Invalid("time already passed")
            end = self._time(a["end"], call) if a.get("end") else start + timedelta(hours=1)
            if end <= start:
                raise Invalid("ends before it starts")
        heads = a.get("heads_up_minutes")
        return {"title": title, "start": start, "end": end, "all_day": all_day, "repeat": rep,
                "heads": float(heads) * 60 if heads is not None else None, "location": str(a.get("location") or ""),
                "private": bool(a.get("private")), "calendar_id": self.target(str(a.get("calendar") or ""), call)}

    def do_add(self, p: dict, call: Call) -> str:
        ev = self.store.add_event(p["title"], p["start"], all_day=p["all_day"], repeat=p["repeat"], heads_up_before=p["heads"],
                                  end=p["end"], location=p["location"], private=p["private"], calendar_id=p["calendar_id"])
        if ev["private"]:
            return "Added. It's private, so I won't bring it up."
        return f"Added {ev['title']}, {self.say_when(ev)}{self.on_whose(ev)}."

    def check_change(self, a: dict, call: Call) -> dict:
        ev = self._by_ref(a.get("id"))
        p = {"ev": ev}
        if a.get("at"):
            p["start"] = self._time(a["at"], call)
        elif a.get("shift_seconds"):
            p["start"] = datetime.fromtimestamp(ev["start"] + float(a["shift_seconds"]))
        if a.get("end"):
            p["end"] = self._time(a["end"], call)
        for k in ("title", "location"):
            if a.get(k):
                p[k] = str(a[k])[:200]
        if "private" in a:
            p["private"] = bool(a["private"])
        if len(p) == 1:
            raise Invalid("change without anything to change")
        return p

    def do_change(self, p: dict, call: Call) -> str:
        ev = p.pop("ev")
        start = p.pop("start", None)
        all_day = bool(ev["all_day"]) and start is not None and start.time() == datetime.min.time() if start else None
        new = self.store.update_event(ev["id"], start=start, all_day=all_day, end=p.pop("end", None), **p)
        if new["private"]:
            return "Done."
        return f"Moved {new['title']} to {self.say_when(new)}." if start else f"Updated {new['title']}."

    def check_cancel(self, a: dict, call: Call) -> dict:
        return self._by_ref(a.get("id"))

    def do_cancel(self, ev: dict, call: Call) -> str:
        self.store.remove_event(ev["id"])
        return "Done." if ev["private"] else f"Took {ev['title']} off the calendar."

    def say_when(self, ev: dict) -> str:
        at, now = datetime.fromtimestamp(ev["start"]), datetime.fromtimestamp(self.ctx.clock())
        if ev["repeat"]:
            return when.say_repeat(ev["repeat"]) + ("" if ev["all_day"] else f" at {when.say_clock(at)}")
        return when.say_day(at.date(), now.date()) + ("" if ev["all_day"] else f" at {when.say_clock(at)}")

    def on_whose(self, ev: dict) -> str:
        cal = self.store.calendar(ev["calendar_id"]) or {"account_id": ""}
        who = self.whose(cal, self._accounts(), self._people())
        return f", on {who}'s calendar" if who and ev["calendar_id"] != self.sync.household() else ""

    # ---------------------------------------------------------------- the dashboard page
    def occurrences(self, first: date, last: date, include_private: bool = True) -> list[dict]:
        cals = {c["id"]: c for c in self.store.calendars()}
        accounts, names = self._accounts(), self._people()
        out = []
        for d, at, ev in self.store.between(first, last):
            if ev["private"] and not include_private:
                continue
            c = cals.get(ev["calendar_id"], {})
            out.append({"id": ev["id"], "day": d.isoformat(), "at": at.timestamp(), "start": ev["start"], "end": ev["end"],
                        "all_day": bool(ev["all_day"]), "title": ev["title"], "location": ev["location"], "notes": ev["notes"],
                        "repeat": ev["repeat"], "private": bool(ev["private"]), "calendar_id": ev["calendar_id"],
                        "calendar": c.get("name", ""), "color": c.get("color", "#6c5ce7"), "whose": self.whose(c, accounts, names),
                        "writable": bool(c.get("writable", 1)), "pending": ev["pending"],
                        "reminder": ev["reminder"]})
        return out

    def upcoming(self, days: int = 14) -> list[dict]:
        today = datetime.fromtimestamp(self.ctx.clock()).date()
        return [{"id": o["id"], "title": "Private" if o["private"] else o["title"], "at": o["at"], "all_day": o["all_day"],
                 "repeat": o["repeat"], "private": o["private"], "color": o["color"]}
                for o in self.occurrences(today, today + timedelta(days=days - 1))]

    def calendars_view(self) -> list[dict]:
        accounts, names = self._accounts(), self._people()
        return [{"id": c["id"], "name": c["name"], "color": c["color"], "ticked": bool(c["ticked"]), "writable": bool(c["writable"]),
                 "whose": self.whose(c, accounts, names), "household": c["id"] == self.sync.household()}
                for c in self.store.calendars()]

    def _form(self, a: dict) -> dict:
        title = str(a.get("title") or "").strip()
        if not title:
            raise ValueError("A title is needed")
        try:
            d = date.fromisoformat(str(a.get("date")))
        except ValueError:
            raise ValueError("Pick a date")
        all_day = bool(a.get("all_day"))
        if all_day:
            last = date.fromisoformat(str(a.get("end_date") or a.get("date")))
            start = datetime.combine(d, datetime.min.time())
            end = datetime.combine(last, datetime.min.time()) + timedelta(days=1)
        else:
            try:
                start = datetime.combine(d, datetime.strptime(str(a.get("start") or "09:00"), "%H:%M").time())
                end = datetime.combine(d, datetime.strptime(str(a.get("end") or ""), "%H:%M").time()) if a.get("end") \
                    else start + timedelta(hours=1)
            except ValueError:
                raise ValueError("Times look like 14:30")
            if end <= start:
                end += timedelta(days=1)   # ends after midnight
        rep = str(a.get("repeat") or "")
        if rep not in REPEATS:
            raise ValueError("Unknown repeat")
        heads = a.get("heads_up")
        return {"title": title[:200], "start": start, "end": end, "all_day": all_day, "repeat": rep,
                "location": str(a.get("location") or "")[:200], "notes": str(a.get("notes") or "")[:2000],
                "private": bool(a.get("private")),
                "heads": float(heads) * 60 if heads not in (None, "", "none") else None}

    async def dashboard(self, action: str, args: dict) -> dict:
        s = self.store
        if action == "range":
            first, last = date.fromisoformat(args["from"]), date.fromisoformat(args["to"])
            if (last - first).days > 62:
                raise ValueError("At most two months at a time")
            return {"events": self.occurrences(first, last), "calendars": self.calendars_view(),
                    "household": self.sync.household(), "now": self.ctx.clock()}
        if action == "add":
            f = self._form(args)
            cid = str(args.get("calendar_id") or self.sync.household())
            ev = s.add_event(f["title"], f["start"], all_day=f["all_day"], repeat=f["repeat"], heads_up_before=f["heads"],
                             end=f["end"], location=f["location"], notes=f["notes"], private=f["private"], calendar_id=cid)
            return {"ok": True, "id": ev["id"]}
        if action == "edit":
            ev = s.event(str(args.get("id")))
            if not ev:
                raise ValueError("That event is gone")
            f = self._form(args)
            s.update_event(ev["id"], start=f["start"], end=f["end"], all_day=f["all_day"], title=f["title"],
                           location=f["location"], notes=f["notes"], private=f["private"], repeat=f["repeat"],
                           reminder=f["heads"] / 60 if f["heads"] is not None else None)
            return {"ok": True}
        if action == "delete":
            s.remove_event(str(args.get("id")))
            return {"ok": True}
        if action == "tick":
            s.upsert_calendar(str(args.get("id")), ticked=int(bool(args.get("value"))))
            for ev in s.db.execute("SELECT * FROM calendar_events WHERE calendar_id = ?", (str(args.get("id")),)).fetchall():
                s.sync_alerts(dict(ev))
            self._wake.set()
            return {"ok": True}
        if action == "sync":
            return {"ok": True, "accounts": await self.sync.run_once()}
        return {"error": f"unknown action {action}"}

    # ---------------------------------------------------------------- background
    def tasks(self) -> list:
        return [self._loop()]

    async def _loop(self) -> None:
        last_tidy = 0.0
        while True:
            seen = self.store.version
            if self.ctx.online:
                await self.sync.run_once()
            if self.store.version != seen:
                await self.ctx.publish("changed")
            if self.ctx.clock() - last_tidy > 3600:
                self.store.prune()
                self.store.refresh_alerts()
                last_tidy = self.ctx.clock()
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), self.ctx.settings["sync_minutes"] * 60)
                await asyncio.sleep(1.0)   # let a burst of changes settle
            except asyncio.TimeoutError:
                pass


def setup(ctx) -> Module:
    return Calendar(ctx)
