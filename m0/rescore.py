"""Re-score saved STT results with the current scoring rules (no re-transcription)."""
import json, os, sys
from pathlib import Path
import benchmark as b

phrases = {p["id"]: p for p in json.loads(b.PHRASES.read_text())["phrases"]}
for f in sorted(b.RESULTS.glob("stt-*.json")):
    r = json.loads(f.read_text()); d = r["details"]
    if "clips" in d:
        hyp = {c["id"]: c for c in d["clips"]}
    else:  # older format: mistakes list + everything else heard exactly
        wrong = {m["id"]: m["heard"] for m in d["mistakes"]}
        hyp = {i: {"hyp": wrong.get(i, phrases[i]["text"]), "seconds": None, "audio_s": None}
               for i in phrases}
    rows = []
    for i, p in phrases.items():
        if i not in hyp:
            continue
        h = hyp[i]["hyp"]
        rows.append({"id": i, "category": p["category"], "wake": p["wake"], "entities": p["entities"], "ref": p["text"],
                     "hyp": h, "ref_n": b.normalise(p["text"]), "hyp_n": b.normalise(h),
                     "seconds": hyp[i].get("seconds") or 0.0, "audio_s": hyp[i].get("audio_s") or 1.0})
    timing = {k: d["overall"][k] for k in ("latency_p50", "latency_p90", "rtf")}
    def sc(rs):
        o = b.score(rs)
        if "clips" not in d:
            o.update(timing)
        return o
    overall = sc(rows)
    d["overall"] = overall
    d["by_category"] = {c: sc([x for x in rows if x["category"] == c]) for c in sorted({x["category"] for x in rows})}
    local = r["engine"].startswith(("whisper", "moonshine"))
    r["passed"] = b.verdict(local, overall)
    r["summary"] = b.summary(r["name"], overall, local)
    tmp = f.with_suffix(".tmp"); tmp.write_text(json.dumps(r, indent=1)); os.replace(tmp, f)
    print(f"{r['name']}: {r['summary']}")
