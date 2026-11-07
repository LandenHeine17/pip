"""A pretend Google for tests (no internet): sign-in, Calendar and Tasks, enough for Pip's modules.

    fake = FakeGoogle()
    transport = fake.transport()          # give it to ModuleContext(transport=…)
    fake.calendars["acct-cal"] = {...}    # what's "in Google"
"""
import json
import secrets
import time
from urllib.parse import parse_qs, unquote

import httpx


class FakeGoogle:
    def __init__(self):
        self.codes: dict[str, str] = {}            # sign-in code → email
        self.refresh: dict[str, str] = {}          # refresh token → email
        self.revoked: list[str] = []
        self.refuse_refresh = False
        self.calendars: dict[str, dict] = {}       # google calendar id → {summary, primary, accessRole, owner email}
        self.events: dict[str, dict[str, dict]] = {}   # calendar id → event id → event
        self.lists: dict[str, dict] = {}           # task list id → {title, owner}
        self.tasks: dict[str, dict[str, dict]] = {}
        self.requests: list[tuple[str, str]] = []
        self.offline = False
        self._n = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _etag(self) -> str:
        self._n += 1
        return f'"e{self._n}"'

    def sign_in_code(self, email: str) -> str:
        code = "code-" + secrets.token_hex(4)
        self.codes[code] = email
        return code

    def _who(self, request) -> str:
        auth = request.headers.get("authorization", "")
        return auth.removeprefix("Bearer access-")

    def add_event(self, cal: str, **ev) -> dict:
        eid = ev.pop("id", "ev" + secrets.token_hex(3))
        e = {"id": eid, "status": "confirmed", "etag": self._etag(), "updated": time.time(), **ev}
        self.events.setdefault(cal, {})[eid] = e
        return e

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.offline:
            raise httpx.ConnectError("offline", request=request)
        path, method = unquote(request.url.path), request.method
        self.requests.append((method, path))
        q = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        body = json.loads(request.content) if request.content and request.headers.get("content-type", "").startswith("application/json") else {}
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()} if not body and request.content else {}
        if request.url.host == "oauth2.googleapis.com":
            if path == "/token":
                if form.get("grant_type") == "authorization_code":
                    email = self.codes.pop(form.get("code", ""), None)
                    if not email or not form.get("code_verifier"):
                        return httpx.Response(400, json={"error": "invalid_grant"})
                    rt = "refresh-" + email
                    self.refresh[rt] = email
                    payload = __import__("base64").urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
                    return httpx.Response(200, json={"access_token": f"access-{email}", "expires_in": 3600,
                                                     "refresh_token": rt, "id_token": f"x.{payload}.y"})
                if form.get("grant_type") == "refresh_token":
                    email = self.refresh.get(form.get("refresh_token", ""))
                    if not email or self.refuse_refresh:
                        return httpx.Response(400, json={"error": "invalid_grant"})
                    return httpx.Response(200, json={"access_token": f"access-{email}", "expires_in": 3600})
            if path == "/revoke":
                self.revoked.append(form.get("token", ""))
                return httpx.Response(200, json={})
        who = self._who(request)
        if request.url.host == "www.googleapis.com" and path.startswith("/calendar/v3"):
            p = path.removeprefix("/calendar/v3")
            if p == "/users/me/calendarList":
                return httpx.Response(200, json={"items": [{"id": cid, **{k: v for k, v in c.items() if k != "owner"}}
                                                           for cid, c in self.calendars.items() if c["owner"] == who]})
            parts = p.split("/")   # ['', 'calendars', cid, 'events', (eid)]
            cid = parts[2]
            evs = self.events.setdefault(cid, {})
            if len(parts) == 4 and method == "GET":
                items = list(evs.values())
                if "syncToken" in q:
                    since = float(q["syncToken"])
                    items = [e for e in items if e["updated"] > since]
                else:
                    items = [e for e in items if e["status"] != "cancelled"]
                return httpx.Response(200, json={"items": items, "nextSyncToken": str(time.time())})
            if len(parts) == 4 and method == "POST":
                e = self.add_event(cid, **body)
                return httpx.Response(200, json=e)
            eid = parts[4]
            e = evs.get(eid)
            if e is None or e["status"] == "cancelled":
                return httpx.Response(404 if method != "DELETE" else 410, json={})
            if method == "GET":
                return httpx.Response(200, json=e)
            if method == "PATCH":
                if request.headers.get("if-match") and request.headers["if-match"] != e["etag"]:
                    return httpx.Response(412, json={"error": "precondition failed"})
                e.update(body, etag=self._etag(), updated=time.time())
                return httpx.Response(200, json=e)
            if method == "DELETE":
                e.update(status="cancelled", updated=time.time())
                return httpx.Response(204)
        if request.url.host == "tasks.googleapis.com":
            p = path.removeprefix("/tasks/v1")
            if p == "/users/@me/lists":
                return httpx.Response(200, json={"items": [{"id": lid, "title": x["title"]} for lid, x in self.lists.items()
                                                           if x["owner"] == who]})
            parts = p.split("/")   # ['', 'lists', lid, 'tasks', (tid)]
            ts = self.tasks.setdefault(parts[2], {})
            if len(parts) == 4 and method == "GET":
                items = list(ts.values())
                if "updatedMin" in q:
                    from datetime import datetime
                    since = datetime.fromisoformat(q["updatedMin"].replace("Z", "+00:00")).timestamp()
                    items = [t for t in items if t["updated_ts"] > since]
                return httpx.Response(200, json={"items": items})
            if len(parts) == 4 and method == "POST":
                tid = "t" + secrets.token_hex(3)
                ts[tid] = {"id": tid, "etag": self._etag(), "updated_ts": time.time(), **body}
                return httpx.Response(200, json=ts[tid])
            t = ts.get(parts[4])
            if t is None or t.get("deleted"):
                return httpx.Response(404, json={})
            if method == "PATCH":
                t.update(body, etag=self._etag(), updated_ts=time.time())
                return httpx.Response(200, json=t)
            if method == "DELETE":
                t.update(deleted=True, updated_ts=time.time())
                return httpx.Response(204)
        return httpx.Response(404, json={"error": f"fake google doesn't know {method} {path}"})


async def connect(google_module, fake: FakeGoogle, person_id: str, email: str) -> dict:
    """Go through Pip's sign-in for one person, as if they'd pasted Google's address back."""
    from urllib.parse import parse_qs as _pq, urlparse
    g = google_module.api
    if not g.configured():
        await google_module.dashboard("save_client", {"client_id": "123-abc.apps.googleusercontent.com",
                                                      "client_secret": "GOCSPX-test-secret"})
    url = (await google_module.dashboard("connect", {"person_id": person_id}))["url"]
    state = _pq(urlparse(url).query)["state"][0]
    await google_module.dashboard("finish", {"pasted": f"http://127.0.0.1:8765/?state={state}&code={fake.sign_in_code(email)}"})
    return g.for_person(person_id)
