"""The decision trace: a 48-hour log of every message's *what*, never its words (PIP-NEW-066).

Only a message's `data` is stored; its `private` part (speech, audio, memory text) never is.
Writes are batched every couple of seconds to spare the SD card.
"""
import json
import sqlite3
import time
from pathlib import Path

from ..common.messages import Message

SCHEMA = """
CREATE TABLE IF NOT EXISTS trace (
    id     INTEGER PRIMARY KEY,
    ts     REAL NOT NULL,
    kind   TEXT NOT NULL,
    type   TEXT NOT NULL,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    data   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS trace_ts ON trace (ts);
"""
NOT_TRACED = {"heartbeat", "speech.level"}  # health is recorded separately; the mouth level is ~10 a second


class Trace:
    def __init__(self, path: Path, keep_hours: float = 48):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.keep_hours = keep_hours
        self.pending: list[tuple] = []

    @staticmethod
    def row(msg: Message) -> dict:
        """What gets logged for a message: everything except `private`."""
        return {"ts": msg.ts, "kind": msg.kind, "type": msg.type, "source": msg.source,
                "target": msg.target, "data": msg.data}

    def add(self, msg: Message) -> dict | None:
        if msg.kind in NOT_TRACED or msg.type in NOT_TRACED:
            return None
        r = self.row(msg)
        self.pending.append((r["ts"], r["kind"], r["type"], r["source"], r["target"],
                             json.dumps(r["data"], separators=(",", ":"), default=str)))
        return r

    def flush(self) -> int:
        n = len(self.pending)
        if n:
            self.db.executemany("INSERT INTO trace (ts, kind, type, source, target, data) VALUES (?,?,?,?,?,?)",
                                self.pending)
            self.db.commit()
            self.pending.clear()
        return n

    def prune(self, now: float | None = None) -> int:
        cutoff = (now or time.time()) - self.keep_hours * 3600
        cur = self.db.execute("DELETE FROM trace WHERE ts < ?", (cutoff,))
        self.db.commit()
        return cur.rowcount

    def recent(self, limit: int = 200, since: float = 0.0) -> list[dict]:
        self.flush()
        rows = self.db.execute("SELECT ts, kind, type, source, target, data FROM trace WHERE ts >= ? "
                               "ORDER BY id DESC LIMIT ?", (since, limit)).fetchall()
        return [{"ts": t, "kind": k, "type": ty, "source": s, "target": tg, "data": json.loads(d)}
                for t, k, ty, s, tg, d in rows]
