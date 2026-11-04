"""M0 voice test: Kokoro vs Piper on the Pi. Synthesises only (no playback), measures speed,
and saves clips to ~/pip-data/m0/tts for the blind listening test on the dashboard (/listen).

    uv run tts_bench.py
"""
import json
import os
import re
import statistics
import time
import wave
from pathlib import Path

import numpy as np

DATA = Path.home() / "pip-data/m0"
MODELS = DATA / "models/tts"
OUT = DATA / "tts"
TASK = DATA / "tasks/tts-bench.json"

REPLIES = [
    "Ten minutes, starting now. I'll yell when the pasta's ready.",
    "It's going to rain tomorrow afternoon, around fifty degrees. Maybe grab a jacket.",
    "Done! Milk and eggs are on the shopping list.",
    "Twelve times thirteen is one hundred fifty six. Show-off question, but I'll allow it.",
    "Good morning! You've got the dentist at nine, so no hitting snooze today.",
    "The capital of Australia is Canberra. Not Sydney, which surprises a lot of people.",
    "Sorry, I didn't catch that. Could you say it again a little closer?",
    "Your pasta timer is done. Go save it before it turns into glue.",
    "Okay, I'll stay quiet for an hour. Say hey Pip if you need me.",
    "I think that was the doorbell. Want me to keep an eye on the time?",
    "Bins go out on Tuesday night. I'll remind you, because you'll forget.",
    "The Raspberry Pi is running at fifty eight degrees, which is perfectly comfy.",
    "Changed it to fifteen minutes. Same timer, more patience.",
    "Here's a fun one: octopuses have three hearts. Overachievers.",
    "Night night. Your first alarm is at seven thirty.",
    "I'm offline right now, so I can only do the basics. Timers still work though!",
    "You asked me that yesterday too. Still Canberra.",
    "Turning off the lights and leaving the lamp on. Cozy mode activated.",
    "Hmm, I'm not sure about that one. Want me to look it up?",
    "Reminder: call the bank. You said it was important, so I'm being annoying about it.",
]
VOICES = [
    ("piper-lessac", "Piper · lessac (Ziggletron's voice)", "piper", "en_US-lessac-medium.onnx"),
    ("piper-ryan", "Piper · ryan (male)", "piper", "en_US-ryan-medium.onnx"),
    ("kokoro-michael", "Kokoro · michael (male)", "kokoro", "am_michael"),
    ("kokoro-adam", "Kokoro · adam (male)", "kokoro", "am_adam"),
    ("kokoro-puck", "Kokoro · puck (male, playful)", "kokoro", "am_puck"),
]


def progress(stage, done, total):
    TASK.parent.mkdir(parents=True, exist_ok=True)
    tmp = TASK.with_suffix(".tmp")
    tmp.write_text(json.dumps({"name": "Voice test: Kokoro vs Piper", "stage": stage, "done": done, "total": total,
                               "note": "silent: makes files only", "finished": stage == "Finished", "failed": False,
                               "updated": time.time()}))
    os.replace(tmp, TASK)


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s]


def save(path, audio_i16, rate):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(audio_i16.tobytes())


def make_engine(kind, arg):
    if kind == "piper":
        from piper import PiperVoice
        v = PiperVoice.load(str(MODELS / arg))
        def synth(text):
            chunks = list(v.synthesize(text))
            return np.concatenate([np.frombuffer(c.audio_int16_bytes, np.int16) for c in chunks]), chunks[0].sample_rate
        return synth
    from kokoro_onnx import Kokoro
    k = Kokoro(str(MODELS / "kokoro-v1.0.int8.onnx"), str(MODELS / "voices-v1.0.bin"))
    def synth(text):
        samples, rate = k.create(text, voice=arg, speed=1.05, lang="en-us")
        return (np.clip(samples, -1, 1) * 32767).astype(np.int16), rate
    return synth


def main():
    results, step, total = [], 0, len(VOICES) * len(REPLIES)
    for vid, label, kind, arg in VOICES:
        synth = make_engine(kind, arg)
        synth("Warming up.")
        first, rtf = [], []
        for n, text in enumerate(REPLIES):
            parts = sentences(text)
            t0 = time.perf_counter(); a0, rate = synth(parts[0]); first.append(time.perf_counter() - t0)
            t1 = time.perf_counter(); audio, rate = synth(text); dt = time.perf_counter() - t1
            rtf.append(dt / (len(audio) / rate))
            save(OUT / vid / f"{n:02d}.wav", audio, rate)
            step += 1
            progress(f"{label}", step, total)
        fl = sorted(first)
        results.append({"voice": vid, "label": label, "first_audio_p50": round(statistics.median(fl), 3),
                        "first_audio_p90": round(fl[int(0.9 * (len(fl) - 1))], 3), "rtf": round(statistics.mean(rtf), 3)})
        print(results[-1], flush=True)
    ok = [r for r in results if r["first_audio_p90"] <= 0.4]
    result = {"id": "tts", "ran_at": time.strftime("%Y-%m-%d %H:%M"), "passed": bool(ok) or None,
              "summary": " · ".join(f"{r['label']}: starts in {r['first_audio_p90']} s (P90), {r['rtf']}× real time" for r in results)
                         + ". Naturalness: do the blind listening test (dashboard → Listen).",
              "details": {"voices": results, "replies": REPLIES}}
    out = DATA / "results/tts.json"
    tmp = out.with_suffix(".tmp"); tmp.write_text(json.dumps(result, indent=1)); os.replace(tmp, out)
    progress("Finished", total, total)
    print(result["summary"])


if __name__ == "__main__":
    main()
