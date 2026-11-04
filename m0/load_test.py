"""M0 Pi load test: Pip's everyday workload for 10 minutes, with Ziggletron paused (D-44). Silent.

Workload: live "Hey Pip" detector (separate process), Piper speaking a reply every 8 s (synthesis only),
a Groq transcription every 20 s. Measures CPU, memory, temperature, throttling and detector lag.

    cd ~/pip/m0 && ~/.local/bin/uv run load_test.py [minutes]
"""
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import benchmark as b
from tts_bench import REPLIES, make_engine

DATA = Path.home() / "pip-data/m0"
TRAIN = DATA / "wake-train"
TASK = DATA / "tasks/load-test.json"
DETECTOR = r'''
import sys, time, json, subprocess, numpy as np
from openwakeword.model import Model
m = Model(wakeword_models=[sys.argv[1]], inference_framework="onnx")
p = subprocess.Popen(["pw-record","--rate","16000","--channels","1","--format","s16","-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
end = time.time() + float(sys.argv[2]); n = 0; worst = 0.0; total = 0.0; t0 = time.time()
while time.time() < end:
    raw = p.stdout.read(2560)
    if len(raw) < 2560: break
    a = time.perf_counter(); m.predict(np.frombuffer(raw, np.int16)); d = time.perf_counter() - a
    n += 1; total += d; worst = max(worst, d)
p.terminate()
wall = time.time() - t0
print(json.dumps({"frames": n, "expected_frames": int(wall / 0.08), "avg_ms": round(1000 * total / max(1, n), 1), "worst_ms": round(1000 * worst, 1)}))
'''


def cpu_times():
    with open("/proc/stat") as fh:
        v = [int(x) for x in fh.readline().split()[1:]]
    return v[3] + v[4], sum(v)


def mem_used_gb():
    info = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":", 1); info[k] = int(v.split()[0])
    return (info["MemTotal"] - info["MemAvailable"]) / 1048576


def temp_c():
    return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000


def throttled():
    out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True).stdout
    m = re.search(r"0x([0-9a-fA-F]+)", out)
    return int(m.group(1), 16) if m else None


def progress(stage, done, total, finished=False):
    TASK.parent.mkdir(parents=True, exist_ok=True)
    tmp = TASK.with_suffix(".tmp")
    tmp.write_text(json.dumps({"name": "Pi load test (silent, Ziggletron paused)", "stage": stage, "done": done,
                               "total": total, "note": "", "finished": finished, "failed": False, "updated": time.time()}))
    os.replace(tmp, TASK)


def main(minutes=10.0):
    secs = minutes * 60
    zigg_was = subprocess.run(["systemctl", "--user", "is-active", "ziggletron.service"], capture_output=True, text=True).stdout.strip()
    if zigg_was == "active":
        subprocess.run(["systemctl", "--user", "stop", "ziggletron.service"])
    model = max((TRAIN / "model_out").glob("hey_pip*.onnx"), key=lambda p: p.stat().st_mtime)
    det = subprocess.Popen([str(TRAIN / ".venv/bin/python"), "-W", "ignore", "-c", DETECTOR, str(model), str(secs)],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=str(Path.home()))
    try:
        speak = make_engine("piper", "en_US-lessac-medium.onnx")
        groq = b.Groq("whisper-large-v3-turbo", b.VOCAB_PROMPT)
        clips = sorted(p.stem for p in b.REC.glob("A*.wav"))
        t0 = time.time(); next_tts = next_stt = t0
        cpu, mem, tmp, tts_s, stt_s = [], [], [], [], []
        idle0, tot0 = cpu_times(); worst_throttle = 0
        while time.time() - t0 < secs:
            now = time.time()
            if now >= next_tts:
                a = time.perf_counter(); speak(random.choice(REPLIES)); tts_s.append(time.perf_counter() - a); next_tts = now + 8
            if now >= next_stt:
                try:
                    _, d = groq(b.load(random.choice(clips))); stt_s.append(d)
                except Exception:
                    stt_s.append(None)
                next_stt = now + 20
            time.sleep(1)
            idle1, tot1 = cpu_times()
            if tot1 > tot0:
                cpu.append(100 * (1 - (idle1 - idle0) / (tot1 - tot0)))
            idle0, tot0 = idle1, tot1
            mem.append(mem_used_gb()); tmp.append(temp_c())
            worst_throttle |= throttled() or 0
            progress("Running", int(time.time() - t0), int(secs))
        detector = json.loads(det.communicate(timeout=60)[0].strip().splitlines()[-1])
    finally:
        if det.poll() is None:
            det.kill()
        if zigg_was == "active":
            subprocess.run(["systemctl", "--user", "start", "ziggletron.service"])
    keeps_up = detector["frames"] >= 0.97 * detector["expected_frames"] and detector["worst_ms"] < 80
    avg_cpu, peak_mem, peak_temp = sum(cpu) / len(cpu), max(mem), max(tmp)
    ok_stt = [x for x in stt_s if x is not None]
    passed = avg_cpu <= 70 and peak_mem <= 5 and keeps_up and peak_temp < 80 and not (worst_throttle & 0xF)
    result = {"id": "pi-load", "ran_at": time.strftime("%Y-%m-%d %H:%M"), "passed": passed,
              "summary": (f"{minutes:.0f} min of Pip's everyday work: CPU average {avg_cpu:.0f}% (peak {max(cpu):.0f}%), "
                          f"memory peak {peak_mem:.1f} GB, temperature peak {peak_temp:.0f} °C, "
                          f"{'no throttling' if not worst_throttle else 'throttling flags ' + hex(worst_throttle)}. "
                          f"Wake detector {'kept up' if keeps_up else 'fell behind'} ({detector['avg_ms']} ms per 80 ms frame, worst {detector['worst_ms']} ms). "
                          f"Piper reply {sum(tts_s) / len(tts_s):.2f} s; Groq {sum(ok_stt) / max(1, len(ok_stt)):.2f} s "
                          f"({len(stt_s) - len(ok_stt)} failed). Ziggletron and the TV kiosk's own load not included (paused/idle)."),
              "details": {"cpu_avg": round(avg_cpu, 1), "cpu_peak": round(max(cpu), 1), "mem_peak_gb": round(peak_mem, 2),
                          "temp_peak_c": peak_temp, "throttled_flags": worst_throttle, "detector": detector,
                          "tts_avg_s": round(sum(tts_s) / len(tts_s), 3), "groq_avg_s": round(sum(ok_stt) / max(1, len(ok_stt)), 3)}}
    (DATA / "results/pi-load.json").write_text(json.dumps(result, indent=1))
    progress("Finished", 1, 1, finished=True)
    print(result["summary"])


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 10.0)
