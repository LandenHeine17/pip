"""The calendar module (D-98, D-99, D-101): Pip's calendar, synced both ways with Google.

    store.py   the events on the Pi (also used by Pip's offline commands) and their heads-ups
    sync.py    two-way sync with each connected person's Google calendars
    module.py  this file: Claude's event actions, what Claude is told, the dashboard page's requests,
               and the sync loop

Whose calendar? An event Pip adds goes on the calendar named ("Work", or a person: "put it on Sam's
calendar"), else the speaker's main one if Pip is sure who it is and they've connected Google, else
the household calendar (picked on an account's Google settings page, or the household_calendar
setting; see sync.py household()).

What Pip may say: private events never (D-99). Each connected person also chooses (on the Google
page) whether Pip may talk about their calendar to everyone, or only to them (recognised "sure").
"""
import asyncio
import difflib
import logging
import re
import secrets
from datetime import date, datetime, timedelta

from pip_assistant.core import when
from pip_assistant.core.modules import Action, Call, Invalid, Module

from .store import CalendarStore
from .sync import CalendarSync

log = logging.getLogger(__name__)
MAX_HEADS_UP = 40320   # minutes (4 weeks): the longest reminder Google accepts
REPEATS = ("", "daily", "weekdays", "weekends", "weekly") + tuple(f"weekly:{i}" for i in range(7))


def short(eid: str) -> str:
    return "#" + eid[:6]


