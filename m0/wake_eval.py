"""Score a trained "Hey Pip" detector on the owner's consented recordings (run in the wake-train venv).

    cd ~/pip-data/m0/wake-train && .venv/bin/python ~/pip/m0/wake_eval.py [model.onnx]
"""
import json
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np
from openwakeword.model import Model

HOME = Path.home()
DATA = HOME / "pip-data/m0"
REC = DATA / "recordings"
PHRASES = HOME / "pip/m0/phrases.json"
MODEL = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA / "wake-train/model_out/hey_pip.onnx"
THRESHOLDS = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]


def load(pid):
    with wave.open(str(REC / f"{pid}.wav")) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return a[int(16000 * 0.3):]


def peak_score(model, audio):
    model.reset()
    x = np.concatenate([np.zeros(8000, np.int16), audio, np.zeros(16000, np.int16)])
    best = 0.0
    for i in range(0, len(x) - 1280 + 1, 1280):  # 80 ms frames, as live
        s = model.predict(x[i:i + 1280])
        best = max(best, max(s.values()))
    return best


def synthetic_positives(name, limit=300):
    d = DATA / f"wake-train/model_out/{name}/positive_test"
    files = sorted(d.glob("*.wav"))[:limit]
    out = []
    for f in files:
        with wave.open(str(f)) as w:
            out.append(np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16))
    return out


def other_phrase(name):
    """Evaluate a detector whose phrase the owner never recorded: synthetic held-out positives,
    and every real recording as a negative (they contain 'Pip', 'Ziggletron' and look-alikes)."""
    phrases = {p["id"]: p for p in json.loads(PHRASES.read_text())["phrases"]}
    ids = sorted(i for i in json.loads((REC / "manifest.json").read_text()) if i in phrases)
    m = Model(wakeword_models=[str(MODEL)], inference_framework="onnx")
    real_pos = [i for i in ids if phrases[i]["wake"] == name]
    ids = [i for i in ids if i not in real_pos]
    neg = {i: peak_score(m, load(i)) for i in ids}
    pos = [peak_score(m, a) for a in synthetic_positives(name)]
    rpos = {i: peak_score(m, load(i)) for i in real_pos}
    rows = []
    for t in THRESHOLDS:
        fw = [i for i in ids if neg[i] >= t]
        rows.append({"threshold": t, "synthetic_recall": round(100 * sum(s >= t for s in pos) / max(1, len(pos)), 1),
                     "real_recall": round(100 * sum(v >= t for v in rpos.values()) / len(rpos), 1) if rpos else None,
                     "real_recall_close": (lambda sel: round(100 * sum(rpos[i] >= t for i in sel) / len(sel), 1) if sel else None)(
                         [i for i in rpos if phrases[i]["category"] == "clear"]),
                     "real_recall_far": (lambda sel: round(100 * sum(rpos[i] >= t for i in sel) / len(sel), 1) if sel else None)(
                         [i for i in rpos if phrases[i]["category"] == "distant"]),
                     "false": len(fw), "real_clips": len(ids), "false_examples": [phrases[i]["text"] for i in fw][:6]})
        r = rows[-1]
        print(f"threshold {t}: synthetic recall {r['synthetic_recall']}% · your recordings {r['real_recall']}% · false wakes {r['false']}/{r['real_clips']} real clips")
    clean = [r for r in rows if r["false"] == 0]
    key = (lambda r: r["real_recall"]) if rpos else (lambda r: r["synthetic_recall"])
    best = max(clean, key=key) if clean else min(rows, key=lambda r: (r["false"], -key(r)))
    label = name.replace("_", " ").title()
    heard = (f"heard {best['real_recall']}% of your real '{label}' recordings (close {best['real_recall_close']}%, "
             f"across the room {best['real_recall_far']}%) and {best['synthetic_recall']}% of synthetic ones") if rpos else \
            f"heard {best['synthetic_recall']}% of held-out synthetic clips (record session D to test your real voice)"
    result = {"id": f"wake-word-{name}", "ran_at": time.strftime("%Y-%m-%d %H:%M"),
              "passed": (best["false"] == 0 and (best["real_recall"] or 0) >= 95) if rpos else (None if best["false"] == 0 else False),
              "summary": (f"Trained '{label}' detector at threshold {best['threshold']}: {heard}; false wakes on {best['false']} "
                          f"of {best['real_clips']} other recordings (which include 'Pip', 'Ziggletron' and look-alikes)."),
              "details": {"model": MODEL.name, "thresholds": rows, "best": best}}
    out = DATA / f"results/wake-word-{name}.json"
    tmp = out.with_suffix(".tmp"); tmp.write_text(json.dumps(result, indent=1)); os.replace(tmp, out)
    print(result["summary"])


