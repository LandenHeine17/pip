"""Turning Pip's listening off and on (D-91): the privacy control now that he listens to everything.

Two separate switches, and Pip listens only when **both** are on:

    dashboard   the Listening switch on the dashboard
    claps       two claps in a row flip this one (only when claps are turned on in the
                dashboard's preferences and a clap microphone is chosen, core/prefs.py)

They're kept apart on purpose: if the dashboard switched him off, clapping can't switch him back on.
The speakerphone's own mute button works as always, on top of both.

What pip-ears does in each case (`ears.mic {mode}`):

    on          normal: wake words, and everything heard goes to speech-to-text
    claps_only  switched off by claps: the Pi only listens for two claps; nothing is recognised or sent
    off         switched off on the dashboard: nothing at all is listened to

Both switches are saved in ~/pip-data/pip.db, so a restart doesn't turn listening back on.
"""
from pathlib import Path
from ..common.db import connect

SCHEMA = "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"


class MicSwitch:
    def __init__(self, path: Path):
        self.db = connect(path, rows=False)
        self.db.execute(SCHEMA)
        self.db.commit()
        self.dashboard = self._get("mic_dashboard", True)
        self.claps = self._get("mic_claps", True)

    def _get(self, key: str, default: bool) -> bool:
        r = self.db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return default if r is None else r[0] == "1"

    def _set(self, key: str, value: bool) -> None:
        self.db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, "1" if value else "0"))
        self.db.commit()

    @property
    def listening(self) -> bool:
        return self.dashboard and self.claps

    @property
    def mode(self) -> str:
        if not self.dashboard:
            return "off"
        return "on" if self.claps else "claps_only"

    def set_dashboard(self, on: bool) -> None:
        self.dashboard = bool(on)
        self._set("mic_dashboard", self.dashboard)

    def clapped(self) -> bool:
        """Two claps were heard. Flips only the clap switch; does nothing while the dashboard has him off.
        Returns whether anything changed."""
        if not self.dashboard:
            return False
        self.claps = not self.claps
        self._set("mic_claps", self.claps)
        return True

    def snapshot(self) -> dict:
        return {"dashboard": self.dashboard, "claps": self.claps, "listening": self.listening, "mode": self.mode}