def heads_up(minutes) -> float | None:
    """A heads-up asked for, in minutes → seconds before the event. Kept to 0–4 weeks: Google refuses a reminder
    longer than that, and one refused event would hold up the rest of the sync."""
    if minutes is None:
        return None
    try:
        m = float(minutes)
    except TypeError:
        raise ValueError("not a number")
    if m != m:   # nan
        raise ValueError("not a number")
    return min(max(m, 0.0), MAX_HEADS_UP) * 60


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
                        "cancel:event": Action(self.check_cancel, self.do_cancel),
                        "look:calendar": Action(self.check_look, self.do_look),
                        "add:calendar": Action(self.check_new_calendar, self.do_new_calendar),
                        "change:calendar": Action(self.check_edit_calendar, self.do_edit_calendar),
                        "cancel:calendar": Action(self.check_delete_calendar, self.do_delete_calendar)}

    def google_hooks(self) -> None:
        hooks = getattr(self.google, "on_connect", None)
        if hooks is not None:
            hooks.append(lambda _aid: self._wake.set())
        self.google.on_disconnect.append(self.forget_account)

    def forget_account(self, aid: str) -> None:
        """Someone disconnected Google: drop Pip's copy of their calendars (nothing is deleted in Google)."""
        for c in self.store.calendars(ignored=True):
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

    def label(self, cal: dict, accounts: dict, names: dict) -> str:
        """How a calendar is named to Claude and on the dashboard: "Landen's main", "Landen: Work", "Pip's own"."""
        if not cal.get("account_id"):
            return "Pip's own" if cal.get("id") == "pip" else cal.get("name", "?")
        who = self.whose(cal, accounts, names)
        if cal.get("is_primary"):
            return f"{who}'s main" if who else "main"
        return f"{who}: {cal['name']}" if who else cal["name"]

    def find_calendar(self, name: str, call: Call, writable: bool = True) -> dict:
        """A calendar from what Claude called it: a calendar's name ("Work", "Landen: Work"), a person ("Sam" =
        their main calendar), or "household". Unknown or ambiguous → Invalid (nothing is done)."""
        want = " ".join(str(name).lower().replace("’", "'").split())
        if want in ("household", "the household", "house"):
            return self.store.calendar(self.sync.household())
        if want in ("pip", "pip's", "pip's own", "pips", "local"):
            return self.store.calendar("pip")
        accounts, names = self._accounts(), self._people()
        cals = [c for c in self.store.calendars() if c["writable"] or not writable]
        keys = {}
        for c in cals:
            for k in {c["name"].lower(), self.label(c, accounts, names).lower()}:
                keys.setdefault(k, []).append(c)
        hits = keys.get(want) or [c for k in difflib.get_close_matches(want, list(keys), n=3, cutoff=0.8) for c in keys[k]]
        if not hits and self.ctx.people and (p := self.ctx.people.by_name(want.removesuffix("'s").removesuffix(" main"))):
            hits = [c for c in cals if c["is_primary"] and accounts.get(c["account_id"], {}).get("person_id") == p["id"]]
            if not hits:
                raise Invalid(f"{p['name']} hasn't connected a calendar")
        hits = list({c["id"]: c for c in hits}.values())
        if len(hits) > 1 and call.person_id:   # "Work": the speaker's own, if two people have one
            mine = [c for c in hits if accounts.get(c["account_id"], {}).get("person_id") == call.person_id]
            hits = mine or hits
        if len(hits) != 1:
            raise Invalid(f"no single calendar called {name}")
        return hits[0]

    def target(self, name: str, call: Call) -> str:
        """The calendar an event goes into: the one named, else the speaker's main one, else the household's."""
        if name:
            return self.find_calendar(name, call)["id"]
        if self.google is None:
            return "pip"
        person = call.person_id
        if person and (acc := self.google.for_person(person)) and (cid := self.sync.primary(acc["id"])):
            return cid
        return self.sync.household()

    def visible(self, ev: dict, call: Call, cals: dict, accounts: dict) -> bool:
        """May Pip mention this event to whoever is asking?"""
        if ev["private"]:
            return False
        acc = accounts.get((cals.get(ev["calendar_id"]) or {}).get("account_id", ""))
        return not acc or acc["sharing"] == "everyone" or acc["person_id"] == call.person_id

    def for_the_room(self, ev: dict | None) -> bool:
        """May this event show on the room's monitor? Only if Pip may mention it to anyone (not private, and
        its account shares with everyone): the same rule as telling Claude, for nobody in particular."""
        if not ev:
            return False
        cals = {c["id"]: c for c in self.store.calendars()}
        return self.visible(ev, Call(now=datetime.fromtimestamp(self.ctx.clock())), cals, self._accounts())

    # ---------------------------------------------------------------- what Claude reads
    def line(self, ev: dict, at: datetime, cals: dict, accounts: dict, names: dict, day: bool = False) -> str:
        # A one-off event listed on a later day of itself (overnight, several days) shows its real start, and
        # the days whenever it doesn't start and end on the same day: "Sun 09:00–Tue 17:00".
        start = at if ev["repeat"] else datetime.fromtimestamp(ev["start"])
        end = start + (datetime.fromtimestamp(ev["end"]) - datetime.fromtimestamp(ev["start"]))
        hours = f"{start:%H:%M}–{end:%H:%M}" if (end - timedelta(seconds=1)).date() == start.date() else \
            f"{start:%a %H:%M}–{end:%a %H:%M}"
        t = (f"{at:%a %Y-%m-%d} " if day else "") + ("all day" if ev["all_day"] else hours)
        rep = f" ({when.say_repeat(ev['repeat'])})" if ev["repeat"] else ""
        where = f" at {ev['location']}" if ev["location"] else ""
        cal = "" if ev["calendar_id"] == self.sync.household() else \
            f" [{self.label(cals.get(ev['calendar_id'], {'account_id': ''}), accounts, names)}]"
        return f"{short(ev['id'])} {ev['title']} {t}{rep}{where}{cal}"

    def summary(self, call: Call) -> str:
        """Kept short, because it goes with every request (D-102): today and tomorrow in full, a count for the
        rest of the week, and the calendars' names. Claude looks anything else up first (ACTION look).
        A join-in check (every ~40 s) gets only what's left of today."""
        cals = {c["id"]: c for c in self.store.calendars()}
        accounts, names = self._accounts(), self._people()
        now = datetime.fromtimestamp(self.ctx.clock())
        today = now.date()
        evs = [e for e in self.store.all_events() if self.visible(e, call, cals, accounts)]
        on = {d: self.store.events_on(d, evs) for d in (today + timedelta(days=i) for i in range(7))}
        later_today = [(at, e) for at, e in on[today] if e["all_day"] or e["end"] > now.timestamp()]

        def day(items):
            return "; ".join(self.line(e, at, cals, accounts, names) for at, e in items) or "nothing"
        if call.kind == "join":
            return f"Calendar, rest of today: {day(later_today)}." if later_today else ""
        tomorrow = today + timedelta(days=1)
        out = f"Calendar today: {day(later_today)}. Tomorrow ({tomorrow:%a %Y-%m-%d}): {day(on[tomorrow])}."
        rest = [f"{d:%a} {len(on[d])}" for d in sorted(on)[2:] if on[d]]
        out += (" Later this week (events per day): " + ", ".join(rest) + "." if rest else " Nothing else this week.")
        out += " For any other day, or the details of those, look it up first (ACTION look)."
        labels = []
        for c in cals.values():
            tags = [t for t, on_ in (("household", c["id"] == self.sync.household()), ("read-only", not c["writable"]),
                                     ("hidden", not c["ticked"]), ("shared with them", c["account_id"] and c["writable"] and not c["owner"])) if on_]
            labels.append(self.label(c, accounts, names) + (f" ({', '.join(tags)})" if tags else ""))
        out += " Calendars: " + "; ".join(labels) + "."
        return out

    # ---------------------------------------------------------------- looking further ahead (ACTION look)
    def check_look(self, a: dict, call: Call) -> dict:
        try:
            first = date.fromisoformat(str(a.get("from") or a.get("date"))[:10])
            last = date.fromisoformat(str(a.get("to") or a.get("from") or a.get("date"))[:10])
        except ValueError:
            raise Invalid("bad dates")
        if last < first or (last - first).days > 30:
            raise Invalid("look at most a month at a time")
        if abs((first - call.now.date()).days) > 400:
            raise Invalid("too far away")
        cal = self.find_calendar(str(a["calendar"]), call, writable=False) if a.get("calendar") else None
        return {"first": first, "last": last, "cal": cal}

    def do_look(self, p: dict, call: Call) -> str:
        cals = {c["id"]: c for c in self.store.calendars()}
        accounts, names = self._accounts(), self._people()
        evs = [e for e in self.store.all_events() if self.visible(e, call, cals, accounts)
               and (p["cal"] is None or e["calendar_id"] == p["cal"]["id"])]
        items = [self.line(e, at, cals, accounts, names, day=True) for _, at, e in self.store.between(p["first"], p["last"], evs)]
        span = f"{p['first']:%a %Y-%m-%d}" + (f" to {p['last']:%a %Y-%m-%d}" if p["last"] != p["first"] else "")
        which = f" ({self.label(p['cal'], accounts, names)})" if p["cal"] else ""
        return f"Calendar{which}, {span}: " + ("; ".join(items) if items else "nothing") + "."

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
        try:
            heads = heads_up(a.get("heads_up_minutes"))
        except ValueError:
            raise Invalid("bad heads-up")
        return {"title": title, "start": start, "end": end, "all_day": all_day, "repeat": rep,
                "heads": heads, "location": str(a.get("location") or ""),
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
            try:
                shift = float(a["shift_seconds"])
            except (TypeError, ValueError):
                raise Invalid("bad shift")
            if not abs(shift) <= 366 * 86400:   # also catches nan / inf
                raise Invalid("shift too big")
            # By the clock on the wall: "a day later" over a clock change is still 9:00 (or midnight, all day)
            p["start"] = datetime.fromtimestamp(ev["start"]) + timedelta(seconds=shift)
            if not call.now - timedelta(days=1) < p["start"] < call.now + timedelta(days=366):
                raise Invalid("time not in the coming year")
        if a.get("end"):
            p["end"] = self._time(a["end"], call)
        elif "start" in p:   # keep its length as the clock shows it (a 9–10 meeting stays 9–10 over a clock change)
            p["end"] = p["start"] + (datetime.fromtimestamp(ev["end"]) - datetime.fromtimestamp(ev["start"]))
        for k in ("title", "location"):
            if a.get(k):
                p[k] = str(a[k])[:200]
        if "private" in a:
            p["private"] = bool(a["private"])
        if a.get("calendar"):
            to = self.find_calendar(str(a["calendar"]), call)
            if to["id"] != ev["calendar_id"]:
                p["to"] = to
        if len(p) == 1:
            raise Invalid("change without anything to change")
        return p

    def do_change(self, p: dict, call: Call):
        if "to" in p:
            return self._move_then_change(p, call)
        ev = p.pop("ev")
        start = p.pop("start", None)
        all_day = bool(ev["all_day"]) and start is not None and start.time() == datetime.min.time() if start else None
        new = self.store.update_event(ev["id"], start=start, all_day=all_day, end=p.pop("end", None), **p)
        if new["private"]:
            return "Done."
        return f"Moved {new['title']} to {self.say_when(new)}." if start else f"Updated {new['title']}."

    async def _move_then_change(self, p: dict, call: Call) -> str:
        to = p.pop("to")
        try:
            moved = await self.sync.move_event(p["ev"], to)
        except Exception as e:
            return self._google_trouble(e)
        p["ev"] = moved
        said = f"Moved {moved['title']} to {self.label(to, self._accounts(), self._people())}."
        if len(p) > 1:
            self.do_change(p, call)
        return "Done." if moved["private"] else said

    def _google_trouble(self, e: Exception) -> str:
        from pip_assistant.modules.google.module import NeedsPermission
        if isinstance(e, NeedsPermission):
            return ("I need one more Google permission for that: reconnect Google on the Google page (press Connect Google again) and tick Select all. "
                    "Nothing was changed.")
        log.info("calendar change failed: %s", type(e).__name__)
        return "I couldn't reach Google just now, so nothing was changed."

    # ---------------------------------------------------------------- whole calendars (D-103)
    COLORS = {"red": "#d50000", "orange": "#f4511e", "yellow": "#f6bf26", "green": "#0b8043", "teal": "#039be5",
              "blue": "#3f51b5", "purple": "#8e24aa", "pink": "#e67c73", "grey": "#616161", "gray": "#616161",
              "brown": "#795548", "black": "#333333"}

    def _color(self, value) -> str | None:
        if not value:
            return None
        v = str(value).strip().lower()
        if v in self.COLORS:
            return self.COLORS[v]
        if re.fullmatch(r"#[0-9a-f]{6}", v):
            return v
        raise Invalid("unknown colour")

    def _name(self, value) -> str:
        name = " ".join(str(value or "").split())[:60]
        if not name:
            raise Invalid("calendar without a name")
        return name

    def check_new_calendar(self, a: dict, call: Call) -> dict:
        name, color = self._name(a.get("name") or a.get("title")), self._color(a.get("color"))
        acc = None
        if self.google is not None:
            who = str(a.get("whose") or "")
            pid = (self.ctx.people.by_name(who) or {}).get("id") if who and self.ctx.people else call.person_id
            acc = self.google.for_person(pid) if pid else None
            if acc is None:
                home = self.store.calendar(self.sync.household()) or {}
                acc = self._accounts().get(home.get("account_id", ""))
        if any(c["name"].lower() == name.lower() and c["account_id"] == (acc or {}).get("id", "") for c in self.store.calendars()):
            raise Invalid("there's already a calendar with that name")
        return {"name": name, "color": color, "acc": acc}

    async def do_new_calendar(self, p: dict, call: Call) -> str:
        if p["acc"] is None:   # nobody has connected Google: one of Pip's own
            cid = "pip:" + secrets.token_hex(4)
            self.store.upsert_calendar(cid, name=p["name"], color=p["color"] or "#6c5ce7", ticked=1, writable=1, owner=1)
        else:
            try:
                cid = await self.sync.create_calendar(p["acc"], p["name"], p["color"])
            except Exception as e:
                return self._google_trouble(e)
        await self.ctx.publish("changed")
        cal = self.store.calendar(cid)
        return f"Made a new calendar: {self.label(cal, self._accounts(), self._people())}."

    def check_edit_calendar(self, a: dict, call: Call) -> dict:
        cal = self.find_calendar(str(a.get("calendar") or a.get("id") or ""), call, writable=False)
        p = {"cal": cal}
        if a.get("name"):
            if not cal["owner"] or cal["is_primary"] and cal["account_id"]:
                raise Invalid("can't rename that calendar")
            p["name"] = self._name(a["name"])
        if a.get("color"):
            p["color"] = self._color(a["color"])
        for k in ("shown", "ignored"):
            if k in a:
                p[k] = bool(a[k])
        if len(p) == 1:
            raise Invalid("nothing to change")
        return p

    async def do_edit_calendar(self, p: dict, call: Call) -> str:
        cal = p["cal"]
        accounts, names = self._accounts(), self._people()
        before = self.label(cal, accounts, names)
        try:
            if cal["account_id"] and ("name" in p or "color" in p):
                acc = self.sync.account(cal)
                if "name" in p:
                    await self.sync.rename_calendar(acc, cal, p["name"])
                if "color" in p:
                    await self.sync.recolor(acc, cal, p["color"])
            elif fields := {k: p[k] for k in ("name", "color") if k in p}:
                self.store.upsert_calendar(cal["id"], **fields)
        except Exception as e:
            return self._google_trouble(e)
        if "shown" in p:
            self.store.upsert_calendar(cal["id"], ticked=int(p["shown"]))
            self.store.refresh_alerts()
        if p.get("ignored"):
            self.store.set_ignored(cal["id"], True)
        await self.ctx.publish("changed")
        if p.get("ignored"):
            return f"I'll ignore {before} from now on. You can bring it back on the Google page."
        if "name" in p:
            return f"Renamed {before} to {p['name']}."
        if "shown" in p and len(p) == 2:
            return f"{'Showing' if p['shown'] else 'Hiding'} {before}."
        return f"Updated {before}."

    def check_delete_calendar(self, a: dict, call: Call) -> dict:
        cal = self.find_calendar(str(a.get("calendar") or a.get("id") or ""), call, writable=False)
        if cal["id"] == "pip" or (cal["is_primary"] and cal["account_id"]):
            raise Invalid("can't delete a main calendar")
        if a.get("confirmed") is not True:
            raise Invalid("deleting a calendar needs a spoken yes first")
        return cal

    async def do_delete_calendar(self, cal: dict, call: Call) -> str:
        name = self.label(cal, self._accounts(), self._people())
        try:
            if cal["account_id"]:
                await self.sync.delete_calendar(self.sync.account(cal), cal)
            else:
                self.store.remove_calendar(cal["id"])
        except Exception as e:
            return self._google_trouble(e)
        await self.ctx.publish("changed")
        return f"Deleted {name}." if cal["owner"] else f"Took {name} off the list."

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
        cal = self.store.calendar(ev["calendar_id"]) or {"account_id": "", "is_primary": 0, "name": ""}
        if ev["calendar_id"] == self.sync.household():
            return ""
        who = self.whose(cal, self._accounts(), self._people())
        if cal["is_primary"]:
            return f", on {who}'s calendar" if who else ""
        return f", on {who}'s {cal['name']} calendar" if who else f", on the {cal['name']} calendar"

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
                "heads_ups": len([a for a in (self.ctx.schedule.active(("event",)) if self.ctx.schedule else [])])}

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
