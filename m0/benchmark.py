"""M0 speech benchmark: score the owner's consented recordings with each STT engine.

    uv run benchmark.py                       # all engines
    uv run benchmark.py groq-turbo whisper-base

Reads ~/pip-data/m0/recordings (+ phrases.json for reference text), writes one
result per engine to ~/pip-data/m0/results/stt-<engine>.json for the dashboard.
Audio never leaves the Pi except to Groq for the groq-* engines.
"""
from __future__ import annotations

import io
import json
import os
import re
import statistics
import subprocess
import sys
import time
import tomllib
import wave
from pathlib import Path

import numpy as np

HOME = Path.home()
DATA = HOME / "pip-data" / "m0"
REC = DATA / "recordings"
RESULTS = DATA / "results"
PHRASES = Path(__file__).with_name("phrases.json")
TRIM_S = 0.3  # drop the speakerphone's start-up click

VOCAB_PROMPT = ("Pip, Hey Pip, Ziggletron. Raspberry Pi, ESP32, ESPHome, MQTT, Tailscale, Groq, Moonshine, "
                "Whisper, Kokoro, Piper, Eightree plug, desk lamp, floor lamp, GitHub, SD card, CPU.")

# ------------------------------------------------------------------ audio

def load(pid: str) -> np.ndarray:
    with wave.open(str(REC / f"{pid}.wav")) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return (a[int(16000 * TRIM_S):].astype(np.float32) / 32768.0)


def wav_bytes(audio: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()

# ------------------------------------------------------------------ text normalisation

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def num_words(n: int) -> str:
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])
    if n < 1000:
        return ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + num_words(n % 100))
    if n < 10000:
        return num_words(n // 1000) + " thousand" + ("" if n % 1000 == 0 else " " + num_words(n % 1000))
    return str(n)


FILLERS = {"um", "uh", "erm", "er", "ah", "hmm", "mm"}
SPELLING = {"o'clock": "oclock", "mom": "mum", "okay": "ok", "alright": "all right", "percent": "percent",
            "esp 32": "esp32", "e s p 32": "esp32", "esp thirty two": "esp32", "esp thirtytwo": "esp32",
            "rasberry": "raspberry", "pippin": "pippin"}


def normalise(text: str) -> str:
    t = text.lower().replace("’", "'").replace("…", " ").replace("%", " percent")
    t = re.sub(r"(\d+):(\d\d)", lambda m: f"{m.group(1)} {m.group(2)}", t)  # 4:30 -> 4 30
    t = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", t)
    t = re.sub(r"[-_/]", " ", t)
    t = re.sub(r"[^\w\s']", " ", t)
    t = re.sub(r"\b\d+\b", lambda m: num_words(int(m.group())), t)
    t = re.sub(r"\s+", " ", t).strip()
    for a, b in SPELLING.items():
        t = re.sub(rf"\b{re.escape(a)}\b", b, t)
    words = [w.strip("'") for w in t.split()]
    return " ".join(w for w in words if w and w not in FILLERS)

# ------------------------------------------------------------------ engines

def groq_key() -> str:
    key = tomllib.loads((HOME / "pip-data" / "secrets.toml").read_text()).get("groq_api_key", "")
    if not key:
        sys.exit("No groq_api_key in ~/pip-data/secrets.toml")
    return key


class Groq:
    def __init__(self, model: str, prompt: str | None = None):
        import httpx
        self.model, self.prompt = model, prompt
        self.http = httpx.Client(timeout=30, headers={"Authorization": f"Bearer {groq_key()}", "User-Agent": "pip-m0"})
        self.local = False

    def __call__(self, audio):
        data = {"model": self.model, "language": "en", "temperature": "0", "response_format": "json"}
        if self.prompt:
            data["prompt"] = self.prompt
        for attempt in range(8):
            t0 = time.perf_counter()
            r = self.http.post("https://api.groq.com/openai/v1/audio/transcriptions", data=data,
                               files={"file": ("clip.wav", wav_bytes(audio), "audio/wav")})
            dt = time.perf_counter() - t0
            if r.status_code == 200:
                return r.json().get("text", ""), dt
            if r.status_code in (429, 500, 502, 503):
                wait = float(r.headers.get("retry-after", 0) or 0) or min(60, 3 * 2 ** attempt)
                print(f"   Groq {r.status_code}, waiting {wait:.0f} s", flush=True)
                time.sleep(wait)
                continue
            raise RuntimeError(f"Groq error {r.status_code}: {r.text[:200]}")
        raise RuntimeError("Groq kept refusing (rate limit)")


class FasterWhisper:
    def __init__(self, size: str):
        from faster_whisper import WhisperModel
        self.m = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=4)
        self.local = True

    def __call__(self, audio):
        t0 = time.perf_counter()
        segs, _ = self.m.transcribe(audio, language="en", beam_size=1, condition_on_previous_text=False)
        text = " ".join(s.text for s in segs)
        return text, time.perf_counter() - t0


