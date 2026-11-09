"""Small preferences changed on Pip's dashboard and kept across restarts (in ~/pip-data/pip.db).

    chime        play the chime when he hears his name or a wake word (D-105). Default on.
    clap_mic     which microphone listens for the two-clap switch (D-91): a PipeWire device name, or ""
                 for none. The speakerphone can't be used: its own noise filter erases claps.
    claps        whether two claps switch listening at all (default off, until the detector is tuned on
                 recordings from that microphone: Session F is recorded with this off).
    state_bars   show each change in what Pip is doing ("Waiting to see if you're done", D-112) as a bar in the
                 dashboard's Conversation, for understanding him. Default off.

Settings that are set once and rarely changed live in ~/pip-data/settings.toml instead (defaults.toml).
"""
from pathlib import Path
from ..common.db import connect

SCHEMA = "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"   # shared with core/mic.py
DEFAULTS = {"chime": True, "clap_mic": "", "claps": False, "state_bars": False}


class Prefs:
    def __init__(self, path: Path):
        self.db = connect(path, rows=False)
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
