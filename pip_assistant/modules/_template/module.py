"""The template module: rolls dice. A small worked example of every part a module can have (ARCHITECTURE §7;
the recipe is in docs/HOW-PIP-WORKS.md, "Making a module"). Copy the folder, rename it, keep what you need.

pip-core calls setup(ctx) once at startup. ctx (core/modules.py, ModuleContext) is everything the module
may use: ctx.settings (from module.toml), ctx.db() (its own <name>_… tables in pip.db), ctx.secrets (its
own section of secrets.toml), ctx.http() (only the hosts its module.toml permits), ctx.publish() (tell the
dashboard and the monitor something changed), ctx.module(name) (the api of a module it `needs`),
ctx.people (names and roles), ctx.alerts (heads-ups for its own items), ctx.clock and ctx.online.

setup returns a Module. Every part is optional:

    actions            ACTION verbs Claude may use ("roll"): checked first, then done
    summary(call)      what Claude should know with every request (keep it short: it costs tokens each time)
    offline()          its own phrases for when Pip is offline ("roll a die")
    home()             a card on the dashboard's front page (the last rolls)
    tv(shown)          what its box on the monitor shows (module.toml [monitor.dice]; static/monitor.js draws it)
    dashboard(...)     its own page's requests (static/index.html)
    tasks()            background jobs (none here)
    api                what other modules may use (none here)

Pip-core and pip-web never name a module; they ask every module for these (tools/check_map.py checks).
"""
import asyncio
import random
import re

from pip_assistant.common import when
from pip_assistant.core.modules import Action, Call, Invalid, Module, OfflineItems

SCHEMA = "CREATE TABLE IF NOT EXISTS _template_rolls (at REAL NOT NULL, sides INTEGER NOT NULL, result INTEGER NOT NULL)"


class Dice(Module):
    def __init__(self, ctx):
        self.ctx = ctx
        self.db = ctx.db()              # its own connection; only tables named _template_… may be changed
        self.db.execute(SCHEMA)
        self.db.commit()
        # ACTION verbs Claude may use. Each is checked first (raise Invalid: nothing is done), then done;
        # `do` returns what Pip says.
        self.actions = {"roll": Action(self.check_roll, self.do_roll)}
        self._offline = DiceOffline(self)

    # ---------------------------------------------------------------- doing it
    def roll(self, sides: int) -> int:
        result = random.randint(1, sides)
        self.db.execute("INSERT INTO _template_rolls VALUES (?, ?, ?)", (self.ctx.clock(), sides, result))
        self.db.commit()
        try:
            asyncio.get_running_loop().create_task(self.ctx.publish("changed"))   # the monitor's box and the page reload
        except RuntimeError:   # no loop running (tests)
            pass
        return result

    def last(self, n: int = 5) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM _template_rolls ORDER BY at DESC LIMIT ?", (n,))]

    # ---------------------------------------------------------------- Claude
    def check_roll(self, a: dict, call: Call) -> int:
        sides = int(a.get("sides") or self.ctx.settings["sides"])
        if not 2 <= sides <= 1000:
            raise Invalid("odd number of sides")
        return sides

    def do_roll(self, sides: int, call: Call) -> str:
        return f"It's a {self.roll(sides)}."

    def summary(self, call: Call) -> str:
        return ""   # what Claude should know with every request (nothing, for dice)

    # ---------------------------------------------------------------- offline, the dashboard, the monitor
    def offline(self) -> OfflineItems:
        return self._offline

    def home(self) -> dict | None:
        rolls = self.last()
        if not rolls:
            return None   # no card until something's been rolled
        return {"title": "Dice · last rolls", "link": f"/m/{self.ctx.name}/", "empty": "No rolls yet.", "remove": "",
                "items": [{"id": str(i), "title": f"Rolled a {r['result']} (d{r['sides']})", "at": r["at"],
                           "all_day": False, "repeat": "", "private": False, "color": ""} for i, r in enumerate(rolls)]}

    def tv(self, shown: list[dict]) -> dict:
        if not any(s["type"] == "dice" for s in shown):
            return {}   # its box isn't on the screen: nothing to send
        rolls = self.last(1)
        return {"dice": {"last": rolls[0]["result"] if rolls else None, "sides": rolls[0]["sides"] if rolls else None}}

    async def dashboard(self, action: str, args: dict) -> dict:
        if action == "rolls":
            return {"rolls": self.last(20)}
        if action == "roll":
            sides = self.check_roll(args, Call())   # Invalid (a ValueError) shows as an error on the page
            return {"result": self.roll(sides)}
        return {"error": f"unknown action {action}"}


class DiceOffline(OfflineItems):
    """"Roll a die" / "roll a twenty-sided die" without Claude (core/commands.py tries each rule in `rules`)."""

    def __init__(self, dice: Dice):
        self.dice = dice
        self.rules = [self.roll]

    def roll(self, t: str, ask) -> str | None:
        m = re.search(r"\broll (a |the )?(?:(\d+)[- ]sided )?(die|dice|d(\d+))\b", when.numbers(t))   # "twenty" → "20"
        if not m:
            return None
        sides = int(m.group(2) or m.group(4) or self.dice.ctx.settings["sides"])
        if not 2 <= sides <= 1000:
            return "That's an odd die."
        return f"It's a {self.dice.roll(sides)}."


def setup(ctx) -> Module:
    return Dice(ctx)
