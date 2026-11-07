"""The calendar module: Pip's own events, two-way sync with a pretend Google, privacy and sharing."""
import sqlite3
from datetime import datetime, timedelta

import pytest

from pip_assistant.common.modules import ROOT, read
from pip_assistant.core.modules import Call, Invalid, Registry
from pip_assistant.core.people import Match, People
from pip_assistant.core.schedule import Schedule
from pip_assistant.modules.google.testing import FakeGoogle, connect

NOW = datetime(2026, 11, 7, 9, 0)


class Clock:
    def __init__(self):
        self.t = NOW.timestamp()

    def __call__(self):
        return self.t


class NoVoices:
    available = False


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    clock, fake = Clock(), FakeGoogle()
    people = People(tmp_path / "pip.db", clock=clock, embedder=NoVoices())
    sched = Schedule(tmp_path / "pip.db", clock=clock)
    reg = Registry.load({}, infos=[read(ROOT / "google"), read(ROOT / "calendar")], schedule=sched, people=people,
                        db_path=tmp_path / "pip.db", transport=fake.transport(), clock=clock)
    return reg, reg.modules["calendar"], fake, people, sched, clock


def me(person) -> Call:
    return Call(speaker=Match(person["id"], person["name"], "sure", 0.9), now=NOW)


def say(reg, verb, a, call=None):
    act = reg.action(verb)
    call = call or Call(now=NOW)
    return act.do(act.check(a, call), call)


def test_old_events_move_into_the_module(tmp_path):
    db = sqlite3.connect(tmp_path / "pip.db")
    db.execute("CREATE TABLE events (id TEXT PRIMARY KEY, title TEXT, start REAL, all_day INTEGER, repeat TEXT, created REAL)")
    db.execute("INSERT INTO events VALUES ('abc123', 'dentist', ?, 0, '', 0)", (NOW.timestamp() + 86400,))
    db.commit()
    clock = Clock()
    sched = Schedule(tmp_path / "pip.db", clock=clock)
    reg = Registry.load({}, infos=[read(ROOT / "google"), read(ROOT / "calendar")], schedule=sched,
                        db_path=tmp_path / "pip.db", clock=clock)
    [ev] = reg.get("calendar").all_events()
    assert ev["id"] == "abc123" and ev["title"] == "dentist" and ev["end"] - ev["start"] == 3600
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'events'").fetchone()


async def test_pips_own_calendar_works_without_google(env):
    reg, cal, fake, people, sched, clock = env
    assert say(reg, "add:event", {"title": "dentist", "at": "2026-11-13T15:00", "heads_up_minutes": 30}) == \
        "Added dentist, Friday at 3 PM."
    assert "dentist Fri 2026-11-13 15:00–16:00" in reg.summary(Call(now=NOW))
    [h] = sched.active(("event",))
    assert datetime.fromtimestamp(h["due"]) == datetime(2026, 11, 13, 14, 30)
    with pytest.raises(Invalid):
        say(reg, "add:event", {"title": "dentist", "at": "2026-11-13T15:00", "calendar": "Sam"})   # no such person


