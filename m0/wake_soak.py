"""Overnight false-wake check: listen to the room with a trained detector and count wakes.

No audio is stored or sent anywhere: only the time and score of each detection.
    cd ~/pip-data/m0/wake-train && .venv/bin/python ~/pip/m0/wake_soak.py model_out/hey_pip.onnx 0.5 07:30
"""
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from openwakeword.model import Model

DATA = Path.home() / "pip-data/m0"
model_path, threshold, until = Path(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
name = model_path.stem
end = time.mktime(time.strptime(time.strftime("%Y-%m-%d ") + until, "%Y-%m-%d %H:%M"))
if end < time.time():
    end += 86400
OUT = DATA / f"results/wake-soak-{name}.json"
TASK = DATA / f"tasks/wake-soak-{name}.json"
label = name.replace("_", " ").title()


def write(path, obj):
    tmp = path.with_suffix(".tmp"); tmp.write_text(json.dumps(obj, indent=1)); os.replace(tmp, path)


m = Model(wakeword_models=[str(model_path)], inference_framework="onnx")
proc = subprocess.Popen(["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", "-"],
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
t0, events, last_hit, last_save, cpu0 = time.time(), [], 0.0, 0.0, time.process_time()
silent_frames = frames = 0
try:
    while time.time() < end:
        raw = proc.stdout.read(2560)  # 80 ms
        if len(raw) < 2560:
            break
        x = np.frombuffer(raw, np.int16)
        frames += 1
        silent_frames += int(not x.any())
        score = max(m.predict(x).values())
        if score >= threshold and time.time() - last_hit > 2:
            events.append({"time": time.strftime("%H:%M:%S"), "score": round(float(score), 3)})
            last_hit = time.time()
        if time.time() - last_save > 60:
            hrs = (time.time() - t0) / 3600
            cpu = 100 * (time.process_time() - cpu0) / max(1, time.time() - t0)
            obj = {"id": f"wake-soak-{name}", "ran_at": time.strftime("%Y-%m-%d %H:%M"), "passed": None,
                   "summary": (f"Listening ({hrs:.1f} h so far, until {until}): {len(events)} false wakes "
                               f"(≈{len(events) / hrs * 8:.1f} per 8 h). Detector uses {cpu:.0f}% of one CPU core. "
                               f"Muted {100 * silent_frames / frames:.0f}% of the time."),
                   "details": {"threshold": threshold, "events": events[-100:], "cpu_pct_one_core": round(cpu, 1),
                               "muted_pct": round(100 * silent_frames / frames, 1)}}
            write(OUT, obj)
            write(TASK, {"name": f"Overnight false-wake check: \"{label}\"", "stage": "Listening (no audio kept)",
                         "done": int(time.time() - t0), "total": int(end - t0), "note": f"{len(events)} false wakes so far",
                         "finished": False, "failed": False, "updated": time.time()})
            last_save = time.time()
finally:
    proc.terminate()
    hrs = (time.time() - t0) / 3600
    per8 = len(events) / hrs * 8 if hrs > 0.1 else None
    obj = json.loads(OUT.read_text()) if OUT.exists() else {"id": f"wake-soak-{name}", "details": {}}
    cpu = obj.get("details", {}).get("cpu_pct_one_core")
    obj.update(ran_at=time.strftime("%Y-%m-%d %H:%M"), passed=(per8 is not None and per8 <= 1),
               summary=(f"Listened {hrs:.1f} h overnight: {len(events)} false wakes (≈{(per8 or 0):.1f} per 8 h; target ≤1)."
                        + (f" Detector used {cpu:.0f}% of one CPU core." if cpu is not None else "")))
    write(OUT, obj)
    write(TASK, {"name": f"Overnight false-wake check: \"{label}\"", "stage": "Finished", "done": 1, "total": 1,
                 "note": f"{len(events)} false wakes in {hrs:.1f} h", "finished": True, "failed": False, "updated": time.time()})
