"""Small preferences changed on Pip's dashboard and kept across restarts (in ~/pip-data/pip.db).

    chime        play the chime when he hears his name or a wake word (D-105). Default on.
    clap_mic     which microphone listens for the two-clap switch (D-91): a PipeWire device name, or ""
                 for none (then the clap switch is off). The speakerphone can't be used: its own noise
                 filter erases claps.

Settings that are set once and rarely changed live in ~/pip-data/settings.toml instead (defaults.toml).
"""
import sqlite3
from pathlib import Path

SCHEMA = "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"   # shared with core/mic.py
DEFAULTS = {"chime": True, "clap_mic": ""}


class Prefs:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(SCHEMA)
        self.db.commit()

    def get(self, key: str):
        default = DEFAULTS[key]
        r = self.db.execute("SELECT value FROM settings WHERE key = ?", ("pref_" + key,)).fetchone()
        if r is None:
            return default
        return r[0] == "1" if isinstance(default, bool) else r[0]

    def set(self, key: str, value) -> None:
        default = DEFAULTS[key]
        if isinstance(default, bool):
            value = "1" if value in (True, 1, "1", "true", "on") else "0"
        else:
            value = str(value or "")[:200]
        self.db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ("pref_" + key, value))
        self.db.commit()

    def snapshot(self) -> dict:
        return {k: self.get(k) for k in DEFAULTS}
