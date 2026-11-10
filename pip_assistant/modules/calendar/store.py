"""The calendar's events, kept on the Pi (the copy Pip uses even offline).

Calendars: "pip" is Pip's own (on the Pi only); more of Pip's own can be made ("pip:<id>"). Each
connected Google account adds its calendars ("g:<account>:<google calendar id>"). A calendar can be
ticked off (hidden, not synced) on the calendar page, or ignored (forgotten) on the Google page.

An event belongs to one calendar. Changes Pip makes to a Google calendar's events are marked
`pending` (add / change / delete) until the sync has sent them to Google (sync.py).

Heads-ups ("Heads up: dentist in 30 minutes") are alerts in Pip's schedule linked by event id;
they're re-made whenever an event changes. Private events never get one (D-99).
"""
import sqlite3
import time
from datetime import date, datetime, timedelta

from pip_assistant.common.messages import new_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS calendar_calendars (
    id          TEXT PRIMARY KEY,          -- 'pip' or 'pip:<id>' (Pip's own), or 'g:<account>:<google calendar id>'
    account_id  TEXT NOT NULL DEFAULT '',  -- the google module's account ('' for Pip's own)
    google_id   TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL,
    color       TEXT NOT NULL DEFAULT '#6c5ce7',
    ticked      INTEGER NOT NULL DEFAULT 1,  -- shown and synced
    writable    INTEGER NOT NULL DEFAULT 1,
    is_primary  INTEGER NOT NULL DEFAULT 0,
    sync_token  TEXT NOT NULL DEFAULT '',
    full_at     REAL NOT NULL DEFAULT 0,     -- when everything was last fetched (then only changes; sync.py)
    reminder    REAL,                        -- the calendar's default heads-up, minutes (Google's default reminder)
    owner       INTEGER NOT NULL DEFAULT 1,  -- theirs (can be renamed / deleted), not just shared with them
    ignored     INTEGER NOT NULL DEFAULT 0,  -- set on the Google page: not synced, not shown, never told to Pip (D-104)
    in_list     INTEGER NOT NULL DEFAULT 1,  -- shown in the dashboard's "next two weeks" list (D-109)
    heads_up    TEXT NOT NULL DEFAULT 'own'  -- off / own (only events with their own reminder) / all / minutes (D-109)
);
CREATE TABLE IF NOT EXISTS calendar_prefs (   -- per account ('' = Pip's own calendar): night, household (D-109)
    account_id  TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    PRIMARY KEY (account_id, key)
);
CREATE TABLE IF NOT EXISTS calendar_events (
    id          TEXT PRIMARY KEY,
    calendar_id TEXT NOT NULL DEFAULT 'pip',
    title       TEXT NOT NULL,
    start       REAL NOT NULL,             -- Unix time (all-day: midnight that day)
    end         REAL NOT NULL,             -- Unix time (all-day: midnight after the last day)
    all_day     INTEGER NOT NULL DEFAULT 0,
    repeat      TEXT NOT NULL DEFAULT '',  -- Pip's own repeats: daily / weekdays / weekends / weekly(:0-6)
    location    TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    private     INTEGER NOT NULL DEFAULT 0, -- Pip never mentions it (D-99)
    reminder    REAL,                      -- heads-up, minutes before (NULL: none)
    reminder_own INTEGER NOT NULL DEFAULT 0, -- 1: the event's own reminder (not just the calendar's automatic one)
    google_id   TEXT NOT NULL DEFAULT '',
    etag        TEXT NOT NULL DEFAULT '',
    pending     TEXT NOT NULL DEFAULT '',  -- '' / add / change / delete: waiting to be sent to Google
    created     REAL NOT NULL,
    updated     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS calendar_events_start ON calendar_events (start);
CREATE INDEX IF NOT EXISTS calendar_events_google ON calendar_events (calendar_id, google_id);
"""


class CalendarStore:
    def __init__(self, db: sqlite3.Connection, alerts=None, clock=None):
        self.db, self.alerts, self.clock = db, alerts, clock or time.time   # alerts: ctx.alerts (core/modules.ModuleAlerts)
        self.db.executescript(SCHEMA)
        have = {r[1] for r in self.db.execute("PRAGMA table_info(calendar_calendars)")}
        for col in ("owner INTEGER NOT NULL DEFAULT 1", "ignored INTEGER NOT NULL DEFAULT 0",
                    "in_list INTEGER NOT NULL DEFAULT 1", "heads_up TEXT NOT NULL DEFAULT 'own'",
                    "full_at REAL NOT NULL DEFAULT 0"):
            if col.split()[0] not in have:
                self.db.execute(f"ALTER TABLE calendar_calendars ADD COLUMN {col}")
                if col.startswith("owner"):   # until the next sync says: read-only ones aren't theirs
                    self.db.execute("UPDATE calendar_calendars SET owner = writable")
        remake = False
        if "reminder_own" not in {r[1] for r in self.db.execute("PRAGMA table_info(calendar_events)")}:
            self.db.execute("ALTER TABLE calendar_events ADD COLUMN reminder_own INTEGER NOT NULL DEFAULT 0")
            self.db.execute("UPDATE calendar_events SET reminder_own = 1 WHERE google_id = '' AND reminder IS NOT NULL")
            self.db.execute("UPDATE calendar_calendars SET sync_token = ''")   # fetch everything again to learn which are own
            remake = True
        self.db.execute("INSERT OR IGNORE INTO calendar_calendars (id, name, color) VALUES ('pip', 'Pip', '#6c5ce7')")
        self.db.commit()
        self.on_change = []          # called with the event id after any change Pip makes (sync, dashboard)
        self.version = 0             # goes up whenever anything changes (so the dashboard knows to refresh)
        if remake:   # heads-ups made under the old rule (every event) go now; the next sync brings back the right ones
            self.remake_alerts()

    # ---------------------------------------------------------------- calendars
    def calendars(self, ignored: bool = False) -> list[dict]:
        """The calendars Pip uses (ignored ones only if asked: the Google page's settings list)."""
        return [dict(r) for r in self.db.execute("SELECT * FROM calendar_calendars ORDER BY account_id != '', is_primary DESC, name")
                if ignored or not r["ignored"]]

    def calendar(self, cid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM calendar_calendars WHERE id = ?", (cid,)).fetchone()
        return dict(r) if r else None

    def upsert_calendar(self, cid: str, **fields) -> None:
        if self.calendar(cid):
            self.db.execute(f"UPDATE calendar_calendars SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                            (*fields.values(), cid))
        else:
            cols = ["id", *fields]
            self.db.execute(f"INSERT INTO calendar_calendars ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                            (cid, *fields.values()))
        self.db.commit()

    def remove_calendar(self, cid: str) -> None:
        self.drop_events(cid)
        self.db.execute("DELETE FROM calendar_calendars WHERE id = ?", (cid,))
        self.db.commit()
        self.version += 1

    def drop_events(self, cid: str) -> None:
        for ev in self.db.execute("SELECT id FROM calendar_events WHERE calendar_id = ?", (cid,)).fetchall():
            self._cancel_alerts(ev["id"])
        self.db.execute("DELETE FROM calendar_events WHERE calendar_id = ?", (cid,))
        self.db.commit()

    def set_ignored(self, cid: str, ignored: bool) -> None:
        """Ignored: Pip forgets its events and stops fetching them; un-ignored: fetched again from scratch."""
        self.upsert_calendar(cid, ignored=int(ignored), sync_token="")
        if ignored:
            self.drop_events(cid)
        self.version += 1

    # ---------------------------------------------------------------- per-account preferences (D-109)
    PREFS = {"night": "1", "household": ""}   # no heads-ups 23:00–07:00 · their calendar for "don't know whose"

    def pref(self, account_id: str, key: str) -> str:
        r = self.db.execute("SELECT value FROM calendar_prefs WHERE account_id = ? AND key = ?", (account_id, key)).fetchone()
        return r["value"] if r else self.PREFS[key]

    def set_pref(self, account_id: str, key: str, value: str) -> None:
        if key not in self.PREFS:
            raise ValueError(f"unknown setting {key}")
        self.db.execute("INSERT OR REPLACE INTO calendar_prefs (account_id, key, value) VALUES (?,?,?)",
                        (account_id, key, str(value)))
        self.db.commit()
        self.version += 1

    def ticked(self) -> set[str]:
        return {r["id"] for r in self.db.execute("SELECT id FROM calendar_calendars WHERE ticked = 1 AND ignored = 0")}

    # ---------------------------------------------------------------- events
    def event(self, eid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM calendar_events WHERE id = ? AND pending != 'delete'", (eid,)).fetchone()
        return dict(r) if r else None

    def by_google(self, cid: str, gid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM calendar_events WHERE calendar_id = ? AND google_id = ?", (cid, gid)).fetchone()
        return dict(r) if r else None

    def all_events(self) -> list[dict]:
        """Events in calendars that are ticked on (not ones waiting to be deleted)."""
        ticked = self.ticked()
        return [dict(r) for r in self.db.execute("SELECT * FROM calendar_events WHERE pending != 'delete' ORDER BY start")
                if r["calendar_id"] in ticked]

    def pending(self, cids: set[str]) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM calendar_events WHERE pending != '' ORDER BY updated")
                if r["calendar_id"] in cids]

    def _google_backed(self, cid: str) -> bool:
        c = self.calendar(cid)
        return bool(c and c["account_id"])

    def add_event(self, title: str, start: datetime, all_day: bool = False, repeat: str = "",
                  heads_up_before: float | None = None, end: datetime | None = None, location: str = "",
                  notes: str = "", private: bool = False, calendar_id: str = "pip") -> dict:
        """Save an event and read it back: Pip only says it's added once it really is (PIP-NEW-039)."""
        cal = self.calendar(calendar_id)
        if not cal or not cal["writable"]:
            raise ValueError("Pip can't add events to that calendar")
        end = end or (start + (timedelta(days=1) if all_day else timedelta(hours=1)))
        if end <= start:
            raise ValueError("an event must end after it starts")
        now, eid = self.clock(), new_id()
        reminder = heads_up_before / 60 if heads_up_before is not None else None   # asked for: always "own"
        self.db.execute("INSERT INTO calendar_events (id, calendar_id, title, start, end, all_day, repeat, location, notes, "
                        "private, reminder, reminder_own, pending, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (eid, calendar_id, title.strip()[:200], start.timestamp(), end.timestamp(), int(all_day), repeat,
                         location.strip()[:200], notes.strip()[:2000], int(private), reminder, int(reminder is not None),
                         "add" if self._google_backed(calendar_id) else "", now, now))
        self.db.commit()
        ev = self.event(eid)
        if not ev:
            raise RuntimeError("event was not saved")
        self.sync_alerts(ev)
        self._changed(eid)
        return ev

    def update_event(self, eid: str, start: datetime | None = None, all_day: bool | None = None, end: datetime | None = None,
                     **fields) -> dict:
        """Change an event (moving keeps its length unless a new end is given). Read back to check."""
        ev = self.event(eid)
        if not ev:
            raise RuntimeError("event not found")
        cal = self.calendar(ev["calendar_id"])
        if cal and not cal["writable"]:
            raise ValueError("That calendar is read-only for Pip")
        new = {k: v for k, v in fields.items() if k in ("title", "location", "notes", "private", "repeat", "reminder")}
        if "reminder" in new:
            new["reminder_own"] = int(new["reminder"] is not None)   # set by hand: their own
        if start is not None:
            length = ev["end"] - ev["start"]
            new["start"] = start.timestamp()
            new["end"] = end.timestamp() if end else start.timestamp() + length
        elif end is not None:
            new["end"] = end.timestamp()
        if all_day is not None:
            new["all_day"] = int(all_day)
        if "private" in new:
            new["private"] = int(bool(new["private"]))
        if new.get("end", ev["end"]) <= new.get("start", ev["start"]):
            raise ValueError("an event must end after it starts")
        if self._google_backed(ev["calendar_id"]) and ev["pending"] != "add":
            new["pending"] = "change"
        new["updated"] = self.clock()
        self.db.execute(f"UPDATE calendar_events SET {', '.join(f'{k} = ?' for k in new)} WHERE id = ?", (*new.values(), eid))
        self.db.commit()
        ev = self.event(eid)
        if not ev or ("start" in new and abs(ev["start"] - new["start"]) > 0.01):
            raise RuntimeError("event was not changed")
        self.sync_alerts(ev)
        self._changed(eid)
        return ev

    def remove_event(self, eid: str) -> None:
        """Deleting an event removes its heads-up too (FEAT-036). A Google event waits to be deleted there."""
        ev = self.event(eid)
        self._cancel_alerts(eid)
        if ev and ev["google_id"]:
            self.db.execute("UPDATE calendar_events SET pending = 'delete', updated = ? WHERE id = ?", (self.clock(), eid))
        else:
            self.db.execute("DELETE FROM calendar_events WHERE id = ?", (eid,))
        self.db.commit()
        self._changed(eid)

    def copy_to(self, ev: dict, cid: str) -> dict:
        """Move an event by making it again in another calendar and removing the original (both synced)."""
        new = self.add_event(ev["title"], datetime.fromtimestamp(ev["start"]), all_day=bool(ev["all_day"]), repeat=ev["repeat"],
                             end=datetime.fromtimestamp(ev["end"]), location=ev["location"], notes=ev["notes"],
                             private=bool(ev["private"]), calendar_id=cid)
        if ev["reminder"] is not None:
            new = self.update_event(new["id"], reminder=ev["reminder"])
        self.remove_event(ev["id"])
        return new

    def forget(self, eid: str) -> None:
        """Gone for good (deleted at Google, or by sync): no further syncing."""
        self._cancel_alerts(eid)
        self.db.execute("DELETE FROM calendar_events WHERE id = ?", (eid,))
        self.db.commit()
        self.version += 1

    def save_synced(self, eid: str | None, **row) -> dict:
        """Store an event exactly as Google has it (from a sync): nothing pending."""
        row["pending"], row["updated"] = "", self.clock()
        if eid and self.db.execute("SELECT 1 FROM calendar_events WHERE id = ?", (eid,)).fetchone():
            self.db.execute(f"UPDATE calendar_events SET {', '.join(f'{k} = ?' for k in row)} WHERE id = ?", (*row.values(), eid))
        else:
            eid = eid or new_id()
            row.setdefault("created", self.clock())
            cols = ["id", *row]
            self.db.execute(f"INSERT INTO calendar_events ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                            (eid, *row.values()))
        self.db.commit()
        ev = dict(self.db.execute("SELECT * FROM calendar_events WHERE id = ?", (eid,)).fetchone())
        self.sync_alerts(ev)
        self.version += 1
        return ev

    def _changed(self, eid: str) -> None:
        self.version += 1
        for fn in self.on_change:
            fn(eid)

    # ---------------------------------------------------------------- heads-ups
    def _cancel_alerts(self, eid: str) -> None:
        if self.alerts is not None:
            self.alerts.cancel_for(eid)

    def heads_up_minutes(self, ev: dict, cal: dict | None) -> float | None:
        """How long before this event Pip gives a heads-up, by the calendar's setting (D-109): off, only events
        with their own reminder ("own", the default), every event with a reminder ("all"), or a fixed time."""
        mode = (cal or {}).get("heads_up", "own")
        if mode == "off":
            return None
        if mode == "own":
            return ev["reminder"] if ev["reminder_own"] else None
        if mode == "all":
            return ev["reminder"]
        try:
            return float(mode)
        except ValueError:
            return None

    def sync_alerts(self, ev: dict) -> None:
        """Re-make an event's heads-up: none if private, past, more than a week away (re-checked later), not
        wanted for that calendar, or at night when that's switched off (D-109)."""
        if self.alerts is None:
            return
        self.alerts.cancel_for(ev["id"])
        cal = self.calendar(ev["calendar_id"])
        mins = self.heads_up_minutes(ev, cal)
        if ev["private"] or mins is None or ev["pending"] == "delete" or ev["calendar_id"] not in self.ticked():
            return
        start = datetime.fromtimestamp(ev["start"])
        base = start.replace(hour=8, minute=0) if ev["all_day"] else start   # all-day: count back from 8 AM (FEAT-036)
        due = base.timestamp() - mins * 60
        at = datetime.fromtimestamp(due)
        if (at.hour >= 23 or at.hour < 7) and self.pref((cal or {}).get("account_id", ""), "night") == "1":
            return   # no heads-ups at night
        soon_enough = ev["repeat"] or due < self.clock() + 7 * 86400   # repeats re-arm themselves after ringing
        if due > self.clock() and soon_enough:
            self.alerts.add(ev["title"], due, length=mins * 60, repeat=ev["repeat"], item_id=ev["id"])

    def refresh_alerts(self) -> None:
        """Daily: give events coming into the next week their heads-up."""
        soon = self.clock() + 7 * 86400
        for ev in self.all_events():
            if self.clock() < ev["start"] < soon and not ev["repeat"]:
                if not self.alerts or not self.alerts.has_alert_for(ev["id"]):
                    self.sync_alerts(ev)

    def remake_alerts(self, cids: set[str] | None = None) -> None:
        """A heads-up setting changed: re-make the heads-ups of those calendars (all, if None)."""
        rows = self.db.execute("SELECT * FROM calendar_events WHERE end > ?", (self.clock(),)).fetchall()
        for ev in rows:
            if cids is None or ev["calendar_id"] in cids:
                self.sync_alerts(dict(ev))
        self.version += 1

    # ---------------------------------------------------------------- when things happen
    @staticmethod
    def occurs_on(ev: dict, d: date) -> datetime | None:
        """When this event happens on day `d` (repeats and several-day events included), or None."""
        start = datetime.fromtimestamp(ev["start"])
        if d < start.date():
            return None
        if start.date() == d:
            return start
        if not ev["repeat"]:
            end = datetime.fromtimestamp(ev["end"])
            last = (end - timedelta(seconds=1)).date()
            return datetime.combine(d, datetime.min.time()) if d <= last else None
        rule, wd = ev["repeat"], d.weekday()
        ok = (rule == "daily" or (rule == "weekdays" and wd < 5) or (rule == "weekends" and wd >= 5) or
              (rule == "weekly" and wd == start.weekday()) or (rule.startswith("weekly:") and wd == int(rule[7:])))
        return datetime.combine(d, start.time()) if ok else None

    def events_on(self, d: date, events: list[dict] | None = None) -> list[tuple[datetime, dict]]:
        out = [(at, ev) for ev in (events if events is not None else self.all_events()) if (at := self.occurs_on(ev, d))]
        return sorted(out, key=lambda x: (not x[1]["all_day"], x[0]))

    def between(self, first: date, last: date, events: list[dict] | None = None) -> list[tuple[date, datetime, dict]]:
        evs = events if events is not None else self.all_events()
        out, d = [], first
        while d <= last:
            out += [(d, at, ev) for at, ev in self.events_on(d, evs)]
            d += timedelta(days=1)
        return out

    def prune(self, keep_days: float = 60) -> None:
        """Forget one-off events long past (repeating ones stay)."""
        self.db.execute("DELETE FROM calendar_events WHERE repeat = '' AND end < ? AND pending = ''",
                        (self.clock() - keep_days * 86400,))
        self.db.commit()
