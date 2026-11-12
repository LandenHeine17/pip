"""The websearch module (D-125), against a pretend Brave Search: short snippets for Claude, the key from
secrets.toml, nothing but api.search.brave.com, and failures that leave Pip saying he couldn't find it."""
import httpx
import pytest

from pip_assistant.common.modules import ROOT, read
from pip_assistant.core.modules import Call, HostNotAllowed, Invalid, Registry

BRAVE = {"web": {"results": [
    {"title": "Nebraska <strong>Huskers</strong> beat Iowa", "description": "Nebraska won <strong>24&ndash;17</strong> on Friday night.",
     "age": "2 days ago"},
    {"title": "Huskers schedule", "description": "Next game: Saturday at 2:30 PM against Wisconsin. " + "More. " * 200},
    {"title": "Third", "description": "Not read: only two results are wanted."}]}}


def registry(tmp_path, monkeypatch, handler, key="BSA-test", results=2):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    (tmp_path / "secrets.toml").write_text(f'[websearch]\napi_key = "{key}"\n' if key else "")
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)
    reg = Registry.load({}, infos=[read(ROOT / "websearch", {"results": results})],
                        db_path=tmp_path / "pip.db", transport=httpx.MockTransport(wrapped))
    return reg.action("look:web"), seen


async def test_a_look_up_gives_claude_a_few_short_results(tmp_path, monkeypatch):
    act, seen = registry(tmp_path, monkeypatch, lambda r: httpx.Response(200, json=BRAVE))
    call = Call()
    text = await act.do(act.check({"do": "look", "kind": "web", "query": "  huskers   score "}, call), call)
    assert text.startswith("Web results for 'huskers score': 1. Nebraska Huskers beat Iowa: Nebraska won 24–17")
    assert "(2 days ago)" in text and "<strong>" not in text and "Third" not in text and len(text) < 800
    [req] = seen
    assert req.url.host == "api.search.brave.com" and req.url.params["q"] == "huskers score"
    assert req.url.params["count"] == "2" and req.headers["X-Subscription-Token"] == "BSA-test"


async def test_no_key_or_no_query_is_refused_before_anything_is_sent(tmp_path, monkeypatch):
    act, seen = registry(tmp_path, monkeypatch, lambda r: httpx.Response(200, json=BRAVE), key="")
    with pytest.raises(Invalid):
        act.check({"query": "huskers"}, Call())
    act, seen = registry(tmp_path, monkeypatch, lambda r: httpx.Response(200, json=BRAVE))
    for q in ("", "x" * 201):
        with pytest.raises(Invalid):
            act.check({"query": q}, Call())
    assert seen == []


@pytest.mark.parametrize("response", [httpx.Response(429), httpx.Response(200, json={"web": {"results": []}})])
async def test_a_failed_or_empty_search_raises(tmp_path, monkeypatch, response):
    """…so pip-core's look-up finds nothing and Pip says "Sorry, I couldn't find that." (core/acting.py)."""
    act, _ = registry(tmp_path, monkeypatch, lambda r: response)
    with pytest.raises((httpx.HTTPError, LookupError)):
        await act.do("huskers", Call())


async def test_only_brave_can_be_reached(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    reg = Registry.load({}, infos=[read(ROOT / "websearch")], db_path=tmp_path / "pip.db",
                        transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    ctx = reg.modules["websearch"].ctx
    async with ctx.http() as http:
        with pytest.raises(HostNotAllowed):
            await http.get("https://example.com/")
