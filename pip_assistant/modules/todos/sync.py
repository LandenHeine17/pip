"""Two-way sync with Google Tasks, per connected account (D-100).

Each time: the account's task lists → todos_lists; then for each list Google's changes since last
time are fetched first (Google wins: if a to-do Pip changed was also changed in Google, Google's
version is kept), and then Pip's remaining changes are sent. Offline, changes just wait.
"""
import logging
from datetime import datetime, timezone
from urllib.parse import quote

from .store import TodoStore

log = logging.getLogger(__name__)
API = "https://tasks.googleapis.com/tasks/v1"


def list_id(account: str, gid: str) -> str:
    return f"g:{account}:{gid}"


def to_google(t: dict) -> dict:
    body = {"title": t["title"], "notes": t["notes"], "status": "completed" if t["done"] else "needsAction"}
    if t["due"]:
        body["due"] = f"{t['due']}T00:00:00.000Z"
    return body


def from_google(item: dict) -> dict:
    done = item.get("status") == "completed"
    done_at = None
    if done and item.get("completed"):
        done_at = datetime.fromisoformat(item["completed"].replace("Z", "+00:00")).timestamp()
    return {"title": (item.get("title") or "").strip()[:300] or "(no title)", "notes": (item.get("notes") or "")[:2000],
            "due": (item.get("due") or "")[:10], "done": int(done), "done_at": done_at,
            "google_id": item["id"], "etag": item.get("etag", "")}


class TodoSync:
    def __init__(self, store: TodoStore, google, clock):
        self.store, self.google, self.clock = store, google, clock

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
            except Exception as e:
                log.info("to-do sync for an account failed: %s", type(e).__name__)
                out[acc["id"]] = type(e).__name__
            self.google.synced(acc["id"], out[acc["id"]], "to-dos")   # the Google page's "last synced"
        return out

    async def _req(self, acc, method, path, **kw):
        return await self.google.request(acc["id"], method, API + path, **kw)

    def default_list(self, account_id: str) -> str | None:
        for lst in self.store.lists():
            if lst["account_id"] == account_id and lst["is_default"]:
                return lst["id"]
        return None

    async def sync_account(self, acc: dict) -> None:
        r = await self._req(acc, "GET", "/users/@me/lists", params={"maxResults": "100"})
        r.raise_for_status()
        seen = set()
        for i, item in enumerate(r.json().get("items", [])):
            lid = list_id(acc["id"], item["id"])
            seen.add(lid)
            self.store.upsert_list(lid, account_id=acc["id"], google_id=item["id"], name=item.get("title", "?"),
                                   is_default=int(i == 0))
        for lst in self.store.lists(ignored=True):
            if lst["account_id"] == acc["id"] and lst["id"] not in seen:
                self.store.remove_list(lst["id"])
        for lst in self.store.lists():
            if lst["account_id"] == acc["id"]:
                await self.pull(acc, lst)
                for t in self.store.pending({lst["id"]}):
                    await self.push(acc, lst, t)

    async def pull(self, acc: dict, lst: dict) -> None:
        base = f"/lists/{quote(lst['google_id'], safe='')}/tasks"
        params = {"showCompleted": "true", "showHidden": "true", "showDeleted": "true", "maxResults": "100"}
        started = self.clock()
        if lst["pulled"]:
            params["updatedMin"] = datetime.fromtimestamp(lst["pulled"] - 60, timezone.utc).isoformat().replace("+00:00", "Z")
        while True:
            r = await self._req(acc, "GET", base, params=params)
            r.raise_for_status()
            page = r.json()
            for item in page.get("items", []):
                local = self.store.by_google(lst["id"], item["id"])
                if item.get("deleted"):
                    if local:
                        self.store.forget(local["id"])
                    continue
                if local and local["pending"] and local["etag"] == item.get("etag"):
                    continue   # Pip's change is on its way and Google's copy hasn't changed
                self.store.save_synced(local["id"] if local else None, list_id=lst["id"], **from_google(item))
            if page.get("nextPageToken"):
                params["pageToken"] = page["nextPageToken"]
                continue
            break
        self.store.upsert_list(lst["id"], pulled=started)

    async def push(self, acc: dict, lst: dict, t: dict) -> None:
        base = f"/lists/{quote(lst['google_id'], safe='')}/tasks"
        if t["pending"] == "add":
            r = await self._req(acc, "POST", base, json=to_google(t))
            r.raise_for_status()
            g = r.json()
            self.store.save_synced(t["id"], google_id=g["id"], etag=g.get("etag", ""))
        elif t["pending"] == "change":
            r = await self._req(acc, "PATCH", f"{base}/{quote(t['google_id'], safe='')}", json=to_google(t))
            if r.status_code in (404, 410):
                self.store.forget(t["id"])
                return
            r.raise_for_status()
            self.store.save_synced(t["id"], etag=r.json().get("etag", ""))
        elif t["pending"] == "delete":
            r = await self._req(acc, "DELETE", f"{base}/{quote(t['google_id'], safe='')}")
            if r.status_code not in (200, 204, 404, 410):
                r.raise_for_status()
            self.store.forget(t["id"])
