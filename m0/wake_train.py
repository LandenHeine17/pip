"""Run the three openWakeWord training stages for "Hey Pip", reporting progress to the dashboard.

    cd ~/pip-data/m0/wake-train && .venv/bin/python ~/pip/m0/wake_train.py [generate|augment|train|all]
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

W = Path.home() / "pip-data/m0/wake-train"
CFG = W / os.environ.get("WAKE_CFG", "hey_pip.yml")
cfg = yaml.safe_load(CFG.read_text())
TASK = Path.home() / f"pip-data/m0/tasks/wake-train-{cfg['model_name']}.json"
LABEL = cfg["target_phrase"][0].title()
OUT = W / cfg["output_dir"] / cfg["model_name"]


def progress(stage, done=0, total=0, note="", finished=False, failed=False):
    TASK.parent.mkdir(parents=True, exist_ok=True)
    obj = {"name": f"Wake-word training: \"{LABEL}\"", "stage": stage, "done": done, "total": total, "note": note,
           "finished": finished, "failed": failed, "updated": time.time()}
    tmp = TASK.with_suffix(".tmp"); tmp.write_text(json.dumps(obj)); os.replace(tmp, TASK)


def count(sub):
    d = OUT / sub
    return len(list(d.glob("*.wav"))) if d.exists() else 0


def run(flag, stage, measure):
    logname = W / f"train-{cfg['model_name']}-{flag.strip('-')}.log"
    log = open(logname, "a")
    p = subprocess.Popen([sys.executable, "-W", "ignore", str(W / "openwakeword/openwakeword/train.py"),
                          "--training_config", str(CFG), flag], cwd=W, stdout=log, stderr=subprocess.STDOUT)
    t0 = time.time()
    while p.poll() is None:
        done, total, note = measure(t0)
        progress(stage, done, total, note)
        time.sleep(15)
    if p.returncode != 0:
        tail = logname.read_text(errors="replace")[-400:]
        progress(f"{stage}: failed", note=tail.replace("\n", " ")[-300:], failed=True)
        sys.exit(p.returncode)


def gen_measure(t0):
    n, nv = cfg["n_samples"], cfg["n_samples_val"]
    have = count("positive_train") + count("positive_test") + count("negative_train") + count("negative_test")
    return have, 2 * (n + nv), f"practice clips made, {int(time.time() - t0) // 60} min so far"


def aug_measure(t0):
    feats = sorted(OUT.glob("*.npy"))
    return len(feats), 4, f"feature files ready, {int(time.time() - t0) // 60} min so far"


def train_measure(t0):
    return 0, 0, f"training, {int(time.time() - t0) // 60} min so far (usually 30–90 min on the Pi)"


def main(which):
    stages = ["generate", "augment", "train"] if which == "all" else [which]
    if "generate" in stages:
        run("--generate_clips", "1/3 Making practice audio", gen_measure)
    if "augment" in stages:
        run("--augment_clips", "2/3 Adding room echo and background sound", aug_measure)
    if "train" in stages:
        run("--train_model", "3/3 Training the detector", train_measure)
    if "train" not in stages:
        progress(f"{stages[-1]} stage done", 1, 1, finished=True)
        return
    model = W / cfg["output_dir"] / f"{cfg['model_name']}.onnx"
    progress("Done" if model.exists() else "Finished, but no model file found", 1, 1,
             note=str(model) if model.exists() else "", finished=model.exists(), failed=not model.exists())


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all")
