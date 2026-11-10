"""The calendar module: Pip's own events, two-way sync with a pretend Google, privacy and sharing."""
import sqlite3
from datetime import datetime

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


def look(reg, call=None, first="2026-11-07", last="2026-12-06"):
    """What Claude gets when it looks the calendar up (ACTION look)."""
    return say(reg, "look:calendar", {"from": first, "to": last}, call)


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
    assert "dentist Fri 2026-11-13 15:00–16:00" in look(reg)
    assert "Fri 1" in reg.summary(Call(now=NOW))      # the short summary only counts it (D-102)
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
    summary = look(reg, me(landen))
    assert "Chem lab" in summary and "Hamilton Hall" in summary and "Doctor" not in summary
    assert "Landen's main (household)" in reg.summary(me(landen))
    heads = {h["label"] for h in sched.active(("event",))}
    assert "Chem lab" not in heads and "Doctor" not in heads     # only the calendar's automatic reminder: no heads-up (D-109)
    main = next(c for c in cal.store.calendars() if c["is_primary"])
    await cal.dashboard("set_calendar", {"id": main["id"], "field": "heads_up", "value": "all"})
    heads = {h["label"] for h in sched.active(("event",))}
    assert "Chem lab" in heads and "Doctor" not in heads         # "every event": now it gets one; private never
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
    assert "Sam's interview" in look(reg, me(landen))
    await reg.modules["google"].dashboard("sharing", {"id": acc["id"], "value": "me"})
    assert "Sam's interview" not in look(reg, me(landen)) and "Sam's interview" not in look(reg, Call(now=NOW))
    assert "Sam's interview" in look(reg, me(sam))
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
    assert "Dinner" not in look(reg)
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


# ---------------------------------------------------------------- D-102: a short summary, and looking further ahead
async def test_claude_gets_a_short_calendar_and_looks_further_ahead(env):
    reg, cal, fake, people, sched, clock = env
    say(reg, "add:event", {"title": "lunch", "at": "2026-11-07T12:00"})          # today
    cal.store.add_event("breakfast", datetime(2026, 11, 7, 8, 0), end=datetime(2026, 11, 7, 8, 45))   # already over
    say(reg, "add:event", {"title": "brunch", "at": "2026-11-08T11:00"})         # tomorrow
    for d in (10, 10, 12):
        say(reg, "add:event", {"title": f"class {d}", "at": f"2026-11-{d}T09:00"})
    say(reg, "add:event", {"title": "far away", "at": "2026-11-28T09:00"})
    full = reg.summary(Call(now=NOW))
    assert "lunch" in full and "brunch" in full and "Tue 2" in full and "Thu 1" in full
    assert "class" not in full and "far away" not in full and "breakfast" not in full
    join = reg.summary(Call(now=NOW, kind="join"))
    assert "lunch" in join and "brunch" not in join and "Calendars" not in join and len(join) < len(full)
    assert "far away Sat 2026-11-28" in say(reg, "look:calendar", {"date": "2026-11-28"})
    assert "nothing" in say(reg, "look:calendar", {"from": "2026-11-29", "to": "2026-11-30"})
    with pytest.raises(Invalid):
        say(reg, "look:calendar", {"from": "2026-11-01", "to": "2027-01-30"})     # at most a month at a time


async def test_the_real_summary_stays_small(env):
    """~100 events in two weeks (like the owner's) → only a few hundred tokens per message."""
    reg, cal, fake, people, sched, clock = env
    for d in range(7, 21):
        for h in range(9, 16):
            say(reg, "add:event", {"title": f"meeting with the project group {h}", "at": f"2026-11-{d:02d}T{h:02d}:00",
                                   "location": "Hamilton Hall"})
    assert len(reg.summary(Call(now=NOW))) < 1800                     # was ~9,000 characters
    assert len(reg.summary(Call(now=NOW, kind="join"))) < 900


