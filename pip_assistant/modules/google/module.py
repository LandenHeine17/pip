"""Google accounts, one per household member (D-97).

Setting up, once (the dashboard page walks through it): the owner creates a free Google Cloud
project, turns on the Calendar and Tasks APIs, and makes a "Desktop app" OAuth client. Its id and
secret are pasted into the dashboard and saved in secrets.toml [google].

Connecting someone: People → their row → Connect Google (or this module's page). Google's sign-in
page opens; afterwards Google sends the browser to http://127.0.0.1:<redirect_port>/?code=… which
won't load (that's expected): the person copies that address into the dashboard, and Pip swaps the
code for a long-lived "refresh token", saved in secrets.toml [google.accounts.<id>]. It never leaves
the Pi except to Google, and is never shown on the dashboard. Disconnect revokes it at Google.

Other modules (calendar, to-dos) use `ctx.module("google")` → GoogleAccounts: request(account, …)
keeps a fresh access token and marks the account "needs reconnecting" (or "missing" permissions) if
Google refuses; on_connect / on_disconnect let them fetch or forget an account's data.
"""
import base64
import hashlib
import json
import secrets as pysecrets
from urllib.parse import parse_qs, urlencode, urlparse

from pip_assistant.common.messages import new_id
from pip_assistant.core.modules import Module

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
G = "https://www.googleapis.com/auth/"
CALENDAR = G + "calendar"   # all of Google Calendar: events, and making / renaming / deleting calendars (D-103)
SCOPES = ["openid", "email", CALENDAR, G + "tasks"]


def enough(granted: set[str]) -> bool:
    """Can the calendar and to-dos sync? (Google lets people untick each permission on its "what Pip can
    access" screen. Accounts connected before D-103 have the narrower calendar.events + calendar.readonly.)"""
    return G + "tasks" in granted and (CALENDAR in granted or {G + "calendar.events", G + "calendar.readonly"} <= granted)


MISSING = "Google only let Pip see your email. Press Connect Google again (no need to disconnect) and tick Select all on Google's \"what Pip can access\" screen."

SCHEMA = """
CREATE TABLE IF NOT EXISTS google_accounts (
    id         TEXT PRIMARY KEY,
    person_id  TEXT NOT NULL,          -- whose account (people.id)
    email      TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'ok',      -- ok / reconnect / missing (permissions not ticked)
    sharing    TEXT NOT NULL DEFAULT 'everyone',-- everyone / me: may Pip talk about it to anyone, or only to them
    created    REAL NOT NULL,
    last_sync  REAL,
    error      TEXT NOT NULL DEFAULT '',
    scopes     TEXT NOT NULL DEFAULT ''        -- what Google let Pip do (space-separated); '' = not known
);
"""


class NeedsReconnect(RuntimeError):
    """Google refused the account's token (revoked or expired): the person must connect again."""


class NeedsPermission(RuntimeError):
    """The account works, but wasn't given this permission (e.g. managing calendars): connect again, Select all."""


