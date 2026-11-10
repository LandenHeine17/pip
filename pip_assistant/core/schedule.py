"""Timers, alarms, reminders, and the heads-ups for calendar events (M1a step 4; FEAT-033–042).

The calendar itself is a module (pip_assistant/modules/calendar, D-96): it asks this schedule to
ring heads-ups ("Heads up: dentist in 30 minutes"), linked to the event by `event_id`.

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
import time
from datetime import datetime
from pathlib import Path

from ..common.messages import Message, new_id
from ..common import when
from ..common.db import connect

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id       TEXT PRIMARY KEY,
    kind     TEXT NOT NULL,       -- timer / alarm / reminder / event (a calendar heads-up)
    label    TEXT NOT NULL,       -- what it's for ("pasta", "take the bins out")
    due      REAL NOT NULL,       -- when it rings (Unix time)
    length   REAL,                -- timers: the length asked for, in seconds
    repeat   TEXT NOT NULL DEFAULT '',   -- '', daily, weekdays, weekends, weekly, weekly:<0-6>
    state    TEXT NOT NULL,       -- waiting / ringing / done / cancelled / missed
    event_id TEXT,                -- heads-ups: the calendar event it belongs to
    planned  TEXT,                -- the local clock time it was set for ('2027-03-14T02:30:00'); a snooze doesn't change it
    created  REAL NOT NULL,
    updated  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS alerts_due ON alerts (state, due);
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
        self.db = connect(path)
        self.db.executescript(SCHEMA)
        if "planned" not in {r[1] for r in self.db.execute("PRAGMA table_info(alerts)")}:
            self.db.execute("ALTER TABLE alerts ADD COLUMN planned TEXT")   # databases made before this column
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

    def get(self, alert_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        return dict(r) if r else None

    def add(self, kind: str, label: str, due: float, length: float | None = None, repeat: str = "",
            event_id: str | None = None, planned: datetime | None = None) -> dict:
        """Save an alert and read it back: Pip only says it's set once it really exists (PIP-NEW-039).
        planned: the clock time asked for, if known. A repeat comes back to it ("2:30 every day" stays 2:30,
        even after a night when 2:30 didn't exist because the clocks went forward)."""
        now = self.clock()
        aid = new_id()
        planned_txt = (planned or datetime.fromtimestamp(due)).isoformat(timespec="seconds")
        self.db.execute("INSERT INTO alerts (id, kind, label, due, length, repeat, state, event_id, planned, created, updated) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (aid, kind, label, due, length, repeat, "waiting", event_id, planned_txt, now, now))
        self.db.commit()
        row = self.get(aid)
        if not row or row["state"] != "waiting" or abs(row["due"] - due) > 0.01:
            raise RuntimeError("alert was not saved")
        self._changed("created", row)
        return row

    def update(self, alert_id: str, **fields) -> dict:
        fields["updated"] = self.clock()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE alerts SET {cols} WHERE id = ?", (*fields.values(), alert_id))
        self.db.commit()
        row = self.get(alert_id)
        if row is None:
            raise RuntimeError("alert not found")
        if fields.get("state") == "waiting" and alert_id in self.ringing:
            # Moved while ringing ("make it 7:30", "add 5 minutes"): it stops now and rings again at the
            # new time, like a cancel stops it.
            self.ringing.remove(alert_id)
            if not self.ringing and _loop_running():
                if self._ring_task and self._ring_task is not asyncio.current_task():
                    self._ring_task.cancel()
                asyncio.ensure_future(self._stop_sound())
                self._notify()
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

    # ---------------------------------------------------------------- heads-ups for other parts (the calendar module)
    def cancel_for(self, item_id: str) -> None:
        """Cancel the alerts linked to an item (an event that moved or was deleted)."""
        self.db.execute("UPDATE alerts SET state = 'cancelled', updated = ? WHERE event_id = ? AND state IN ('waiting','ringing')",
                        (self.clock(), item_id))
        self.db.commit()
        rung = [i for i in self.ringing if (self.get(i) or {}).get("event_id") == item_id]
        for i in rung:
            self.ringing.remove(i)
        if rung and not self.ringing and _loop_running():
            asyncio.ensure_future(self._stop_sound())
            self._notify()

    def has_alert_for(self, item_id: str) -> bool:
        return bool(self.db.execute("SELECT 1 FROM alerts WHERE event_id = ? AND state IN ('waiting','ringing')",
                                    (item_id,)).fetchone())

    def find(self, ref: str) -> tuple[str, dict] | None:
        """A timer, alarm or reminder from the short id Claude sees ('#a1b2c3'): ('alert', row)."""
        ref = ref.strip().lstrip("#").lower()
        if len(ref) < 4:
            return None
        hits = [("alert", a) for a in self.active(("timer", "alarm", "reminder")) if a["id"].startswith(ref)]
        return hits[0] if len(hits) == 1 else None

    @staticmethod
    def short(item_id: str) -> str:
        return "#" + item_id[:6]

    def prune(self, keep_days: float = 60) -> None:
        """Forget finished alerts long past."""
        self.db.execute("DELETE FROM alerts WHERE state NOT IN ('waiting','ringing') AND updated < ?",
                        (self.clock() - keep_days * 86400,))
        self.db.commit()

    # ---------------------------------------------------------------- what Pip knows (for Claude and the dashboard)
    def snapshot(self) -> dict:
        """For the dashboard. (Calendar events come from the calendar module; pip-core adds them.)"""
        now = self.clock()
        alerts = [{**a, "left_s": round(a["due"] - now, 1)} for a in self.active()]
        return {"alerts": alerts, "ringing": list(self.ringing), "missed": self.recent_missed(), "now": now,
                "snooze_minutes": self.snooze_min}

    def summary(self) -> str:
        """Timers, alarms and reminders, plainly, for Claude (sent privately, never logged)."""
        parts = []
        for a in self.active(("timer", "alarm", "reminder")):
            due = datetime.fromtimestamp(a["due"])
            if a["kind"] == "timer":
                parts.append(f"{self.short(a['id'])} timer '{a['label']}' with {when.say_left(a['due'] - self.clock())} left")
            else:
                rep = f" ({when.say_repeat(a['repeat'])})" if a["repeat"] else ""
                parts.append(f"{self.short(a['id'])} {a['kind']} '{a['label']}' at {due:%a %Y-%m-%d %H:%M}{rep}")
        out = ("Set: " + "; ".join(parts) + ".") if parts else "No timers, alarms or reminders set."
        ringing = [r for r in (self.get(i) for i in self.ringing) if r]
        if ringing:
            out = "RINGING NOW: " + "; ".join(f"{self.short(r['id'])} {r['kind']} '{r['label']}'" for r in ringing) + ". " + out
        elif self.last_rung and self.clock() - self.last_rung[1] < 15 * 60 and (r := self.get(self.last_rung[0])):
            out += f" Just rang: {self.short(r['id'])} {r['kind']} '{r['label']}'."
        return out

    # ---------------------------------------------------------------- ringing
    @staticmethod
    def announcement(a: dict) -> str:
        label = a["label"].strip()
        if a["kind"] == "timer":
            return "Your timer is done." if label in ("", "timer") else f"Your {label} timer is done."
        if a["kind"] == "alarm":
            # The time it was set for ("It's 7 AM"), not the clock now: it repeats while ringing (and in tests).
            return f"It's {when.say_clock(datetime.fromtimestamp(a['due']))}. Time to get up!" if label in ("", "alarm") \
                else f"Alarm: {label}."
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
        kind = (self.get(self.ringing[-1]) or {}).get("kind", "timer")
        await self._command("ears", "ears.ring", {"tone": self.tones.get(kind, "classic"),
                                                   "max_seconds": self.ring_s + 30})
        try:
            while self.ringing:
                rows = [r for r in (self.get(i) for i in self.ringing) if r]
                text = " ".join(dict.fromkeys(self.announcement(r) for r in rows))
                await self._command("ears", "ears.say", {"reason": "alarm"}, {"text": text})
                await asyncio.sleep(self.repeat_every)
                if self.clock() - started >= self.ring_s:
                    ids, self.ringing = list(self.ringing), []
                    for i in ids:
                        if r := self.get(i):
                            self._finish(r, "done")
                    await self._stop_sound()
                    self._notify()
                    await self._event("alert.unanswered", {"count": len(ids)})
                    return
        except asyncio.CancelledError:
            pass

    def _finish(self, a: dict, state: str) -> None:
        """Done (or missed): a repeating one moves on to its next time, keeping its clock time."""
        if a["repeat"]:
            # From the time it was set for, not when it last rang: a snoozed 7 AM alarm is still 7 AM tomorrow.
            base = datetime.fromisoformat(a["planned"]) if a.get("planned") else datetime.fromtimestamp(a["due"])
            nxt = when.next_repeat(a["repeat"], base, self._now_dt())
            if nxt:
                self.update(a["id"], state="waiting", due=nxt.timestamp(), planned=nxt.isoformat(timespec="seconds"))
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
            if r := self.get(i):
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
