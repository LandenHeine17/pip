"""Bring over from Ziggletron: its memories (into the memory inbox) and its personality (step 8, plan §3).

Run from the Settings page ("Bring over from Ziggletron"), behind the core.migrate request:

    {"action": "get"}     → dry run: {"available", "memories": {new, duplicate, refused, total},
                             "personality": bool (would be brought over), "done_at": unix or None}
    {"action": "import"}  → {"memories": {imported, duplicate, refused}, "personality": "imported" | "skipped",
                             "error"?}

Read-only towards Ziggletron (AC-SEC-04, D-82): ~/ziggletron/data/household.json and ~/ziggletron/behavior.md
are only opened for reading, their SHA-256 is checked before and after (a change is reported as an error),
nothing is written under ~/ziggletron and Ziggletron is never started.

- Each memory goes to the inbox (pending), so someone approves it first (D-07). It's dated now, not when
  Ziggletron saved it (pending items expire 14 days after they're created); the old date is in its source.
  Ziggletron's kinds: rule → rule, household → household, person → personal, note → household. Its "about"
  words that are the name of someone in Pip's household become that person; other names (words written with
  a capital letter in the memory) are only mentioned in why it's waiting.
- Duplicates of what Pip already remembers, and anything the credential check refuses, are skipped and counted.
- The personality: behavior.md without its header comment and without parts about things Pip can't do (texting,
  pictures, changing his own code), with the name as {name}. It becomes the active personality ("From
  Ziggletron"); Pip's previous one stays in the personality history for undo.
- Running it again brings nothing new: the Ziggletron ids already imported and the date are kept in the
  settings table (key ziggletron_imported).

Only counts are returned: never memory texts.
"""
import difflib
import hashlib
import json
import re
import time
from pathlib import Path

from ..common import personality as personality_module
from .memory import CREDENTIAL, Refused
from .prefs import SCHEMA as SETTINGS_SCHEMA

KEY = "ziggletron_imported"
KINDS = {"rule": "rule", "household": "household", "person": "personal", "note": "household"}
# Things Ziggletron could do that Pip can't: a section or paragraph about them is left out of the personality
NOT_PIP = re.compile(r"\b(sms|texts?|texting|text messages?|messages? (?:to|from)|phone numbers?|pictures?|images?|"
                     r"photos?|camera|self[- ]?edit\w*|edit(?:ing)? (?:your|its) own|your own code|"
                     r"(?:change|changing|rewrite|rewriting) (?:your|its) own|coding|code changes?)\b", re.I)
ROOM_HEAD = "The room:"


class ChangedUnderneath(RuntimeError):
    pass


def _sha(path: Path) -> str | None:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return None


def convert_personality(raw: str) -> str:
    """behavior.md → a Pip personality: header comment removed, Ziggletron-only parts left out, the name as
    {name}, and only the {name}/{location} placeholders left."""
    text = re.sub(r"\A\s*(<!--.*?-->\s*)+", "", raw.replace("\r\n", "\n"), flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"\bZiggletron\b", "{name}", text)
    out, skipping = [], False
    for block in re.split(r"\n\s*\n", text):
        b = block.strip()
        if not b:
            continue
        if b.startswith("#"):
            skipping = bool(NOT_PIP.search(b.splitlines()[0]))
            if skipping:
                continue
        if skipping:
            continue
        lines = [ln for ln in b.splitlines() if not (ln.lstrip().startswith(("-", "*")) and NOT_PIP.search(ln))]
        if not lines or (not lines[0].lstrip().startswith(("-", "*", "#")) and NOT_PIP.search("\n".join(lines))):
            continue
        out.append("\n".join(lines))
    text = "\n\n".join(out)
    text = re.sub(r"\{(?!(?:name|location)\})([^{}]*)\}", r"\1", text)      # other placeholders: plain words
    text = re.sub(r"\{(?!(?:name|location)\})|(?<!\{name)(?<!\{location)\}", "", text)   # stray brackets
    # Pip's transcripts mark who spoke and where he was cut off; Ziggletron's text doesn't explain that
    if ROOM_HEAD not in text:
        room = personality_module.BUILTIN[personality_module.BUILTIN.index(ROOM_HEAD):]
        text += "\n\n" + room[:room.index("\n\n")]
    return text.strip()


