"""Conversation memory (M2, D-128): Pip remembers what was talked about, as short notes kept for 30 days.

Until now Pip only had the room transcript: the last 40 lines, at most 3 hours back, in memory only. This module
keeps more, but only as **notes**, never the words:

  1. `room_heard` (every line heard, D-121) collects the current conversation in memory, with who said it.
     Nothing is heard while listening is off, and nothing from while an alarm rings with listening off (D-127).
  2. When the room has been quiet `quiet_minutes` (or after `max_lines` lines), it asks Claude for 1–4 lines of
     notes (`ctx.summarize`, through pip-thinker): topics, plans, who took part, no quotes, nothing secret. The
     lines are then forgotten.
  3. The notes are kept in `recall_notes` (pip.db) for `keep_days` days, then deleted. Never logged: the trace
     gets only how long they were.
  4. With every message Claude gets the last day's notes (`summary`); older ones it looks up
     (`ACTION {"do": "look", "kind": "recall", "query": …}` or `"day"`).
  5. Its dashboard page (Conversations) lists them by day, each with Delete, and Delete all.
"""
import re
import time
from datetime import datetime, timedelta

from pip_assistant.core.modules import Action, Call, Invalid, Module

SCHEMA = """CREATE TABLE IF NOT EXISTS recall_notes (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    began  REAL NOT NULL,
    ended  REAL NOT NULL,
    people TEXT NOT NULL DEFAULT '',
    notes  TEXT NOT NULL
)"""
CONTEXT_HOURS = 24
CONTEXT_CHARS = 800
MIN_LINES = 3          # fewer lines than this isn't a conversation worth notes
CHECK_SECONDS = 30


def who(call: Call) -> str:
    sp = call.speaker
    if sp is None or not getattr(sp, "name", ""):
        return "Someone"
    return sp.name if sp.level == "sure" else f"{sp.name}?" if sp.level == "probably" else "Someone"