async def test_two_way_sync_with_google(env):
    reg, cal, fake, people, sched, clock = env
    landen = people.add_person("Landen", "admin")
    fake.calendars["landen@example.com"] = {"summary": "Landen", "primary": True, "accessRole": "owner",
                                            "owner": "landen@example.com", "backgroundColor": "#0b8043",
                                            "defaultReminders": [{"method": "popup", "minutes": 10}]}
    fake.add_event("landen@example.com", summary="Chem lab", start={"dateTime": "2026-11-09T13:00:00-06:00"},
                   end={"dateTime": "2026-11-09T15:00:00-06:00"}, location="Hamilton Hall")
    fake.add_event("landen@example.com", summary="Doctor", visibility="private",
                   start={"dateTime": "2026-11-10T09:00:00-06:00"}, end={"dateTime": "2026-11-10T09:30:00-06:00"})
    say(reg, "add:event", {"title": "pizza night", "at": "2026-11-08T19:00"})      # on Pip's own calendar first
    acc = await connect(reg.modules["google"], fake, landen["id"], "landen@example.com")
    await cal.sync.run_once()
    titles = {e["title"]: e for e in cal.store.all_events()}
    assert {"Chem lab", "Doctor", "pizza night"} <= set(titles)
    # Pip's own upcoming events went into the household's (Landen's) Google calendar on first connect
    assert any(e["summary"] == "pizza night" for e in fake.events["landen@example.com"].values())
    # Private events: never told to Claude, no heads-up (D-99); others get Google's default 10-minute reminder
    summary = reg.summary(me(landen))
    assert "Chem lab" in summary and "Hamilton Hall" in summary and "Doctor" not in summary
    assert "Landen's calendar" in summary
    heads = {h["label"] for h in sched.active(("event",))}
    assert "Chem lab" in heads and "Doctor" not in heads
    # A voice-added event from Landen goes to his Google calendar, straight away on the next sync
    say(reg, "add:event", {"title": "gym", "at": "2026-11-11T07:00"}, me(landen))
    await cal.sync.run_once()
    assert any(e["summary"] == "gym" for e in fake.events["landen@example.com"].values())
    # Moved in Google → moved in Pip; deleted in Google → gone
    lab = next(e for e in fake.events["landen@example.com"].values() if e["summary"] == "Chem lab")
    lab.update(start={"dateTime": "2026-11-09T14:00:00-06:00"}, end={"dateTime": "2026-11-09T16:00:00-06:00"},
               etag='"moved"', updated=clock() + 10 ** 9)
    doctor = next(e for e in fake.events["landen@example.com"].values() if e["summary"] == "Doctor")
    doctor.update(status="cancelled", updated=clock() + 10 ** 9)
    await cal.sync.run_once()
    titles = {e["title"]: e for e in cal.store.all_events()}
    assert datetime.fromtimestamp(titles["Chem lab"]["start"]).hour == datetime.fromisoformat("2026-11-09T14:00:00-06:00").astimezone().hour
    assert "Doctor" not in titles
    # Changed in Pip → changed in Google
    ref = "#" + titles["Chem lab"]["id"][:6]
    assert say(reg, "change:event", {"id": ref, "location": "Room 101"}) == "Updated Chem lab."
    await cal.sync.run_once()
    assert next(e for e in fake.events["landen@example.com"].values() if e["summary"] == "Chem lab")["location"] == "Room 101"
    # Deleted in Pip → deleted in Google
    say(reg, "cancel:event", {"id": ref})
    await cal.sync.run_once()
    assert next(e for e in fake.events["landen@example.com"].values() if e["summary"] == "Chem lab")["status"] == "cancelled"


async def test_google_wins_a_conflict_and_offline_changes_wait(env):
    reg, cal, fake, people, sched, clock = env
    landen = people.add_person("Landen", "admin")
    fake.calendars["landen@example.com"] = {"summary": "Landen", "primary": True, "accessRole": "owner", "owner": "landen@example.com"}
    g = fake.add_event("landen@example.com", summary="study group", start={"dateTime": "2026-11-12T18:00:00-06:00"},
                       end={"dateTime": "2026-11-12T19:00:00-06:00"})
    await connect(reg.modules["google"], fake, landen["id"], "landen@example.com")
    await cal.sync.run_once()
    [ev] = [e for e in cal.store.all_events() if e["title"] == "study group"]
    cal.store.update_event(ev["id"], title="study group (Pip)")
    g.update(summary="study group (Google)", etag='"theirs"')     # changed in Google meanwhile
    await cal.sync.run_once()
    assert [e["title"] for e in cal.store.all_events()] == ["study group (Google)"]
    # Offline: the change waits, then goes when Pip is back
    fake.offline = True
    say(reg, "add:event", {"title": "laundry", "at": "2026-11-08T10:00"}, me(landen))
    await cal.sync.run_once()
    assert [e["pending"] for e in cal.store.all_events() if e["title"] == "laundry"] == ["add"]
    fake.offline = False
    await cal.sync.run_once()
    assert any(e["summary"] == "laundry" for e in fake.events["landen@example.com"].values())


