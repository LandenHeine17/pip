#!/usr/bin/env python3
"""Do the smart plugs on Pip's own network (pip-plugs) switch directly? Settles D-36's local control (D-139).

    systemd-run --user --same-dir --pipe --wait -q -p MemoryMax=4500M ~/.local/bin/uv run tools/plug_check.py [--switch]

Uses the tuya module itself: lists the plugs from Tuya's cloud, listens 20 s for the plugs announcing themselves
on pip-plugs, and asks each one found whether it's on, directly (timed). With --switch it also turns each found
plug off and back on directly (the owner watching: whatever's plugged in blinks off for a moment). No sound.
Results, with no names, ids or keys: ~/pip-data/m0/results/tuya-local.json.
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
from pip_assistant.common.modules import ROOT, read  # noqa: E402
from pip_assistant.core.modules import Registry  # noqa: E402

OUT = Path.home() / "pip-data" / "m0" / "results" / "tuya-local.json"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--switch", action="store_true", help="also turn each plug found off and on again")
    ap.add_argument("--listen", type=float, default=20.0)
    a = ap.parse_args()
    cfg = settings.load()
    reg = Registry.load(cfg, infos=[read(ROOT / "tuya", cfg.get("modules", {}).get("tuya", {}))])
    mod = reg.modules["tuya"]
    await mod.refresh()
    print(f"Tuya's cloud lists {len(mod.plugs)} plug(s), {len(mod.keys)} with a local key.")
    await mod._listen()
    print(f"Listening {a.listen:.0f} s on ports {sorted(mod.listening)} for the plugs…", flush=True)
    await asyncio.sleep(a.listen)
    rows = []
    for n, pid in enumerate(mod.plugs, 1):
        plug = mod.near(pid)
        row = {"plug": n, "heard": pid in mod.found, "version": (mod.found.get(pid) or {}).get("version")}
        if plug:
            t = time.perf_counter()
            try:
                row["on"] = await mod.local.state(plug)
                row["status_s"] = round(time.perf_counter() - t, 3)
                if a.switch:
                    for on in (not row["on"], row["on"]):
                        t = time.perf_counter()
                        await mod.local.switch(plug, on)
                        row.setdefault("switch_s", []).append(round(time.perf_counter() - t, 3))
                        await asyncio.sleep(1.5)
            except Exception as e:
                row["error"] = type(e).__name__
        rows.append(row)
        print(f"plug {n}: {row}")
    for t in mod.listening.values():
        t.close()
    timed = [s for r in rows for s in r.get("switch_s", [])] or [r["status_s"] for r in rows if "status_s" in r]
    ok = any("status_s" in r for r in rows)
    out = {"id": "tuya-local", "ran_at": time.strftime("%Y-%m-%d %H:%M"), "passed": ok,
           "settles": "D-36 / D-139: are the plugs on Pip's own network switched directly?",
           "plugs_in_cloud": len(mod.plugs), "plugs_heard": sum(r["heard"] for r in rows),
           "median_s": round(statistics.median(timed), 3) if timed else None, "switched": a.switch, "rows": rows}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1))
    print(("Direct control works" if ok else "No plug answered directly") + f"; results in {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
