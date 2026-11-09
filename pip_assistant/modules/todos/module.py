"""The to-dos module (D-100): household and personal to-do lists, synced both ways with Google Tasks.

    store.py   the to-dos on the Pi
    sync.py    two-way sync with each connected person's Google Tasks
    module.py  this file: Claude's to-do actions, what Claude is told, the dashboard page, the sync loop

Whose list: the person named ("add it to Sam's list"), else the speaker's if Pip is sure who it is
and they've connected Google, else the household's (the first list of a connected account, admins'
first, or Pip's own "Household" list). Sharing follows each person's setting on the Google page, like
the calendar. core/dashapi.py asks screen_list() for the to-do boxes on the monitor.
"""
import asyncio
from datetime import date, datetime

from pip_assistant.core import when
from pip_assistant.core.modules import Action, Call, Invalid, Module

from .store import TodoStore
from .sync import TodoSync


def short(tid: str) -> str:
    return "#t" + tid[:5]


class Todos(Module):
    def __init__(self, ctx):
        self.ctx = ctx
        self.google = ctx.module("google")
        self.store = TodoStore(ctx.db(), ctx.clock)
        self.api = self.store
        self.sync = TodoSync(self.store, self.google, ctx.clock)
        self._wake = asyncio.Event()
        self.store.on_change.append(self._wake.set)
        if self.google is not None:
            self.google.on_connect.append(lambda _aid: self._wake.set())
            self.google.on_disconnect.append(
                lambda aid: [self.store.remove_list(x["id"]) for x in self.store.lists(ignored=True) if x["account_id"] == aid])
        self.actions = {"add_task": Action(self.check_add, self.do_add),
                        "complete_task": Action(self.check_ref, self.do_complete),
                        "change_task": Action(self.check_change, self.do_change),
                        "delete_task": Action(self.check_ref, self.do_delete)}

    # ---------------------------------------------------------------- whose list, who may hear
    def _accounts(self) -> dict:
        return {a["id"]: a for a in (self.google.accounts() if self.google else [])}

    def _names(self) -> dict:
        return {p["id"]: p["name"] for p in self.ctx.people.all()} if self.ctx.people else {}

    def whose(self, lst: dict, accounts: dict, names: dict) -> str:
        acc = accounts.get(lst.get("account_id", ""))
        return names.get(acc["person_id"], "") if acc else ""

    def household(self) -> str:
        """The household's list: an admin's (else anyone's) connected Google account's first list, or Pip's own."""
        for a in (self.google.household_accounts() if self.google else []):
            if lid := self.sync.default_list(a["id"]):
                return lid
        return "pip"

    def target(self, name: str, call: Call) -> str:
        """The list a new to-do goes on: that person's (Invalid if they haven't connected Google), else the
        speaker's, else the household's."""
        person = None
        if name:
            p = self.ctx.people.by_name(name) if self.ctx.people else None
            if not p:
                raise Invalid(f"no person called {name}")
            person = p["id"]
        else:
            person = call.person_id
        if person and self.google and (acc := self.google.for_person(person)) and (lid := self.sync.default_list(acc["id"])):
            return lid
        if name:
            raise Invalid(f"{name} hasn't connected Google")
        return self.household()

    def visible(self, t: dict, call: Call, lists: dict, accounts: dict) -> bool:
        """May Pip mention this to-do to whoever is asking?"""
        acc = accounts.get((lists.get(t["list_id"]) or {}).get("account_id", ""))
        return not acc or acc["sharing"] == "everyone" or acc["person_id"] == call.person_id

    def screen_list(self, name: str = "") -> dict:
        """A to-do list for the monitor (D-111): its open to-dos, by name, or the household's. Only a
        list Pip may mention to everyone goes on the screen."""
        lists = self.store.lists()
        want = str(name or "").strip().lower()
        lst = (next((x for x in lists if x["name"].lower() == want), None)
               or next((x for x in lists if want and want in x["name"].lower()), None)) if want else \
            next((x for x in lists if x["id"] == self.household()), None)
        if not lst:
            return {"name": name or "To-dos", "missing": True, "items": []}
        acc = self._accounts().get(lst.get("account_id", ""))
        if acc and acc["sharing"] != "everyone":
            return {"name": lst["name"], "private": True, "items": []}
        return {"name": lst["name"], "items": [t["title"] for t in self.store.items() if t["list_id"] == lst["id"]][:20]}

    # ---------------------------------------------------------------- what Claude reads
    def summary(self, call: Call) -> str:
        if call.kind == "join":   # join-in checks run every ~40 s: keep them small (D-102)
            return ""
        lists = {x["id"]: x for x in self.store.lists()}
        accounts, names = self._accounts(), self._names()
        items = [t for t in self.store.items() if self.visible(t, call, lists, accounts)][:40]
        if not items:
            return "No open to-dos."
        out = []
        for t in items:
            who = self.whose(lists.get(t["list_id"], {}), accounts, names)
            due = f", due {t['due']}" if t["due"] else ""
            out.append(f"{short(t['id'])} {t['title']}{due}" + (f" ({who}'s list)" if who else ""))
        return "Open to-dos: " + "; ".join(out) + "."

    def find(self, ref: str) -> str | None:
        return "task" if self._match(ref) else None

    def _match(self, ref) -> dict | None:
        ref = str(ref or "").strip().lstrip("#").lower()
        ref = ref[1:] if ref.startswith("t") and len(ref) >= 5 else ref
        hits = [t for t in self.store.items(include_done_since=0) if len(ref) >= 4 and t["id"].startswith(ref)]
        return hits[0] if len(hits) == 1 else None

    # ---------------------------------------------------------------- Claude's actions
    @staticmethod
    def _due(value) -> str:
        if not value:
            return ""
        try:
            return date.fromisoformat(str(value)[:10]).isoformat()
        except ValueError:
            raise Invalid("bad due date")

    def check_add(self, a: dict, call: Call) -> dict:
        title = str(a.get("title") or "").strip()
        if not title:
            raise Invalid("to-do without words")
        return {"title": title[:300], "due": self._due(a.get("due")), "list_id": self.target(str(a.get("whose") or ""), call)}

    def do_add(self, p: dict, call: Call) -> str:
        t = self.store.add(p["title"], p["list_id"], due=p["due"])
        who = self.whose(self.store.get_list(t["list_id"]) or {}, self._accounts(), self._names())
        whose = f"{who}'s to-dos" if who and t["list_id"] != self.household() else "the to-do list"
        due = f", for {when.say_day(date.fromisoformat(t['due']), datetime.fromtimestamp(self.ctx.clock()))}" if t["due"] else ""
        return f"Added {t['title']} to {whose}{due}."

    def check_ref(self, a: dict, call: Call) -> dict:
        t = self._match(a.get("id"))
        if not t:
            raise Invalid("unknown to-do")
        return t

    def do_complete(self, t: dict, call: Call) -> str:
        self.store.update(t["id"], done=True)
        return f"Ticked off {t['title']}."

    def check_change(self, a: dict, call: Call) -> dict:
        t = self.check_ref(a, call)
        changes = {}
        if a.get("title"):
            changes["title"] = str(a["title"]).strip()[:300]
        if "due" in a:
            changes["due"] = self._due(a.get("due"))
        if not changes:
            raise Invalid("nothing to change")
        return {"t": t, "changes": changes}

    def do_change(self, p: dict, call: Call) -> str:
        t = self.store.update(p["t"]["id"], **p["changes"])
        return f"Updated {t['title']}."

    def do_delete(self, t: dict, call: Call) -> str:
        self.store.remove(t["id"])
        return f"Removed {t['title']} from the list."

    # ---------------------------------------------------------------- the dashboard page
    async def dashboard(self, action: str, args: dict) -> dict:
        s = self.store
        if action == "list":
            accounts, names = self._accounts(), self._names()
            since = self.ctx.clock() - self.ctx.settings["show_done_days"] * 86400
            return {"lists": [{**x, "whose": self.whose(x, accounts, names)} for x in s.lists()],
                    "items": s.items(include_done_since=since), "household": self.household()}
        if action == "account_lists":   # an account's settings page (D-109): its lists, ignored ones too
            aid = str(args.get("account") or "")
            return {"lists": [x for x in s.lists(ignored=True) if x["account_id"] == aid]}
        if action == "set_list":
            lst = s.get_list(str(args.get("id")))
            if not lst or lst["id"] == "pip":
                raise ValueError("That list can't be ignored")
            s.set_ignored(lst["id"], bool(args.get("ignored")))
            return {"ok": True, "lists": [x for x in s.lists(ignored=True) if x["account_id"] == lst["account_id"]]}
        if action == "add":
            t = s.add(str(args.get("title", "")), str(args.get("list_id") or self.household()), due=str(args.get("due") or ""))
            return {"ok": True, "id": t["id"]}
        if action == "toggle":
            t = s.item(str(args.get("id")))
            if not t:
                raise ValueError("That to-do is gone")
            s.update(t["id"], done=not t["done"])
            return {"ok": True}
        if action == "edit":
            s.update(str(args.get("id")), title=str(args.get("title", "")).strip()[:300] or "(no title)",
                     due=str(args.get("due") or ""))
            return {"ok": True}
        if action == "delete":
            s.remove(str(args.get("id")))
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
                last_tidy = self.ctx.clock()
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), self.ctx.settings["sync_minutes"] * 60)
                await asyncio.sleep(1.0)
            except asyncio.TimeoutError:
                pass


def setup(ctx) -> Module:
    return Todos(ctx)