class Recall(Module):
    def __init__(self, ctx):
        self.ctx = ctx
        self.db = ctx.db()
        self.db.execute(SCHEMA)
        self.db.commit()
        self.lines: list[tuple[float, str, str]] = []   # (when, who, text): the conversation now, in memory only
        self.busy = False
        self.actions = {"look:recall": Action(self.check_look, self.do_look)}

    # ---------------------------------------------------------------- hearing
    def room_heard(self, text: str, call: Call) -> None:
        text = (text or "").strip()
        if text:
            self.lines.append((self.ctx.clock(), who(call), text))
        return None

    def due(self) -> bool:
        if len(self.lines) >= int(self.ctx.settings["max_lines"]):
            return True
        return bool(self.lines) and self.ctx.clock() - self.lines[-1][0] >= float(self.ctx.settings["quiet_minutes"]) * 60

    async def write_notes(self) -> int | None:
        """The conversation so far → notes (if it's worth any). The lines are forgotten either way."""
        lines, self.lines = self.lines, []
        if len(lines) < MIN_LINES:
            return None
        rendered = [f"[{time.strftime('%H:%M', time.localtime(t))}] {w}: {x}" for t, w, x in lines]
        notes = (await self.ctx.summarize(rendered)).strip()
        if not notes:
            return None
        people = ", ".join(sorted({w.rstrip("?") for _, w, _ in lines if w != "Someone"}))
        cur = self.db.execute("INSERT INTO recall_notes (began, ended, people, notes) VALUES (?, ?, ?, ?)",
                              (lines[0][0], lines[-1][0], people, notes[:1200]))
        self.db.commit()
        await self.ctx.publish("saved", {"lines": len(lines), "chars": len(notes)})   # numbers only
        return cur.lastrowid

    def prune(self) -> int:
        cutoff = self.ctx.clock() - float(self.ctx.settings["keep_days"]) * 86400
        cur = self.db.execute("DELETE FROM recall_notes WHERE ended < ?", (cutoff,))
        self.db.commit()
        return cur.rowcount

    def tasks(self) -> list:
        return [self._loop()]

    async def _loop(self) -> None:
        import asyncio
        last_prune = 0.0
        while True:
            await asyncio.sleep(CHECK_SECONDS)
            if self.due() and not self.busy:
                self.busy = True
                try:
                    await self.write_notes()
                except Exception as e:   # (never the words)
                    import logging
                    logging.getLogger(__name__).warning("conversation notes failed: %s", type(e).__name__)
                finally:
                    self.busy = False
            if self.ctx.clock() - last_prune > 3600:
                self.prune()
                last_prune = self.ctx.clock()

    # ---------------------------------------------------------------- reading
    def notes(self, since: float = 0.0, until: float | None = None, limit: int = 500) -> list[dict]:
        until = until if until is not None else self.ctx.clock() + 1
        rows = self.db.execute("SELECT id, began, ended, people, notes FROM recall_notes WHERE ended >= ? AND began <= ? "
                               "ORDER BY began DESC LIMIT ?", (since, until, limit)).fetchall()
        return [{"id": r[0], "start": r[1], "end": r[2], "people": r[3], "notes": r[4]} for r in rows]

    @staticmethod
    def line(n: dict) -> str:
        a, b = time.localtime(n["start"]), time.localtime(n["end"])
        when = f"{time.strftime('%a %b %-d %H:%M', a)}–{time.strftime('%H:%M', b)}"
        return f"[{when}{', ' + n['people'] if n['people'] else ''}] {n['notes']}"

    def summary(self, call: Call) -> str:
        """The last day's notes, newest first, kept short (they go with every request)."""
        out, size = [], 0
        for n in self.notes(since=self.ctx.clock() - CONTEXT_HOURS * 3600):
            s = self.line(n).replace("\n", " ")
            if size + len(s) > CONTEXT_CHARS:
                break
            out.append(s)
            size += len(s)
        return ("Earlier conversations (your notes, newest first): " + " ".join(out)) if out else ""

    def check_look(self, a: dict, call: Call) -> dict:
        query, day = str(a.get("query") or "").strip()[:100], str(a.get("day") or "").strip()
        if day:
            try:
                d = datetime.strptime(day, "%Y-%m-%d")
            except ValueError:
                raise Invalid("day must be YYYY-MM-DD")
            return {"day": d}
        if not query:
            raise Invalid("a query or a day")
        return {"query": query}

    def do_look(self, plan: dict, call: Call) -> str:
        if "day" in plan:
            d = plan["day"]
            found = self.notes(since=d.timestamp(), until=(d + timedelta(days=1)).timestamp())
            what = d.strftime("%A %B %-d")
        else:
            words = [w for w in re.findall(r"[a-z0-9']+", plan["query"].lower()) if len(w) > 2]
            scored = []
            for n in self.notes():
                hay = (n["notes"] + " " + n["people"]).lower()
                score = sum(1 for w in words if w in hay)
                if score:
                    scored.append((score, n["start"], n))
            found = [n for *_, n in sorted(scored, key=lambda x: (-x[0], -x[1]))]
            what = f"\"{plan['query']}\""
        if not found:
            return f"No notes of conversations for {what} (notes are kept {self.ctx.settings['keep_days']} days)."
        return f"Your notes of conversations for {what}: " + " ".join(self.line(n) for n in found[:3])

    async def forget_person(self, person_id: str, name: str) -> int:
        """They're removed from the household: notes of conversations they took part in are deleted."""
        if not name:
            return 0
        ids = [n["id"] for n in self.notes() if name in [p.strip() for p in n["people"].split(",")]]
        for i in ids:
            self.db.execute("DELETE FROM recall_notes WHERE id = ?", (i,))
        self.db.commit()
        return len(ids)

    # ---------------------------------------------------------------- its page
    async def dashboard(self, action: str, args: dict) -> dict:
        if action == "list":
            return {"notes": self.notes(), "keep_days": self.ctx.settings["keep_days"], "waiting": len(self.lines)}
        if action == "delete":
            self.db.execute("DELETE FROM recall_notes WHERE id = ?", (int(args.get("id") or 0),))
            self.db.commit()
            await self.ctx.publish("changed")
            return {"ok": True}
        if action == "delete_all":
            self.db.execute("DELETE FROM recall_notes")
            self.db.commit()
            self.lines = []
            await self.ctx.publish("changed")
            return {"ok": True}
        return {"error": f"unknown action {action}"}


def setup(ctx) -> Module:
    return Recall(ctx)
