"""Two-way sync with Google Calendar, per connected account (D-98).

Every couple of minutes, and straight after Pip changes something:

    1. the account's calendar list → calendar_calendars (new ones ticked on; gone ones removed)
    2. Pip's own changes waiting for this account are sent (add / change / delete)
    3. each ticked calendar is fetched: the first time days_back to days_ahead (settings; 30 and 180),
       then only what changed (Google's sync token). Repeating events arrive as single occurrences.

The first time an account connects, if its main calendar is the household's, Pip's own upcoming
events move into it (setting upload_on_connect). Pip can also make, rename, recolour and delete
whole calendars, and move events between calendars (D-103).

Conflicts: Google wins. A change is sent with the event's last known version ("If-Match"); if
someone changed it in Google meanwhile, Google refuses, Pip drops his change and takes Google's.
If Pip is offline, changes simply wait. Event text is data, never instructions (ARCHITECTURE §9).
"""
import logging
from datetime import datetime, timedelta
from urllib.parse import quote

from .store import CalendarStore

log = logging.getLogger(__name__)
API = "https://www.googleapis.com/calendar/v3"
RRULE = {"daily": "RRULE:FREQ=DAILY", "weekdays": "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
         "weekends": "RRULE:FREQ=WEEKLY;BYDAY=SA,SU", "weekly": "RRULE:FREQ=WEEKLY"}
DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def rrule(repeat: str) -> list[str]:
    if repeat.startswith("weekly:"):
        return [f"RRULE:FREQ=WEEKLY;BYDAY={DAYS[int(repeat[7:])]}"]
    return [RRULE[repeat]] if repeat in RRULE else []


def calendar_id(account: str, gid: str) -> str:
    return f"g:{account}:{gid}"


def _ts(when: dict) -> tuple[float, bool]:
    """Google's start/end → (Unix time, all-day?)."""
    if "date" in when:
        return datetime.fromisoformat(when["date"]).timestamp(), True
    return datetime.fromisoformat(when["dateTime"].replace("Z", "+00:00")).timestamp(), False


def _when(ts: float, all_day: bool) -> dict:
    dt = datetime.fromtimestamp(ts)
    return {"date": dt.date().isoformat()} if all_day else {"dateTime": dt.astimezone().isoformat()}


def to_google(ev: dict) -> dict:
    body = {"summary": ev["title"], "start": _when(ev["start"], ev["all_day"]), "end": _when(ev["end"], ev["all_day"]),
            "location": ev["location"], "description": ev["notes"],
            "visibility": "private" if ev["private"] else "default"}
    if ev["repeat"]:
        body["recurrence"] = rrule(ev["repeat"])
    if ev["reminder"] is not None:
        body["reminders"] = {"useDefault": False, "overrides": [{"method": "popup", "minutes": int(ev["reminder"])}]}
    return body


def from_google(item: dict, default_reminder: float | None) -> dict:
    start, all_day = _ts(item["start"])
    end, _ = _ts(item.get("end") or item["start"])
    rem = item.get("reminders") or {}
    if rem.get("useDefault", True):
        reminder, own = default_reminder, False   # just the calendar's automatic reminder
    else:
        mins = [o["minutes"] for o in rem.get("overrides", []) if o.get("method") == "popup"]
        reminder, own = (float(min(mins)) if mins else None), bool(mins)   # set on this event by hand
    return {"title": (item.get("summary") or "(no title)")[:200], "start": start, "end": max(end, start + 60),
            "all_day": int(all_day), "repeat": "", "location": (item.get("location") or "")[:200],
            "notes": (item.get("description") or "")[:2000],
            "private": int(item.get("visibility") in ("private", "confidential")), "reminder": reminder,
            "reminder_own": int(own),
            "google_id": item["id"], "etag": item.get("etag", "")}


