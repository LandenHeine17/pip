"""The dashboard's same-site check (FEAT-072, kept from Ziggletron; D-116).

Pip's dashboard has no logins (D-116): it's reachable only on the owner's Tailscale devices. What
stands between another website and Pip is this check, so a page open on the owner's phone can't
quietly change Pip:

- The Host name must be Pip's own: an IP address, localhost, this machine's name (or <name>.local,
  or its Tailscale name <name>.<tailnet>.ts.net), or one listed in `allowed_hosts`. A website that
  points its own name at Pip's address ("DNS rebinding") is refused.
- A request that changes something (POST, PUT, PATCH, DELETE) must be JSON, and if the browser says
  which page sent it (Origin, or Referer when there's no Origin) that page must be Pip's own.
  Browsers won't send JSON to another site without asking it first, and Pip never says yes.

Reading (GET) needs nothing extra, so the monitor's kiosk at http://127.0.0.1:8790/tv keeps working.
A module's page may read with GET, but only actions in module_get_allowed(); everything else is POST.
"""

import ipaddress
import socket
from urllib.parse import urlsplit

CHANGING = {"POST", "PUT", "PATCH", "DELETE"}

# Module dashboard actions that only read (the pages call these with GET). Every other action changes
# something and must come as a POST. Found from each module's dashboard() and its static pages.
MODULE_READ_ACTIONS = frozenset({
    "status",            # google, screen (also the default when no action is given)
    "range",             # calendar: the events between two dates
    "all_calendars",     # calendar: every calendar, ignored ones too
    "account_settings",  # calendar: one account's calendars and settings
    "list",              # todos: the lists and their items
    "account_lists",     # todos: one account's lists
})


def module_get_allowed(action: str) -> bool:
    """True if a module action only reads, so it may come as a GET (/api/m/<name>?action=…)."""
    return action in MODULE_READ_ACTIONS


def _split(netloc: str, scheme: str = "") -> tuple[str, int | None]:
    """'Name:port' → ('name', port). A missing port becomes the scheme's usual one if we know it."""
    try:
        parts = urlsplit(f"//{netloc}")
        name, port = (parts.hostname or "").rstrip("."), parts.port
    except ValueError:
        return "", None
    if port is None:
        port = {"http": 80, "https": 443}.get(scheme)
    return name, port


def host_allowed(host: str, allowed_hosts=()) -> bool:
    """Is this Host header one of Pip's own names?"""
    name, _ = _split(host)
    if not name:
        return False
    try:
        ipaddress.ip_address(name)
        return True   # an address, not a name: a rebinding attack can't produce one of these
    except ValueError:
        pass
    me = socket.gethostname().lower()
    extra = {h.lower() for h in allowed_hosts}
    return name in {"localhost", me, f"{me}.local", *extra} or (
        name.endswith(".ts.net") and name.split(".")[0] == me)   # this machine's Tailscale name


def same_site(page: str, host: str) -> bool:
    """Did the page at `page` (an Origin or Referer) come from `host` (the Host header)?"""
    try:
        parts = urlsplit(page)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False   # "null" (a sandboxed page) or something odd
    o_name, o_port = _split(parts.netloc, parts.scheme)
    h_name, h_port = _split(host)
    if not o_name or o_name != h_name:
        return False
    # The Host header leaves out the usual port (80/443); the page's address may too.
    return h_port is None and o_port in (80, 443) or h_port == o_port


def _refuse(status: int, text: str):
    body = text.encode()
    return [{"type": "http.response.start", "status": status,
             "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode())]},
            {"type": "http.response.body", "body": body}]


class SameSite:
    """ASGI middleware: SameSite(app, allowed_hosts=[...]). Wrap the whole Starlette app with it."""

    def __init__(self, app, allowed_hosts=()):
        self.app = app
        self.allowed_hosts = tuple(allowed_hosts)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host = headers.get("host", "")
        problem = None
        if not host_allowed(host, self.allowed_hosts):
            problem = (421, "Unknown host name. Use Pip's own address, or add the name to the dashboard's allowed hosts.")
        elif scope["type"] == "websocket" or scope.get("method", "GET").upper() in CHANGING:
            page = headers.get("origin") or headers.get("referer")
            kind = headers.get("content-type", "").split(";")[0].strip().lower()
            if page is not None and not same_site(page, host):
                problem = (403, "Cross-site request refused")
            elif scope["type"] == "http" and kind != "application/json":
                problem = (403, "Cross-site request refused (send JSON)")
        if problem is None:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1008})
        for message in _refuse(*problem):
            await send(message)