# ---------------------------------------------------------------- D-103: knowing and managing calendars
async def google_with_two_calendars(env, narrow=False):
    reg, cal, fake, people, sched, clock = env
    landen = people.add_person("Landen", "admin")
    fake.narrow = narrow
    fake.calendars["landen@example.com"] = {"summary": "landen@example.com", "primary": True, "accessRole": "owner",
                                            "owner": "landen@example.com"}
    fake.calendars["work1"] = {"summary": "Work", "accessRole": "owner", "owner": "landen@example.com"}
    fake.calendars["shared1"] = {"summary": "Abby & Landen", "accessRole": "writer", "owner": "landen@example.com"}
    fake.calendars["hol"] = {"summary": "Holidays", "accessRole": "reader", "owner": "landen@example.com"}
    fake.add_event("work1", summary="standup", start={"dateTime": "2026-11-07T15:00:00-06:00"},
                   end={"dateTime": "2026-11-07T15:15:00-06:00"})
    fake.add_event("hol", summary="Veterans Day", start={"date": "2026-11-11"}, end={"date": "2026-11-12"})
    await connect(reg.modules["google"], fake, landen["id"], "landen@example.com")
    await cal.sync.run_once()
    return landen


async def test_pip_knows_which_calendar_and_moves_events(env):
    reg, cal, fake, people, sched, clock = env
    landen = await google_with_two_calendars(env)
    s = reg.summary(me(landen))
    assert "Landen's main (household)" in s and "Landen: Work" in s and "Landen: Holidays (read-only)" in s
    assert "Abby & Landen (shared with them)" in s and "standup" in s and "[Landen: Work]" in s
    # Adding to a named calendar
    assert say(reg, "add:event", {"title": "gym", "at": "2026-11-07T17:00", "calendar": "work"}, me(landen)) == \
        "Added gym, today at 5 PM, on Landen's Work calendar."
    with pytest.raises(Invalid):
        say(reg, "add:event", {"title": "x", "at": "2026-11-07T17:00", "calendar": "Holidays"}, me(landen))  # read-only
    with pytest.raises(Invalid):
        say(reg, "add:event", {"title": "x", "at": "2026-11-07T17:00", "calendar": "Gym"}, me(landen))       # no such one
    await cal.sync.run_once()
    # Moving within the account: Google's own move (keeps everything)
    standup = next(e for e in cal.store.all_events() if e["title"] == "standup")
    said = await say(reg, "change:event", {"id": "#" + standup["id"][:6], "calendar": "Landen's main"}, me(landen))
    assert said == "Moved standup to Landen's main."
    assert any(e["summary"] == "standup" and e["status"] != "cancelled" for e in fake.events["landen@example.com"].values())
    assert cal.store.event(standup["id"])["calendar_id"].endswith(":landen@example.com")
    # From Pip's own calendar to a Google one: made there, gone here
    cal.store.add_event("pizza", datetime(2026, 11, 9, 19, 0), calendar_id="pip")
    pizza = next(e for e in cal.store.all_events() if e["title"] == "pizza")
    assert await say(reg, "change:event", {"id": "#" + pizza["id"][:6], "calendar": "Work"}) == "Moved pizza to Landen: Work."
    await cal.sync.run_once()
    assert any(e["summary"] == "pizza" for e in fake.events["work1"].values())
    assert [e["calendar_id"] for e in cal.store.all_events() if e["title"] == "pizza"] != ["pip"]


