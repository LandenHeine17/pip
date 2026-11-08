"""To-dos kept on the Pi (the copy Pip uses even offline).

Lists: "pip" is the household's own list on the Pi. Each connected Google account adds its Google
Tasks lists ("g:<account>:<list id>"). Pip's changes to Google-backed to-dos wait as `pending`
until the sync has sent them.
"""
import sqlite3
import time

from pip_assistant.common.messages import new_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS todos_lists (
    id          TEXT PRIMARY KEY,          -- 'pip', or 'g:<account>:<google list id>'
    account_id  TEXT NOT NULL DEFAULT '',
    google_id   TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL,
    is_default  INTEGER NOT NULL DEFAULT 0, -- the account's first list ("My Tasks")
    pulled      REAL NOT NULL DEFAULT 0,    -- when Google was last asked for changes
    ignored     INTEGER NOT NULL DEFAULT 0  -- set on the account's settings page: not synced, shown or told to Pip (D-109)
);
CREATE TABLE IF NOT EXISTS todos_items (
    id          TEXT PRIMARY KEY,
    list_id     TEXT NOT NULL DEFAULT 'pip',
    title       TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT '',
    due         TEXT NOT NULL DEFAULT '',  -- YYYY-MM-DD or ''
    done        INTEGER NOT NULL DEFAULT 0,
    done_at     REAL,
    google_id   TEXT NOT NULL DEFAULT '',
    etag        TEXT NOT NULL DEFAULT '',
    pending     TEXT NOT NULL DEFAULT '',  -- '' / add / change / delete
    created     REAL NOT NULL,
    updated     REAL NOT NULL
);
"""


class TodoStore:
    def __init__(self, db: sqlite3.Connection, clock=time.time):
        self.db, self.clock = db, clock
        self.db.executescript(SCHEMA)
        if "ignored" not in {r[1] for r in self.db.execute("PRAGMA table_info(todos_lists)")}:
            self.db.execute("ALTER TABLE todos_lists ADD COLUMN ignored INTEGER NOT NULL DEFAULT 0")
        self.db.execute("INSERT OR IGNORE INTO todos_lists (id, name, is_default) VALUES ('pip', 'Household', 1)")
        self.db.commit()
        self.on_change = []
        self.version = 0

    def _changed(self) -> None:
        self.version += 1
        for fn in self.on_change:
            fn()

    # ---------------------------------------------------------------- lists
    def lists(self, ignored: bool = False) -> list[dict]:
        """The lists Pip uses (ignored ones only if asked: the account's settings page)."""
        return [dict(r) for r in self.db.execute("SELECT * FROM todos_lists ORDER BY account_id != '', name")
                if ignored or not r["ignored"]]

    def set_ignored(self, lid: str, ignored: bool) -> None:
        """Ignored: its to-dos are forgotten here and it isn't fetched; un-ignored: fetched again from scratch."""
        self.upsert_list(lid, ignored=int(ignored), pulled=0)
        if ignored:
            self.db.execute("DELETE FROM todos_items WHERE list_id = ?", (lid,))
            self.db.commit()
        self._changed()

    def get_list(self, lid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM todos_lists WHERE id = ?", (lid,)).fetchone()
        return dict(r) if r else None

    def upsert_list(self, lid: str, **fields) -> None:
        if self.get_list(lid):
            self.db.execute(f"UPDATE todos_lists SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), lid))
        else:
            cols = ["id", *fields]
            self.db.execute(f"INSERT INTO todos_lists ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", (lid, *fields.values()))
        self.db.commit()

    def remove_list(self, lid: str) -> None:
        self.db.execute("DELETE FROM todos_items WHERE list_id = ?", (lid,))
        self.db.execute("DELETE FROM todos_lists WHERE id = ?", (lid,))
        self.db.commit()
        self._changed()

    # ---------------------------------------------------------------- to-dos
    def item(self, tid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM todos_items WHERE id = ? AND pending != 'delete'", (tid,)).fetchone()
        return dict(r) if r else None

    def by_google(self, lid: str, gid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM todos_items WHERE list_id = ? AND google_id = ?", (lid, gid)).fetchone()
        return dict(r) if r else None

    def items(self, include_done_since: float | None = None) -> list[dict]:
        used = {x["id"] for x in self.lists()}
        rows = [dict(r) for r in self.db.execute("SELECT * FROM todos_items WHERE pending != 'delete' ORDER BY done, due = '', due, created")
                if r["list_id"] in used]
        return [r for r in rows if not r["done"] or (include_done_since is not None and (r["done_at"] or 0) >= include_done_since)]

    def pending(self, lids: set[str]) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM todos_items WHERE pending != '' ORDER BY updated") if r["list_id"] in lids]

    def _backed(self, lid: str) -> bool:
        lst = self.get_list(lid)
        return bool(lst and lst["account_id"])

    def add(self, title: str, list_id: str = "pip", due: str = "", notes: str = "") -> dict:
        if not self.get_list(list_id):
            raise ValueError("no such list")
        title = title.strip()[:300]
        if not title:
            raise ValueError("A to-do needs some words")
        now, tid = self.clock(), new_id()
        self.db.execute("INSERT INTO todos_items (id, list_id, title, notes, due, pending, created, updated) VALUES (?,?,?,?,?,?,?,?)",
                        (tid, list_id, title, notes.strip()[:2000], due, "add" if self._backed(list_id) else "", now, now))
        self.db.commit()
        row = self.item(tid)
        if not row:
            raise RuntimeError("to-do was not saved")
        self._changed()
        return row

    def update(self, tid: str, **fields) -> dict:
        row = self.item(tid)
        if not row:
            raise RuntimeError("to-do not found")
        new = {k: v for k, v in fields.items() if k in ("title", "notes", "due", "done")}
        if "done" in new:
            new["done"] = int(bool(new["done"]))
            new["done_at"] = self.clock() if new["done"] else None
        if self._backed(row["list_id"]) and row["pending"] != "add":
            new["pending"] = "change"
        new["updated"] = self.clock()
        self.db.execute(f"UPDATE todos_items SET {', '.join(f'{k} = ?' for k in new)} WHERE id = ?", (*new.values(), tid))
        self.db.commit()
        self._changed()
        return self.item(tid)

    def remove(self, tid: str) -> None:
        row = self.item(tid)
        if row and row["google_id"]:
            self.db.execute("UPDATE todos_items SET pending = 'delete', updated = ? WHERE id = ?", (self.clock(), tid))
        else:
            self.db.execute("DELETE FROM todos_items WHERE id = ?", (tid,))
        self.db.commit()
        self._changed()

    def forget(self, tid: str) -> None:
        self.db.execute("DELETE FROM todos_items WHERE id = ?", (tid,))
        self.db.commit()
        self.version += 1

    def save_synced(self, tid: str | None, **row) -> None:
        row["pending"], row["updated"] = "", self.clock()
        if tid and self.db.execute("SELECT 1 FROM todos_items WHERE id = ?", (tid,)).fetchone():
            self.db.execute(f"UPDATE todos_items SET {', '.join(f'{k} = ?' for k in row)} WHERE id = ?", (*row.values(), tid))
        else:
            row.setdefault("created", self.clock())
            cols = ["id", *row]
            self.db.execute(f"INSERT INTO todos_items ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                            (tid or new_id(), *row.values()))
        self.db.commit()
        self.version += 1

    def prune(self, days: float = 30) -> None:
        self.db.execute("DELETE FROM todos_items WHERE done = 1 AND pending = '' AND done_at < ?", (self.clock() - days * 86400,))
        self.db.commit()