class Moonshine:
    def __init__(self, name: str):
        from moonshine_onnx import MoonshineOnnxModel, load_tokenizer
        self.m, self.tok = MoonshineOnnxModel(model_name=name), load_tokenizer()
        self.local = True

    def __call__(self, audio):
        t0 = time.perf_counter()
        max_len = max(8, int(len(audio) / 16000 * 6))  # ~6 tokens per second: stops repetition loops
        tokens = self.m.generate(audio[None, :], max_len=max_len)
        return self.tok.decode_batch(tokens)[0], time.perf_counter() - t0


ENGINES = {
    "groq-turbo": ("stt-groq-turbo", "Groq Whisper Large V3 Turbo", lambda: Groq("whisper-large-v3-turbo")),
    "groq-turbo-vocab": ("stt-groq-turbo-vocab", "Groq Turbo + vocabulary hint", lambda: Groq("whisper-large-v3-turbo", VOCAB_PROMPT)),
    "groq-large": ("stt-groq-large", "Groq Whisper Large V3", lambda: Groq("whisper-large-v3")),
    "whisper-base": ("stt-local-whisper", "Local Whisper base.en (on the Pi)", lambda: FasterWhisper("base.en")),
    "whisper-small": ("stt-local-whisper-small", "Local Whisper small.en (on the Pi)", lambda: FasterWhisper("small.en")),
    "moonshine-tiny": ("stt-local-moonshine-tiny", "Moonshine tiny (on the Pi)", lambda: Moonshine("moonshine/tiny")),
    "moonshine-base": ("stt-local-moonshine", "Moonshine base (on the Pi)", lambda: Moonshine("moonshine/base")),
}

# ------------------------------------------------------------------ scoring

def has_wake(norm: str) -> bool:
    return re.search(r"\bpip\b", norm) is not None


WAKE_RE = re.compile(r"^(?:(?:hey|so|ok|okay)\s+)?pip\b\s*|\s*\bpip$")


def strip_wake(norm: str) -> str:
    """Remove the wake phrase at the start or end: a separate on-device detector handles it."""
    return WAKE_RE.sub("", norm).strip()


def body_pair(r):
    """Reference and hypothesis with the wake-phrase region removed from both.

    If the phrase calls Pip, whatever the engine wrote where the name was (e.g. "hip hip", "hit")
    is dropped by aligning on the first/last real word of the reference."""
    ref, hyp = strip_wake(r["ref_n"]).split(), strip_wake(r["hyp_n"]).split()
    if r["wake"] != "none" and ref and hyp:
        if r["ref_n"].split()[0] in ("pip", "hey") and ref[0] in hyp[:3]:
            hyp = hyp[hyp.index(ref[0]):]
        if r["ref_n"].split()[-1] == "pip" and ref[-1] in hyp[-3:]:
            k = len(hyp) - 1 - hyp[::-1].index(ref[-1])
            hyp = hyp[:k + 1]
    return " ".join(ref), " ".join(hyp)


def score(rows):
    import jiwer
    speech = [r for r in rows]
    pairs = [body_pair(r) for r in speech]
    refs = [p[0] or "empty" for p in pairs]
    hyps = [p[1] for p in pairs]
    hyps_wake = [r["hyp_n"] for r in speech]
    out = {"wer": round(100 * jiwer.wer(refs, hyps), 1),
           "wer_with_wake": round(100 * jiwer.wer([r["ref_n"] or "empty" for r in speech], hyps_wake), 1)}
    called = [r for r in rows if r["wake"] != "none"]
    neg = [r for r in rows if r["category"] == "negative"]
    out["wake_recall"] = round(100 * sum(has_wake(r["hyp_n"]) for r in called) / len(called), 1) if called else None
    out["false_wake"] = sum(has_wake(r["hyp_n"]) for r in neg)
    out["negatives"] = len(neg)
    ents = [(e, r) for r in rows for e in r["entities"]]
    out["entity_acc"] = round(100 * sum(normalise(e) in r["hyp_n"] for e, r in ents) / len(ents), 1) if ents else None
    def truncated(r):
        rw, hw = (x.split() for x in body_pair(r))
        if not rw:
            return False
        return (not hw) or (rw[0] not in hw[:3]) or (rw[-1] not in hw[-3:])
    out["truncated_pct"] = round(100 * sum(truncated(r) for r in rows) / len(rows), 1)
    out["empty"] = sum(1 for r in rows if not r["hyp_n"])
    lat = sorted(r["seconds"] for r in rows)
    out["latency_p50"] = round(statistics.median(lat), 2)
    out["latency_p90"] = round(lat[int(0.9 * (len(lat) - 1))], 2)
    out["rtf"] = round(sum(r["seconds"] for r in rows) / sum(r["audio_s"] for r in rows), 3)
    return out


