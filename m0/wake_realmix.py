"""Prepare "Hey Pip" v2 training data: synthetic clips from v1 plus half of the owner's real recordings.

The other half of the real recordings is held out for testing (split.json), so the evaluation stays fair.
Owner consent: recordings may be kept on the Pi and used for training (D-72). Nothing here leaves the Pi.

    ~/pip-data/m0/wake-train/.venv/bin/python ~/pip/m0/wake_realmix.py
"""
import json
import shutil
import wave
from pathlib import Path

import numpy as np

HOME = Path.home()
W = HOME / "pip-data/m0/wake-train"
REC = HOME / "pip-data/m0/recordings"
PHRASES = HOME / "pip/m0/phrases.json"
SRC, DST = W / "model_out/hey_pip", W / "model_out/hey_pip_v2"
COPIES = 15  # each real clip appears this many times (augmentation varies every copy)


def main():
    phrases = {p["id"]: p for p in json.loads(PHRASES.read_text())["phrases"]}
    have = json.loads((REC / "manifest.json").read_text())
    ids = sorted(i for i in have if i in phrases and phrases[i]["session"] in "ABC")
    train, test = [], []
    for wake in ("hey pip", "pip", "none"):
        for cat in sorted({phrases[i]["category"] for i in ids}):
            group = [i for i in ids if phrases[i]["wake"] == wake and phrases[i]["category"] == cat]
            train += group[0::2]; test += group[1::2]
    DST.mkdir(parents=True, exist_ok=True)
    for sub in ("positive_train", "positive_test", "negative_train", "negative_test"):
        d = DST / sub
        if not d.exists():
            shutil.copytree(SRC / sub, d)  # reuse v1's synthetic clips
    added = {"positive": 0, "negative": 0}
    for i in train:
        p = phrases[i]
        if p["wake"] == "pip":  # bare "Pip" isn't the trained phrase: leave it out of both classes
            continue
        kind = "positive" if p["wake"] == "hey pip" else "negative"
        with wave.open(str(REC / f"{i}.wav")) as w:
            a = np.frombuffer(w.readframes(w.getnframes()), np.int16)[int(16000 * 0.3):]
        for k in range(COPIES):
            out = DST / f"{kind}_train" / f"real_{i}_{k:02d}.wav"
            with wave.open(str(out), "wb") as o:
                o.setnchannels(1); o.setsampwidth(2); o.setframerate(16000); o.writeframes(a.tobytes())
            added[kind] += 1
    (DST / "split.json").write_text(json.dumps({"train": train, "test": test}, indent=1))
    print(f"train ids {len(train)}, held-out test ids {len(test)}; added {added} real training files")


if __name__ == "__main__":
    main()
