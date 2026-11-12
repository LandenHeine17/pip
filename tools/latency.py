#!/usr/bin/env python3
"""How long Pip takes to answer, and how often he's talked over: read from the decision trace (pip.db).

    uv run tools/latency.py                  # the last 48 hours, as a table
    uv run tools/latency.py --hours 24 --json out.json

The trace holds only *what* happened, never the words (core/trace.py), so this is all times and labels. For
each request said to Pip it works out where the seconds went, from when they stopped talking to when his voice
started (the conversation-speed work, D-122–D-124; baseline 2026-11-12 in docs/PIP-SPEC.md §2):

    pause      the segmenter's end-of-speech pause ([listen] end_silence_seconds)
    stt        speech-to-text (and the voice check)
    hold       "are you done?" (core/turns.py) plus the chime
    claude     thinker.ask → first sentence
    voice      first sentence → his voice starts (Piper); for an ACTION or a fast command, the request (or
               the decision) → his voice, since there's no streamed sentence

Requests since D-122 carry their own `since_speech_s` in `turn.decision` (core works it out); older ones are
pieced together from the messages around them (the 0.7 s pause assumed). Read-only: it never changes pip.db.
"""
import argparse
import collections
import json
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_DB = Path.home() / "pip-data" / "pip.db"
PAUSE_S = 0.7    # [listen] end_silence_seconds, for requests from before `quiet_s` was traced


def load(db: Path, hours: float, now: float | None = None) -> list[tuple[float, str, dict]]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    since = (now or time.time()) - hours * 3600
    rows = [(ts, ty, json.loads(d)) for ts, ty, d in
            con.execute("SELECT ts, type, data FROM trace WHERE ts >= ? ORDER BY id", (since,))]
    con.close()
    return rows


def pct(xs: list[float], p: float) -> float | None:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(p * len(xs)))], 2) if xs else None


def stats(xs: list[float]) -> dict:
    return {"n": len(xs), "p50": pct(xs, .5), "p90": pct(xs, .9)}


def requests(rows: list) -> list[dict]:
    """One entry per request answered (or acted on) for someone who spoke to Pip: its path and its stages."""
    out = []
    for i, (ts, ty, d) in enumerate(rows):
        fast = ty == "turn.decision" and d.get("decision") == "fast_command"
        asked = ty == "thinker.ask" and "answer_id" in d and d.get("kind") in ("direct", "mentioned")
        if not (fast or asked):
            continue
        r = {"kind": "fast" if fast else d["kind"], "reason": d.get("reason", ""), "outcome": "other"}
        # what came before: the decision (new traces), speech-to-text and the utterance
        stt = utt = dec = None
        for j in range(i - (0 if fast else 1), max(-1, i - 400), -1):
            t2, ty2, d2 = rows[j]
            if ty2 == "turn.decision" and dec is None and d2.get("decision") in ("answered", "fast_command"):
                dec = (t2, d2)
            if ty2 == "stt.done" and stt is None:
                stt = (t2, d2)
            if ty2 == "heard.utterance" and stt is not None and utt is None and t2 <= stt[0]:
                utt = (t2, d2)
                break
        if stt:
            r["reason"] = r["reason"] or stt[1].get("reason", "")
        # what came after: the first sentence, his voice, the result
        first = voice = result = None
        aid = d.get("answer_id")
        for t2, ty2, d2 in rows[i + 1:i + 400]:
            if t2 - ts > 60:
                break
            if ty2 == "answer.sentence" and aid and d2.get("answer_id") == aid and first is None:
                first = t2
            if ty2 == "speech.started" and voice is None and (fast or first is not None or result is not None):
                voice = t2
            if ty2 == "thinker.ask" and "total_s" in d2 and result is None and not fast:
                result = d2
            if ty2 == "speech.interrupted" and voice is not None and t2 - voice < 30:
                r["talked_over"] = True
            if ty2 == "thinker.ask" and "answer_id" in d2 or ty2 == "turn.decision" and \
                    d2.get("decision") in ("answered", "fast_command") and t2 > ts + 0.01:
                break
        if result is not None:
            r["warm"] = result.get("warm")
            r["searched"] = bool(result.get("searched"))
            if result.get("passed"):
                r["outcome"] = "passed"
            elif result.get("action"):
                r["outcome"] = "action"
        if fast:
            r["outcome"] = "fast"
        elif first is not None and r["outcome"] == "other":
            r["outcome"] = "spoke"
        if utt and stt and r["kind"] != "mentioned":   # (a mention is room talk: no speech-to-text step of its own)
            pause = float(utt[1].get("quiet_s", PAUSE_S))
            r["pause"] = pause
            r["stt"] = stt[0] - utt[0]
            r["hold"] = ts - stt[0]
        if first is not None:
            r["claude"] = first - ts
        if voice is not None and r["outcome"] != "passed":    # (PASS: whatever he says next isn't this answer)
            r["voice"] = voice - (first if first is not None else ts)
            if dec and "since_speech_s" in dec[1]:
                r["total"] = dec[1]["since_speech_s"] + (voice - dec[0])
            elif utt:
                r["total"] = voice - utt[0] + r.get("pause", PAUSE_S)
        out.append(r)
    return out


