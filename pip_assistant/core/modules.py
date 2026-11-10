"""Plugging modules into pip-core (ARCHITECTURE §7, D-96).

At startup every enabled module (common/modules.py finds them) is imported and its
`setup(ctx)` is called with a ModuleContext: everything the module may use, and nothing more.
setup returns a Module. Each of its parts is optional:

    actions           ACTION verbs Claude may use ("add_task", "add:event" = do add, kind event)
                      → Action(check, do): everything is checked before anything is done; `do` may be
                      async (e.g. it has to ask Google). "look:<kind>" is a look-up: its `do` returns
                      text for Claude, who is then asked again with it (the calendar beyond tomorrow)
    summary(call)     its part of what Claude reads with each request (sent privately); keep it short,
                      and shorter still when call.kind == "join" (join-in checks run every ~40 s)
    find(ref)         an item from a short id Claude used ("#e1a2b3") → kind name, e.g. "event"
    dashboard(...)    answers its dashboard page (/m/<name>/ ↔ /api/m/<name>)
    tasks()           background jobs (e.g. syncing every two minutes)
    offline()         its part of Pip's own offline commands (OfflineItems below; the calendar's phrases)
    home()            a card on the dashboard's front page (the calendar's next two weeks)
    tv(shown)         what its boxes on the monitor show (the box kinds are in module.toml's [monitor.<kind>],
                      drawn by static/monitor.js); tv_layout() is only for the module that lays out the screen
    room_may_see(a)   may this timer, alarm or heads-up show on the room's monitor? (the calendar hides the
                      heads-ups of events it mayn't mention to everyone)
    people_links(id)  links for a person on the dashboard's People card ("Connect Google")
    api               a small object other modules may use (each module documents its own)

Pip-core and pip-web never name a module: they ask every module for these (tools/check_map.py checks).

What a module gets (ModuleContext): its settings; its own tables in pip.db (named <module>_…); its
own section of secrets.toml; events it publishes (module.<name>.*); heads-ups for its own items
(`alerts`, ModuleAlerts below); who's in the household (`people`, ModulePeople: names and roles, never
voices); whether Pip is online; the clock; the modules it `needs` (their small `api`); the kinds of box
every module adds to the monitor (`boxes`, for the screen module); and an HTTP client that can only reach
the hosts its module.toml lists under permissions. Nothing else of pip-core.
"""
import asyncio
import importlib
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import httpx

from ..common import paths
from ..common.messages import Message
from ..common.modules import ModuleInfo, enabled
from ..common.secrets import ScopedSecrets
from ..common.db import connect

log = logging.getLogger(__name__)


class Invalid(ValueError):
    """An action that doesn't check out: nothing is done."""


@dataclass
class Call:
    """Who's asking and when, passed to actions and summaries."""
    speaker: Any = None               # people.Match or None
    now: datetime = field(default_factory=datetime.now)
    kind: str = "direct"              # direct / mentioned / join (a join-in check gets a shorter summary)

    @property
    def person_id(self) -> str | None:
        sp = self.speaker
        return sp.person_id if sp is not None and sp.level == "sure" and not sp.guest else None


@dataclass
class Action:
    check: Callable                   # (action dict, Call) → a checked plan; raise Invalid if it doesn't check out
    do: Callable                      # (plan, Call) → what Pip says


