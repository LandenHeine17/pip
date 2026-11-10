"""Whose calendar it is, and who may hear about it (part of the calendar module; module.py puts it together).

Whose calendar? An event Pip adds goes on the calendar named ("Work", or a person: "put it on Sam's
calendar"), else the speaker's main one if Pip is sure who it is and they've connected Google, else
the household calendar (picked on an account's Google settings page, or the household_calendar
setting; see sync.py household()).

What Pip may say: private events never (D-99). Each connected person also chooses (on the Google
page) whether Pip may talk about their calendar to everyone, or only to them (recognised "sure").
`may_hear` is that rule for one person; `for_the_room` is it for nobody in particular (the monitor).
"""
import difflib
from datetime import datetime

from pip_assistant.core.modules import Call, Invalid


class Visibility:
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

    def may_hear(self, ev: dict, call: Call) -> bool:
        """May the person asking (`call`) hear about, or change, this event? Used for Claude's actions and for
        Pip's own offline commands (core/commands.py), so both follow the same rule."""
        return self.visible(ev, call, {c["id"]: c for c in self.store.calendars()}, self._accounts())

    def for_the_room(self, ev: dict | None) -> bool:
        """May this event show on the room's monitor? Only if Pip may mention it to anyone (not private, and
        its account shares with everyone): the same rule as telling Claude, for nobody in particular."""
        if not ev:
            return False
        cals = {c["id"]: c for c in self.store.calendars()}
        return self.visible(ev, Call(now=datetime.fromtimestamp(self.ctx.clock())), cals, self._accounts())
