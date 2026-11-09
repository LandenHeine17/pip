"""The template module: rolls dice. A small worked example of a module (ARCHITECTURE §7).

pip-core calls setup(ctx) once at startup. ctx (core/modules.py, ModuleContext) is everything the
module may use: ctx.settings (from module.toml), ctx.db() (its own <name>_… tables), ctx.secrets
(its own section of secrets.toml), ctx.http() (only the hosts its module.toml permits), ctx.publish(),
ctx.module(name) (a module it `needs`), ctx.people, ctx.schedule, ctx.clock and ctx.online.

setup returns a Module. Every part is optional; this one uses `actions` and `summary`. The others are
find(ref) (an item from a short id Claude used), dashboard(action, args) (its dashboard page's
requests), tasks() (background jobs) and `api` (what other modules may use).
"""
import random

from pip_assistant.core.modules import Action, Call, Invalid, Module


class Dice(Module):
    def __init__(self, ctx):
        self.ctx = ctx
        # ACTION verbs Claude may use. Each is checked first (raise Invalid: nothing is done), then done;
        # `do` returns what Pip says.
        self.actions = {"roll": Action(self.check_roll, self.roll)}

    def check_roll(self, a: dict, call: Call) -> int:
        sides = int(a.get("sides") or self.ctx.settings["sides"])
        if not 2 <= sides <= 1000:
            raise Invalid("odd number of sides")
        return sides

    def roll(self, sides: int, call: Call) -> str:
        return f"It's a {random.randint(1, sides)}."

    def summary(self, call: Call) -> str:
        return ""  # what Claude should know with every request (nothing, for dice)


def setup(ctx) -> Module:
    return Dice(ctx)