class OfflineItems:
    """What a module adds to Pip's own offline commands (core/commands.py, D-94); returned by Module.offline().

    Its own phrases go in `rules`: each is (t, ask) → a reply, or None if it isn't that phrase. `t` is what was
    said, cleaned up (lower case, no "Pip", no "please"); `ask` (core/commands.Asking) is who's asking, the time,
    and Pip's shared helpers (ask a question back, what "that" means, matching names).
    Its items with a time (calendar events) also join the shared "move it to 4", "push it back an hour" and
    "cancel that" rules, next to timers and alarms: `items`, `get`, `when`, `move` and `cancel` say how."""
    kind = ""           # what one item is called ("event"): Pip says "You don't have any events."
    words = ""          # a regex of words that mean this kind ("event|appointment|calendar")
    empty = ""          # what Pip says when they ask to change one and there are none ("Your calendar's empty.")
    rules: list = []

    def owns(self, t: str) -> bool:
        """Is this phrase clearly about this module ("take X off the calendar")? Then the timers' cancel rule steps aside."""
        return False

    def items(self, call) -> list[dict]:
        """Every item the person asking (`call`, None = the dashboard) may hear about. Each has "id" and "title"."""
        return []

    def get(self, item_id: str, call) -> dict | None:
        """One item, for "that" / "it"; None if it's gone or `call` mayn't hear about it."""
        return None

    def when(self, row: dict) -> float:
        """The item's time (a timestamp)."""
        return 0.0

    def move(self, row: dict, at: datetime | None = None, shift: float | None = None) -> str:
        """Move it to `at`, or by `shift` seconds, and say what it is now."""
        raise NotImplementedError

    def cancel(self, row: dict) -> str:
        """Remove it, and say so."""
        raise NotImplementedError


class Module:
    """What setup() returns. Override what the module needs."""
    actions: dict = {}
    api: Any = None

    def summary(self, call: Call) -> str:
        return ""

    def find(self, ref: str) -> str | None:
        return None

    async def dashboard(self, action: str, args: dict) -> dict:
        return {"error": f"unknown action {action}"}

    def tasks(self) -> list:
        return []

    def offline(self) -> OfflineItems | None:
        """What it adds to Pip's offline commands (see OfflineItems), or None."""
        return None

    def home(self) -> dict | None:
        """A card on the dashboard's front page, or None. Sent privately (titles are personal):
        {"title": "Calendar · next two weeks", "link": "/m/calendar/", "empty": "Nothing on the calendar.",
         "items": [{"id", "title", "at" (timestamp), "all_day", "repeat", "private", "color"}],
         "remove": "delete" (its dashboard action that removes one item by "id"; "" for none)}"""
        return None

    def tv(self, shown: list[dict]) -> dict:
        """What its boxes on the monitor show: {box kind: data for that kind's drawing in static/monitor.js}.
        `shown`: the boxes on the screen now ({"type", and what each was told}). Only what the room may
        see; sent privately."""
        return {}

    def tv_layout(self) -> dict | None:
        """Only for the module that lays out the screen (modules/screen): its layout, or None."""
        return None

    def room_may_see(self, alert: dict) -> bool | None:
        """May this alert (a timer, alarm, reminder or heads-up) show on the room's monitor? None: not
        this module's business. An alert linked to an item (`event_id`) shows only if its module says yes."""
        return None

    def people_links(self, person_id: str) -> list[dict]:
        """Links for one person on the dashboard's People card: [{"label", "href"}]."""
        return []


class HostNotAllowed(PermissionError):
    pass


class ModuleAlerts:
    """What a module may do with Pip's alerts (core/schedule.py): heads-ups for its own items. A heads-up rings
    like a reminder ("Heads up: dentist in half an hour"), and is linked to the item by its id."""

    def __init__(self, schedule):
        self._s = schedule

    def add(self, label: str, due: float, length: float | None = None, repeat: str = "", item_id: str = "") -> dict:
        """A heads-up at `due` (a timestamp); `length`: how long before the item it is (the words say "in …")."""
        return self._s.add("event", label, due, length=length, repeat=repeat, event_id=item_id)

    def cancel_for(self, item_id: str) -> None:
        """Every heads-up for this item goes (the item was moved, changed or removed)."""
        self._s.cancel_for(item_id)

    def has_alert_for(self, item_id: str) -> bool:
        return self._s.has_alert_for(item_id)

    def waiting(self) -> int:
        """How many heads-ups (any module's) are waiting."""
        return len(self._s.active(("event",)))