class GoogleAccounts:
    """What other modules use (their ctx.module("google"))."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.db = ctx.db()
        self.db.executescript(SCHEMA)
        if "scopes" not in {r[1] for r in self.db.execute("PRAGMA table_info(google_accounts)")}:
            self.db.execute("ALTER TABLE google_accounts ADD COLUMN scopes TEXT NOT NULL DEFAULT ''")
        self.db.commit()
        self._tokens: dict[str, tuple[str, float]] = {}   # account id → (access token, expires at)
        self._pending: dict[str, dict] = {}               # sign-ins in progress: state → {verifier, person_id, at}
        self.on_connect: list = []                        # other modules' callbacks: f(account id) after connecting
        self.on_disconnect: list = []                     # …and before an account is forgotten (they drop its copies)
        self._sync_errors: dict[str, dict[str, str]] = {}  # account id → {"calendar": "", "to-dos": "ConnectError"}

    # ---------------------------------------------------------------- the shared OAuth client
    def configured(self) -> bool:
        s = self.ctx.secrets.all()
        return bool(s.get("client_id") and s.get("client_secret"))

    def save_client(self, client_id: str, client_secret: str) -> None:
        client_id, client_secret = client_id.strip(), client_secret.strip()
        if not client_id.endswith(".apps.googleusercontent.com") or len(client_secret) < 10:
            raise ValueError("That doesn't look like a Google OAuth client id and secret")
        data = self.ctx.secrets.all()
        data.update(client_id=client_id, client_secret=client_secret)
        self.ctx.secrets.save(data)

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.ctx.settings['redirect_port']}/"

    # ---------------------------------------------------------------- accounts
    def accounts(self, person_id: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM google_accounts", ()
        if person_id:
            q, args = q + " WHERE person_id = ?", (person_id,)
        return [dict(r) for r in self.db.execute(q + " ORDER BY created", args)]

    def account(self, aid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM google_accounts WHERE id = ?", (aid,)).fetchone()
        return dict(r) if r else None

    def for_person(self, person_id: str | None) -> dict | None:
        """That person's first working account, if any."""
        rows = [a for a in self.accounts(person_id) if a["status"] == "ok"] if person_id else []
        return rows[0] if rows else None

    def household_accounts(self, email: str = "") -> list[dict]:
        """Connected accounts in the order to use for "the household's" calendar or list: the one with
        this email first (a setting), else admins' accounts first."""
        accs = [a for a in self.accounts() if a["status"] == "ok"]
        if email:
            return sorted(accs, key=lambda a: a["email"].lower() != email.lower())
        people = self.ctx.people
        admins = {p["id"] for p in people.all() if p["role"] == "admin"} if people else set()
        return sorted(accs, key=lambda a: a["person_id"] not in admins)

    def set(self, aid: str, **fields) -> None:
        if fields:
            self.db.execute(f"UPDATE google_accounts SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                            (*fields.values(), aid))
            self.db.commit()

    def synced(self, aid: str, error: str = "", part: str = "calendar") -> None:
        """A sync of one part (the calendar, the to-dos) finished, with its error or "". The Google page shows
        the last sync time and every part's latest error, so one part succeeding doesn't hide another failing."""
        errors = self._sync_errors.setdefault(aid, {})
        errors[part] = error
        shown = "; ".join(f"{p}: {e}" for p, e in errors.items() if e)
        self.set(aid, last_sync=self.ctx.clock(), error=shown[:200])

    # ---------------------------------------------------------------- signing in (PKCE, paste-back)
    def start_sign_in(self, person_id: str) -> str:
        if not self.configured():
            raise ValueError("Set up the Google client first (step 1 on this page)")
        if self.ctx.people is not None and not self.ctx.people.get(person_id):
            raise ValueError("No such person")
        verifier = base64.urlsafe_b64encode(pysecrets.token_bytes(48)).rstrip(b"=").decode()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = pysecrets.token_urlsafe(16)
        now = self.ctx.clock()
        self._pending = {k: v for k, v in self._pending.items() if now - v["at"] < 1800}
        self._pending[state] = {"verifier": verifier, "person_id": person_id, "at": now}
        return AUTH_URL + "?" + urlencode({
            "client_id": self.ctx.secrets.get("client_id"), "redirect_uri": self.redirect_uri,
            "response_type": "code", "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})

    async def finish_sign_in(self, pasted: str) -> dict:
        """`pasted` is the address Google sent the browser to (or just its code=… part)."""
        q = parse_qs(urlparse(pasted.strip()).query) or parse_qs(pasted.strip())
        if q.get("error"):
            raise ValueError(f"Google said: {q['error'][0]}")
        code, state = (q.get("code") or [""])[0], (q.get("state") or [""])[0]
        pend = self._pending.pop(state, None)
        if not code or not pend:
            raise ValueError("That isn't the address from this sign-in. Press Connect again and paste the new address.")
        s = self.ctx.secrets.all()
        async with self.ctx.http() as http:
            r = await http.post(TOKEN_URL, data={
                "code": code, "client_id": s.get("client_id"), "client_secret": s.get("client_secret"),
                "redirect_uri": self.redirect_uri, "grant_type": "authorization_code", "code_verifier": pend["verifier"]})
        if r.status_code != 200:
            try:
                why = r.json().get("error", r.status_code)
            except ValueError:   # not JSON (an error page): just the status
                why = r.status_code
            raise ValueError(f"Google didn't accept the sign-in ({why})")
        tok = r.json()
        if not tok.get("refresh_token"):
            raise ValueError("Google didn't give a lasting sign-in. Remove Pip's access in your Google account and connect again.")
        email = _email(tok.get("id_token", ""))
        old = [a for a in self.accounts(pend["person_id"]) if a["email"] == email and email]
        aid = old[0]["id"] if old else new_id()
        granted = str(tok.get("scope") or "")
        missing = bool(granted) and not enough(set(granted.split()))
        status, error = ("missing", MISSING) if missing else ("ok", "")
        if old:
            self.set(aid, status=status, error=error, scopes=granted)
        else:
            self.db.execute("INSERT INTO google_accounts (id, person_id, email, created, status, error, scopes) "
                            "VALUES (?,?,?,?,?,?,?)", (aid, pend["person_id"], email, self.ctx.clock(), status, error, granted))
            self.db.commit()
        data = self.ctx.secrets.all()
        data.setdefault("accounts", {})[aid] = {"refresh_token": tok["refresh_token"]}
        self.ctx.secrets.save(data)
        self._tokens[aid] = (tok["access_token"], self.ctx.clock() + float(tok.get("expires_in", 3600)) - 60)
        await self.ctx.publish("connected", {"account": aid})
        if not missing:
            for fn in self.on_connect:
                fn(aid)
        return self.account(aid)

    async def disconnect(self, aid: str) -> None:
        token = ((self.ctx.secrets.get("accounts") or {}).get(aid) or {}).get("refresh_token")
        if token:
            try:
                async with self.ctx.http() as http:
                    await http.post(REVOKE_URL, data={"token": token})
            except Exception:
                pass  # forgotten here either way
        for fn in self.on_disconnect:
            fn(aid)
        data = self.ctx.secrets.all()
        (data.get("accounts") or {}).pop(aid, None)
        self.ctx.secrets.save(data)
        self.db.execute("DELETE FROM google_accounts WHERE id = ?", (aid,))
        self.db.commit()
        self._tokens.pop(aid, None)
        await self.ctx.publish("disconnected", {"account": aid})

    # ---------------------------------------------------------------- using an account
    async def _access_token(self, aid: str) -> str:
        tok = self._tokens.get(aid)
        if tok and tok[1] > self.ctx.clock():
            return tok[0]
        s = self.ctx.secrets.all()
        refresh = ((s.get("accounts") or {}).get(aid) or {}).get("refresh_token")
        if not refresh:
            self.set(aid, status="reconnect")
            raise NeedsReconnect(aid)
        async with self.ctx.http() as http:
            r = await http.post(TOKEN_URL, data={"client_id": s.get("client_id"), "client_secret": s.get("client_secret"),
                                                 "refresh_token": refresh, "grant_type": "refresh_token"})
        if r.status_code in (400, 401):
            self.set(aid, status="reconnect", error="Google refused the sign-in: connect again")
            raise NeedsReconnect(aid)
        r.raise_for_status()
        tok = r.json()
        self._tokens[aid] = (tok["access_token"], self.ctx.clock() + float(tok.get("expires_in", 3600)) - 60)
        return tok["access_token"]

    async def request(self, aid: str, method: str, url: str, manage: bool = False, **kw):
        """Call a Google API as this account. Returns the httpx response (any status).
        manage=True: an extra like making a calendar; if that permission is missing only that call fails
        (NeedsPermission), the account keeps syncing."""
        token = await self._access_token(aid)
        headers = {**kw.pop("headers", {}), "Authorization": f"Bearer {token}"}
        async with self.ctx.http() as http:
            r = await http.request(method, url, headers=headers, **kw)
        if r.status_code == 401:  # token expired early: once more with a fresh one
            self._tokens.pop(aid, None)
            token = await self._access_token(aid)
            headers["Authorization"] = f"Bearer {token}"
            async with self.ctx.http() as http:
                r = await http.request(method, url, headers=headers, **kw)
        if r.status_code == 403 and "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in r.text:
            if manage:
                raise NeedsPermission(aid)
            self.set(aid, status="missing", error=MISSING)
            raise NeedsReconnect(aid)
        return r

    def snapshot(self) -> dict:
        """What the Google page shows (never a token or the client secret)."""
        names = {p["id"]: p["name"] for p in (self.ctx.people.all() if self.ctx.people else [])}
        return {"configured": self.configured(), "redirect_uri": self.redirect_uri,
                "accounts": [{**a, "person": names.get(a["person_id"], "?"), "can_manage": CALENDAR in a["scopes"].split()}
                             for a in self.accounts()],
                "people": [{"id": k, "name": v} for k, v in names.items()]}


def _email(id_token: str) -> str:
    """The email in Google's id_token (received straight from Google over HTTPS, so not re-verified)."""
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("email", "")
    except Exception:
        return ""


class Google(Module):
    """No actions for Claude: just the Google page and the accounts other modules use (its api)."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.api = GoogleAccounts(ctx)
        self.actions = {}

    async def dashboard(self, action: str, args: dict) -> dict:
        g = self.api
        if action == "status":
            return g.snapshot()
        if action == "save_client":
            g.save_client(args.get("client_id", ""), args.get("client_secret", ""))
            return g.snapshot()
        if action == "connect":
            return {"url": g.start_sign_in(str(args.get("person_id", "")))}
        if action == "finish":
            a = await g.finish_sign_in(str(args.get("pasted", "")))
            return {"ok": True, "email": a["email"], **g.snapshot()}
        if action == "sharing":
            if args.get("value") not in ("everyone", "me"):
                raise ValueError("sharing must be everyone or me")
            g.set(str(args.get("id")), sharing=args["value"])
            await self.ctx.publish("changed", {})
            return g.snapshot()
        if action == "disconnect":
            await g.disconnect(str(args.get("id")))
            return g.snapshot()
        return {"error": f"unknown action {action}"}


def setup(ctx) -> Module:
    return Google(ctx)
