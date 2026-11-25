"""The record of every self-change: table `builder_changes` in ~/pip-data/pip.db (D-152; M1b's D-118 before it).

One row per change. `summary` is Pip's one-line paraphrase (no names), shown on the dashboard's Changes page and
said aloud when the change goes live. `request` is Pip's full write-up for Claude, which may quote what was said:
it's **private**. It only goes into the Claude Hub chat (its first message); it's never shown on Pip's
dashboard, logged, committed or put in a status message, and it's blanked after `[builder] keep_days` (90).

The columns from D-118's sandboxed pipeline (questions, plan, diff, …) stay for the old changes' history.
"""
import json
import sqlite3
import time
from pathlib import Path
from ..common.db import connect

# Where a change can be (docs/HOW-PIP-WORKS.md §6). Claude Hub does the planning and building; Pip only follows.
STATUSES = ("asked", "planning", "building", "ready", "live", "dropped", "undoing", "undone", "failed")
ACTIVE = {"asked", "planning", "building", "ready"}           # a Claude Hub chat is (or will be) working on it
FINAL = {"dropped", "undone", "failed",                        # over (live can still be undone)
         # D-118's statuses, kept for the old changes' history:
         "rolled_back", "rejected", "stopped"}
OLD_OPEN = ("questions", "plan_ready", "checking", "deploying")  # D-118 statuses with no meaning any more
ANNOUNCE = {"live", "dropped"}                                 # said to the room once (D-152)
LOG_MAX = 400                                                  # steps kept per change (oldest dropped)

SCHEMA = """CREATE TABLE IF NOT EXISTS builder_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL,
    asked_by TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    request TEXT NOT NULL DEFAULT '',
    questions TEXT NOT NULL DEFAULT '[]',
    answers TEXT NOT NULL DEFAULT '[]',
    plan TEXT NOT NULL DEFAULT '{}',
    summary TEXT NOT NULL DEFAULT '',
    risk TEXT NOT NULL DEFAULT '',
    files TEXT NOT NULL DEFAULT '[]',
    diff TEXT NOT NULL DEFAULT '',
    test_report TEXT NOT NULL DEFAULT '',
    base TEXT NOT NULL DEFAULT '',
    commit_sha TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL DEFAULT '',
    release TEXT NOT NULL DEFAULT '',
    undo_commit TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    cost_usd REAL NOT NULL DEFAULT 0,
    log TEXT NOT NULL DEFAULT '[]',
    created REAL NOT NULL,
    updated REAL NOT NULL)"""
# Added for Claude Hub (D-152). ALTERed in on first start; the rows already there are marked announced.
NEW_COLUMNS = {"hub_name": "TEXT NOT NULL DEFAULT ''",        # the inbox file's name (pip-change-<id>)
               "hub_session": "TEXT NOT NULL DEFAULT ''",     # the Claude Hub chat's id (for the link)
               "hub_state": "TEXT NOT NULL DEFAULT ''",       # queued, working, waiting, idle or failed
               "commits": "TEXT NOT NULL DEFAULT '[]'",       # its commits (Pip-Change: <id>), oldest first
               "announced": "TEXT NOT NULL DEFAULT ''"}       # the status Pip last told the room about
JSON_FIELDS = {"questions", "answers", "plan", "files", "log", "commits"}
COLUMNS = {"status", "asked_by", "source", "request", "questions", "answers", "plan", "summary", "risk", "files",
           "diff", "test_report", "base", "commit", "branch", "release", "undo_commit", "error", "cost_usd",
           "hub_name", "hub_session", "hub_state", "commits", "announced"}


class Store:
    def __init__(self, path: Path, clock=time.time):
        self.clock = clock
        self.db = connect(path)
        self.db.execute(SCHEMA)
        have = {r[1] for r in self.db.execute("PRAGMA table_info(builder_changes)")}
        if not set(NEW_COLUMNS) <= have:
            for col, decl in NEW_COLUMNS.items():
                if col not in have:
                    self.db.execute(f"ALTER TABLE builder_changes ADD COLUMN {col} {decl}")
            # D-118's changes: nothing to announce, and the ones still open in the old pipeline end here
            self.db.execute("UPDATE builder_changes SET announced = status")
            q = ",".join("?" * len(OLD_OPEN))
            self.db.execute(f"UPDATE builder_changes SET status = 'stopped', error = 'Self-changes moved to Claude "
                            f"Hub (D-152): ask again if you still want it.' WHERE status IN ({q}) OR "
                            "(status IN ('asked', 'planning', 'building', 'ready') AND hub_name = '')", OLD_OPEN)
        self.db.commit()

    @staticmethod
    def _out(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        for k in JSON_FIELDS:
            d[k] = json.loads(d.get(k) or ("{}" if k == "plan" else "[]"))
        d["id"] = str(d["id"])
        d["commit"] = d.pop("commit_sha")
        return d

    def add(self, request: str, summary: str, asked_by: str, source: str) -> str:
        now = self.clock()
        cur = self.db.execute("INSERT INTO builder_changes (status, asked_by, source, request, summary, created, "
                              "updated) VALUES ('asked', ?, ?, ?, ?, ?, ?)", (asked_by, source, request, summary, now, now))
        self.db.commit()
        return str(cur.lastrowid)

    def get(self, cid) -> dict | None:
        try:
            n = int(cid)
        except (TypeError, ValueError):
            return None
        return self._out(self.db.execute("SELECT * FROM builder_changes WHERE id = ?", (n,)).fetchone())

    def all(self, limit: int = 100) -> list[dict]:
        rows = self.db.execute("SELECT * FROM builder_changes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._out(r) for r in rows]

    def with_status(self, *statuses: str) -> list[dict]:
        """Changes in these statuses, oldest first."""
        q = ",".join("?" * len(statuses))
        rows = self.db.execute(f"SELECT * FROM builder_changes WHERE status IN ({q}) ORDER BY id", statuses)
        return [self._out(r) for r in rows.fetchall()]

    def update(self, cid, **fields) -> None:
        bad = set(fields) - COLUMNS
        if bad:
            raise KeyError(f"unknown change fields: {sorted(bad)}")
        sets, vals = [], []
        for k, v in fields.items():
            sets.append(f"{'commit_sha' if k == 'commit' else k} = ?")
            vals.append(json.dumps(v) if k in JSON_FIELDS else v)
        sets.append("updated = ?")
        vals += [self.clock(), int(cid)]
        self.db.execute(f"UPDATE builder_changes SET {', '.join(sets)} WHERE id = ?", vals)
        self.db.commit()

    def log(self, cid, kind: str, text: str) -> None:
        """Add a step to the change's log (shown live on the Changes page)."""
        row = self.db.execute("SELECT log FROM builder_changes WHERE id = ?", (int(cid),)).fetchone()
        if row is None:
            return
        steps = json.loads(row[0] or "[]")
        steps.append({"at": round(self.clock(), 1), "kind": kind, "text": text[:2000]})
        self.db.execute("UPDATE builder_changes SET log = ?, updated = ? WHERE id = ?",
                        (json.dumps(steps[-LOG_MAX:]), self.clock(), int(cid)))
        self.db.commit()

    def forget_old(self, keep_days: float) -> int:
        """Blank the words of requests (and answers) older than keep_days. Returns how many were blanked."""
        cutoff = self.clock() - keep_days * 86400
        cur = self.db.execute("UPDATE builder_changes SET request = '', answers = '[]' "
                              "WHERE created < ? AND (request != '' OR answers != '[]')", (cutoff,))
        self.db.commit()
        return cur.rowcount