class ModulePeople:
    """Who's in the household (core/people.py), read-only: id, name and role. Never voices or anything else."""
    FIELDS = ("id", "name", "role")

    def __init__(self, people):
        self._p = people

    def _small(self, p: dict | None) -> dict | None:
        return {k: p[k] for k in self.FIELDS} if p else None

    def all(self) -> list[dict]:
        return [self._small(p) for p in self._p.all()]

    def get(self, person_id: str) -> dict | None:
        return self._small(self._p.get(person_id))

    def by_name(self, name: str) -> dict | None:
        """The household member with this name, allowing for how speech-to-text spells it ("Landon" → Landen)."""
        return self._small(self._p.by_name(name))


class ModuleContext:
    def __init__(self, info: ModuleInfo, bus=None, schedule=None, people=None, net=None, clock=time.time,
                 db_path=None, modules: dict | None = None, transport=None, boxes: dict | None = None):
        self.info, self.name, self.settings = info, info.name, info.settings
        self.boxes = boxes or {}   # every loaded module's kinds of monitor box ({kind: {label, size, …, "module"}})
        self.bus, self.net, self.clock = bus, net, clock
        self.alerts = ModuleAlerts(schedule) if schedule is not None else None   # heads-ups for its own items
        self.people = ModulePeople(people) if people is not None else None       # names and roles only
        self.secrets = ScopedSecrets(info.name)
        self._db_path = db_path or paths.database()
        self._modules = modules or {}
        self._transport = transport  # tests: a fake internet

    def db(self) -> sqlite3.Connection:
        """Its own connection to pip.db. Tables must be named <module>_… (checked)."""
        con = connect(self._db_path)
        prefix = self.name + "_"

        def guard(action, table, arg2, *_):
            if action == sqlite3.SQLITE_ALTER_TABLE:   # here SQLite gives ("main", table): adding a column later
                table = arg2
            if action in (sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_INSERT,
                          sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_ALTER_TABLE,
                          sqlite3.SQLITE_CREATE_INDEX) and table and not table.startswith((prefix, "sqlite_")):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        con.set_authorizer(guard)
        return con

    def module(self, name: str):
        """Another module's api, if this module declared it in `needs`."""
        if name not in self.info.needs:
            raise PermissionError(f"module {self.name} didn't declare it needs {name}")
        m = self._modules.get(name)
        return m.api if m else None

    @property
    def online(self) -> bool:
        return self.net is None or self.net.online

    def http(self, **kw) -> httpx.AsyncClient:
        """An HTTP client limited to the hosts in module.toml's permissions."""
        hosts = self.info.hosts

        async def check(request: httpx.Request):
            h = request.url.host
            if not any(h == x or h.endswith("." + x) for x in hosts):
                raise HostNotAllowed(f"module {self.name} may not reach {h}")
        kw.setdefault("timeout", 15)
        return httpx.AsyncClient(transport=self._transport, event_hooks={"request": [check]}, **kw)

    async def publish(self, kind: str, data: dict | None = None, private: dict | None = None) -> None:
        if self.bus is not None:
            await self.bus.publish(Message("event", f"module.{self.name}.{kind}", source="core",
                                           data=data or {}, private=private or {}))