async def test_making_renaming_colouring_and_deleting_calendars(env):
    reg, cal, fake, people, sched, clock = env
    landen = await google_with_two_calendars(env)
    assert await say(reg, "add:calendar", {"name": "Gym", "color": "green"}, me(landen)) == "Made a new calendar: Landen: Gym."
    gid = next(k for k, c in fake.calendars.items() if c["summary"] == "Gym")
    assert fake.calendars[gid]["backgroundColor"] == "#0b8043"
    assert await say(reg, "change:calendar", {"calendar": "gym", "name": "Fitness"}, me(landen)) == "Renamed Landen: Gym to Fitness."
    assert fake.calendars[gid]["summary"] == "Fitness"
    await say(reg, "change:calendar", {"calendar": "Fitness", "color": "purple"}, me(landen))
    assert fake.calendars[gid]["backgroundColor"] == "#8e24aa"
    assert await say(reg, "change:calendar", {"calendar": "Fitness", "shown": False}, me(landen)) == "Hiding Landen: Fitness."
    assert "Fitness (hidden)" in reg.summary(me(landen))
    # Deleting: only after a spoken yes; main calendars never; a shared one only leaves their list
    with pytest.raises(Invalid):
        say(reg, "cancel:calendar", {"calendar": "Fitness"}, me(landen))
    with pytest.raises(Invalid):
        say(reg, "cancel:calendar", {"calendar": "Landen's main", "confirmed": True}, me(landen))
    assert await say(reg, "cancel:calendar", {"calendar": "Fitness", "confirmed": True}, me(landen)) == "Deleted Landen: Fitness."
    assert gid not in fake.calendars and "Fitness" not in reg.summary(me(landen))
    assert await say(reg, "cancel:calendar", {"calendar": "Abby & Landen", "confirmed": True}, me(landen)) == \
        "Took Landen: Abby & Landen off the list."
    # The same from the dashboard
    r = await cal.dashboard("new_calendar", {"name": "Trips", "color": "#039be5"})
    assert r["said"] == "Made a new calendar: Landen: Trips."
    trips = next(c for c in cal.calendars_view() if c["name"] == "Trips")
    await cal.dashboard("edit_calendar", {"id": trips["id"], "name": "Travel"})
    await cal.dashboard("delete_calendar", {"id": trips["id"]})
    assert not any(c["name"] in ("Trips", "Travel") for c in cal.calendars_view())


async def test_without_the_wider_permission_pip_says_how_and_keeps_syncing(env):
    reg, cal, fake, people, sched, clock = env
    landen = await google_with_two_calendars(env, narrow=True)
    said = await say(reg, "add:calendar", {"name": "Gym"}, me(landen))
    assert "reconnect Google" in said and "Select all" in said
    assert reg.modules["google"].api.accounts()[0]["status"] == "ok"     # the account itself is fine
    assert (await reg.modules["google"].dashboard("status", {}))["accounts"][0]["can_manage"] is False
    with pytest.raises(ValueError, match="reconnect"):
        await cal.dashboard("new_calendar", {"name": "Gym"})


# ---------------------------------------------------------------- D-104: ignored calendars
async def test_an_ignored_calendar_is_gone_everywhere_until_brought_back(env):
    reg, cal, fake, people, sched, clock = env
    landen = await google_with_two_calendars(env)
    hol = next(c for c in cal.calendars_view() if c["name"] == "Holidays")
    assert "Veterans Day" in say(reg, "look:calendar", {"from": "2026-11-11"})
    await cal.dashboard("ignore", {"id": hol["id"], "value": True})
    await cal.sync.run_once()
    assert not any(c["name"] == "Holidays" for c in cal.calendars_view())             # no tick box on the calendar page
    assert "Holidays" not in reg.summary(me(landen))
    assert "Veterans Day" not in say(reg, "look:calendar", {"from": "2026-11-11"})
    assert not cal.store.db.execute("SELECT 1 FROM calendar_events WHERE calendar_id = ?", (hol["id"],)).fetchone()
    assert [c["ignored"] for c in (await cal.dashboard("all_calendars", {}))["calendars"] if c["name"] == "Holidays"] == [True]
    with pytest.raises(Invalid):
        say(reg, "look:calendar", {"from": "2026-11-11", "calendar": "Holidays"})
    # By voice too; bringing it back is on the Google page
    assert "ignore" in await say(reg, "change:calendar", {"calendar": "Abby & Landen", "ignored": True}, me(landen))
    await cal.dashboard("ignore", {"id": hol["id"], "value": False})
    await cal.sync.run_once()
    assert "Veterans Day" in say(reg, "look:calendar", {"from": "2026-11-11"})