def verdict(engine_local: bool, s: dict):
    ok = s["wer"] <= 10 and (s["wake_recall"] or 0) >= 95 and s["false_wake"] == 0
    ok = ok and (s["rtf"] < 0.5 if engine_local else s["latency_p90"] <= 1.0)
    return ok


def summary(name, s, local):
    speed = f"real-time factor {s['rtf']}" if local else f"P90 {s['latency_p90']} s"
    return (f"Words wrong {s['wer']}% (not counting the name) · wake word heard {s['wake_recall']}% · false 'Pip' in {s['false_wake']}/{s['negatives']} "
            f"look-alikes · names/terms {s['entity_acc']}% · cut-off {s['truncated_pct']}% · {speed}")

# ------------------------------------------------------------------ main

def ziggletron(action: str):
    subprocess.run(["systemctl", "--user", action, "ziggletron.service"], capture_output=True, timeout=30)


def main(argv):
    limit = None
    if "--limit" in argv:
        k = argv.index("--limit"); limit = int(argv[k + 1]); argv = argv[:k] + argv[k + 2:]
    names = argv or list(ENGINES)
    phrases = {p["id"]: p for p in json.loads(PHRASES.read_text())["phrases"]}
    manifest = json.loads((REC / "manifest.json").read_text())
    ids = sorted(i for i in manifest if i in phrases)
    if limit:
        ids = ids[::max(1, len(ids) // limit)][:limit]
    print(f"{len(ids)} recordings")
    audio = {i: load(i) for i in ids}
    RESULTS.mkdir(parents=True, exist_ok=True)
    zigg_was = subprocess.run(["systemctl", "--user", "is-active", "ziggletron.service"], capture_output=True, text=True).stdout.strip()
    try:
        for n in names:
            rid, label, make = ENGINES[n]
            is_local = n.startswith(("whisper", "moonshine"))
            if is_local and zigg_was == "active":
                ziggletron("stop")  # fair CPU timing (D-44); restarted in finally
            print(f"== {label}", flush=True)
            eng = make()
            if is_local:  # warm-up
                eng(audio[ids[0]])
            rows = []
            for k, i in enumerate(ids, 1):
                p = phrases[i]
                text, secs = eng(audio[i])
                rows.append({"id": i, "category": p["category"], "session": p["session"], "wake": p["wake"],
                             "entities": p["entities"], "ref": p["text"], "hyp": text.strip(),
                             "ref_n": normalise(p["text"]), "hyp_n": normalise(text), "seconds": round(secs, 3),
                             "audio_s": round(len(audio[i]) / 16000, 2)})
                if k % 25 == 0:
                    print(f"   {k}/{len(ids)}", flush=True)
            overall = score(rows)
            by_cat = {c: score([r for r in rows if r["category"] == c]) for c in sorted({r["category"] for r in rows})}
            worst = sorted((r for r in rows if r["ref_n"] != r["hyp_n"]), key=lambda r: r["category"])
            result = {"id": rid, "engine": n, "name": label, "ran_at": time.strftime("%Y-%m-%d %H:%M"),
                      "passed": verdict(is_local, overall), "summary": summary(label, overall, is_local),
                      "details": {"overall": overall, "by_category": by_cat,
                                  "mistakes": [{"id": r["id"], "said": r["ref"], "heard": r["hyp"]} for r in worst],
                                  "clips": [{k: r[k] for k in ("id", "hyp", "seconds", "audio_s")} for r in rows]}}
            if limit:
                print("   (trial run, not saved)"); del eng; continue
            tmp = RESULTS / f"{rid}.tmp"
            tmp.write_text(json.dumps(result, indent=1)); os.replace(tmp, RESULTS / f"{rid}.json")
            print("   " + result["summary"], flush=True)
            del eng
    finally:
        if zigg_was == "active":
            ziggletron("start")


if __name__ == "__main__":
    main(sys.argv[1:])
