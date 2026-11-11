"""The dashboard's same-site check (FEAT-072, D-116): web/security.py."""

import socket

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from pip_assistant.web.security import (
    SameSite,
    host_allowed,
    module_get_allowed,
    same_site,
)

PIP = "100.64.0.1:8790"   # any Tailscale-range address will do


async def page(request):
    return PlainTextResponse("ok")


async def change(request):
    return JSONResponse({"ok": True, "got": await request.json()})


def client(allowed=()):
    app = Starlette(routes=[Route("/tv", page), Route("/", page), Route("/api/x", change, methods=["POST", "DELETE"])])
    return TestClient(SameSite(app, allowed_hosts=allowed), base_url=f"http://{PIP}")


def test_same_origin_json_post_passes():
    r = client().post("/api/x", json={"a": 1}, headers={"Origin": f"http://{PIP}"})
    assert r.status_code == 200 and r.json()["got"] == {"a": 1}


def test_json_post_without_origin_passes():
    assert client().post("/api/x", json={}).status_code == 200   # curl, or an older browser


def test_cross_site_post_refused():
    r = client().post("/api/x", json={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and "Cross-site" in r.text


def test_cross_site_referer_refused_when_no_origin():
    r = client().post("/api/x", json={}, headers={"Referer": "https://evil.example/page"})
    assert r.status_code == 403
    r = client().post("/api/x", json={}, headers={"Referer": f"http://{PIP}/m/todos/"})
    assert r.status_code == 200


def test_null_origin_refused():
    assert client().post("/api/x", json={}, headers={"Origin": "null"}).status_code == 403


def test_other_port_is_another_site():
    r = client().post("/api/x", json={}, headers={"Origin": "http://100.64.0.1:8780"})
    assert r.status_code == 403


def test_non_json_post_refused():
    r = client().post("/api/x", content="a=1", headers={"Content-Type": "application/x-www-form-urlencoded",
                                                        "Origin": f"http://{PIP}"})
    assert r.status_code == 403
    r = client().post("/api/x", content="{}", headers={"Content-Type": "text/plain"})
    assert r.status_code == 403   # a "simple" cross-site form post with no Origin


def test_delete_needs_the_same_rules():
    assert client().delete("/api/x", headers={"Origin": "https://evil.example"}).status_code == 403


def test_bad_host_refused():
    r = client().get("/tv", headers={"Host": "evil.example"})
    assert r.status_code in (403, 421)
    r = client().get("/", headers={"Host": "ziggletronshome.evil.example"})
    assert r.status_code in (403, 421)


def test_kiosk_get_needs_nothing_extra():
    r = client().get("/tv", headers={"Host": "127.0.0.1:8790"})
    assert r.status_code == 200 and r.text == "ok"
    assert client().get("/", headers={"Host": "localhost:8790"}).status_code == 200


def test_tailscale_name_over_https():
    me = socket.gethostname().lower()
    host = f"{me}.tail0000.ts.net"
    c = client()
    assert c.get("/", headers={"Host": host}).status_code == 200
    # tailscale serve keeps the Host header; the page's address has no port (https → 443)
    r = c.post("/api/x", json={}, headers={"Host": host, "Origin": f"https://{host}"})
    assert r.status_code == 200
    assert c.get("/", headers={"Host": "someoneelse.tail0000.ts.net"}).status_code in (403, 421)


def test_configured_extra_host():
    assert client(["pip.home"]).get("/", headers={"Host": "pip.home:8790"}).status_code == 200
    assert host_allowed("PIP.home", ["pip.home"])
    assert not host_allowed("", [])


def test_same_site_ports():
    assert same_site("https://a.ts.net", "a.ts.net")
    assert same_site("https://a.ts.net", "a.ts.net:443")
    assert same_site("http://1.2.3.4:8790", "1.2.3.4:8790")
    assert not same_site("http://1.2.3.4", "1.2.3.4:8790")
    assert not same_site("javascript:alert(1)", "1.2.3.4:8790")


@pytest.mark.parametrize("action", ["status", "range", "list", "all_calendars", "account_settings", "account_lists"])
def test_module_read_actions_allowed_by_get(action):
    assert module_get_allowed(action)


@pytest.mark.parametrize("action", ["add", "edit", "delete", "tick", "toggle", "sync", "show", "undo", "set_pref",
                                    "set_calendar", "set_list", "new_calendar", "disconnect", "connect", "finish",
                                    "save_client", "sharing", "ignore", ""])
def test_module_write_actions_need_post(action):
    assert not module_get_allowed(action)
