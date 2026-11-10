"""The calendar's dashboard page (/m/calendar/), its card on the dashboard's front page, its box on the monitor,
and its part of a Google account's settings page (part of the calendar module; module.py puts it together).
"""
from datetime import date, datetime, timedelta

from pip_assistant.core.modules import Call

from .actions import REPEATS, heads_up


class DashboardPage:
    # ---------------------------------------------------------------- the dashboard page
    def occurrences(self, first: date, last: date) -> list[dict]:
        """Every event happening between those days (private ones too: the dashboard shows them, marked)."""
        cals = {c["id"]: c for c in self.store.calendars()}
        accounts, names = self._accounts(), self._people()
        out = []
        for d, at, ev in self.store.between(first, last):
            c = cals.get(ev["calendar_id"], {})
            out.append({"id": ev["id"], "day": d.isoformat(), "at": at.timestamp(), "start": ev["start"], "end": ev["end"],
                        "all_day": bool(ev["all_day"]), "title": ev["title"], "location": ev["location"], "notes": ev["notes"],
                        "repeat": ev["repeat"], "private": bool(ev["private"]), "calendar_id": ev["calendar_id"],
                        "calendar": c.get("name", ""), "color": c.get("color", "#6c5ce7"), "whose": self.whose(c, accounts, names),
                        "writable": bool(c.get("writable", 1)), "pending": ev["pending"],
                        "reminder": ev["reminder"]})
        return out

    def upcoming(self, days: int = 14) -> list[dict]:
        """The dashboard's "next two weeks" list: only calendars chosen for it (D-109)."""
        today = datetime.fromtimestamp(self.ctx.clock()).date()
        listed = {c["id"] for c in self.store.calendars() if c["in_list"]}
        accounts = self._accounts()
        everyone = {c["id"] for c in self.store.calendars()   # calendars Pip may mention to anyone (the room's monitor)
                    if (accounts.get(c["account_id"]) or {"sharing": "everyone"})["sharing"] == "everyone"}
        # A timed event running past midnight is listed once, at its start (not again at "12:00 AM" the next
        # day); several-day all-day events still show on each of their days. One that started before today
        # and is still going (day 2 of a conference) is listed today, so it doesn't disappear.
        return [{"id": o["id"], "title": "Private" if o["private"] else o["title"], "at": o["at"], "all_day": o["all_day"],
                 "repeat": o["repeat"], "private": o["private"], "color": o["color"],
                 "shared": not o["private"] and o["calendar_id"] in everyone}
                for o in self.occurrences(today, today + timedelta(days=days - 1)) if o["calendar_id"] in listed
                and (o["all_day"] or o["repeat"] or o["at"] == o["start"]
                     or datetime.fromtimestamp(o["at"]).date() == today)]

    # ---------------------------------------------------------------- the dashboard's front page and the monitor
    def home(self) -> dict:
        """The "next two weeks" card on the dashboard's front page; its ✕ removes an event (dashboard "delete")."""
        return {"title": "Calendar · next two weeks", "link": "/m/calendar/", "empty": "Nothing on the calendar.",
                "items": self.upcoming(14), "remove": "delete"}

    def tv(self, shown: list[dict]) -> dict:
        """The calendar box on the monitor (static/monitor.js, D-110): today's and tomorrow's events from calendars
        in the two-week list, never private ones nor any from an account that shares only with its owner."""
        events = [{"title": e["title"], "at": e["at"], "all_day": e["all_day"], "color": e["color"]}
                  for e in self.upcoming(2) if e["shared"]]
        return {"calendar": {"events": events[:12]}}

    def room_may_see(self, alert: dict) -> bool | None:
        """An event's heads-up shows on the room's monitor only if Pip may mention that event to anyone."""
        if alert.get("kind") != "event" or not alert.get("event_id"):
            return None
        return self.for_the_room(self.store.event(alert["event_id"]))

    def calendars_view(self, ignored: bool = False) -> list[dict]:
        accounts, names = self._accounts(), self._people()
        return [{"id": c["id"], "name": c["name"], "color": c["color"], "ticked": bool(c["ticked"]), "writable": bool(c["writable"]),
                 "whose": self.whose(c, accounts, names), "household": c["id"] == self.sync.household(),
                 "label": self.label(c, accounts, names), "owner": bool(c["owner"]), "primary": bool(c["is_primary"]) or c["id"] == "pip",
                 "account_id": c["account_id"], "ignored": bool(c["ignored"])}
                for c in self.store.calendars(ignored=ignored)]

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
            try:
                last = date.fromisoformat(str(a.get("end_date") or a.get("date")))
            except ValueError:
                raise ValueError("Pick a last day")
            if last < d:
                raise ValueError("The last day can't be before the first")
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
        try:
            heads = heads_up(None if heads in ("", "none") else heads)
        except ValueError:
            raise ValueError("The heads-up is a number of minutes")
        return {"title": title[:200], "start": start, "end": end, "all_day": all_day, "repeat": rep,
                "location": str(a.get("location") or "")[:200], "notes": str(a.get("notes") or "")[:2000],
                "private": bool(a.get("private")), "heads": heads}

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
        if action in ("new_calendar", "edit_calendar", "delete_calendar", "ignore"):
            return await self._calendar_settings(action, args)
        if action == "all_calendars":   # every calendar, ignored ones too
            return {"calendars": self.calendars_view(ignored=True)}
        if action in ("account_settings", "set_calendar", "set_pref"):
            return self._account_settings(action, args)
        if action == "sync":
            return {"ok": True, "accounts": await self.sync.run_once()}
        return {"error": f"unknown action {action}"}

    HEADS_UP = ("off", "own", "all", "5", "10", "15", "30", "60", "120", "1440")

    def _account_settings(self, action: str, args: dict) -> dict:
        """An account's settings page (D-109): its calendars (use/ignore, calendar page, two-week list, heads-ups),
        no heads-ups at night, and which calendar is the household's."""
        s = self.store
        if action == "set_calendar":
            cal = s.calendar(str(args.get("id")))
            if not cal:
                raise ValueError("That calendar is gone")
            field, value = str(args.get("field")), args.get("value")
            if field == "ignored":
                s.set_ignored(cal["id"], bool(value))
                self._wake.set()
            elif field in ("ticked", "in_list"):
                s.upsert_calendar(cal["id"], **{field: int(bool(value))})
            elif field == "heads_up":
                if str(value) not in self.HEADS_UP:
                    raise ValueError("Unknown heads-up choice")
                s.upsert_calendar(cal["id"], heads_up=str(value))
            else:
                raise ValueError("Unknown setting")
            if field in ("ticked", "heads_up"):
                s.remake_alerts({cal["id"]})
            account = cal["account_id"]
        else:
            account = str(args.get("account") or "")
            if action == "set_pref":
                key, value = str(args.get("key")), args.get("value")
                if key == "night":
                    value = "1" if value in (True, 1, "1", "true") else "0"
                elif key == "household":
                    value = str(value or "")
                    c = s.calendar(value) if value else None
                    if value and (not c or c["account_id"] != account or not c["writable"]):
                        raise ValueError("Pick one of this account's calendars that Pip can add to")
                s.set_pref(account, key, value)
                if key == "night":
                    s.remake_alerts({c["id"] for c in s.calendars(ignored=True) if c["account_id"] == account})
        mine = [c for c in self.calendars_view(ignored=True) if c["account_id"] == account]
        raw = {c["id"]: c for c in s.calendars(ignored=True)}
        for c in mine:
            c.update(in_list=bool(raw[c["id"]]["in_list"]), heads_up=raw[c["id"]]["heads_up"])
        return {"ok": True, "calendars": mine, "night": s.pref(account, "night") == "1",
                "household": s.pref(account, "household"), "household_now": self.sync.household(),
                "heads_ups": self.ctx.alerts.waiting() if self.ctx.alerts else 0}

    async def _calendar_settings(self, action: str, args: dict) -> dict:
        """The dashboard's calendar settings (same as asking Pip; the page asks "are you sure" before deleting)."""
        call = Call(now=datetime.fromtimestamp(self.ctx.clock()))
        if action == "new_calendar":
            p = {"name": self._name(args.get("name")), "color": self._color(args.get("color")), "acc": None}
            if self.google is not None and args.get("account"):
                p["acc"] = self.google.account(str(args["account"]))
            elif self.google is not None:
                p["acc"] = self.check_new_calendar({"name": p["name"]}, call)["acc"]
            said = await self.do_new_calendar(p, call)
        else:
            cal = self.store.calendar(str(args.get("id")))
            if not cal:
                raise ValueError("That calendar is gone")
            if action == "ignore":
                if cal["id"] == "pip":
                    raise ValueError("Pip's own calendar can't be ignored")
                self.store.set_ignored(cal["id"], bool(args.get("value")))
                self._wake.set()
                await self.ctx.publish("changed")
                return {"ok": True, "calendars": self.calendars_view(ignored=True)}
            if action == "delete_calendar":
                if cal["id"] == "pip" or (cal["is_primary"] and cal["account_id"]):
                    raise ValueError("A main calendar can't be deleted")
                said = await self.do_delete_calendar(cal, call)
            else:
                p = {"cal": cal}
                if args.get("name") and args["name"] != cal["name"]:
                    p["name"] = self._name(args["name"])
                if args.get("color") and args["color"] != cal["color"]:
                    p["color"] = self._color(args["color"])
                said = await self.do_edit_calendar(p, call) if len(p) > 1 else "Nothing changed."
        if said.startswith(("I need", "I couldn't")):
            raise ValueError(said)
        return {"ok": True, "said": said}
