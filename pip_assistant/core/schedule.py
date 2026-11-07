"""Timers, alarms, reminders and the household calendar (M1a step 4; FEAT-033–042).

Everything is saved in ~/pip-data/pip.db the moment it's made, so it survives restarts and power
cuts (FEAT-041). A small loop checks every half second for anything due. That loop never waits
for the internet or the AI, so alarms are never held up (PIP-NEW-078).

When something is due, Pip rings: pip-ears loops the ringtone and Pip says what it is ("Your
pasta timer is done."), repeating it every 30 seconds. It keeps ringing until someone answers
("okay", "stop", "I'm up", his name, "snooze"), it's shut off on the dashboard, or nobody has
answered after a few minutes (FEAT-037, 039).

Labels and reminder texts are what people said, so they live only in the database and in
messages' `private` part, never in the decision trace (D-34).
"""
import asyncio
import contextlib
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from ..common.messages import Message, new_id
from . import when

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id       TEXT PRIMARY KEY,
    kind     TEXT NOT NULL,       -- timer / alarm / reminder / event (a calendar heads-up)
    label    TEXT NOT NULL,       -- what it's for ("pasta", "take the bins out")
    due      REAL NOT NULL,       -- when it rings (Unix time)
    length   REAL,                -- timers: the length asked for, in seconds
    repeat   TEXT NOT NULL DEFAULT '',   -- '', daily, weekdays, weekends, weekly:<0-6>
    state    TEXT NOT NULL,       -- waiting / ringing / done / cancelled / missed
    event_id TEXT,
    created  REAL NOT NULL,
    updated  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS alerts_due ON alerts (state, due);