def test_an_older_database_is_upgraded(tmp_path, monkeypatch):
    """Installing over the Pi's existing pip.db: the new columns are added (a module may alter its own tables)."""
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    db = sqlite3.connect(tmp_path / "pip.db")
    db.execute("CREATE TABLE google_accounts (id TEXT PRIMARY KEY, person_id TEXT NOT NULL, email TEXT NOT NULL DEFAULT '', "
               "status TEXT NOT NULL DEFAULT 'ok', sharing TEXT NOT NULL DEFAULT 'everyone', created REAL NOT NULL, "
               "last_sync REAL, error TEXT NOT NULL DEFAULT '')")
    db.execute("CREATE TABLE calendar_calendars (id TEXT PRIMARY KEY, account_id TEXT NOT NULL DEFAULT '', google_id TEXT NOT NULL "
               "DEFAULT '', name TEXT NOT NULL, color TEXT NOT NULL DEFAULT '#6c5ce7', ticked INTEGER NOT NULL DEFAULT 1, "
               "writable INTEGER NOT NULL DEFAULT 1, is_primary INTEGER NOT NULL DEFAULT 0, sync_token TEXT NOT NULL DEFAULT '', reminder REAL)")
    db.commit()
    reg = Registry.load({}, infos=[read(ROOT / "google"), read(ROOT / "calendar")], db_path=tmp_path / "pip.db")
    assert set(reg.modules) == {"google", "calendar"}
    assert [c["ignored"] for c in reg.modules["calendar"].calendars_view(ignored=True)] == [False]


# ---------------------------------------------------------------- D-109: an account's settings
async def classes_and_dentist(env):
    """A main calendar with an automatic 30-minute reminder: classes use it, the dentist has its own."""
    reg, cal, fake, people, sched, clock = env
    landen = people.add_person("Landen", "admin")
    fake.calendars["landen@example.com"] = {"summary": "landen@example.com", "primary": True, "accessRole": "owner",
                                            "owner": "landen@example.com",
                                            "defaultReminders": [{"method": "popup", "minutes": 30}]}
    fake.calendars["fam"] = {"summary": "Family", "accessRole": "owner", "owner": "landen@example.com"}
    for d in (9, 10, 11):
        fake.add_event("landen@example.com", summary=f"Calc class {d}", start={"dateTime": f"2026-11-{d:02d}T10:00:00-06:00"},
                       end={"dateTime": f"2026-11-{d:02d}T11:00:00-06:00"})
    fake.add_event("landen@example.com", summary="Dentist", start={"dateTime": "2026-11-09T15:00:00-06:00"},
                   end={"dateTime": "2026-11-09T16:00:00-06:00"},
                   reminders={"useDefault": False, "overrides": [{"method": "popup", "minutes": 60}]})
    fake.add_event("fam", summary="Mom's flight", start={"dateTime": "2026-11-10T07:30:00-06:00"},
                   end={"dateTime": "2026-11-10T09:00:00-06:00"},
                   reminders={"useDefault": False, "overrides": [{"method": "popup", "minutes": 60}]})
    acc = await connect(reg.modules["google"], fake, landen["id"], "landen@example.com")
    await cal.sync.run_once()
    main = next(c for c in cal.store.calendars() if c["is_primary"])
    fam = next(c for c in cal.store.calendars() if c["name"] == "Family")
    return acc, main, fam


def heads(sched):
    return sorted(h["label"] for h in sched.active(("event",)))


