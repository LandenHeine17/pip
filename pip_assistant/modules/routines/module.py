"""The routines module (D-143, PIP-NEW-041, PIP-NEW-088): a few steps run together by one phrase, or by the
wake-up alarm. Shared: anyone in the room can run them.

A routine is a name, the phrases that run it ("goodnight", "good night"), and its steps, in order:
    switch   a device or group on or off, through the home_control module ("lights", "fans", "everything",
             "desk lamp"): {"do": "switch", "target": "lights", "on": false}
    say      a line: {"do": "say", "text": "Goodnight!"}
    read     something Pip reads out (readouts.py: weather, today, tomorrow, alarm, todos, missed, fun):
             {"do": "read", "what": "tomorrow"}
Five come built in (BUILT_IN below); they can be changed and put back, not deleted. More are made on the
Routines page (static/index.html). Everything is kept in its own tables, routines_list and routines_state.

How one runs:
  - Said to Pip in so many words ("Pip, goodnight", "I'm heading out"): `room_heard` runs it at once and answers
    with a `Done`, so Claude isn't asked. The switching happens in the background (a device that doesn't answer
    is logged and shows on the Home card).
  - In other words ("Pip, night night", "run movie time"): Claude's `routine` action. Then the switching is
    waited for, and Pip says which devices didn't answer.
  - The wake-up alarm, an alarm whose name has `wake_label` ("wake up") in it: `on_alert` hears pip-core's
    alarms. When it rings, the Wake-up routine runs (every lamp on). When someone stops it, the Morning briefing
    is what Pip says once he's free. Snoozed: nothing more (the lamps stay on). Nobody answered: no briefing.
  - "I'm heading out" puts the room "out": anything that rings with nobody answering until "I'm back" is kept
    (its kind and name only) for "I'm back" to read out once.
"""
import asyncio
import json
import logging
import re

from pip_assistant.common.messages import new_id
from pip_assistant.core.modules import Action, Call, Done, Invalid, Module