CREATE TABLE IF NOT EXISTS events (
    id        TEXT PRIMARY KEY,
    title     TEXT NOT NULL,
    start     REAL NOT NULL,      -- Unix time (all-day events: midnight that day)
    all_day   INTEGER NOT NULL DEFAULT 0,
    repeat    TEXT NOT NULL DEFAULT '',
    created   REAL NOT NULL
);
"""


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


class Schedule:
    def __init__(self, path: Path, bus=None, cfg: dict | None = None, clock=time.time):
        s = (cfg or {}).get("alarms", {})
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.bus = bus
        self.clock = clock
        self.ring_s = s.get("ring_minutes", 5.0) * 60
        self.repeat_every = s.get("announce_every_seconds", 30.0)
        self.snooze_min = s.get("snooze_minutes", 5.0)
        self.grace_s = s.get("missed_grace_minutes", 30.0) * 60
        self.tones = s.get("ringtones", {"timer": "kitchen", "alarm": "classic", "reminder": "chimes", "event": "chimes"})
        self.ringing: list[str] = []          # ids ringing right now, oldest first
        self._ring_task: asyncio.Task | None = None
        self._wake = asyncio.Event()          # "something changed": re-check what's due now
        self.last_rung: tuple[str, float] | None = None  # (id, when): "snooze that" / "change that alarm"
        self.watchers: list = []              # called with no arguments when ringing starts or stops (Pip's state)

    # ---------------------------------------------------------------- storage
    def _now_dt(self) -> datetime:
        return datetime.fromtimestamp(self.clock())

    def _row(self, alert_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        return dict(r) if r else None

    def add(self, kind: str, label: str, due: float, length: float | None = None, repeat: str = "",
            event_id: str | None = None) -> dict:
        """Save an alert and read it back: Pip only says it's set once it really exists (PIP-NEW-039)."""
        now = self.clock()
        aid = new_id()
        self.db.execute("INSERT INTO alerts (id, kind, label, due, length, repeat, state, event_id, created, updated) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)", (aid, kind, label, due, length, repeat, "waiting", event_id, now, now))
        self.db.commit()
        row = self._row(aid)
        if not row or row["state"] != "waiting" or abs(row["due"] - due) > 0.01:
            raise RuntimeError("alert was not saved")
        self._changed("created", row)
        return row

    def update(self, alert_id: str, **fields) -> dict:
        fields["updated"] = self.clock()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE alerts SET {cols} WHERE id = ?", (*fields.values(), alert_id))
        self.db.commit()
        row = self._row(alert_id)
        if row is None:
            raise RuntimeError("alert not found")
        self._changed("updated", row)
        return row

    def active(self, kinds: tuple[str, ...] = ("timer", "alarm", "reminder", "event")) -> list[dict]:
        q = f"SELECT * FROM alerts WHERE state IN ('waiting','ringing') AND kind IN ({','.join('?' * len(kinds))}) ORDER BY due"
        return [dict(r) for r in self.db.execute(q, kinds)]

    def recent_missed(self, hours: float = 12) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM alerts WHERE state = 'missed' AND updated > ? ORDER BY due",
                                                  (self.clock() - hours * 3600,))]

    def cancel(self, alert_id: str) -> dict:
        row = self.update(alert_id, state="cancelled")
        if alert_id in self.ringing:
            self.ringing.remove(alert_id)
            if not self.ringing and _loop_running():
                asyncio.ensure_future(self._stop_sound())
                self._notify()
        return row

    # ---------------------------------------------------------------- calendar
    def add_event(self, title: str, start: datetime, all_day: bool = False, repeat: str = "",
                  heads_up_before: float | None = None) -> dict:
        now = self.clock()
        eid = new_id()
        self.db.execute("INSERT INTO events (id, title, start, all_day, repeat, created) VALUES (?,?,?,?,?,?)",
                        (eid, title, start.timestamp(), int(all_day), repeat, now))
        self.db.commit()
        ev = self.event(eid)
        if not ev:
            raise RuntimeError("event was not saved")
        if heads_up_before is not None:
            # All-day events' heads-ups count back from 8 AM on the day (FEAT-036)
            base = start.replace(hour=8, minute=0) if all_day else start
            due = base.timestamp() - heads_up_before
            if due > now:
                self.add("event", title, due, length=heads_up_before, repeat=repeat, event_id=eid)
        self._changed("event", {"id": eid, "kind": "event"})
        return ev

    def event(self, eid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
        return dict(r) if r else None

    def remove_event(self, eid: str) -> None:
        """Deleting an event removes its heads-up too (FEAT-036)."""
        self.db.execute("DELETE FROM events WHERE id = ?", (eid,))
        self.db.execute("UPDATE alerts SET state = 'cancelled', updated = ? WHERE event_id = ? AND state IN ('waiting','ringing')",
                        (self.clock(), eid))
        self.db.commit()
        self._changed("event", {"id": eid, "kind": "event"})

    def update_event(self, eid: str, start: datetime, all_day: bool | None = None) -> dict:
        """Move an event. Its heads-up moves by the same amount, so it still warns the same time before."""
        ev = self.event(eid)
        if not ev:
            raise RuntimeError("event not found")
        shift = start.timestamp() - ev["start"]
        all_day = bool(ev["all_day"]) if all_day is None else all_day
        self.db.execute("UPDATE events SET start = ?, all_day = ? WHERE id = ?", (start.timestamp(), int(all_day), eid))
        self.db.execute("UPDATE alerts SET due = due + ?, updated = ? WHERE event_id = ? AND state = 'waiting'",
                        (shift, self.clock(), eid))
        self.db.commit()
        ev = self.event(eid)
        if not ev or abs(ev["start"] - start.timestamp()) > 0.01:
            raise RuntimeError("event was not moved")
        self._changed("event", {"id": eid, "kind": "event"})
        return ev

    def find(self, ref: str) -> tuple[str, dict] | None:
        """An item from the short id Claude sees ('#a1b2c3'): ('alert', row) or ('event', row)."""
        ref = ref.strip().lstrip("#").lower()
        if len(ref) < 4:
            return None
        hits = [("alert", a) for a in self.active() if a["id"].startswith(ref)] + \
               [("event", e) for e in self.all_events() if e["id"].startswith(ref)]
        return hits[0] if len(hits) == 1 else None

    @staticmethod
    def short(item_id: str) -> str:
        return "#" + item_id[:6]

    def all_events(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM events ORDER BY start")]

    @staticmethod
    def occurs_on(ev: dict, d: date) -> datetime | None:
        """When this event happens on day `d` (repeating events included), or None."""
        start = datetime.fromtimestamp(ev["start"])
        if d < start.date():
            return None
        if start.date() == d:
            return start
        rule, wd = ev["repeat"], d.weekday()
        ok = (rule == "daily" or (rule == "weekdays" and wd < 5) or (rule == "weekends" and wd >= 5) or
              (rule == "weekly" and wd == start.weekday()) or (rule.startswith("weekly:") and wd == int(rule[7:])))
        return datetime.combine(d, start.time()) if ok else None

    def events_on(self, d: date) -> list[tuple[datetime, dict]]:
        out = [(at, ev) for ev in self.all_events() if (at := self.occurs_on(ev, d))]
        return sorted(out, key=lambda x: (not x[1]["all_day"], x[0]))

    def prune_events(self, keep_days: float = 60) -> None:
        """Forget one-off events long past (repeating ones stay)."""
        self.db.execute("DELETE FROM events WHERE repeat = '' AND start < ?", (self.clock() - keep_days * 86400,))
        self.db.execute("DELETE FROM alerts WHERE state NOT IN ('waiting','ringing') AND updated < ?",
                        (self.clock() - keep_days * 86400,))
        self.db.commit()

    # ---------------------------------------------------------------- what Pip knows (for Claude and the dashboard)
    def snapshot(self) -> dict:
        now = self.clock()
        today = self._now_dt().date()
        alerts = [{**a, "left_s": round(a["due"] - now, 1)} for a in self.active()]
        upcoming = []
        for i in range(14):
            d = today + timedelta(days=i)
            for at, ev in self.events_on(d):
                upcoming.append({"id": ev["id"], "title": ev["title"], "at": at.timestamp(), "all_day": bool(ev["all_day"]),
                                 "repeat": ev["repeat"]})
        return {"alerts": alerts, "ringing": list(self.ringing), "events": upcoming,
                "missed": self.recent_missed(), "now": now, "snooze_minutes": self.snooze_min}

    def summary(self) -> str:
        """A short plain description for Claude, so it can talk about them (sent privately, never logged)."""
        now_dt = self._now_dt()
        parts = []
        for a in self.active():
            due = datetime.fromtimestamp(a["due"])
            if a["kind"] == "timer":
                parts.append(f"{self.short(a['id'])} timer '{a['label']}' with {when.say_left(a['due'] - self.clock())} left")
            else:
                rep = f" ({when.say_repeat(a['repeat'])})" if a["repeat"] else ""
                parts.append(f"{self.short(a['id'])} {a['kind']} '{a['label']}' at {due:%a %Y-%m-%d %H:%M}{rep}")
        evs, seen = [], set()
        for i in range(14):
            d = now_dt.date() + timedelta(days=i)
            for at, ev in self.events_on(d):
                if ev["id"] in seen:
                    continue
                seen.add(ev["id"])
                rep = f" ({when.say_repeat(ev['repeat'])})" if ev["repeat"] else ""
                evs.append(f"{self.short(ev['id'])} {ev['title']} {at:%a %Y-%m-%d}" + (" all day" if ev["all_day"] else f" {at:%H:%M}") + rep)
        out = ("Set: " + "; ".join(parts) + ".") if parts else "No timers, alarms or reminders set."
        ringing = [r for r in (self._row(i) for i in self.ringing) if r]
        if ringing:
            out = "RINGING NOW: " + "; ".join(f"{self.short(r['id'])} {r['kind']} '{r['label']}'" for r in ringing) + ". " + out
        elif self.last_rung and self.clock() - self.last_rung[1] < 15 * 60 and (r := self._row(self.last_rung[0])):
            out += f" Just rang: {self.short(r['id'])} {r['kind']} '{r['label']}'."
        out += (" Calendar, next two weeks: " + "; ".join(evs) + ".") if evs else " Nothing on the calendar in the next two weeks."
        return out

    # ---------------------------------------------------------------- ringing
    @staticmethod
    def announcement(a: dict) -> str:
        label = a["label"].strip()
        if a["kind"] == "timer":
            return "Your timer is done." if label in ("", "timer") else f"Your {label} timer is done."
        if a["kind"] == "alarm":
            return f"It's {when.say_clock(datetime.now())}. Time to get up!" if label in ("", "alarm") else f"Alarm: {label}."
        if a["kind"] == "event":
            before = a.get("length") or 0
            return f"Heads up: {label} is starting now." if before < 60 else f"Heads up: {label} in {when.say_duration(before)}."
        return f"Reminder: {label}."

    async def run(self) -> None:
        """Check what's due, forever. Missed while Pip was off? Ring if recent, else note it (FEAT-041)."""
        self._startup_catch_up()
        while True:
            try:
                await self._tick()
            except Exception:
                log.exception("schedule check failed")
            self._wake.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), 0.5)

    def _startup_catch_up(self) -> None:
        now = self.clock()
        for a in [dict(r) for r in self.db.execute("SELECT * FROM alerts WHERE state IN ('waiting','ringing') AND due <= ?", (now,))]:
            if a["state"] == "ringing" or now - a["due"] <= self.grace_s:
                self.update(a["id"], state="waiting")  # rings on the first check
            else:
                self._finish(a, "missed")

    async def _tick(self) -> None:
        now = self.clock()
        due = [dict(r) for r in self.db.execute("SELECT * FROM alerts WHERE state = 'waiting' AND due <= ? ORDER BY due", (now,))]
        if not due:
            return
        for a in due:
            self.update(a["id"], state="ringing")
            self.ringing.append(a["id"])
            self.last_rung = (a["id"], self.clock())
            await self._event("alert.ringing", {"id": a["id"], "kind": a["kind"]}, {"label": a["label"]})
        if self._ring_task and not self._ring_task.done():
            self._ring_task.cancel()  # a new one takes over: announce it now, ring time starts again
        self._ring_task = asyncio.create_task(self._ring())
        self._notify()

    async def _ring(self) -> None:
        started = self.clock()
        kind = (self._row(self.ringing[-1]) or {}).get("kind", "timer")
        await self._command("ears", "ears.ring", {"tone": self.tones.get(kind, "classic"),
                                                   "max_seconds": self.ring_s + 30})
        try:
            while self.ringing:
                rows = [r for r in (self._row(i) for i in self.ringing) if r]
                text = " ".join(dict.fromkeys(self.announcement(r) for r in rows))
                await self._command("ears", "ears.say", {"reason": "alarm"}, {"text": text})
                await asyncio.sleep(self.repeat_every)
                if self.clock() - started >= self.ring_s:
                    ids = list(self.ringing)
                    for i in ids:
                        if r := self._row(i):
                            self._finish(r, "done")
                    self.ringing.clear()
                    await self._stop_sound()
                    self._notify()
                    await self._event("alert.unanswered", {"count": len(ids)})
                    return
        except asyncio.CancelledError:
            pass

    def _finish(self, a: dict, state: str) -> None:
        """Done (or missed): a repeating one moves on to its next time, keeping its clock time."""
        if a["repeat"]:
            nxt = when.next_repeat(a["repeat"], datetime.fromtimestamp(a["due"]), self._now_dt())
            if nxt:
                self.update(a["id"], state="waiting", due=nxt.timestamp())
                if state == "missed":
                    self.db.execute("INSERT INTO alerts (id, kind, label, due, length, repeat, state, event_id, created, updated) "
                                    "VALUES (?,?,?,?,?,?,?,?,?,?)", (new_id(), a["kind"], a["label"], a["due"], a["length"], "",
                                                                     "missed", a["event_id"], self.clock(), self.clock()))
                    self.db.commit()
                return
        self.update(a["id"], state=state)

    async def acknowledge(self, how: str) -> int:
        """Someone answered: stop ringing. Returns how many were stopped."""
        ids, self.ringing = list(self.ringing), []
        if self._ring_task:
            self._ring_task.cancel()
        for i in ids:
            if r := self._row(i):
                self._finish(r, "done")
        await self._stop_sound()
        self._notify()
        if ids:
            await self._event("alert.stopped", {"how": how, "count": len(ids)})
        return len(ids)

    async def snooze(self, minutes: float | None = None, how: str = "voice") -> int:
        """Ring again later: the same alert, not a copy, so repeats aren't doubled (FEAT-039)."""
        minutes = minutes or self.snooze_min
        ids, self.ringing = list(self.ringing), []
        if self._ring_task:
            self._ring_task.cancel()
        for i in ids:
            self.update(i, state="waiting", due=self.clock() + minutes * 60)
        await self._stop_sound()
        self._notify()
        if ids:
            await self._event("alert.snoozed", {"how": how, "minutes": minutes, "count": len(ids)})
        return len(ids)

    async def _stop_sound(self) -> None:
        await self._command("ears", "ears.ring_stop")
        await self._command("ears", "ears.stop")

    # ---------------------------------------------------------------- messages
    def _notify(self) -> None:
        for w in self.watchers:
            try:
                w()
            except Exception:
                log.exception("schedule watcher failed")

    def _changed(self, what: str, row: dict) -> None:
        self._wake.set()
        if self.bus and _loop_running():
            asyncio.ensure_future(self._event("alert.changed", {"what": what, "kind": row.get("kind"),
                                                                "state": row.get("state")}))

    async def _event(self, msg_type: str, data: dict, private: dict | None = None) -> None:
        if self.bus:
            await self.bus.publish(Message("event", msg_type, source="core", data=data, private=private or {}))

    async def _command(self, target: str, msg_type: str, data: dict | None = None, private: dict | None = None) -> None:
        if self.bus:
            await self.bus.route(Message("command", msg_type, source="core", target=target,
                                         data=data or {}, private=private or {}))