async def test_heads_ups_only_for_events_with_their_own_reminder_by_default(env):
    reg, cal, fake, people, sched, clock = env
    acc, main, fam = await classes_and_dentist(env)
    local = lambda iso: datetime.fromisoformat(iso).astimezone().replace(tzinfo=None)
    # Classes only have the calendar's automatic reminder: no heads-up. The dentist and Mom's flight have their own.
    # (Mom's flight at 7:30 has its heads-up at 6:30: night, so skipped.)
    night_hour = local("2026-11-10T06:30:00-06:00").hour
    assert heads(sched) == (["Dentist"] if night_hour < 7 or night_hour >= 23 else ["Dentist", "Mom's flight"])
    await cal.dashboard("set_pref", {"account": acc["id"], "key": "night", "value": False})
    assert heads(sched) == ["Dentist", "Mom's flight"]
    await cal.dashboard("set_calendar", {"id": fam["id"], "field": "heads_up", "value": "off"})
    assert heads(sched) == ["Dentist"]
    await cal.dashboard("set_calendar", {"id": main["id"], "field": "heads_up", "value": "all"})
    assert heads(sched) == ["Calc class 10", "Calc class 11", "Calc class 9", "Dentist"]
    await cal.dashboard("set_calendar", {"id": main["id"], "field": "heads_up", "value": "15"})
    dentist = next(h for h in sched.active(("event",)) if h["label"] == "Dentist")
    assert datetime.fromtimestamp(dentist["due"]) == local("2026-11-09T14:45:00-06:00")
    with pytest.raises(ValueError):
        await cal.dashboard("set_calendar", {"id": main["id"], "field": "heads_up", "value": "7"})


async def test_the_dashboard_lists_and_household_choice(env):
    reg, cal, fake, people, sched, clock = env
    acc, main, fam = await classes_and_dentist(env)
    assert any(e["title"].startswith("Calc") for e in cal.upcoming(14))
    await cal.dashboard("set_calendar", {"id": main["id"], "field": "in_list", "value": False})
    assert [e["title"] for e in cal.upcoming(14)] == ["Mom's flight"]          # classes left out of the two-week list
    assert cal.sync.household() == main["id"]
    r = await cal.dashboard("set_pref", {"account": acc["id"], "key": "household", "value": fam["id"]})
    assert r["household_now"] == fam["id"] and cal.sync.household() == fam["id"]
    assert say(reg, "add:event", {"title": "game night", "at": "2026-11-12T19:00"}) == "Added game night, Thursday at 7 PM."
    with pytest.raises(ValueError):
        await cal.dashboard("set_pref", {"account": acc["id"], "key": "household", "value": "pip"})   # not this account's
    s = await cal.dashboard("account_settings", {"account": acc["id"]})
    assert {c["name"] for c in s["calendars"]} == {"landen@example.com", "Family"} and s["night"] is True


async def test_the_timers_list_shows_only_the_next_day_of_heads_ups(env, tmp_path):
    from pip_assistant.core.dashapi import alerts_snapshot
    reg, cal, fake, people, sched, clock = env
    acc, main, fam = await classes_and_dentist(env)
    await cal.dashboard("set_calendar", {"id": main["id"], "field": "heads_up", "value": "all"})
    sched.add("timer", "pasta", clock() + 600, length=600)
    snap = alerts_snapshot(sched, reg)
    soon = [a["label"] for a in snap["alerts"]]
    assert "pasta" in soon and all(a["due"] <= clock() + 86400 for a in snap["alerts"] if a["kind"] == "event")
    assert snap["later_heads_ups"] == len(sched.active(("event",))) - len([a for a in snap["alerts"] if a["kind"] == "event"])
    assert snap["later_heads_ups"] >= 3


