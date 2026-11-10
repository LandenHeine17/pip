"""What Claude reads about the calendar, and Claude's calendar actions (part of the calendar module; module.py
puts it together).

    summary         today's and tomorrow's events, how many later this week, the calendars' names
    look:calendar   any other days, on request ({"do": "look", "kind": "calendar", ...}): Claude is asked again
    add / change / cancel: event         one event (by its short id, "#e1a2b3")
    add / change / cancel: calendar      a whole calendar (D-103)

Every action is checked first (check_*: raise Invalid and nothing is done), then done (do_*). Only events
the person asking may hear about can be found or changed (visibility.py may_hear).
"""
import logging
import re
import secrets
from datetime import date, datetime, timedelta

from pip_assistant.common import when
from pip_assistant.core.modules import Call, Invalid

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


class ClaudeActions:
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

    def _by_ref(self, ref: str, call: Call) -> dict:
        """The event Claude means by its id; only one the person asking may hear about (else "unknown event")."""
        ref = str(ref or "").strip().lstrip("#").lower()
        hits = [e for e in self.store.all_events() if len(ref) >= 4 and e["id"].startswith(ref) and self.may_hear(e, call)]
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
        ev = self._by_ref(a.get("id"), call)
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
        if self.google is not None and isinstance(e, self.google.NeedsPermission):
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
        return self._by_ref(a.get("id"), call)

    def do_cancel(self, ev: dict, call: Call) -> str:
        self.store.remove_event(ev["id"])
        return "Done." if ev["private"] else f"Took {ev['title']} off the calendar."

    def say_when(self, ev: dict) -> str:
        at, now = datetime.fromtimestamp(ev["start"]), datetime.fromtimestamp(self.ctx.clock())
        if ev["repeat"]:
            return when.say_repeat(ev["repeat"]) + ("" if ev["all_day"] else f" at {when.say_clock(at)}")
        return when.say_day(at.date(), now) + ("" if ev["all_day"] else f" at {when.say_clock(at)}")

    def on_whose(self, ev: dict) -> str:
        cal = self.store.calendar(ev["calendar_id"]) or {"account_id": "", "is_primary": 0, "name": ""}
        if ev["calendar_id"] == self.sync.household():
            return ""
        who = self.whose(cal, self._accounts(), self._people())
        if cal["is_primary"]:
            return f", on {who}'s calendar" if who else ""
        return f", on {who}'s {cal['name']} calendar" if who else f", on the {cal['name']} calendar"
