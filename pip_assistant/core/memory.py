"""What Pip remembers, and the memory inbox (M1a step 5; PIP-NEW-028–032, D-15, D-16, D-54).

Claude asks for changes with memory ACTIONs (core/actions.py); the dashboard's Memory Center
changes them through core/main.py. Claude sees the memories it may use with each request (for_claude).

    "Pip, remember the bins go out Tuesday night"   → saved now, confirmed with the exact wording
    "Pip, remember Sam is allergic to peanuts"      → about someone else, and health: waits in the
                                                       memory inbox on the dashboard for approval
    "Pip, forget that I like mushrooms"             → that exact memory is deleted, and Pip says which

Rules (from the memory policy, docs/archive/.../06-MEMORY-AND-DATA-POLICY.md):
- Only things someone **asked** Pip to remember are saved. Nothing overheard is ever saved.
- **Sensitive** kinds (health, money, relationships, religion or politics, someone's whereabouts or
  schedule, anything about a child) and things **about another person** wait for approval.
- Visible to the **household** by default (D-16); a memory can be marked private, and then it's only
  used when its owner is recognised "sure".
- **No silent loss**: no limit on how many; nothing is deleted unless someone deletes it. Archived
  memories are kept but not used. Pending items expire after 14 days if nobody approves them.
- Passwords, card numbers and phone numbers are refused: they're not memories (secrets file instead).

Memory texts are what people said, so they live only here and in messages' `private` part (D-34).
"""
import difflib
import json
import re
import sqlite3
import time
from pathlib import Path

from ..common.messages import new_id

