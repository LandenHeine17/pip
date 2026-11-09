"""The record of every self-change: table `builder_changes` in ~/pip-data/pip.db (M1b, PIP-NEW-069–076).

One row per change. `request` holds what was asked, in the asker's words: it's **private**. It's shown on the
dashboard's Changes page, and never logged, committed or put in a status message; it's blanked after
`[builder] keep_days` (default 90), along with the answers to Pip's questions.
"""
import json
import sqlite3
import time
from pathlib import Path

# The order a change goes through (docs/HOW-PIP-WORKS.md "How Pip changes himself").
STATUSES = ("asked", "planning", "questions", "plan_ready", "building", "checking", "ready", "deploying",
            "live", "rolled_back", "failed", "rejected", "stopped", "undone")
RUNNING = {"planning", "building", "checking"}              # a job is working on it right now
WAITING = {"questions", "plan_ready", "ready"}              # waiting for the owner on the dashboard
FINAL = {"live", "rolled_back", "failed", "rejected", "stopped", "undone"}
LOG_MAX = 400                                               # steps kept per change (oldest dropped)

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
JSON_FIELDS = {"questions", "answers", "plan", "files", "log"}
COLUMNS = {"status", "asked_by", "source", "request", "questions", "answers", "plan", "summary", "risk", "files",
           "diff", "test_report", "base", "commit", "branch", "release", "undo_commit", "error", "cost_usd"}


class Store:
    def __init__(self, path: Path, clock=time.time):
        self.clock = clock
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute(SCHEMA)
        self.db.commit()

    @staticmethod
    def _out(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        for k in JSON_FIELDS:
            d[k] = json.loads(d[k] or ("{}" if k == "plan" else "[]"))
        d["id"] = str(d["id"])
        d["commit"] = d.pop("commit_sha")
        return d

    def add(self, request: str, asked_by: str, source: str) -> str:
        now = self.clock()
        cur = self.db.execute("INSERT INTO builder_changes (status, asked_by, source, request, created, updated) "
                              "VALUES ('asked', ?, ?, ?, ?, ?)", (asked_by, source, request, now, now))
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

    def add_cost(self, cid, usd) -> None:
        try:
            usd = max(0.0, float(usd or 0))
        except (TypeError, ValueError):
            return
        self.db.execute("UPDATE builder_changes SET cost_usd = cost_usd + ? WHERE id = ?", (usd, int(cid)))
        self.db.commit()

    def forget_old(self, keep_days: float) -> int:
        """Blank the words of requests (and answers) older than keep_days. Returns how many were blanked."""
        cutoff = self.clock() - keep_days * 86400
        cur = self.db.execute("UPDATE builder_changes SET request = '', answers = '[]' "
                              "WHERE created < ? AND (request != '' OR answers != '[]')", (cutoff,))
        self.db.commit()
        return cur.rowcount