async def test_an_only_me_calendar_stays_off_the_room_monitor(env):
    from pip_assistant.core.dashapi import tv_snapshot
    reg, cal, fake, people, sched, clock = env
    sam = people.add_person("Sam")
    fake.calendars["sam@example.com"] = {"summary": "Sam", "primary": True, "accessRole": "owner", "owner": "sam@example.com"}
    fake.add_event("sam@example.com", summary="Sam's therapy", start={"dateTime": "2026-11-07T15:00:00-06:00"},
                   end={"dateTime": "2026-11-07T16:00:00-06:00"}, reminders={"useDefault": False, "overrides": [
                       {"method": "popup", "minutes": 400}]})
    acc = await connect(reg.modules["google"], fake, sam["id"], "sam@example.com")
    await cal.sync.run_once()
    snap = tv_snapshot(sched, reg)
    assert "Sam's therapy" in [e["title"] for e in snap["events"]]           # shared with everyone: on the monitor
    await reg.modules["google"].dashboard("sharing", {"id": acc["id"], "value": "me"})
    sched.add("event", "Sam's therapy", clock() + 60, event_id=next(iter(cal.store.all_events()))["id"])
    snap = tv_snapshot(sched, reg)
    assert "Sam's therapy" not in [e["title"] for e in snap["events"]]       # only for Sam: not on the room's screen
    assert "Sam's therapy" not in [a["label"] for a in snap["alerts"]]       # nor its heads-up
    assert [e["shared"] for e in cal.upcoming(2)] == [False]


async def test_an_overnight_or_several_day_event_is_listed_once_and_told_with_its_days(env):
    reg, cal, fake, people, sched, clock = env
    say(reg, "add:event", {"title": "party", "at": "2026-11-07T22:00", "end": "2026-11-08T01:00"})
    say(reg, "add:event", {"title": "conference", "at": "2026-11-08T09:00", "end": "2026-11-10T17:00"})
    say(reg, "add:event", {"title": "trip", "date": "2026-11-11", "all_day": True})
    [trip] = [e for e in cal.store.all_events() if e["title"] == "trip"]
    cal.store.update_event(trip["id"], end=datetime(2026, 11, 13))                # two days, all day
    assert [e["title"] for e in cal.upcoming(14)] == ["party", "conference", "trip", "trip"]
    told = look(reg, first="2026-11-07", last="2026-11-10")
    assert "party Sat 2026-11-07 Sat 22:00–Sun 01:00" in told and "conference Sun 2026-11-08 Sun 09:00–Tue 17:00" in told
    assert "conference Tue 2026-11-10 Sun 09:00–Tue 17:00" in told               # still on, with its real times


async def test_one_change_google_refuses_doesnt_stop_the_sync(env):
    import httpx
    reg, cal, fake, people, sched, clock = env
    landen = people.add_person("Landen", "admin")
    fake.calendars["landen@example.com"] = {"summary": "Landen", "primary": True, "accessRole": "owner", "owner": "landen@example.com"}
    await connect(reg.modules["google"], fake, landen["id"], "landen@example.com")
    await cal.sync.run_once()
    say(reg, "add:event", {"title": "visa renewal", "at": "2026-12-20T10:00", "heads_up_minutes": 50400}, me(landen))
    [visa] = cal.store.all_events()
    assert visa["reminder"] == 40320                                  # kept to Google's 4-week limit
    say(reg, "add:event", {"title": "lab report", "at": "2026-12-21T10:00"}, me(landen))
    handle = fake.handle

    def picky(request):   # Google refuses the visa one (as it would a bad reminder), takes the rest
        if request.method == "POST" and b"visa" in request.content:
            return httpx.Response(400, json={"error": {"message": "Invalid reminder"}}, request=request)
        return handle(request)
    cal.ctx._transport.handler = picky
    fake.add_event("landen@example.com", summary="Chem lab", start={"dateTime": "2026-11-09T13:00:00-06:00"},
                   end={"dateTime": "2026-11-09T15:00:00-06:00"})
    assert await cal.sync.run_once() == {next(iter(cal._accounts())): ""}
    titles = {e["title"]: e for e in cal.store.all_events()}
    assert "Chem lab" in titles and not titles["visa renewal"]["pending"]       # Google's came in; the refused one let go
    assert any(e["summary"] == "lab report" for e in fake.events["landen@example.com"].values())
    with pytest.raises(Invalid):
        say(reg, "add:event", {"title": "x", "at": "2026-12-20T10:00", "heads_up_minutes": "soon"})