CATEGORIES = {"preference": "Preferences", "household": "Household facts", "rule": "Rules for Pip",
              "personal": "About people", "device": "Devices and places"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id         TEXT PRIMARY KEY,
    text       TEXT NOT NULL,
    category   TEXT NOT NULL,
    about      TEXT NOT NULL DEFAULT '[]',   -- JSON list of person ids it's about
    owner      TEXT NOT NULL DEFAULT '',     -- person id (private memories) or '' (household)
    visibility TEXT NOT NULL DEFAULT 'household',   -- household / private (D-16)
    status     TEXT NOT NULL,                -- active / pending / archived
    confirmed  INTEGER NOT NULL DEFAULT 1,   -- 1 = someone said it; 0 = Pip inferred it
    sensitive  INTEGER NOT NULL DEFAULT 0,
    source     TEXT NOT NULL DEFAULT '',     -- who asked and how ("Landen, by voice"); never a transcript
    why_pending TEXT NOT NULL DEFAULT '',
    created    REAL NOT NULL,
    updated    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS memories_status ON memories (status);
"""

# Not memories: these belong in the secrets file (memory policy §2)
CREDENTIAL = re.compile(
    r"\b(password|passcode|passwd|pin code|pin is|pin number|api key|secret key|token is|login is|security code|cvv|"
    r"(door|gate|lock|alarm|garage|safe|wi-?fi|access|entry|key ?pad) code|code is|combination)\b|"
    r"\b(?:\d[ -]?){13,19}\b|"                       # card numbers
    r"(?:\+?\d[\s().-]?){10,}",                      # phone numbers
    re.I)


class Refused(ValueError):
    pass


class Memory:
    def __init__(self, path: Path, clock=time.time):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.clock = clock

    @staticmethod
    def short(mid: str) -> str:
        return "#m" + mid[:6]

    def _row(self, mid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM memories WHERE id = ?", (mid,)).fetchone()
        return self._out(r) if r else None

    @staticmethod
    def _out(r) -> dict:
        d = dict(r)
        d["about"] = json.loads(d["about"] or "[]")
        return d

    def find(self, ref: str) -> dict | None:
        ref = ref.strip().lstrip("#").lower()
        ref = ref[1:] if ref.startswith("m") and len(ref) > 4 else ref
        if len(ref) < 4:
            return None
        hits = [self._out(r) for r in self.db.execute("SELECT * FROM memories WHERE id LIKE ?", (ref + "%",))]
        return hits[0] if len(hits) == 1 else None

    def items(self, status: str | None = None, q: str = "") -> list[dict]:
        rows = [self._out(r) for r in self.db.execute(
            "SELECT * FROM memories" + (" WHERE status = ?" if status else "") + " ORDER BY updated DESC",
            (status,) if status else ())]
        if q:
            q = q.lower()
            rows = [r for r in rows if q in r["text"].lower() or q in r["category"]]
        return rows

    def duplicate_of(self, text: str) -> dict | None:
        t = text.lower().strip(" .")
        for r in self.items():
            if r["status"] == "archived":
                continue
            if difflib.SequenceMatcher(None, t, r["text"].lower().strip(" .")).ratio() >= 0.9:
                return r
        return None

    # ---------------------------------------------------------------- changing memories
    def add(self, text: str, category: str = "household", about: list[str] | None = None, owner: str = "",
            private: bool = False, sensitive: bool = False, pending_because: str = "", source: str = "",
            confirmed: bool = True, own: bool = False) -> tuple[dict, str]:
        """Save one memory. Returns (row, outcome) with outcome 'saved', 'pending', 'duplicate' (already
        remembered) or 'waiting' (already in the inbox). own: the person it's about said it themselves (Pip
        sure it's them), so even a sensitive fact is saved straight away (D-88).
        Raises Refused for credentials and empty text."""
        text = re.sub(r"\s+", " ", (text or "")).strip()[:300]
        if len(text) < 3:
            raise Refused("nothing to remember")
        if CREDENTIAL.search(text):
            raise Refused("credential")
        if (dup := self.duplicate_of(text)) is not None:
            return dup, "waiting" if dup["status"] == "pending" else "duplicate"
        category = category if category in CATEGORIES else "household"
        if sensitive and not pending_because and not own:
            pending_because = "sensitive"
        status = "pending" if pending_because else "active"
        now = self.clock()
        mid = new_id()
        self.db.execute("INSERT INTO memories (id, text, category, about, owner, visibility, status, confirmed, sensitive, "
                        "source, why_pending, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (mid, text, category, json.dumps(about or []), owner,
                         "private" if private and owner else "household", status, int(confirmed), int(sensitive),
                         source[:80], pending_because, now, now))
        self.db.commit()
        row = self._row(mid)
        if not row:
            raise RuntimeError("memory was not saved")
        return row, "pending" if status == "pending" else "saved"

    def is_about(self, row: dict, person_id: str, name: str = "") -> bool:
        """Is this memory about that person? (Listed in "about", or, for older wordings like "Landen says
        Sam is…" that didn't list anyone, their name is in it.)"""
        return person_id in row["about"] or bool(name and re.search(rf"\b{re.escape(name)}\b", row["text"], re.I))

    @staticmethod
    def may_see(row: dict, person_id: str | None) -> bool:
        """A private memory is only for its owner, even while it waits in the inbox (D-88): Sam is never shown,
        and can't approve, Alex's private note about Sam's surprise party."""
        return row["visibility"] != "private" or bool(person_id and row["owner"] == person_id)

    def confirm_pending(self, text: str, person_id: str, name: str = "") -> dict | None:
        """The person a waiting memory is about says the same thing: approve that one (with their wording)."""
        t = text.lower().strip(" .")
        for r in self.items("pending"):
            if not self.is_about(r, person_id, name) or not self.may_see(r, person_id):
                continue
            old = r["text"].lower().strip(" .")
            if t in old or old in t or difflib.SequenceMatcher(None, t, old).ratio() >= 0.75:
                self.update(r["id"], text=text)
                return self.approve(r["id"])
        return None

    def update(self, mid: str, **fields) -> dict:
        allowed = {k: v for k, v in fields.items() if k in ("text", "category", "visibility", "status", "about", "owner")}
        if "text" in allowed:
            allowed["text"] = re.sub(r"\s+", " ", allowed["text"]).strip()[:300]
            if CREDENTIAL.search(allowed["text"]):
                raise Refused("credential")
            if len(allowed["text"]) < 3:
                raise Refused("nothing to remember")
        if "about" in allowed:
            allowed["about"] = json.dumps(allowed["about"])
        if allowed.get("visibility") not in (None, "household", "private"):
            raise ValueError("visibility")
        if allowed.get("status") not in (None, "active", "pending", "archived"):
            raise ValueError("status")
        allowed["updated"] = self.clock()
        self.db.execute(f"UPDATE memories SET {', '.join(f'{k} = ?' for k in allowed)} WHERE id = ?", (*allowed.values(), mid))
        self.db.commit()
        row = self._row(mid)
        if not row:
            raise ValueError("No such memory")
        return row

    def approve(self, mid: str) -> dict:
        self.db.execute("UPDATE memories SET why_pending = '' WHERE id = ?", (mid,))
        return self.update(mid, status="active")

    def delete(self, mid: str) -> None:
        self.db.execute("DELETE FROM memories WHERE id = ?", (mid,))
        self.db.commit()

    def forget_person(self, pid: str) -> int:
        """Memories about or owned by someone who's removed (PIP-NEW-033, basic form)."""
        rows = [r for r in self.items() if pid in r["about"] or r["owner"] == pid]
        for r in rows:
            self.delete(r["id"])
        return len(rows)

    def prune(self, pending_days: float = 14) -> None:
        """Unapproved suggestions expire; nothing else is ever deleted automatically (no silent loss)."""
        self.db.execute("DELETE FROM memories WHERE status = 'pending' AND created < ?", (self.clock() - pending_days * 86400,))
        self.db.commit()

    # ---------------------------------------------------------------- what Claude sees
    def for_claude(self, speaker_id: str | None = None, speaker_level: str = "unknown", limit: int = 200,
                   speaker_name: str = "") -> str:
        """Active memories Pip may use right now: household ones, plus the speaker's private ones if
        they're recognised "sure" (memory policy §4.2). Rules for Pip come first."""
        rows = [r for r in self.items("active")
                if r["visibility"] == "household" or (speaker_level == "sure" and r["owner"] == speaker_id)]
        rows.sort(key=lambda r: (r["category"] != "rule", -r["updated"]))
        lines = [f"{self.short(r['id'])} {r['text']}" + (" (private)" if r["visibility"] == "private" else "") for r in rows[:limit]]
        out = ("What you remember: " + "; ".join(lines) + ".") if lines else "You don't remember anything yet."
        if speaker_level == "sure" and speaker_id:   # the speaker may approve what's waiting about them (D-107)
            waiting = [f"{self.short(r['id'])} {r['text']}" for r in self.items("pending")
                       if self.is_about(r, speaker_id, speaker_name) and self.may_see(r, speaker_id)]
            if waiting:
                out += " Waiting in the inbox, about the speaker: " + "; ".join(waiting[:10]) + "."
        return out

    def counts(self) -> dict:
        return dict(self.db.execute("SELECT status, COUNT(*) FROM memories GROUP BY status").fetchall())
