"""People and their voices (M1a step 5; PIP-NEW-024, 025, 027, D-77, D-87).

Who lives here is a short list kept in ~/pip-data/pip.db (names, role, whether they've opted out of
having room speech sent to the cloud). Each person can have a **voice profile**: a few "voiceprints"
(192 numbers each, from the TitaNet model, computed on the Pi in ~40 ms). Pip compares what it hears
with the profiles and says how sure it is:

    sure      ("Landen")   personal things, private memories
    probably  ("Landen?")  low-stakes personalising only
    unknown   ("Someone")

Voice is never enough for anything administrative; that needs the dashboard (PIP-NEW-027).
Voiceprints never leave the Pi and are never sent to Claude (memory policy category 8).

Guests: someone Pip doesn't know who introduces themselves or says "leave me out" gets a voice
remembered **only for this visit** (in memory, forgotten after 2 hours of silence or a restart, D-77).
"""
import asyncio
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..common import paths
from ..common.messages import new_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    role      TEXT NOT NULL DEFAULT 'member',   -- admin / member
    opted_out INTEGER NOT NULL DEFAULT 0,       -- room speech never leaves the Pi (D-77)
    created   REAL NOT NULL,
    last_heard REAL
);
CREATE TABLE IF NOT EXISTS voices (
    id        TEXT PRIMARY KEY,
    person_id TEXT NOT NULL,
    model     TEXT NOT NULL,      -- profiles belong to one model; a new model means re-learning
    emb       BLOB NOT NULL,      -- float32 voiceprint (never leaves the Pi)
    source    TEXT NOT NULL,      -- guided / introduction / m0 recordings / learned
    created   REAL NOT NULL
);
"""


@dataclass
class Match:
    person_id: str | None = None
    name: str = ""
    level: str = "unknown"        # sure / probably / unknown
    score: float = 0.0
    guest: bool = False
    opted_out: bool = False

    @property
    def label(self) -> str:
        return {"sure": self.name, "probably": f"{self.name}?"}.get(self.level, "Someone")


class Embedder:
    """TitaNet small through sherpa-onnx, loaded on first use."""

    def __init__(self, model_path: Path, threads: int = 1):
        self.model_path, self.threads = model_path, threads
        self.ex = None
        self.lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self.model_path.exists()

    def _embed(self, pcm: bytes) -> np.ndarray:
        if self.ex is None:
            import sherpa_onnx
            self.ex = sherpa_onnx.SpeakerEmbeddingExtractor(
                sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(self.model_path), num_threads=self.threads))
        s = self.ex.create_stream()
        s.accept_waveform(16000, np.frombuffer(pcm, np.int16).astype(np.float32) / 32768)
        s.input_finished()
        e = np.array(self.ex.compute(s), dtype=np.float32)
        return e / (np.linalg.norm(e) or 1.0)

    async def embed(self, pcm: bytes) -> np.ndarray:
        async with self.lock:
            return await asyncio.get_running_loop().run_in_executor(None, self._embed, pcm)


class People:
    def __init__(self, path: Path, cfg: dict | None = None, clock=time.time, embedder=None):
        v = (cfg or {}).get("voices", {})
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.clock = clock
        self.model = v.get("model", "nemo_en_titanet_small")
        self.sure = v.get("sure", 0.45)
        self.probably = v.get("probably", 0.33)
        self.margin = v.get("margin", 0.05)
        self.min_seconds = v.get("min_seconds", 0.8)
        self.embedder = embedder or Embedder(paths.data_dir() / "models" / "speaker" / f"{self.model}.onnx")
        self.guests: dict[str, dict] = {}   # visit-only voices: {id: {name, emb, opted_out, last}}
        self._profiles = None               # cache: {person_id: mean voiceprint}

    # ---------------------------------------------------------------- people
    def all(self) -> list[dict]:
        rows = [dict(r) for r in self.db.execute("SELECT * FROM people ORDER BY created")]
        counts = dict(self.db.execute("SELECT person_id, COUNT(*) FROM voices WHERE model = ? GROUP BY person_id", (self.model,)).fetchall())
        for r in rows:
            r["voice_samples"] = counts.get(r["id"], 0)
        return rows

    def get(self, pid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM people WHERE id = ?", (pid,)).fetchone()
        return dict(r) if r else None

    def by_name(self, name: str) -> dict | None:
        name = name.strip().lower()
        for p in self.all():
            if p["name"].lower() == name or p["name"].lower().split()[0] == name:
                return p
        return None

    def add_person(self, name: str, role: str = "member") -> dict:
        name = re.sub(r"\s+", " ", name).strip()[:40]
        if not name:
            raise ValueError("A name is needed")
        if self.by_name(name):
            raise ValueError(f"{name} is already on the list")
        if role not in ("admin", "member"):
            raise ValueError("Role must be admin or member")
        pid = new_id()
        self.db.execute("INSERT INTO people (id, name, role, opted_out, created) VALUES (?,?,?,?,?)",
                        (pid, name.title() if name.islower() else name, role, 0, self.clock()))
        self.db.commit()
        return self.get(pid)

    def update_person(self, pid: str, **fields) -> dict:
        allowed = {k: v for k, v in fields.items() if k in ("name", "role", "opted_out")}
        if "opted_out" in allowed:
            allowed["opted_out"] = int(bool(allowed["opted_out"]))
        if allowed:
            self.db.execute(f"UPDATE people SET {', '.join(f'{k} = ?' for k in allowed)} WHERE id = ?", (*allowed.values(), pid))
            self.db.commit()
        p = self.get(pid)
        if not p:
            raise ValueError("No such person")
        return p

    def remove_person(self, pid: str) -> None:
        """Removes the person and their voice. (Their memories are handled by Memory.forget_person.)"""
        self.db.execute("DELETE FROM voices WHERE person_id = ?", (pid,))
        self.db.execute("DELETE FROM people WHERE id = ?", (pid,))
        self.db.commit()
        self._profiles = None

    # ---------------------------------------------------------------- voices
    async def add_samples(self, pid: str, pcms: list[bytes], source: str) -> int:
        if not self.get(pid):
            raise ValueError("No such person")
        n = 0
        for pcm in pcms:
            if len(pcm) < 2 * 16000 * self.min_seconds:
                continue
            e = await self.embedder.embed(pcm)
            self.db.execute("INSERT INTO voices (id, person_id, model, emb, source, created) VALUES (?,?,?,?,?,?)",
                            (new_id(), pid, self.model, e.astype(np.float32).tobytes(), source, self.clock()))
            n += 1
        self.db.commit()
        self._profiles = None
        return n

    def forget_voice(self, pid: str) -> None:
        self.db.execute("DELETE FROM voices WHERE person_id = ?", (pid,))
        self.db.commit()
        self._profiles = None

    def profiles(self) -> dict[str, np.ndarray]:
        if self._profiles is None:
            by: dict[str, list] = {}
            for pid, emb in self.db.execute("SELECT person_id, emb FROM voices WHERE model = ?", (self.model,)):
                by.setdefault(pid, []).append(np.frombuffer(emb, np.float32))
            self._profiles = {pid: (m := np.mean(v, 0)) / (np.linalg.norm(m) or 1.0) for pid, v in by.items()}
        return self._profiles

    def has_opt_outs(self) -> bool:
        self._expire_guests()
        return any(g["opted_out"] for g in self.guests.values()) or \
            bool(self.db.execute("SELECT 1 FROM people WHERE opted_out = 1 LIMIT 1").fetchone())

    async def identify(self, pcm: bytes) -> Match:
        """Who is this? Compares with every profile (household and this visit's guests)."""
        if not self.embedder.available or len(pcm) < 2 * 16000 * self.min_seconds:
            return Match()
        profs = self.profiles()
        self._expire_guests()
        if not profs and not self.guests:
            return Match()
        e = await self.embedder.embed(pcm)
        scores = [(float(np.dot(e, p)), pid, False) for pid, p in profs.items()]
        scores += [(float(np.dot(e, g["emb"])), gid, True) for gid, g in self.guests.items()]
        scores.sort(reverse=True)
        best, pid, guest = scores[0]
        second = scores[1][0] if len(scores) > 1 else -1.0
        level = "sure" if best >= self.sure and best - second >= self.margin else \
            "probably" if best >= self.probably else "unknown"
        if level == "unknown":
            return Match(score=round(best, 3), level="unknown")
        if guest:
            g = self.guests[pid]
            g["last"] = self.clock()
            return Match(pid, g["name"], level, round(best, 3), guest=True, opted_out=g["opted_out"])
        p = self.get(pid) or {"name": "?", "opted_out": 0}
        if level == "sure":
            self.db.execute("UPDATE people SET last_heard = ? WHERE id = ?", (self.clock(), pid))
            self.db.commit()
        return Match(pid, p["name"], level, round(best, 3), opted_out=bool(p["opted_out"]))

    # ---------------------------------------------------------------- guests (this visit only)
    async def add_guest(self, name: str, pcm: bytes, opted_out: bool = False) -> dict | None:
        if not self.embedder.available or len(pcm) < 2 * 16000 * self.min_seconds:
            return None
        gid = "guest-" + new_id()
        self.guests[gid] = {"name": (name or "Guest").strip()[:30] or "Guest", "emb": await self.embedder.embed(pcm),
                            "opted_out": opted_out, "last": self.clock()}
        return self.guests[gid]

    def promote_guest(self, gid: str) -> dict:
        """A visitor who introduced themselves turns out to live here: add them, keeping the voice Pip heard."""
        g = self.guests.get(gid)
        if not g:
            raise ValueError("That visitor has been forgotten (2 hours of quiet). Add them by name instead.")
        person = self.add_person(g["name"], "member")
        self.db.execute("INSERT INTO voices (id, person_id, model, emb, source, created) VALUES (?,?,?,?,?,?)",
                        (new_id(), person["id"], self.model, g["emb"].astype(np.float32).tobytes(), "introduction", self.clock()))
        self.db.commit()
        if g["opted_out"]:
            self.update_person(person["id"], opted_out=True)
        del self.guests[gid]
        self._profiles = None
        return person

    def _expire_guests(self, after: float = 2 * 3600) -> None:
        for gid in [g for g, v in self.guests.items() if self.clock() - v["last"] > after]:
            del self.guests[gid]

    # ---------------------------------------------------------------- for Claude and the dashboard
    def summary(self, speaker: Match | None) -> str:
        names = [p["name"] for p in self.all()]
        who = f"Speaking: {speaker.name} ({speaker.level})" if speaker and speaker.level != "unknown" else "Speaking: not recognised"
        return f"{who}. Household: {', '.join(names) if names else 'nobody set up yet'}."

    def snapshot(self) -> dict:
        self._expire_guests()
        return {"people": self.all(), "voice_model": self.embedder.available,
                "guests": [{"id": gid, "name": g["name"], "opted_out": g["opted_out"]} for gid, g in self.guests.items()],
                "m0_recordings": bool(m0_owner_clips(limit_only=True))}


def m0_owner_clips(limit_only: bool = False) -> list[bytes]:
    """The owner's own M0 recordings, usable to learn their voice (D-72): 5 close + 3 across the room."""
    import wave
    rec = paths.data_dir() / "m0" / "recordings"
    phrases = Path(__file__).resolve().parents[2] / "m0" / "phrases.json"
    if not (rec / "manifest.json").exists() or not phrases.exists():
        return []
    cat = {p["id"]: (p["session"], p["category"]) for p in json.loads(phrases.read_text())["phrases"]}
    have = sorted(json.loads((rec / "manifest.json").read_text()))
    keys = [k for k in have if cat.get(k) == ("A", "clear")][:5] + [k for k in have if cat.get(k) == ("B", "distant")][:3]
    if limit_only:
        return keys
    out = []
    for k in keys:
        with wave.open(str(rec / f"{k}.wav")) as w:
            out.append(w.readframes(w.getnframes()))
    return out