async def test_events_coming_into_the_window_arrive_with_the_weekly_full_fetch(env):
    reg, cal, fake, people, sched, clock = env
    landen = people.add_person("Landen", "admin")
    fake.calendars["landen@example.com"] = {"summary": "Landen", "primary": True, "accessRole": "owner", "owner": "landen@example.com"}
    await connect(reg.modules["google"], fake, landen["id"], "landen@example.com")
    await cal.sync.run_once()
    ev = fake.add_event("landen@example.com", summary="Spring break", start={"dateTime": "2027-05-10T09:00:00-05:00"},
                        end={"dateTime": "2027-05-10T10:00:00-05:00"})
    ev["updated"] = 0                     # made long ago: an incremental fetch won't bring it
    await cal.sync.run_once()
    assert "Spring break" not in [e["title"] for e in cal.store.all_events()]
    clock.t += 8 * 86400
    await cal.sync.run_once()
    assert "Spring break" in [e["title"] for e in cal.store.all_events()]


async def test_moving_by_a_day_over_the_clock_change_keeps_the_clock_time(env):
    reg, cal, fake, people, sched, clock = env
    clock.t = datetime(2027, 3, 10, 9, 0).timestamp()
    c = Call(now=datetime(2027, 3, 10, 9, 0))
    say(reg, "add:event", {"title": "moving day", "date": "2027-03-13", "all_day": True}, c)
    say(reg, "add:event", {"title": "standup", "at": "2027-03-13T09:00"}, c)
    for e in cal.store.all_events():
        say(reg, "change:event", {"id": e["id"][:6], "shift_seconds": 86400}, c)
    told = look(reg, c, "2027-03-13", "2027-03-15")
    assert "moving day Sun 2027-03-14 all day" in told and "standup Sun 2027-03-14 09:00–10:00" in told
    items = told.split(": ", 1)[1]
    assert "Sat" not in items and "Mon" not in items                  # not an hour off into the next or last day
    [ev] = [e for e in cal.store.all_events() if e["title"] == "standup"]
    for bad in ("1e12", "nan", "inf", "soon"):
        with pytest.raises(Invalid):
            say(reg, "change:event", {"id": ev["id"][:6], "shift_seconds": bad}, c)


async def test_offline_an_only_me_calendar_is_heard_and_changed_only_by_its_owner(env):
    """Pip's own offline commands (core/commands.py) follow the calendar's privacy rule, like Claude's actions."""
    from pip_assistant.core.commands import Commands
    reg, cal, fake, people, sched, clock = env
    landen, sam = people.add_person("Landen", "admin"), people.add_person("Sam")
    fake.calendars["sam@example.com"] = {"summary": "Sam", "primary": True, "accessRole": "owner", "owner": "sam@example.com"}
    fake.add_event("sam@example.com", summary="interview", start={"dateTime": "2026-11-07T15:00:00-06:00"},
                   end={"dateTime": "2026-11-07T16:00:00-06:00"})
    acc = await connect(reg.modules["google"], fake, sam["id"], "sam@example.com")
    await cal.sync.run_once()
    await reg.modules["google"].dashboard("sharing", {"id": acc["id"], "value": "me"})
    cmds = Commands(sched, clock=clock, items=[cal.offline()])
    assert "interview" not in await cmds.handle("what's on the calendar today", me(landen))
    assert "interview" not in await cmds.handle("what's on the calendar today", Call(now=NOW))   # an unknown voice
    assert "can't find" in await cmds.handle("delete interview from the calendar", me(landen))
    ev = next(iter(cal.store.all_events()))
    with pytest.raises(Invalid):                                   # Claude can't act on it for Landen either
        say(reg, "cancel:event", {"id": ev["id"]}, me(landen))
    assert "interview" in await cmds.handle("what's on the calendar today", me(sam))
    assert "interview" in await cmds.handle("what's on the calendar today")                   # the dashboard
    await cmds.handle("delete interview from the calendar", me(sam))
    assert not list(cal.store.all_events())