class Migration:
    def __init__(self, memory, people, personality_store=personality_module, source: Path | None = None):
        self.memory = memory
        self.people = people
        self.store = personality_store
        self.source = Path(source) if source is not None else Path.home() / "ziggletron"
        self.db = memory.db
        self.db.execute(SETTINGS_SCHEMA)
        self.db.commit()

    @property
    def household(self) -> Path:
        return self.source / "data" / "household.json"

    @property
    def behavior(self) -> Path:
        return self.source / "behavior.md"

    # ---------------------------------------------------------------- record of what was imported
    def _record(self) -> dict:
        r = self.db.execute("SELECT value FROM settings WHERE key = ?", (KEY,)).fetchone()
        try:
            d = json.loads(r[0]) if r else {}
        except ValueError:
            d = {}
        return {"done_at": d.get("done_at"), "ids": list(d.get("ids", [])), "personality": bool(d.get("personality"))}

    def _save_record(self, rec: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (KEY, json.dumps(rec)))
        self.db.commit()

    # ---------------------------------------------------------------- reading Ziggletron (read-only)
    def _checksums(self) -> tuple:
        return _sha(self.household), _sha(self.behavior)

    def _read(self) -> tuple[list[dict], str | None]:
        memories, behavior = [], None
        if self.household.is_file():
            with open(self.household, "r", encoding="utf-8") as f:
                data = json.load(f)
            memories = [m for m in (data.get("memories") or []) if isinstance(m, dict)]
        if self.behavior.is_file():
            with open(self.behavior, "r", encoding="utf-8") as f:
                behavior = f.read()
        return memories, behavior

    @staticmethod
    def _clean(text) -> str:
        return re.sub(r"\s+", " ", str(text or "")).strip()[:300]

    def _plan(self, memories: list[dict], rec: dict) -> list[tuple[str, dict]]:
        """What would happen to each memory: ("new" | "duplicate" | "refused", memory)."""
        out, seen = [], set()
        for m in memories:
            text = self._clean(m.get("fact"))
            key = text.lower().strip(" .")
            if m.get("id") and str(m["id"]) in rec["ids"]:      # brought over before (even if since deleted)
                seen.add(key)
                out.append(("duplicate", m))
            elif len(text) < 3 or CREDENTIAL.search(text):
                out.append(("refused", m))
            elif any(difflib.SequenceMatcher(None, key, k).ratio() >= 0.9 for k in seen) or \
                    self.memory.duplicate_of(text) is not None:     # the same test Memory.add uses
                out.append(("duplicate", m))
            else:
                seen.add(key)
                out.append(("new", m))
        return out

    def _personality_text(self, behavior: str | None) -> str | None:
        if not behavior:
            return None
        try:
            return self.store.validate(convert_personality(behavior))
        except ValueError:
            return None

    def _would_bring_personality(self, behavior: str | None, rec: dict) -> bool:
        text = self._personality_text(behavior)
        return bool(text) and not rec["personality"] and text != self.store.read()[0]

    # ---------------------------------------------------------------- the two actions
    async def handle(self, data: dict) -> dict:
        action = (data or {}).get("action", "get")
        try:
            if action == "get":
                return self.dry_run()
            if action == "import":
                return self.run()
        except ChangedUnderneath as e:
            return {"error": str(e)}
        except (OSError, ValueError) as e:
            return {"error": f"Couldn't read Ziggletron's files ({type(e).__name__})."}
        return {"error": "Unknown action"}

    def dry_run(self) -> dict:
        rec = self._record()
        before = self._checksums()
        available = before != (None, None)
        if not available:
            return {"available": False, "memories": {"new": 0, "duplicate": 0, "refused": 0, "total": 0},
                    "personality": False, "done_at": rec["done_at"]}
        memories, behavior = self._read()
        plan = self._plan(memories, rec)
        result = {"available": True,
                  "memories": {k: sum(1 for p, _ in plan if p == k) for k in ("new", "duplicate", "refused")}
                  | {"total": len(plan)},
                  "personality": self._would_bring_personality(behavior, rec),
                  "done_at": rec["done_at"]}
        self._check_unchanged(before)
        return result

    def _check_unchanged(self, before: tuple) -> None:
        if self._checksums() != before:
            raise ChangedUnderneath("Ziggletron's files changed while they were being read, so nothing more was "
                                    "done. Make sure Ziggletron is off and try again.")

    def _about(self, m: dict) -> tuple[list[str], list[str]]:
        """(Pip person ids, other names to mention) from Ziggletron's "about" words."""
        ids, others = [], []
        fact = str(m.get("fact") or "")
        names = {p["name"].lower(): p["id"] for p in self.people.all()} | \
                {p["name"].split()[0].lower(): p["id"] for p in self.people.all() if p["name"].split()}
        for word in m.get("about") or []:
            w = str(word).strip()
            if not w:
                continue
            if w.lower() in names:
                if names[w.lower()] not in ids:
                    ids.append(names[w.lower()])
            elif re.search(rf"\b{re.escape(w)}\b", fact, re.I) and \
                    all(part[:1].isupper() for part in re.findall(rf"\b{re.escape(w)}\b", fact, re.I)):
                name = w.title()
                if name not in others:
                    others.append(name)
        return ids, others

    def run(self) -> dict:
        rec = self._record()
        before = self._checksums()
        if before == (None, None):
            return {"memories": {"imported": 0, "duplicate": 0, "refused": 0}, "personality": "skipped",
                    "error": "Ziggletron's files weren't found."}
        memories, behavior = self._read()
        self._check_unchanged(before)   # fail before changing anything
        counts = {"imported": 0, "duplicate": 0, "refused": 0}
        for verdict, m in self._plan(memories, rec):
            if verdict != "new":
                counts[verdict] += 1
                continue
            ids, others = self._about(m)
            saved = str(m.get("saved") or "")[:10]
            source = f"Imported from Ziggletron (saved {saved})" if re.fullmatch(r"\d{4}-\d\d-\d\d", saved) \
                else "Imported from Ziggletron"
            why = "from Ziggletron" + (", about " + ", ".join(others[:4]) if others else "")
            try:
                _, outcome = self.memory.add(self._clean(m.get("fact")), KINDS.get(str(m.get("kind")), "household"),
                                             about=ids, pending_because=why[:120], source=source)
            except Refused:
                counts["refused"] += 1
                continue
            if outcome in ("duplicate", "waiting"):
                counts["duplicate"] += 1
                continue
            counts["imported"] += 1
            if m.get("id"):
                rec["ids"].append(str(m["id"]))
        result = {"memories": counts, "personality": "skipped"}
        if self._would_bring_personality(behavior, rec):
            self.store.save(self._personality_text(behavior), label="From Ziggletron")
            rec["personality"] = True
            result["personality"] = "imported"
        rec["done_at"] = time.time()
        self._save_record(rec)
        try:
            self._check_unchanged(before)
        except ChangedUnderneath as e:
            result["error"] = str(e)
        return result