async def test_a_roommate_can_keep_his_calendar_to_himself(env):
    reg, cal, fake, people, sched, clock = env
    landen, sam = people.add_person("Landen", "admin"), people.add_person("Sam")
    fake.calendars["sam@example.com"] = {"summary": "Sam", "primary": True, "accessRole": "owner", "owner": "sam@example.com"}
    fake.add_event("sam@example.com", summary="Sam's interview", start={"dateTime": "2026-11-09T10:00:00-06:00"},
                   end={"dateTime": "2026-11-09T11:00:00-06:00"})
    acc = await connect(reg.modules["google"], fake, sam["id"], "sam@example.com")
    await cal.sync.run_once()
    assert "Sam's interview" in reg.summary(me(landen))
    await reg.modules["google"].dashboard("sharing", {"id": acc["id"], "value": "me"})
    assert "Sam's interview" not in reg.summary(me(landen)) and "Sam's interview" not in reg.summary(Call(now=NOW))
    assert "Sam's interview" in reg.summary(me(sam))
    # "put it on Sam's calendar"
    say(reg, "add:event", {"title": "rent due", "date": "2026-11-15", "all_day": True, "calendar": "Sam"}, me(landen))
    await cal.sync.run_once()
    assert any(e["summary"] == "rent due" for e in fake.events["sam@example.com"].values())


async def test_the_dashboard_page(env):
    reg, cal, fake, people, sched, clock = env
    r = await cal.dashboard("add", {"title": "Dinner", "date": "2026-11-14", "start": "19:00", "end": "21:00",
                                    "location": "Home", "private": True, "heads_up": "15"})
    out = await cal.dashboard("range", {"from": "2026-11-01", "to": "2026-11-30"})
    [ev] = out["events"]
    assert ev["title"] == "Dinner" and ev["private"] and ev["location"] == "Home"   # the owner's own view shows it
    assert not sched.active(("event",))                                            # but private: no heads-up
    assert "Dinner" not in reg.summary(Call(now=NOW))
    await cal.dashboard("edit", {"id": r["id"], "title": "Dinner out", "date": "2026-11-14", "start": "20:00", "end": "22:00"})
    [ev] = (await cal.dashboard("range", {"from": "2026-11-14", "to": "2026-11-14"}))["events"]
    assert ev["title"] == "Dinner out" and datetime.fromtimestamp(ev["start"]).hour == 20 and not ev["private"]
    await cal.dashboard("delete", {"id": r["id"]})
    assert (await cal.dashboard("range", {"from": "2026-11-01", "to": "2026-11-30"}))["events"] == []
    with pytest.raises(ValueError):
        await cal.dashboard("range", {"from": "2026-01-01", "to": "2026-12-31"})


async def test_disconnecting_forgets_that_persons_calendars_here(env):
    reg, cal, fake, people, sched, clock = env
    sam = people.add_person("Sam")
    fake.calendars["sam@example.com"] = {"summary": "Sam", "primary": True, "accessRole": "owner", "owner": "sam@example.com"}
    fake.add_event("sam@example.com", summary="practice", start={"dateTime": "2026-11-09T10:00:00-06:00"},
                   end={"dateTime": "2026-11-09T11:00:00-06:00"})
    acc = await connect(reg.modules["google"], fake, sam["id"], "sam@example.com")
    await cal.sync.run_once()
    assert [e["title"] for e in cal.store.all_events()] == ["practice"]
    await reg.modules["google"].dashboard("disconnect", {"id": acc["id"]})
    assert cal.store.all_events() == [] and [c["id"] for c in cal.store.calendars()] == ["pip"]
    assert fake.events["sam@example.com"]                          # nothing deleted in Google
