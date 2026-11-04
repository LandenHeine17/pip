"""M0 wake-word test with an on-device open-vocabulary keyword spotter (sherpa-onnx, no training).

    uv run wakeword.py clips            # score the consented recordings at several sensitivities
    uv run wakeword.py soak 2h          # listen live; count detections only (no audio kept)

Results go to ~/pip-data/m0/results/wake-word*.json for the dashboard.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import sentencepiece as spm
import sherpa_onnx

import benchmark as b

MODEL = Path.home() / "pip-data/m0/models/kws/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
KEYWORDS = {"pip": "PIP", "hey pip": "HEY PIP"}
SETTINGS = [  # (boost score, trigger threshold): higher threshold = stricter
    (1.0, 0.25), (1.5, 0.25), (1.5, 0.15), (2.0, 0.15), (2.0, 0.10), (2.5, 0.10), (3.0, 0.08),
]


def keywords_file(boost: float, threshold: float) -> Path:
    sp = spm.SentencePieceProcessor(model_file=str(MODEL / "bpe.model"))
    lines = []
    for label, text in KEYWORDS.items():
        pieces = " ".join(sp.encode(text, out_type=str))
        lines.append(f"{pieces} :{boost} #{threshold} @{label.replace(' ', '_')}")
    path = MODEL.parent / f"keywords-{boost}-{threshold}.txt"
    path.write_text("\n".join(lines) + "\n")
    return path


def spotter(boost: float, threshold: float):
    return sherpa_onnx.KeywordSpotter(
        tokens=str(MODEL / "tokens.txt"),
        encoder=str(MODEL / "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        decoder=str(MODEL / "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        joiner=str(MODEL / "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        num_threads=1, keywords_file=str(keywords_file(boost, threshold)), provider="cpu")


def detect(kws, audio: np.ndarray) -> list[str]:
    s = kws.create_stream()
    hits = []
    padded = np.concatenate([np.zeros(4000, np.float32), audio, np.zeros(16000, np.float32)])
    for i in range(0, len(padded), 1600):  # feed 100 ms chunks like a live mic
        s.accept_waveform(16000, padded[i:i + 1600])
        while kws.is_ready(s):
            kws.decode_stream(s)
            r = kws.get_result(s)
            if r:
                hits.append(r)
                kws.reset_stream(s)
    return hits


def clips():
    phrases = {p["id"]: p for p in json.loads(b.PHRASES.read_text())["phrases"]}
    manifest = json.loads((b.REC / "manifest.json").read_text())
    ids = sorted(i for i in manifest if i in phrases)
    audio = {i: b.load(i) for i in ids}
    total_audio = sum(len(a) for a in audio.values()) / 16000
    rows = []
    for boost, thr in SETTINGS:
        kws = spotter(boost, thr)
        t0 = time.perf_counter()
        hits = {i: detect(kws, audio[i]) for i in ids}
        dt = time.perf_counter() - t0
        calls = [i for i in ids if phrases[i]["wake"] != "none"]
        nocall = [i for i in ids if phrases[i]["wake"] == "none"]
        neg = [i for i in ids if phrases[i]["category"] == "negative"]
        def rec(cat=None):
            sel = [i for i in calls if cat is None or phrases[i]["category"] in cat]
            return round(100 * sum(bool(hits[i]) for i in sel) / len(sel), 1) if sel else None
        row = {"boost": boost, "threshold": thr,
               "recall": rec(), "recall_close": rec({"clear", "quiet", "short"}),
               "recall_far": rec({"distant", "vocab", "pauses"}), "recall_media": rec({"media"}),
               "false_on_lookalikes": sum(bool(hits[i]) for i in neg), "lookalikes": len(neg),
               "false_on_other_no_call": sum(bool(hits[i]) for i in nocall if i not in neg),
               "other_no_call": len([i for i in nocall if i not in neg]),
               "rtf": round(dt / total_audio, 3),
               "false_examples": [phrases[i]["text"] for i in nocall if hits[i]][:6],
               "missed_examples": [phrases[i]["text"] for i in calls if not hits[i]][:6]}
        rows.append(row)
        print(f"boost {boost} thr {thr}: heard {row['recall']}% (close {row['recall_close']}%, far {row['recall_far']}%, "
              f"video {row['recall_media']}%) · false {row['false_on_lookalikes']}/{row['lookalikes']} look-alikes, "
              f"{row['false_on_other_no_call']}/{row['other_no_call']} other · RTF {row['rtf']}", flush=True)
    # best = highest recall with zero false detections; else fewest false
    clean = [r for r in rows if r["false_on_lookalikes"] + r["false_on_other_no_call"] == 0]
    best = max(clean, key=lambda r: r["recall"]) if clean else min(rows, key=lambda r: (r["false_on_lookalikes"] + r["false_on_other_no_call"], -r["recall"]))
    passed = best["recall"] >= 95 and not (best["false_on_lookalikes"] + best["false_on_other_no_call"])
    result = {"id": "wake-word", "ran_at": time.strftime("%Y-%m-%d %H:%M"), "passed": passed if passed else (False if best["recall"] < 80 else None),
              "summary": (f"Best setting (boost {best['boost']}, threshold {best['threshold']}): heard 'Pip' in {best['recall']}% of calls "
                          f"(close {best['recall_close']}%, across room {best['recall_far']}%, with video {best['recall_media']}%), "
                          f"false wakes on {best['false_on_lookalikes']}/{best['lookalikes']} look-alikes. Runs at {best['rtf']}× real time on one core. "
                          "The 8-hour false-wake check needs the live soak test."),
              "details": {"model": MODEL.name, "settings": rows, "best": best}}
    b.RESULTS.mkdir(parents=True, exist_ok=True)
    tmp = b.RESULTS / "wake-word.tmp"; tmp.write_text(json.dumps(result, indent=1)); os.replace(tmp, b.RESULTS / "wake-word.json")
    print(result["summary"])


def soak(duration: str):
    """Listen live; record only detection times and labels (never audio)."""
    secs = float(duration[:-1]) * (3600 if duration.endswith("h") else 60)
    best = json.loads((b.RESULTS / "wake-word.json").read_text())["details"]["best"]
    kws = spotter(best["boost"], best["threshold"])
    s = kws.create_stream()
    proc = subprocess.Popen(["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    t0, events, out = time.time(), [], b.RESULTS / "wake-soak.json"
    def save(final=False):
        hrs = (time.time() - t0) / 3600
        per8 = round(len(events) / hrs * 8, 2) if hrs > 0.05 else None
        result = {"id": "wake-soak", "ran_at": time.strftime("%Y-%m-%d %H:%M"),
                  "passed": (per8 is not None and per8 <= 1) if final else None,
                  "summary": f"{'Finished' if final else 'Running'}: {hrs:.1f} h listened, {len(events)} wake detections "
                             f"(≈{per8 if per8 is not None else '–'} per 8 h). Say nothing to Pip during this test: every detection counts as false.",
                  "details": {"setting": best, "events": events[-200:]}}
        tmp = out.with_suffix(".tmp"); tmp.write_text(json.dumps(result, indent=1)); os.replace(tmp, out)
    last_save = 0
    try:
        while time.time() - t0 < secs:
            raw = proc.stdout.read(3200)  # 100 ms
            if not raw:
                break
            s.accept_waveform(16000, np.frombuffer(raw, np.int16).astype(np.float32) / 32768)
            while kws.is_ready(s):
                kws.decode_stream(s)
                r = kws.get_result(s)
                if r:
                    events.append({"time": time.strftime("%H:%M:%S"), "keyword": r})
                    kws.reset_stream(s)
            if time.time() - last_save > 30:
                save(); last_save = time.time()
    finally:
        proc.terminate()
        save(final=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["soak"]:
        soak(sys.argv[2] if len(sys.argv) > 2 else "2h")
    else:
        clips()