def main():
    if MODEL.stem != "hey_pip":
        return other_phrase(MODEL.stem)
    phrases = {p["id"]: p for p in json.loads(PHRASES.read_text())["phrases"]}
    ids = sorted(i for i in json.loads((REC / "manifest.json").read_text()) if i in phrases and phrases[i]["session"] in "ABC")
    m = Model(wakeword_models=[str(MODEL)], inference_framework="onnx")
    t0 = time.perf_counter()
    scores = {i: peak_score(m, load(i)) for i in ids}
    dt = time.perf_counter() - t0
    audio_s = sum(len(load(i)) for i in ids) / 16000
    hey = [i for i in ids if phrases[i]["wake"] == "hey pip"]
    plain = [i for i in ids if phrases[i]["wake"] == "pip"]
    nocall = [i for i in ids if phrases[i]["wake"] == "none"]
    rows = []
    for t in THRESHOLDS:
        def rate(sel, cats=None):
            sel = [i for i in sel if cats is None or phrases[i]["category"] in cats]
            return round(100 * sum(scores[i] >= t for i in sel) / len(sel), 1) if sel else None
        rows.append({"threshold": t, "hey_pip": rate(hey), "hey_pip_close": rate(hey, {"clear", "quiet", "short"}),
                     "hey_pip_far": rate(hey, {"distant", "vocab", "pauses"}), "hey_pip_media": rate(hey, {"media"}),
                     "plain_pip": rate(plain), "false": sum(scores[i] >= t for i in nocall), "no_call": len(nocall),
                     "false_examples": [phrases[i]["text"] for i in nocall if scores[i] >= t][:5]})
        r = rows[-1]
        print(f"threshold {t}: 'Hey Pip' heard {r['hey_pip']}% (close {r['hey_pip_close']}%, far {r['hey_pip_far']}%, "
              f"video {r['hey_pip_media']}%) · bare 'Pip' {r['plain_pip']}% · false {r['false']}/{r['no_call']}")
    clean = [r for r in rows if r["false"] == 0]
    best = max(clean, key=lambda r: r["hey_pip"]) if clean else min(rows, key=lambda r: (r["false"], -r["hey_pip"]))
    passed = best["hey_pip"] >= 95 and best["false"] == 0
    result = {"id": "wake-word-trained", "ran_at": time.strftime("%Y-%m-%d %H:%M"),
              "passed": True if passed else (False if best["hey_pip"] < 80 else None),
              "summary": (f"Trained 'Hey Pip' detector at threshold {best['threshold']}: heard 'Hey Pip' in {best['hey_pip']}% "
                          f"(close {best['hey_pip_close']}%, across room {best['hey_pip_far']}%, with video {best['hey_pip_media']}%); "
                          f"false wakes on {best['false']}/{best['no_call']} phrases that don't call Pip. "
                          f"Bare 'Pip' (not trained for it): {best['plain_pip']}%. Speed {dt / audio_s:.3f}× real time."),
              "details": {"model": MODEL.name, "thresholds": rows, "best": best,
                          "scores": {i: round(s, 3) for i, s in scores.items()}}}
    out = DATA / "results/wake-word-trained.json"
    tmp = out.with_suffix(".tmp"); tmp.write_text(json.dumps(result, indent=1)); os.replace(tmp, out)
    print(result["summary"])


if __name__ == "__main__":
    main()
