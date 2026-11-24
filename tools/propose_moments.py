#!/usr/bin/env python3
"""Which of Pip's memories are really funny moments? (D-147: those belong in the Hall of Fame, not in what Pip
carries into every answer.)

    uv run tools/propose_moments.py            # ask Claude once, and flag its picks for the owner to review
    uv run tools/propose_moments.py --dry-run  # only count them
    uv run tools/propose_moments.py --clear    # take the flags off again

Claude reads the active, household-visible memories once (one request, no tools) and names the ones that record
something funny that happened rather than a fact Pip needs. Each is **only flagged** ("Move? …" in its `review`
field): on the dashboard's Memory card, tick "to review" and choose "Move to Hall of Fame" or "Keep as a memory"
for each. Nothing is moved or deleted here.

Prints numbers only, never a memory's text. The memories go to Claude (as they do in every conversation) and
nowhere else.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pip_assistant.common import paths, settings  # noqa: E402
from pip_assistant.core.memory import Memory  # noqa: E402

FLAG = "Move? This looks like a funny moment more than something to remember."   # the Memory card shows "Move to …" for "Move?"
SYSTEM = """You sort the saved memories of a household voice assistant called Pip. A memory should be something \
lasting that Pip needs when he answers: a rule for how he behaves, a fact about the home or a person, a \
preference. Some entries are instead a record of a funny moment: a joke someone made, a roast, a running gag's \
origin, an anecdote about something that happened once. Those belong in the Hall of Fame instead.

Reply with only the ids of the entries that are funny moments, one per line, like: #m1a2b3
Nothing else: no explanations. If you're unsure about one, leave it out (it stays a memory). Rules for how Pip \
should behave are never moments, even when they're funny. If none are moments, reply with just NONE."""
REF = re.compile(r"#m[0-9a-z]{6}")


def candidates(memory: Memory) -> list[dict]:
    """The memories Claude is shown: active, visible to the household, not already flagged for something."""
    return [r for r in memory.items("active") if r["visibility"] == "household" and not r["review"]]


def picked(reply: str, rows: list[dict], memory: Memory) -> list[dict]:
    """The memories Claude named, among those it was shown (anything else in its reply is ignored)."""
    shown = {memory.short(r["id"]): r for r in rows}
    out = []
    for ref in REF.findall(reply or ""):
        if ref in shown and shown[ref] not in out:
            out.append(shown[ref])
    return out


def ask_claude(system: str, user: str, model: str, effort: str, timeout: float = 180) -> str:
    exe = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    args = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--system-prompt", system, "--tools", "", *(["--model", model] if model else []),
            *(["--effort", effort] if effort else []), "--no-session-persistence", "--setting-sources", "",
            "--strict-mcp-config", "--disable-slash-commands"]
    msg = json.dumps({"type": "user", "message": {"role": "user", "content": user}}) + "\n"
    with tempfile.TemporaryDirectory(prefix="pip-propose-") as wd:   # a neutral folder: no project files loaded
        p = subprocess.run(args, input=msg.encode(), capture_output=True, cwd=wd, timeout=timeout)
    for line in p.stdout.decode(errors="replace").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "result":
            if d.get("is_error"):
                raise RuntimeError("Claude reported an error")
            return str(d.get("result") or "")
    raise RuntimeError("Claude gave no answer")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="count Claude's picks, flag nothing")
    ap.add_argument("--clear", action="store_true", help="take this tool's flags off again")
    a = ap.parse_args()
    memory = Memory(paths.database())
    if a.clear:
        flagged = [r for r in memory.items() if r["review"] == FLAG]
        for r in flagged:
            memory.update(r["id"], review="")
        print(f"{len(flagged)} flags taken off.")
        return 0
    rows = candidates(memory)
    if not rows:
        print("No memories to look at.")
        return 0
    cfg = settings.load()["thinker"]
    user = "The memories:\n" + "\n".join(f"{memory.short(r['id'])} [{r['category']}] {r['text']}" for r in rows)
    picks = picked(ask_claude(SYSTEM, user, cfg.get("model", ""), cfg.get("effort", "")), rows, memory)
    kinds: dict[str, int] = {}
    for r in picks:
        kinds[r["category"]] = kinds.get(r["category"], 0) + 1
        if not a.dry_run:
            memory.update(r["id"], review=FLAG)
    by_kind = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "none"
    print(f"{len(rows)} memories read; {len(picks)} look like funny moments ({by_kind}).")
    print("Nothing was flagged (--dry-run)." if a.dry_run else
          "They're flagged: on Pip's dashboard, Memory card, tick \"to review\" and choose Move or Keep for each.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