from .readouts import READS, Readouts

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS routines_list (
    id       TEXT PRIMARY KEY,
    builtin  TEXT NOT NULL DEFAULT '',   -- goodnight / wakeup / briefing / out / back; '' for your own
    name     TEXT NOT NULL,
    phrases  TEXT NOT NULL,              -- JSON list: what runs it when said to Pip
    steps    TEXT NOT NULL,              -- JSON list of steps (above)
    position INTEGER NOT NULL DEFAULT 0,
    updated  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS routines_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# The built-in routines (owner, 2026-11-23). `starts` is what runs it besides its name: phrases, or the wake-up
# alarm ("rings", "stopped"), shown on the page.
BUILT_IN = {
    "goodnight": {"name": "Goodnight", "phrases": ["goodnight", "good night", "night night"], "steps": [
        {"do": "switch", "target": "lights", "on": False}, {"do": "switch", "target": "fans", "on": True},
        {"do": "say", "text": "Goodnight!"}, {"do": "read", "what": "alarm"}, {"do": "read", "what": "tomorrow"}]},
    "wakeup": {"name": "Wake-up", "phrases": [], "starts": "rings", "steps": [
        {"do": "switch", "target": "lights", "on": True}]},
    "briefing": {"name": "Morning briefing", "phrases": ["good morning"], "starts": "stopped", "steps": [
        {"do": "say", "text": "Good morning!"}, {"do": "read", "what": "weather"}, {"do": "read", "what": "today"},
        {"do": "read", "what": "todos"}, {"do": "read", "what": "fun"}]},
    "out": {"name": "Heading out", "phrases": ["i'm heading out", "i'm leaving", "i'm going out", "heading out"],
            "steps": [{"do": "switch", "target": "everything", "on": False}, {"do": "say", "text": "See you later!"}]},
    "back": {"name": "I'm back", "phrases": ["i'm back", "i'm home", "we're back", "we're home"], "steps": [
        {"do": "switch", "target": "lights", "on": True}, {"do": "say", "text": "Welcome back!"},
        {"do": "read", "what": "missed"}]},
}
MAX_STEPS, MAX_PHRASES = 12, 6
MISSED_KEEP_S = 24 * 3600   # what rang while you were out is kept a day at most

# Said around a routine's phrase without making it something else: his name, greetings, "everyone".
AROUND = re.compile(r"^(?:(?:hey|hi|ok|okay|alright|all right|well|so|yo|oh|and|right)\s+)*|"
                    r"(?:\s+(?:now|then|everyone|everybody|guys|all|buddy|pal|mate|for now|for the night))*$")
NAME = re.compile(r"\b(?:hey |hi |ok |okay )?(?:pip|pipp|pips|pippy|pippie)\b")


def plain(text: str) -> str:
    """How a phrase is compared: lower case, no punctuation or apostrophes, no "Pip", no "hey"/"everyone"."""
    t = (text or "").lower().replace("’", "'").replace("'", "")
    t = NAME.sub(" ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return AROUND.sub("", t).strip()


def check_steps(steps) -> list[dict]:
    """A routine's steps from the page, checked (raises Invalid)."""
    if not isinstance(steps, list) or not steps:
        raise Invalid("A routine needs at least one step.")
    if len(steps) > MAX_STEPS:
        raise Invalid(f"At most {MAX_STEPS} steps.")
    out = []
    for s in steps:
        do = (s or {}).get("do")
        if do == "switch":
            target = re.sub(r"\s+", " ", str(s.get("target") or "")).strip()[:40]
            if not target or not isinstance(s.get("on"), bool):
                raise Invalid("A switch step needs a device (or lights, fans, everything) and on or off.")
            out.append({"do": "switch", "target": target, "on": s["on"]})
        elif do == "say":
            text = re.sub(r"\s+", " ", str(s.get("text") or "")).strip()[:200]
            if not text:
                raise Invalid("A say step needs something to say.")
            out.append({"do": "say", "text": text})
        elif do == "read":
            if s.get("what") not in READS:
                raise Invalid("Read out what? " + ", ".join(READS))
            out.append({"do": "read", "what": s["what"]})
        else:
            raise Invalid("Each step switches something, says something or reads something out.")
    return out


class Routines(Module):
    def __init__(self, ctx):
        self.ctx = ctx
        self.db = ctx.db()
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.seed()
        self.reads = Readouts(ctx)
        self.actions = {"routine": Action(self.check_run, self.do_run)}
        self.jobs: set[asyncio.Task] = set()   # switching started by room_heard (kept so they aren't lost)

    # ---------------------------------------------------------------- storing them
    def seed(self) -> None:
        """Add any built-in routine that isn't there yet (a new one in a later version too)."""
        have = {r[0] for r in self.db.execute("SELECT builtin FROM routines_list WHERE builtin != ''")}
        for pos, (key, r) in enumerate(BUILT_IN.items()):
            if key not in have:
                self.db.execute("INSERT INTO routines_list VALUES (?,?,?,?,?,?,?)",
                                (new_id(), key, r["name"], json.dumps(r["phrases"]), json.dumps(r["steps"]), pos,
                                 self.ctx.clock()))
        self.db.commit()

    def all(self) -> list[dict]:
        rows = self.db.execute("SELECT * FROM routines_list ORDER BY position, name").fetchall()
        return [{**dict(r), "phrases": json.loads(r["phrases"]), "steps": json.loads(r["steps"]),
                 "starts": BUILT_IN.get(r["builtin"], {}).get("starts", "")} for r in rows]

    def get(self, rid: str) -> dict | None:
        return next((r for r in self.all() if r["id"] == rid), None)

    def builtin(self, key: str) -> dict | None:
        return next((r for r in self.all() if r["builtin"] == key), None)

    def state(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM routines_state WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_state(self, key: str, value) -> None:
        if value is None:
            self.db.execute("DELETE FROM routines_state WHERE key = ?", (key,))
        else:
            self.db.execute("INSERT OR REPLACE INTO routines_state VALUES (?, ?)", (key, json.dumps(value)))
        self.db.commit()

    def find(self, said: str) -> dict | None:
        """The routine they mean, by its name or one of its phrases ("goodnight", "movie time")."""
        p = plain(said)
        if not p:
            return None
        for r in self.all():
            if p == plain(r["name"]) or p in (plain(x) for x in r["phrases"]):
                return r
        return None

    # ---------------------------------------------------------------- running one
    def words(self, r: dict, call: Call) -> str:
        """What Pip says for this routine (its say and read steps), from what's at hand."""
        missed = self.take_missed() if any(s["do"] == "read" and s["what"] == "missed" for s in r["steps"]) else []
        parts = [s["text"] if s["do"] == "say" else self.reads.say(s["what"], call, missed)
                 for s in r["steps"] if s["do"] in ("say", "read")]
        return " ".join(p for p in parts if p)

    async def switch(self, r: dict) -> list[str]:
        """Do its switch steps, in order. Returns what couldn't be switched."""
        home = self.ctx.module("home_control")
        missed = []
        for s in r["steps"]:
            if s["do"] != "switch" or home is None:
                continue
            try:
                missed += [m for m in await home.switch(s["target"], s["on"]) if m not in missed]
            except LookupError:
                missed.append(f"anything called {s['target']}")
            except Exception:
                log.exception("a routine's switch step failed")
                missed.append(f"the {s['target']}")
        return missed

    def before(self, r: dict) -> None:
        """What running it changes besides its steps: heading out starts keeping what rings; it's remembered as
        the last routine run (the Overview's card)."""
        if r["builtin"] == "out":
            self.set_state("out_since", self.ctx.clock())
            self.set_state("missed", [])
        self.set_state("last", {"name": r["name"], "at": self.ctx.clock()})
        self.changed()

    def take_missed(self) -> list[dict]:
        """What rang while you were out, once: reading it out clears it, and the room is back."""
        missed = [m for m in self.state("missed", []) if self.ctx.clock() - m.get("at", 0) < MISSED_KEEP_S]
        self.set_state("missed", None)
        self.set_state("out_since", None)
        return missed

    async def run(self, r: dict, call: Call) -> str:
        """Run it all and wait for it: the switching, then the words, then anything that didn't switch."""
        self.before(r)
        needs = {s["what"] for s in r["steps"] if s["do"] == "read"}
        missed, _ = await asyncio.gather(self.switch(r), self.reads.warm(fun="fun" in needs))
        said = self.words(r, call)
        if missed:
            said += f" I couldn't reach {' or '.join(missed)}."
        return said.strip() or "Done."

    def changed(self) -> None:
        try:
            asyncio.get_running_loop().create_task(self.ctx.publish("changed"))   # the page and the card redraw
        except RuntimeError:   # no loop running (tests)
            pass

    # ---------------------------------------------------------------- said to Pip
    def room_heard(self, text: str, call: Call) -> str | None:
        """"Pip, goodnight", "I'm heading out": a routine's phrase said to Pip, and nothing more, runs it at once."""
        if call.kind != "direct":
            return None
        p = plain(text)
        r = next((r for r in self.all() if p and p in (plain(x) for x in r["phrases"])), None)
        if r is None:
            return None
        self.before(r)
        job = asyncio.get_running_loop().create_task(self._switch_quietly(r))
        self.jobs.add(job)
        job.add_done_callback(self.jobs.discard)
        return Done(self.words(r, call) or "Done.")

    async def _switch_quietly(self, r: dict) -> None:
        missed = await self.switch(r)
        if missed:
            log.warning("a routine couldn't switch %d device(s)", len(missed))

    def check_run(self, a: dict, call: Call) -> dict:
        r = self.find(str(a.get("name") or ""))
        if r is None:
            raise Invalid("which routine: " + ", ".join(x["name"] for x in self.all()))
        return r

    async def do_run(self, r: dict, call: Call) -> str:
        return await self.run(r, call)

    def summary(self, call: Call) -> str:
        if call.kind == "join":
            return ""
        names = [r["name"] + (" (after the wake-up alarm)" if r["starts"] == "stopped" else
                              " (when the wake-up alarm rings)" if r["starts"] == "rings" else "") for r in self.all()]
        return "Routines: " + "; ".join(names) + "."

    # ---------------------------------------------------------------- the wake-up alarm, and what rang while out
    def wake(self, alerts: list[dict]) -> bool:
        label = str(self.ctx.settings.get("wake_label") or "wake up").lower().replace("-", " ").strip()
        return bool(label) and any(a.get("kind") == "alarm" and label in str(a.get("label") or "").lower()
                                   .replace("-", " ") for a in alerts)

    async def on_alert(self, event: str, alerts: list[dict]) -> str | None:
        if event == "unanswered" and self.state("out_since") is not None:
            kept = self.state("missed", []) + [{"kind": a.get("kind"), "label": a.get("label") or "",
                                                "at": self.ctx.clock()} for a in alerts]
            self.set_state("missed", kept[-20:])
            return None
        if not self.wake(alerts):
            return None
        if event == "ringing" and (r := self.builtin("wakeup")) is not None:
            self.before(r)
            missed = await self.switch(r)
            if missed:
                log.warning("the wake-up routine couldn't switch %d device(s)", len(missed))
            await self.reads.warm(fun=True)   # ready for the briefing
            return None
        if event == "stopped" and (r := self.builtin("briefing")) is not None:
            return await self.run(r, Call(kind="direct"))   # nobody in particular: only what all may hear
        return None

    def tasks(self) -> list:
        return [self.reads.keep_warm()] if self.reads.weather is not None else []

    # ---------------------------------------------------------------- the dashboard
    def next_wake(self) -> dict | None:
        if self.ctx.alerts is None:
            return None
        return next((a for a in self.ctx.alerts.upcoming("alarm") if self.wake([a])), None)

    def home(self) -> dict | None:
        items = []
        if a := self.next_wake():
            items.append({"id": "wake", "title": "Wake-up alarm", "at": a["due"], "all_day": False,
                          "repeat": a["repeat"], "private": False, "color": ""})
        if last := self.state("last"):
            items.append({"id": "last", "title": f"Last run: {last['name']}", "at": last["at"], "all_day": False,
                          "repeat": "", "private": False, "color": ""})
        if not items:
            return None
        return {"title": "Routines", "link": f"/m/{self.ctx.name}/", "empty": "", "remove": "", "items": items}

    async def dashboard(self, action: str, args: dict) -> dict:
        if action == "list":
            home = self.ctx.module("home_control")
            wake = self.next_wake()
            return {"routines": self.all(), "reads": READS, "devices": home.devices() if home else None,
                    "wake": {"due": wake["due"], "repeat": wake["repeat"]} if wake else None,
                    "wake_label": self.ctx.settings.get("wake_label"), "last": self.state("last"),
                    "out_since": self.state("out_since")}
        if action == "save":
            return self.save(args)
        if action == "delete":
            r = self.get(str(args.get("id") or ""))
            if r is None or r["builtin"]:
                raise Invalid("Only your own routines can be deleted.")
            self.db.execute("DELETE FROM routines_list WHERE id = ?", (r["id"],))
            self.db.commit()
            self.changed()
            return {"ok": True}
        if action == "reset":
            r = self.get(str(args.get("id") or ""))
            if r is None or not r["builtin"]:
                raise Invalid("Only a built-in routine can be put back.")
            b = BUILT_IN[r["builtin"]]
            self.db.execute("UPDATE routines_list SET name = ?, phrases = ?, steps = ?, updated = ? WHERE id = ?",
                            (b["name"], json.dumps(b["phrases"]), json.dumps(b["steps"]), self.ctx.clock(), r["id"]))
            self.db.commit()
            self.changed()
            return {"ok": True}
        if action == "run":
            r = self.get(str(args.get("id") or ""))
            if r is None:
                raise Invalid("No such routine.")
            return {"said": await self.run(r, Call(kind="direct"))}   # done for real; the words shown, not said
        return {"error": f"unknown action {action}"}

    def save(self, args: dict) -> dict:
        """A routine from the page: a new one (no id) or a change. Names and phrases must be unique."""
        rid = str(args.get("id") or "")
        old = self.get(rid) if rid else None
        if rid and old is None:
            raise Invalid("No such routine.")
        name = re.sub(r"\s+", " ", str(args.get("name") or "")).strip()[:40]
        if not plain(name):
            raise Invalid("Give it a name.")
        phrases = [re.sub(r"\s+", " ", str(p)).strip()[:40] for p in (args.get("phrases") or []) if plain(str(p))]
        if len(phrases) > MAX_PHRASES:
            raise Invalid(f"At most {MAX_PHRASES} phrases.")
        if not phrases and not (old and BUILT_IN.get(old["builtin"], {}).get("starts")):
            phrases = [name]   # said by its name
        steps = check_steps(args.get("steps"))
        mine = {plain(name)} | {plain(p) for p in phrases}
        for r in self.all():
            if r["id"] == rid:
                continue
            theirs = {plain(r["name"])} | {plain(p) for p in r["phrases"]}
            if clash := mine & theirs:
                raise Invalid(f"\"{clash.pop()}\" already runs {r['name']}.")
        if old:
            self.db.execute("UPDATE routines_list SET name = ?, phrases = ?, steps = ?, updated = ? WHERE id = ?",
                            (name, json.dumps(phrases), json.dumps(steps), self.ctx.clock(), rid))
        else:
            rid = new_id()
            pos = (self.db.execute("SELECT MAX(position) FROM routines_list").fetchone()[0] or 0) + 1
            self.db.execute("INSERT INTO routines_list VALUES (?,?,?,?,?,?,?)",
                            (rid, "", name, json.dumps(phrases), json.dumps(steps), pos, self.ctx.clock()))
        self.db.commit()
        self.changed()
        return {"ok": True, "id": rid}

    # ---------------------------------------------------------------- Settings → Your data
    def export_data(self) -> dict | None:
        return {"routines": self.all()}

    async def wipe_data(self) -> int:
        """What rang while you were out (its names are what people said); the routines themselves stay."""
        n = len(self.state("missed", []))
        self.set_state("missed", None)
        return n


def setup(ctx) -> Module:
    return Routines(ctx)