class Registry:
    def __init__(self):
        self.modules: dict[str, Module] = {}
        self.infos: dict[str, ModuleInfo] = {}
        self._tasks: list[asyncio.Task] = []

    @classmethod
    def load(cls, cfg: dict, infos: list[ModuleInfo] | None = None, **ctx_kw) -> "Registry":
        reg = cls()
        infos = [i for i in (infos if infos is not None else enabled(cfg)) if i.enabled]
        boxes = {kind: {**b, "module": i.name} for i in infos for kind, b in i.boxes.items()}
        for info in infos:
            try:
                code = importlib.import_module(f"pip_assistant.modules.{info.name}.module")
                mod = code.setup(ModuleContext(info, modules=reg.modules, boxes=boxes, **ctx_kw))
            except Exception:
                log.exception("module %s failed to start; Pip carries on without it", info.name)
                continue
            reg.modules[info.name], reg.infos[info.name] = mod, info
            log.info("module %s loaded", info.name)
        return reg

    def get(self, name: str):
        """A module's api (or None if it isn't loaded)."""
        m = self.modules.get(name)
        return m.api if m else None

    def module(self, name: str) -> Module | None:
        """A loaded module itself (or None)."""
        return self.modules.get(name)

    # ---------------------------------------------------------------- Claude's actions
    def action(self, verb: str) -> Action | None:
        for m in self.modules.values():
            if verb in m.actions:
                return m.actions[verb]
        return None

    def kind_of(self, ref: str) -> str | None:
        """Which kind of item a short id is ('event', 'task'), asking each module."""
        for m in self.modules.values():
            if kind := m.find(ref):
                return kind
        return None

    # ---------------------------------------------------------------- Pip's offline commands
    def offline_items(self) -> list[OfflineItems]:
        out = []
        for name, m in self.modules.items():
            try:
                if (o := m.offline()) is not None:
                    out.append(o)
            except Exception:
                log.exception("module %s offline commands failed", name)
        return out

    # ---------------------------------------------------------------- the dashboard and the monitor
    def _each(self, what: str, call, default):
        """call(module) for every module, skipping (and logging) one that fails."""
        out = []
        for name, m in self.modules.items():
            try:
                out.append((name, call(m)))
            except Exception:
                log.exception("module %s %s failed", name, what)
                out.append((name, default))
        return out

    def home_cards(self) -> list[dict]:
        """The cards modules add to the dashboard's front page, each with its module's name."""
        return [{**card, "module": name} for name, card in self._each("home", lambda m: m.home(), None) if card]

    def tv(self) -> dict:
        """What the monitor shows from modules: the screen's layout ("screen") and each box kind's data ("boxes")."""
        out, layout = {"boxes": {}}, None
        for _, lay in self._each("tv_layout", lambda m: m.tv_layout(), None):
            layout = layout or lay
        shown = []
        if layout:
            out["screen"] = layout
            shown = list(layout.get("view", {}).get("sections", []))
        for _, boxes in self._each("tv", lambda m: m.tv(shown), {}):
            out["boxes"].update(boxes or {})
        return out

    def room_may_see(self, alert: dict) -> bool:
        """May this alert show on the room's monitor? One linked to an item (a calendar event's heads-up) only if
        its module says so (so it stays hidden if that module is off); others unless a module says no."""
        said = [v for _, v in self._each("room_may_see", lambda m: m.room_may_see(alert), False) if v is not None]
        if alert.get("event_id"):
            return bool(said) and all(said)
        return all(said)

    def people_links(self, person_id: str) -> list[dict]:
        return [link for _, links in self._each("people_links", lambda m: m.people_links(person_id), []) for link in links or []]

    # ---------------------------------------------------------------- what Claude reads
    def summary(self, call: Call) -> str:
        parts = []
        for name, m in self.modules.items():
            try:
                if s := m.summary(call):
                    parts.append(s)
            except Exception:
                log.exception("module %s summary failed", name)
        return " ".join(parts)

    # ---------------------------------------------------------------- dashboard pages
    async def request(self, msg: Message):
        name, action = msg.data.get("module", ""), msg.data.get("action", "")
        m = self.modules.get(name)
        if m is None:
            return {"error": f"no module {name}"}
        try:
            out = await m.dashboard(action, dict(msg.private or {}))
        except Exception as e:
            log.exception("module %s dashboard %s failed", name, action)
            return {"error": str(e)[:200] if isinstance(e, ValueError) else type(e).__name__}
        return ({"ok": "error" not in out}, out)   # results travel privately (titles, names)

    def pages(self) -> list[dict]:
        return [{"name": i.name, "label": i.label or i.name.title(), "page": i.page}
                for i in self.infos.values() if i.page]

    # ---------------------------------------------------------------- background jobs
    def start(self) -> None:
        for name, m in self.modules.items():
            for job in m.tasks():
                self._tasks.append(asyncio.create_task(self._guard(name, job)))

    @staticmethod
    async def _guard(name, job):
        try:
            await job
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("module %s background job stopped", name)

    def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
