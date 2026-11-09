"""How much Pip uses Claude (D-69, PIP-NEW-089).

Every Claude answer (and every self-change job) ends with a "result" from the Claude Code CLI: what it cost
and whether it failed. Each one is counted here, by day and kind, in the `usage` table of ~/pip-data/pip.db.
Nothing about what was asked or said is stored: only counts, errors and cost.

    kinds: answer (said to Pip), mention (his name came up), join (joining in), preview (Settings' "Try it"),
           builder (self-changes), other

The Pro plan doesn't say how much allowance is left, so there is no "80 % used" warning: Pip only knows
he's hit the limit when Claude says so ("usage limit reached … resets 3pm"). Then joining in and
self-changes pause until the reset time (or an hour, if none is given); replies to people keep trying.
"""
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import paths

KINDS = ("answer", "join", "mention", "preview", "builder", "other")
SCHEMA = """CREATE TABLE IF NOT EXISTS usage (
    day TEXT NOT NULL, kind TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0, errors INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0, PRIMARY KEY (day, kind))"""
DEFAULT_PAUSE = 3600.0

# What the Claude Code CLI says when the plan's allowance is used up (its wording changes between versions)
LIMIT = re.compile(r"usage limit|limit reached|rate limit|hit your limit|out of (?:extra )?usage|"
                   r"limit will reset|resets? (?:at )?\d", re.I)
EPOCH = re.compile(r"\|\s*(\d{10})\b")                  # older CLIs: "Claude AI usage limit reached|1731430800"
RESETS = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?", re.I)


def is_limit_error(text: str | None) -> bool:
    """Is this Claude saying the plan's usage limit (or a rate limit) has been reached?"""
    return bool(text) and bool(LIMIT.search(text))


def reset_time(text: str | None, now: float | None = None) -> float | None:
    """When the limit resets, as a unix time, if the message says ("…|1731430800", "resets 3pm",
    "resets at 15:30"). A clock time is the next time it comes round (local time)."""
    if not text:
        return None
    now = time.time() if now is None else now
    if m := EPOCH.search(text):
        at = float(m.group(1))
        return at if at > now else None
    m = RESETS.search(text)
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower().replace(".", "")
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    base = datetime.fromtimestamp(now)
    at = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if at.timestamp() <= now:
        at += timedelta(days=1)
    return at.timestamp()


def paused_until(text: str | None, now: float | None = None) -> float:
    """How long to pause joining in and self-changes after a limit error: until the reset, or an hour."""
    now = time.time() if now is None else now
    return reset_time(text, now) or now + DEFAULT_PAUSE


class Usage:
    """The usage table. Several programs write to it (pip-thinker, pip-builder), so each call opens its own
    short connection."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else paths.database()
        with self._db() as db:
            db.execute(SCHEMA)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def record(self, kind: str, cost_usd: float | None = None, error: bool = False, day: date | None = None) -> None:
        """Count one Claude request of this kind (and its cost, if the CLI reported one)."""
        kind = kind if kind in KINDS else "other"
        day = (day or date.today()).isoformat()
        try:
            cost = max(0.0, float(cost_usd or 0))
        except (TypeError, ValueError):
            cost = 0.0
        with self._db() as db:
            db.execute("INSERT INTO usage (day, kind, count, errors, cost_usd) VALUES (?, ?, 1, ?, ?) "
                       "ON CONFLICT (day, kind) DO UPDATE SET count = count + 1, errors = errors + excluded.errors, "
                       "cost_usd = cost_usd + excluded.cost_usd", (day, kind, int(bool(error)), cost))

    def summary(self, since: date, until: date | None = None) -> dict:
        """Totals from `since` to `until` (today): {"requests", "errors", "cost_usd", "by_kind": {kind: count}}."""
        until = until or date.today()
        with self._db() as db:
            rows = db.execute("SELECT kind, SUM(count), SUM(errors), SUM(cost_usd) FROM usage "
                              "WHERE day BETWEEN ? AND ? GROUP BY kind", (since.isoformat(), until.isoformat())).fetchall()
        return {"requests": sum(r[1] for r in rows), "errors": sum(r[2] for r in rows),
                "cost_usd": round(sum(r[3] for r in rows), 4), "by_kind": {r[0]: r[1] for r in rows}}

    def today(self, today: date | None = None) -> dict:
        today = today or date.today()
        return self.summary(today, today)

    def week(self, today: date | None = None) -> dict:
        """The last 7 days, today included."""
        today = today or date.today()
        return self.summary(today - timedelta(days=6), today)