class CalendarSync:
    def __init__(self, store: CalendarStore, google, settings: dict, clock):
        self.store, self.google, self.settings, self.clock = store, google, settings, clock

    async def run_once(self) -> dict:
        """Sync every connected account. Returns {account: error or ''}."""
        out = {}
        if self.google is None:
            return out
        for acc in self.google.accounts():
            if acc["status"] != "ok":
                continue
            try:
                await self.sync_account(acc)
                out[acc["id"]] = ""
            except Exception as e:  # e.g. offline: changes wait, the local copy stays
                log.info("calendar sync for an account failed: %s", type(e).__name__)
                out[acc["id"]] = type(e).__name__
            self.google.synced(acc["id"], out[acc["id"]])
        return out

    async def _req(self, acc: dict, method: str, path: str, **kw):
        return await self.google.request(acc["id"], method, API + path, **kw)

    def account(self, cal: dict) -> dict:
        acc = self.google.account(cal["account_id"]) if self.google and cal["account_id"] else None
        if not acc:
            raise LookupError("not a Google calendar")
        return acc

    async def sync_account(self, acc: dict) -> None:
        first = not any(c["account_id"] == acc["id"] for c in self.store.calendars(ignored=True))
        await self.calendars(acc)
        if first and self.settings.get("upload_on_connect", True) and self.household() == self.primary(acc["id"]):
            self.adopt_local(self.primary(acc["id"]))
        mine = {c["id"] for c in self.store.calendars() if c["account_id"] == acc["id"]}
        for ev in self.store.pending(mine):
            await self.push(acc, ev)
        for c in self.store.calendars():
            if c["account_id"] == acc["id"] and c["ticked"]:
                await self.pull(acc, c)

    # ---------------------------------------------------------------- calendars
    async def calendars(self, acc: dict) -> None:
        r = await self._req(acc, "GET", "/users/me/calendarList")
        r.raise_for_status()
        seen = set()
        for item in r.json().get("items", []):
            cid = calendar_id(acc["id"], item["id"])
            seen.add(cid)
            rems = [o["minutes"] for o in item.get("defaultReminders", []) if o.get("method") == "popup"]
            fields = {"account_id": acc["id"], "google_id": item["id"], "name": item.get("summaryOverride") or item.get("summary", "?"),
                      "color": item.get("backgroundColor", "#4285f4"),
                      "writable": int(item.get("accessRole") in ("owner", "writer")),
                      "owner": int(item.get("accessRole") == "owner"),
                      "is_primary": int(bool(item.get("primary"))), "reminder": float(min(rems)) if rems else None}
            if not self.store.calendar(cid):
                fields["ticked"] = int(item.get("selected", True))
            self.store.upsert_calendar(cid, **fields)
        for c in self.store.calendars(ignored=True):
            if c["account_id"] == acc["id"] and c["id"] not in seen:
                self.store.remove_calendar(c["id"])

    def primary(self, account_id: str) -> str | None:
        for c in self.store.calendars():
            if c["account_id"] == account_id and c["is_primary"]:
                return c["id"]
        return None

    def household(self) -> str:
        """Where events go when Pip doesn't know whose they are: the calendar chosen on an account's settings page
        (D-109), else the household_calendar setting's account, else the first admin's main calendar."""
        if self.google is None:
            return "pip"
        accounts = self.google.household_accounts(self.settings.get("household_calendar", ""))
        for a in accounts:
            chosen = self.store.calendar(self.store.pref(a["id"], "household"))
            if chosen and chosen["account_id"] == a["id"] and chosen["writable"] and not chosen["ignored"]:
                return chosen["id"]
        for a in accounts:
            if cid := self.primary(a["id"]):
                return cid
        return "pip"

    def adopt_local(self, cid: str | None) -> None:
        """First connect: Pip's own upcoming events move into the household's Google calendar."""
        if not cid:
            return
        for ev in self.store.all_events():
            if ev["calendar_id"] == "pip" and (ev["repeat"] or ev["end"] > self.clock()):
                self.store.db.execute("UPDATE calendar_events SET calendar_id = ?, pending = 'add' WHERE id = ?", (cid, ev["id"]))
        self.store.db.commit()

    # ---------------------------------------------------------------- sending Pip's changes
    async def push(self, acc: dict, ev: dict) -> None:
        cal = self.store.calendar(ev["calendar_id"])
        base = f"/calendars/{quote(cal['google_id'], safe='')}/events"
        if ev["pending"] == "add":
            r = await self._req(acc, "POST", base, json=to_google(ev))
            r.raise_for_status()
            item = r.json()
            if ev["repeat"]:
                # Google now has the repeating event; its occurrences come back with the next fetch
                self.store.forget(ev["id"])
                return
            self.store.save_synced(ev["id"], **{k: v for k, v in from_google(item, cal["reminder"]).items()
                                                if k in ("google_id", "etag")})
        elif ev["pending"] == "change":
            r = await self._req(acc, "PATCH", f"{base}/{quote(ev['google_id'], safe='')}", json=to_google(ev),
                                headers={"If-Match": ev["etag"]} if ev["etag"] else {})
            if r.status_code == 412:      # changed in Google meanwhile: Google wins
                await self._refetch(acc, cal, ev)
                return
            if r.status_code in (404, 410):
                self.store.forget(ev["id"])
                return
            r.raise_for_status()
            self.store.save_synced(ev["id"], etag=r.json().get("etag", ""))
        elif ev["pending"] == "delete":
            r = await self._req(acc, "DELETE", f"{base}/{quote(ev['google_id'], safe='')}")
            if r.status_code not in (200, 204, 404, 410):
                r.raise_for_status()
            self.store.forget(ev["id"])

    async def _refetch(self, acc, cal, ev) -> None:
        r = await self._req(acc, "GET", f"/calendars/{quote(cal['google_id'], safe='')}/events/{quote(ev['google_id'], safe='')}")
        if r.status_code in (404, 410) or r.json().get("status") == "cancelled":
            self.store.forget(ev["id"])
        else:
            r.raise_for_status()
            self.store.save_synced(ev["id"], calendar_id=cal["id"], **from_google(r.json(), cal["reminder"]))

    # ---------------------------------------------------------------- whole calendars (D-103; needs the wider permission)
    async def create_calendar(self, acc: dict, name: str, color: str | None) -> str:
        r = await self._req(acc, "POST", "/calendars", manage=True, json={"summary": name})
        r.raise_for_status()
        gid = r.json()["id"]
        cid = calendar_id(acc["id"], gid)
        self.store.upsert_calendar(cid, account_id=acc["id"], google_id=gid, name=name, color=color or "#a4bdfc",
                                   writable=1, owner=1, is_primary=0, ticked=1)
        if color:
            await self.recolor(acc, self.store.calendar(cid), color)
        return cid

    async def rename_calendar(self, acc: dict, cal: dict, name: str) -> None:
        r = await self._req(acc, "PATCH", f"/calendars/{quote(cal['google_id'], safe='')}", manage=True, json={"summary": name})
        r.raise_for_status()
        self.store.upsert_calendar(cal["id"], name=name)

    async def recolor(self, acc: dict, cal: dict, color: str) -> None:
        r = await self._req(acc, "PATCH", f"/users/me/calendarList/{quote(cal['google_id'], safe='')}", manage=True,
                            params={"colorRgbFormat": "true"}, json={"backgroundColor": color, "foregroundColor": "#000000"})
        r.raise_for_status()
        self.store.upsert_calendar(cal["id"], color=color)

    async def delete_calendar(self, acc: dict, cal: dict) -> None:
        """Their own calendar is deleted in Google; one shared with them is only taken off their list."""
        gid = quote(cal["google_id"], safe="")
        path = f"/calendars/{gid}" if cal["owner"] else f"/users/me/calendarList/{gid}"
        r = await self._req(acc, "DELETE", path, manage=True)
        if r.status_code not in (200, 204, 404, 410):
            r.raise_for_status()
        self.store.remove_calendar(cal["id"])

    async def move_event(self, ev: dict, to: dict) -> dict:
        """Move an event to another calendar. Within one Google account Google moves it (keeping everything);
        otherwise it's made in the new calendar and deleted from the old one (sent with the next sync)."""
        frm = self.store.calendar(ev["calendar_id"])
        if (ev["google_id"] and not ev["pending"] and frm and frm["account_id"] and frm["account_id"] == to["account_id"]):
            acc = self.account(frm)
            r = await self._req(acc, "POST", f"/calendars/{quote(frm['google_id'], safe='')}/events/"
                                f"{quote(ev['google_id'], safe='')}/move", manage=True, params={"destination": to["google_id"]})
            if r.status_code == 200:
                return self.store.save_synced(ev["id"], calendar_id=to["id"], **from_google(r.json(), to["reminder"]))
            if r.status_code == 403 and "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in r.text:
                from pip_assistant.modules.google.module import NeedsPermission
                raise NeedsPermission(acc["id"])
            # e.g. one day of a repeating event (Google can't move those): copy it over instead
        return self.store.copy_to(ev, to["id"])

    # ---------------------------------------------------------------- fetching Google's changes
    async def pull(self, acc: dict, cal: dict) -> None:
        base = f"/calendars/{quote(cal['google_id'], safe='')}/events"
        params = {"singleEvents": "true", "showDeleted": "true", "maxResults": "250"}
        full = not cal["sync_token"]
        if full:
            now = datetime.fromtimestamp(self.clock()).astimezone()
            params["timeMin"] = (now - timedelta(days=self.settings.get("days_back", 30))).isoformat()
            params["timeMax"] = (now + timedelta(days=self.settings.get("days_ahead", 180))).isoformat()
        else:
            params["syncToken"] = cal["sync_token"]
        seen, token = set(), ""
        while True:
            r = await self._req(acc, "GET", base, params=params)
            if r.status_code == 410:            # Google forgot our place: start again from scratch
                self.store.upsert_calendar(cal["id"], sync_token="")
                return await self.pull(acc, {**cal, "sync_token": ""})
            r.raise_for_status()
            page = r.json()
            for item in page.get("items", []):
                seen.add(item["id"])
                self._apply(cal, item)
            if page.get("nextPageToken"):
                params["pageToken"] = page["nextPageToken"]
                continue
            token = page.get("nextSyncToken", "")
            break
        if full:  # anything we had that Google no longer lists (and isn't Pip's unsent change) is gone
            for ev in self.store.db.execute("SELECT * FROM calendar_events WHERE calendar_id = ? AND google_id != '' "
                                            "AND pending = ''", (cal["id"],)).fetchall():
                if ev["google_id"] not in seen:
                    self.store.forget(ev["id"])
        self.store.upsert_calendar(cal["id"], sync_token=token)

    def _apply(self, cal: dict, item: dict) -> None:
        local = self.store.by_google(cal["id"], item["id"])
        if item.get("status") == "cancelled":
            if local:
                self.store.forget(local["id"])
            return
        if local and local["pending"] and local["etag"] == item.get("etag"):
            return  # Pip's own change to this event is still on its way; Google hasn't changed it
        self.store.save_synced(local["id"] if local else None, calendar_id=cal["id"], **from_google(item, cal["reminder"]))