def talk_overs(rows: list) -> collections.Counter:
    """What followed each time someone talked over Pip: a request (real), noise, or nothing (false)."""
    c = collections.Counter()
    for i, (ts, ty, d) in enumerate(rows):
        if ty == "speech.overlap_ignored":
            c["ignored (no voice)"] += 1
        if ty != "speech.interrupted":
            continue
        what = "nothing"
        for t2, ty2, d2 in rows[i + 1:i + 80]:
            if t2 - ts > 8:
                break
            if ty2 == "heard.noise":
                what = "noise"
                break
            if ty2 == "turn.decision" and d2.get("decision") == "resumed_after_backchannel":
                what = "resumed"
                break
            if ty2 == "stt.done":
                what = "words" if d2.get("chars") else "empty"
                break
        c[what] += 1
    return c


def report(rows: list) -> dict:
    reqs = requests(rows)
    by = collections.defaultdict(list)
    for r in reqs:
        by[(r["kind"], r["outcome"])].append(r)
    groups = {}
    for (kind, outcome), rs in sorted(by.items()):
        g = {"requests": len(rs)}
        for stage in ("pause", "stt", "hold", "claude", "voice", "total"):
            xs = [r[stage] for r in rs if stage in r]
            if xs:
                g[stage] = stats(xs)
        warm = [r["warm"] for r in rs if r.get("warm") is not None]
        if warm:
            g["warm_share"] = round(sum(warm) / len(warm), 2)
        g["talked_over"] = sum(1 for r in rs if r.get("talked_over"))
        groups[f"{kind}/{outcome}"] = g
    follow = [r for r in reqs if r["reason"] == "follow_up"]
    decisions = collections.Counter(d.get("decision") for _, ty, d in rows if ty == "turn.decision")
    return {
        "from": rows[0][0] if rows else None, "to": rows[-1][0] if rows else None,
        "spoken_direct": stats([r["total"] for r in reqs if r["kind"] == "direct" and r["outcome"] == "spoke" and "total" in r]),
        "groups": groups,
        "talk_overs": dict(talk_overs(rows)),
        "follow_ups": {"requests": len(follow), "passed": sum(1 for r in follow if r["outcome"] == "passed")},
        "decisions": dict(decisions),
        "join_ins": {"considered": sum(1 for _, ty, _ in rows if ty == "join.considered"),
                     "spoke": sum(1 for _, ty, d in rows if ty == "conversation.event" and d.get("kind") == "joined")},
    }


def show(rep: dict) -> str:
    s = rep["spoken_direct"]
    lines = [f"End of speech → Pip's voice (direct, spoken answers): p50 {s['p50']} s · p90 {s['p90']} s · n {s['n']}", "",
             f"{'kind/outcome':22s} {'n':>4s}  " + "  ".join(f"{k:>11s}" for k in ("pause", "stt", "hold", "claude", "voice", "total"))]
    for name, g in rep["groups"].items():
        cells = [f"{g[k]['p50']}/{g[k]['p90']}" if k in g else "–" for k in ("pause", "stt", "hold", "claude", "voice", "total")]
        lines.append(f"{name:22s} {g['requests']:4d}  " + "  ".join(f"{c:>11s}" for c in cells))
    lines += ["", "(each cell: p50/p90 seconds)", "",
              f"Talked over: {rep['talk_overs']}",
              f"Follow-ups: {rep['follow_ups']['requests']} requests, {rep['follow_ups']['passed']} passed by Claude",
              f"Joining in: {rep['join_ins']['considered']} considered, {rep['join_ins']['spoke']} spoke",
              f"Decisions: {rep['decisions']}"]
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--hours", type=float, default=48)
    p.add_argument("--json", type=Path, help="also write the numbers here")
    a = p.parse_args(argv)
    rep = report(load(a.db, a.hours))
    print(show(rep))
    if a.json:
        a.json.write_text(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
