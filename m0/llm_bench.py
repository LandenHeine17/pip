"""M0 local fallback model test: how fast can a small on-device model answer short requests?

    ~/pip-data/m0/llm/.venv/bin/python ~/pip/m0/llm_bench.py
"""
import json
import os
import statistics
import time
from pathlib import Path

from llama_cpp import Llama

DATA = Path.home() / "pip-data/m0"
MODELS = DATA / "models/llm"
TASK = DATA / "tasks/llm-bench.json"
SYSTEM = ("You are Pip, a playful household voice assistant running offline on a Raspberry Pi. "
          "Answer in one or two short spoken sentences. No lists, no markdown.")
PROMPTS = [
    "What's twelve times thirteen?",
    "Tell me a short joke.",
    "How many days are in a leap year?",
    "Can you set a timer for ten minutes?",
    "What should I make for dinner with eggs and rice?",
    "Is it okay to leave rice out overnight?",
    "How do you spell necessary?",
    "Why is the sky blue?",
]
CANDIDATES = [("qwen2.5-1.5b", "Qwen 2.5 1.5B (Q4)", "qwen2.5-1.5b-instruct-q4_k_m.gguf"),
              ("llama3.2-1b", "Llama 3.2 1B (Q4)", "Llama-3.2-1B-Instruct-Q4_K_M.gguf")]


def progress(stage, done, total, finished=False):
    TASK.parent.mkdir(parents=True, exist_ok=True)
    tmp = TASK.with_suffix(".tmp")
    tmp.write_text(json.dumps({"name": "Local fallback model speed", "stage": stage, "done": done, "total": total,
                               "note": "", "finished": finished, "failed": False, "updated": time.time()}))
    os.replace(tmp, TASK)


def main():
    rows, step = [], 0
    for mid, label, fname in CANDIDATES:
        llm = Llama(model_path=str(MODELS / fname), n_ctx=1024, n_threads=4, verbose=False)
        llm.create_chat_completion([{"role": "user", "content": "Hi"}], max_tokens=4)  # warm-up
        firsts, totals, tps, answers = [], [], [], []
        for p in PROMPTS:
            t0 = time.perf_counter(); first = None; text = ""; n = 0
            for chunk in llm.create_chat_completion([{"role": "system", "content": SYSTEM}, {"role": "user", "content": p}],
                                                    max_tokens=60, temperature=0.7, stream=True):
                d = chunk["choices"][0]["delta"].get("content")
                if d:
                    if first is None:
                        first = time.perf_counter() - t0
                    text += d; n += 1
            total = time.perf_counter() - t0
            firsts.append(first or total); totals.append(total)
            tps.append(n / max(1e-3, total - (first or 0)))
            answers.append({"q": p, "a": text.strip(), "first_s": round(first or total, 2), "total_s": round(total, 2)})
            step += 1; progress(label, step, len(CANDIDATES) * len(PROMPTS))
        rows.append({"model": mid, "label": label, "first_word_p50": round(statistics.median(firsts), 2),
                     "reply_p50": round(statistics.median(totals), 2), "reply_max": round(max(totals), 2),
                     "tokens_per_s": round(statistics.mean(tps), 1), "answers": answers})
        print({k: v for k, v in rows[-1].items() if k != "answers"}, flush=True)
        del llm
    best = min(rows, key=lambda r: r["reply_p50"])
    result = {"id": "local-llm", "ran_at": time.strftime("%Y-%m-%d %H:%M"), "passed": best["reply_p50"] <= 10,
              "summary": " · ".join(f"{r['label']}: first word {r['first_word_p50']} s, full reply {r['reply_p50']} s "
                                    f"(slowest {r['reply_max']} s), {r['tokens_per_s']} words-ish/s" for r in rows)
                         + ". See details for its actual answers.",
              "details": {"models": rows}}
    out = DATA / "results/local-llm.json"
    tmp = out.with_suffix(".tmp"); tmp.write_text(json.dumps(result, indent=1)); os.replace(tmp, out)
    progress("Finished", 1, 1, finished=True)


if __name__ == "__main__":
    main()
