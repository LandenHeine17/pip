#!/usr/bin/env python3
"""Which Claude model should Pip use? (D-137: Ziggletron used Opus and had more personality; Pip used Haiku.)

    systemd-run --user --same-dir --pipe --wait -q -p MemoryMax=4500M ~/.local/bin/uv run tools/model_compare.py

Sends the same made-up situations (no real conversation: names and lines are invented) to each model, with
Pip's real instructions (personality, rules, modules), the way pip-thinker asks: a pre-started `claude` process,
the effort from [thinker]. Records how long until the first sentence, the whole time, and what he'd say.
Nothing is spoken. Results: ~/pip-data/m0/results/model-compare.json (`--out`).
"""
import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pip_assistant.common import settings  # noqa: E402
from pip_assistant.thinker.claude import ClaudeCLI  # noqa: E402
from pip_assistant.thinker.main import Thinker  # noqa: E402

OUT = Path.home() / "pip-data" / "m0" / "results" / "model-compare.json"
PEOPLE = "Speaking: {who} (sure). Household: Alex, Sam."
# (what it tests, kind, transcript lines, who's speaking, what a good answer does)
SITUATIONS = [
    ("question", "direct", ["[18:02] Sam: Pip, what should I make for dinner? I've got eggs, rice and some sad spinach."],
     "Sam", "answers, with some flavour"),
    ("tease", "direct", ["[18:05] Alex: Pip, you're basically a calculator with anxiety."], "Alex", "gives it right back"),
    ("reaction", "follow_up", ["[18:06] Sam: Pip, is cereal a soup?", "[18:06] Pip: Technically yes. Spiritually, no.",
                               "[18:06] Sam: That tracks."], "Sam", "a quick reply in kind, not PASS"),
    ("teased back", "follow_up", ["[18:08] Alex: Pip, who's smarter, me or Sam?", "[18:08] Pip: Sam. It's not close.",
                                  "[18:08] Alex: Wow. Nobody asked the toaster."], "Alex", "gives it right back"),
    ("roast", "direct", ["[18:10] Sam: Pip, roast Alex's cooking. He burned water yesterday."], "Sam",
     "a real roast, not cruel"),
    ("join in", "join", ["[18:12] Alex: I set off the smoke alarm making popcorn again.",
                         "[18:12] Sam: That's the third time this week.", "[18:12] Alex: The microwave hates me."],
     "", "joins with a jab (SAY:), or passes"),
    ("fact", "direct", ["[18:14] Alex: Pip, how far away is the moon?"], "Alex", "short answer"),
    ("timer", "direct", ["[18:15] Sam: Pip, set a timer for ten minutes for the pizza."], "Sam", "ACTION only"),
    ("mentioned", "mentioned", ["[18:16] Sam: Pip was glitchy all morning honestly."], "Sam", "PASS"),
    ("never mind", "direct", ["[18:17] Alex: Pip, what's the... actually never mind."], "Alex", "PASS"),
]


class FixedWeather:
    async def text(self):
        return "Cloudy, 41F, wind 10 mph; tomorrow rain, high 45F."


async def one(cli, system, label, kind, lines, who):
    user = Thinker.user_text("", lines, now="Wednesday November 18 2026, 6:17 PM", kind=kind,
                             people=PEOPLE.format(who=who) if who else "Household: Alex, Sam.",
                             schedule="No timers, alarms or reminders set.", memories="You don't remember anything yet.")
    said = []

    async def on_sentence(s):
        said.append(s)

    async def on_searching():
        pass
    t0 = time.perf_counter()
    text, info = await cli.ask(label, system, user, on_sentence, on_searching, held=kind == "join")
    total = round(time.perf_counter() - t0, 2)
    # a one-sentence reply is only released when the answer ends, so its first words come at the end
    return {"first_s": info.get("first_sentence_s") or (total if said else None), "total_s": total,
            "said": " ".join(said), "raw": text.strip()[:400], "passed": bool(info.get("passed"))}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="haiku,sonnet,opus")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    cfg = settings.load()
    th = Thinker(None, cfg)
    th.weather = FixedWeather()
    system = await th.system_prompt()
    effort = cfg["thinker"]["effort"]
    out = {"when": time.strftime("%Y-%m-%d %H:%M"), "effort": effort, "prompt_words": len(system.split()), "models": {}}
    for model in a.models.split(","):
        cli = ClaudeCLI(model, False, 60.0, effort)
        await cli.prepare(system)
        rows = []
        for r in range(a.rounds):
            for name, kind, lines, who, good in SITUATIONS:
                res = await one(cli, system, f"{model}-{name}-{r}", kind, lines, who)
                rows.append({"situation": name, "round": r, "good": good, **res})
                print(f"{model:7} {name:12} {res['first_s'] or '-'!s:>5} {res['total_s']:>5}  {res['said'] or '(' + res['raw'][:60] + ')'}",
                      flush=True)
        firsts = [x["first_s"] for x in rows if x["first_s"]]
        out["models"][model] = {"median_first_s": round(statistics.median(firsts), 2) if firsts else None,
                                "median_total_s": round(statistics.median(x["total_s"] for x in rows), 2), "rows": rows}
        if cli.warm and cli.warm.returncode is None:
            cli.warm.kill()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1))
    for m, v in out["models"].items():
        print(f"{m}: first sentence {v['median_first_s']} s, whole answer {v['median_total_s']} s (median)")


if __name__ == "__main__":
    asyncio.run(main())
