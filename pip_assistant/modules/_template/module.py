"""The template module: rolls dice. Shows every part a module can have (ARCHITECTURE §7).

setup(ctx) is called once when pip-core starts. ctx gives the module its settings, its own
tables, its own secrets section, and so on (see core/modules.py, ModuleContext).
"""
import random

from pip_assistant.core.modules import Action, Call, Invalid, Module


class Dice(Module):
    def __init__(self, ctx):
        self.ctx = ctx
        # ACTION verbs Claude may use: check first (raise Invalid if it doesn't make sense), then do.
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
