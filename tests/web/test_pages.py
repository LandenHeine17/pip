"""The dashboard's pages (D-148): each is served, and each but the Guide gets the shared menu and status bar."""
from starlette.testclient import TestClient

from pip_assistant.web.main import PAGES, State, build_app


def test_every_page_is_served_with_the_shared_frame():
    client = TestClient(build_app(None, State(), {}))
    for path, name in PAGES.items():
        r = client.get(path)
        assert r.status_code == 200 and "text/html" in r.headers["content-type"], path
        assert '<script src="/pip.js"></script>' in r.text, path
        if name != "guide.html":   # the Guide keeps its own chapter list instead of the menu
            assert "<main>" in r.text, path
    js = client.get("/pip.js")
    assert js.status_code == 200 and "pip-nav" in js.text and "/api/modules" in js.text
    talk = client.get("/talk.js")
    assert talk.status_code == 200 and "javascript" in talk.headers["content-type"] and "/api/chat" in talk.text
    for path in ("/", "/talk"):
        assert '<script src="/talk.js"></script>' in client.get(path).text


def test_the_old_one_page_dashboards_parts_each_have_a_page():
    """What used to be squeezed onto one page: the feed and chat (Talk), people, memory, programs and the trace."""
    client = TestClient(build_app(None, State(), {}))
    assert 'id="feed"' in client.get("/talk").text
    assert "/api/memory/action" in client.get("/memory").text
    assert "/person?id=" in client.get("/people").text and "PipPerson" in client.get("/person").text
    system = client.get("/system").text
    assert 'id="programs"' in system and 'id="trace"' in system
