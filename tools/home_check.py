#!/usr/bin/env python3
"""Does Pip reach every home device, and how? Settles D-36's local control for each device (D-139, D-140).

    systemd-run --user --same-dir --pipe --wait -q -p MemoryMax=4500M ~/.local/bin/uv run tools/home_check.py [--switch]

Uses the home_control module itself: lists the devices from Tuya's cloud, finds each one on pip-plugs by its MAC address
(`locate`), and asks each one found whether it's on, directly (timed). With --switch it also turns each device
off and back on (directly if it's on pip-plugs, else through the cloud; the owner watching: each lamp and the fan
blinks off for a moment). No sound. Results, with devices numbered (no names, ids, MACs or keys):
~/pip-data/m0/results/home-devices.json.
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

OUT = Path.home() / "pip-data" / "m0" / "results" / "home-devices.json"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--switch", action="store_true", help="also turn each device off and back on")
    a = ap.parse_args()
    cfg = settings.load()
    reg = Registry.load(cfg, infos=[read(ROOT / "home_control", cfg.get("modules", {}).get("home_control", {}))])
    mod = reg.modules["home_control"]
    await mod.refresh()   # the devices, their keys and MACs; then where each is on pip-plugs
    print(f"Tuya's cloud lists {len(mod.plugs)} device(s); {len(mod.found)} found on pip-plugs.")
    rows = []
    for n, (pid, p) in enumerate(mod.plugs.items(), 1):
        row = {"device": n, "kind": mod.kind(pid), "on_pip_plugs": pid in mod.found}
        try:
            if pid in mod.found:
                t = time.perf_counter()
                row["on"] = await mod.direct(pid)
                row["status_s"], row["version"] = round(time.perf_counter() - t, 3), mod.found[pid]["version"]
            else:
                row["on"] = p["on"]
            if a.switch:
                for on in (not row["on"], row["on"]):
                    t = time.perf_counter()
                    await mod.switch(pid, on, publish=False)
                    row.setdefault("switch_s", []).append(round(time.perf_counter() - t, 3))
                    row["how"] = mod.switched[pid][1]
                    await asyncio.sleep(1.5)
        except Exception as e:
            row["error"] = type(e).__name__
        rows.append(row)
        print(f"{p['name']:12} {row}")
    direct = [s for r in rows if r.get("how") == "direct" for s in r.get("switch_s", [])]
    cloud = [s for r in rows if r.get("how") == "cloud" for s in r.get("switch_s", [])]
    out = {"id": "home-devices", "ran_at": time.strftime("%Y-%m-%d %H:%M"),
           "passed": bool(rows) and not any("error" in r for r in rows),
           "settles": "D-36 / D-139 / D-140: does Pip reach every home device, directly on pip-plugs?",
           "devices": len(rows), "on_pip_plugs": sum(r["on_pip_plugs"] for r in rows), "switched": a.switch,
           "median_direct_switch_s": round(statistics.median(direct), 3) if direct else None,
           "median_cloud_switch_s": round(statistics.median(cloud), 3) if cloud else None, "rows": rows}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1))
    print(f"{out['on_pip_plugs']} of {len(rows)} on pip-plugs; direct switch {out['median_direct_switch_s']} s, "
          f"cloud {out['median_cloud_switch_s']} s (median). Results in {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
